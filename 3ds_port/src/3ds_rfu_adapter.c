// Emulated GBA Wireless Adapter, its radio the 3DS's local wireless
// (3ds_local.c): other 3DS systems nearby in the same session. Called once a
// frame from CtrGame_VBlank.
//
// librfu talks to the adapter with commands (0x9966LLCC + LL words of
// parameters) and gets an acknowledgement (0x9966LL(CC|0x80) + LL words).
// On the GBA every word goes over SIO with an interrupt-driven handshake
// (librfu_intr.c); here STWI_start_Command hands the whole command to
// PortRfu_Command, which answers it at once, as librfu's interrupt handler
// would once the last word had arrived. The game code above librfu
// (AgbRfu_LinkManager.c, link_rfu_2.c, the Union Room) runs unchanged.
//
// The adapter can also talk first while the GBA is the clock slave (after
// the "...AndChange" commands): data from the other side is ready (0x28) or
// the link was lost (0x29). Those are delivered once a frame from
// PortRfu_Frame, like the serial interrupt would, except that a host's
// "data ready" comes straight after its send (see SendDataReady).
//
// Over local wireless:
//   - a host's broadcast data (what searching adapters list) goes to every
//     player in the session (LocalLink_SetAdvert);
//   - connecting, accepting, data frames and disconnecting are messages
//     between the two players (PEER_* below).
// A host only sends its next frame to its children once each of them has
// answered the last one (the "ack" bits of 0x28), so the link runs as fast as
// the round trip allows and slower links stall the link layer, which is
// what the game's link code expects of a busy radio.
//
// Link battles also have a direct channel (PlatformLinkBattle*, below):
// whole battle messages go straight between the players instead of through
// the link layer.
//
// Command reference: the GBA Wireless Adapter notes in afska's
// gba-link-connection (docs/wireless_adapter.md) and librfu itself.

#include <stdio.h>
#include "global.h"
#include "3ds_local.h"
#include "local_link.h"
#include "3ds_log.h"
#include "librfu.h"

enum
{
    PEER_CONNECT = 1,    // child -> host: let me in
    PEER_ACCEPT = 2,     // host -> child: u8 slot
    PEER_REJECT = 3,     // host -> child
    PEER_DATA = 4,       // u8 bytes, data: a frame of link data either way
    PEER_DISCONNECT = 5, // either way
    PEER_BATTLE = 6,     // u16 total, u16 offset, data: part of a battle message
    PEER_BLOCK = 7,      // the same for a link block (u8 sender's multiplayer id, block)
};

enum
{
    AD_IDLE,
    AD_HOST,       // hosting (open while hostOpen) or holding children
    AD_SEARCH,     // listing hosts
    AD_CONNECTING, // waiting for a host to accept us
    AD_CHILD,      // connected to a host
};

enum
{
    CONNECT_NONE,
    CONNECT_PENDING,
    CONNECT_ACCEPTED,
    CONNECT_REJECTED,
};

// SystemStatus states (bits 24-31)
enum
{
    SYS_IDLE = 0,
    SYS_HOST_CLOSED = 1,
    SYS_HOST_OPEN = 2,
    SYS_SEARCHING = 3,
    SYS_CONNECTING = 4,
    SYS_CLIENT = 5,
};

#define FRAME_QUEUE 16
#define MAX_FRAME_BYTES 127    // a host's frame is at most 87, a child's 31
#define PENDING_FRAMES 120     // how long a connect request waits for us to host
#define RECENT_HOST_MS 4000    // hosts stay listed this long after they stop
#define MAX_PENDING 4
#define MAX_IN_FLIGHT 10       // frames a host gets ahead of a child's answers
#define SIGNAL_GOOD 0xC0

struct FrameQueue
{
    u8 head, count;
    u8 len[FRAME_QUEUE];
    u8 data[FRAME_QUEUE][MAX_FRAME_BYTES];
};

struct Adapter
{
    u32 frame;
    u32 generation;      // session the links belong to
    u8 state;
    bool8 hostOpen;
    u8 maxChildren;
    bool8 haveBroadcast;
    u8 broadcast[LOCAL_ADVERT_SIZE];
    u32 setup;

    // As host
    struct
    {
        bool8 active;
        u16 id;
        struct FrameQueue frames;
        u32 sent, answered; // frames sent to it, and its answers
        bool8 leaving;      // gone: drop once its last frames are read
        u8 leaveReason;
    } children[RFU_CHILD_MAX];
    struct
    {
        u16 id;
        u32 since;
    } pending[MAX_PENDING];
    u8 numPending;

    // As child
    u16 hostId;
    u8 slot;
    u8 connect;
    bool8 owesReply;     // got a frame from the host and hasn't answered yet
    bool8 partialAck;    // as host: the last "data ready" left a child out
    bool8 linkLost;      // tell the game (0x29) when it next waits
    u8 linkLostReason;
    bool8 hostLeaving;   // host gone: lose the link once its last frames are read
    u8 hostLeavingReason;
    struct FrameQueue fromHost;
    u32 hostFramesIn, hostFramesOut; // frames from the host: arrived, and given to the game

    bool8 idleWaitPending; // waiting with no link at all: answer next frame
    u8 lostChildren;       // as host: children gone, to tell the game (0x29) when it next waits
    u8 lostChildrenReason;
};

static struct Adapter sA;

// The direct channels (see "Direct channels" below): messages in the order
// they came (our own too), and the ones still arriving in parts.
#define CHANNEL_MSG_MAX 1024
#define CHANNEL_QUEUE 64
#define CHANNEL_CHUNK 150 // fits a LocalEvent with the header

struct ChannelQueue
{
    u8 head, count;
    u16 len[CHANNEL_QUEUE];
    u32 after[CHANNEL_QUEUE]; // a child's blocks: host frames the game must have first
    u8 data[CHANNEL_QUEUE][CHANNEL_MSG_MAX];
};

static struct ChannelQueue sBattleQueue; // battle messages
static struct ChannelQueue sBlockQueue;  // link blocks: the sender's multiplayer id, then the block
static struct
{
    u16 from;
    u8 type;
    u16 total, got;
    u8 data[CHANNEL_MSG_MAX];
} sParts[2 * (RFU_CHILD_MAX + 1)];

static void ChannelReceive(u8 type, u16 from, const u8 *msg, u32 len);
static void ClearBlockChannel(void);

// rfu_trace=1 in settings.txt: log commands and events (to port.log, with
// debug.txt).
int CtrSettings_GetInt(const char *key, int fallback);
static int sTrace = -1;
#define TRACE(...) do { \
    if (sTrace < 0) sTrace = CtrSettings_GetInt("rfu_trace", 0); \
    if (sTrace > 0) { char _t[160]; snprintf(_t, sizeof(_t), __VA_ARGS__); \
        CtrLog_Write(CTR_LOG_GAME, "rfu %5u %s", (unsigned)sA.frame, _t); } \
} while (0)

// ---------------------------------------------------------------------------

static u16 MyId(void)
{
    u16 id = LocalLink_MyId();
    return id ? id : 1;
}

static void QueueClear(struct FrameQueue *q)
{
    q->head = q->count = 0;
}

static void QueuePush(struct FrameQueue *q, const u8 *data, u32 len)
{
    u8 i;
    if (q->count == FRAME_QUEUE)
    {
        // The game stopped reading: keep the newest.
        static unsigned told;
        if (told++ < 8)
            CtrLog_Write(CTR_LOG_ERROR, "rfu: frame queue full: oldest frame dropped");
        q->head = (q->head + 1) % FRAME_QUEUE;
        q->count--;
    }
    i = (q->head + q->count) % FRAME_QUEUE;
    if (len > MAX_FRAME_BYTES)
        len = MAX_FRAME_BYTES;
    q->len[i] = len;
    memcpy(q->data[i], data, len);
    q->count++;
}

// Returns the frame's length; the data is valid until the next push.
static u32 QueuePop(struct FrameQueue *q, const u8 **data)
{
    u8 i;
    if (q->count == 0)
        return 0;
    i = q->head;
    q->head = (q->head + 1) % FRAME_QUEUE;
    q->count--;
    *data = q->data[i];
    return q->len[i];
}

static void SendPeer(u16 id, u8 type, const void *data, u32 len)
{
    u8 buf[2 + MAX_FRAME_BYTES];
    buf[0] = type;
    if (len > sizeof(buf) - 1)
        len = sizeof(buf) - 1;
    if (len)
        memcpy(buf + 1, data, len);
    LocalLink_Send(id, buf, 1 + len);
}

static u8 NumChildren(void)
{
    u8 i, n = 0;
    for (i = 0; i < RFU_CHILD_MAX; i++)
        n += sA.children[i].active;
    return n;
}

static u8 NextFreeSlot(void)
{
    u8 i;
    if (NumChildren() >= sA.maxChildren)
        return 0xFF;
    for (i = 0; i < RFU_CHILD_MAX; i++)
        if (!sA.children[i].active)
            return i;
    return 0xFF;
}

static void UpdateAdvert(void)
{
    LocalLink_SetAdvert(sA.state == AD_HOST && sA.hostOpen, NextFreeSlot(),
                     sA.haveBroadcast ? sA.broadcast : NULL);
}

static void DropChild(u8 slot, bool8 tell)
{
    if (!sA.children[slot].active)
        return;
    if (tell)
        SendPeer(sA.children[slot].id, PEER_DISCONNECT, NULL, 0);
    sA.children[slot].active = FALSE;
    sA.children[slot].leaving = FALSE;
    QueueClear(&sA.children[slot].frames);
    sA.children[slot].sent = sA.children[slot].answered = 0;
}

static void LeaveHost(bool8 tell)
{
    if (sA.hostId && tell && (sA.connect == CONNECT_ACCEPTED || sA.connect == CONNECT_PENDING))
        SendPeer(sA.hostId, PEER_DISCONNECT, NULL, 0);
    sA.hostId = 0;
    sA.connect = CONNECT_NONE;
    sA.hostLeaving = FALSE;
    sA.owesReply = FALSE;
    QueueClear(&sA.fromHost);
    sA.hostFramesIn = sA.hostFramesOut = 0;
}

static void ResetAdapter(bool8 tell)
{
    u8 i;
    for (i = 0; i < RFU_CHILD_MAX; i++)
        DropChild(i, tell);
    for (i = 0; i < sA.numPending; i++)
        if (tell)
            SendPeer(sA.pending[i].id, PEER_REJECT, NULL, 0);
    sA.numPending = 0;
    LeaveHost(tell);
    sA.state = AD_IDLE;
    sA.hostOpen = FALSE;
    sA.haveBroadcast = FALSE;
    sA.maxChildren = RFU_CHILD_MAX;
    sA.linkLost = FALSE;
    sA.idleWaitPending = FALSE;
    sA.lostChildren = 0;
    sA.partialAck = FALSE;
    ClearBlockChannel();
    UpdateAdvert();
}

static bool8 AcceptChild(u16 id)
{
    u8 slot, i;

    for (i = 0; i < RFU_CHILD_MAX; i++)
    {
        if (sA.children[i].active && sA.children[i].id == id)
        {
            u8 s = i;
            SendPeer(id, PEER_ACCEPT, &s, 1); // asked again: same answer
            return TRUE;
        }
    }
    slot = NextFreeSlot();
    if (slot == 0xFF)
        return FALSE;
    if (NumChildren() == 0)
        ClearBlockChannel(); // a new link
    sA.children[slot].active = TRUE;
    sA.children[slot].id = id;
    QueueClear(&sA.children[slot].frames);
    sA.children[slot].sent = sA.children[slot].answered = 0;
    SendPeer(id, PEER_ACCEPT, &slot, 1);
    UpdateAdvert();
    return TRUE;
}

static void RemovePending(u8 index)
{
    sA.numPending--;
    memmove(&sA.pending[index], &sA.pending[index + 1], (sA.numPending - index) * sizeof(sA.pending[0]));
}

static void LostHost(u8 reason)
{
    LeaveHost(FALSE);
    if (sA.state == AD_CHILD || sA.state == AD_CONNECTING)
        sA.state = AD_IDLE;
    sA.linkLost = TRUE;
    sA.linkLostReason = reason;
}

// ---------------------------------------------------------------------------
// Messages from other players

static void PeerGone(u16 id, u8 reason);

static void HandlePeerMessage(u16 from, const u8 *msg, u32 len)
{
    u8 i;

    if (len == 0)
        return;
    TRACE("peer %04X msg %u len %u", from, msg[0], (unsigned)len);
    switch (msg[0])
    {
    case PEER_CONNECT:
        if (sA.state == AD_HOST && sA.hostOpen)
        {
            if (!AcceptChild(from))
                SendPeer(from, PEER_REJECT, NULL, 0);
        }
        else if (sA.numPending < MAX_PENDING)
        {
            // Not hosting this moment (Union Room adapters alternate): hold
            // the request for a while.
            for (i = 0; i < sA.numPending && sA.pending[i].id != from; i++)
                ;
            if (i == sA.numPending)
            {
                sA.pending[i].id = from;
                sA.pending[i].since = sA.frame;
                sA.numPending++;
            }
        }
        else
        {
            SendPeer(from, PEER_REJECT, NULL, 0);
        }
        break;
    case PEER_ACCEPT:
        if (sA.state == AD_CONNECTING && sA.hostId == from && sA.connect == CONNECT_PENDING && len >= 2)
        {
            sA.slot = msg[1] & 3;
            sA.connect = CONNECT_ACCEPTED;
            sA.state = AD_CHILD;
            sA.linkLost = FALSE;
            QueueClear(&sA.fromHost);
            sA.hostFramesIn = sA.hostFramesOut = 0;
            ClearBlockChannel();
        }
        else if (!(sA.state == AD_CHILD && sA.hostId == from))
        {
            SendPeer(from, PEER_DISCONNECT, NULL, 0); // we gave up meanwhile
        }
        break;
    case PEER_REJECT:
        if (sA.state == AD_CONNECTING && sA.hostId == from)
            sA.connect = CONNECT_REJECTED;
        break;
    case PEER_DATA:
        if (len < 2)
            break;
        if (sA.state == AD_CHILD && sA.hostId == from)
        {
            u8 count = sA.fromHost.count;
            QueuePush(&sA.fromHost, msg + 2, len - 2 < msg[1] ? len - 2 : msg[1]);
            if (sA.fromHost.count != count)
                sA.hostFramesIn++;
            break;
        }
        for (i = 0; i < RFU_CHILD_MAX; i++)
            if (sA.children[i].active && sA.children[i].id == from)
            {
                QueuePush(&sA.children[i].frames, msg + 2, len - 2 < msg[1] ? len - 2 : msg[1]);
                sA.children[i].answered++;
            }
        break;
    case PEER_DISCONNECT:
        PeerGone(from, REASON_DISCONNECTED);
        break;
    case PEER_BATTLE:
    case PEER_BLOCK:
        // Only from the link: a late one from a player no longer on it was
        // filed as the host's own block (player 0).
        if (sA.state == AD_HOST)
        {
            for (i = 0; i < RFU_CHILD_MAX; i++)
                if (sA.children[i].active && sA.children[i].id == from)
                    break;
            if (i == RFU_CHILD_MAX)
                break;
        }
        else if (!(sA.state == AD_CHILD && sA.hostId == from))
        {
            break;
        }
        ChannelReceive(msg[0], from, msg + 1, len - 1);
        break;
    }
}

// ---------------------------------------------------------------------------
// Direct channels
//
// The link layer (link_rfu_2.c) sends everything in the radio's frames: 12
// bytes of a block a frame, a child's only once the host has echoed it back,
// which costs a round trip or two per block on top. Two kinds of data skip
// it and go whole, straight to
// the other players: a host sends to all its children, a child to its host,
// which passes it on to the other children as the radio would; everyone gets
// their own back too, as from the link layer.
//  - Battle messages (battle_controllers.c): a command for a battler's
//    controller, an answer for the battle engine, "done".
//  - Link blocks (SendBlock: trades, Trainer Cards, the battle setup...),
//    delivered to the link layer as if they had come in frames.

static void QueuePushMessage(struct ChannelQueue *q, const u8 *data, u16 size, u32 after)
{
    u8 i;
    if (q->count == CHANNEL_QUEUE || size > CHANNEL_MSG_MAX)
    {
        /* A lost message stalls the battle or the link for good: say so. */
        CtrLog_Write(CTR_LOG_ERROR, "rfu: channel queue full (%u, %u bytes): message dropped", q->count, size);
        return;
    }
    i = (q->head + q->count) % CHANNEL_QUEUE;
    q->len[i] = size;
    q->after[i] = after;
    memcpy(q->data[i], data, size);
    q->count++;
}

// Removes the n-th message (0 = oldest) into `out`; returns its length.
static u16 QueueTakeMessage(struct ChannelQueue *q, u8 n, void *out, u16 max)
{
    u8 i = (q->head + n) % CHANNEL_QUEUE;
    u16 len = q->len[i] < max ? q->len[i] : max;
    memcpy(out, q->data[i], len);
    for (; n + 1 < q->count; n++)
    {
        u8 from = (q->head + n + 1) % CHANNEL_QUEUE;
        q->len[i] = q->len[from];
        q->after[i] = q->after[from];
        memcpy(q->data[i], q->data[from], q->len[from]);
        i = from;
    }
    q->count--;
    return len;
}

static void ChannelSendTo(u8 type, u16 to, const u8 *data, u16 size)
{
    u8 buf[1 + 4 + CHANNEL_CHUNK];
    u16 offset = 0;

    do
    {
        u16 n = size - offset < CHANNEL_CHUNK ? size - offset : CHANNEL_CHUNK;
        buf[0] = type;
        buf[1] = size;
        buf[2] = size >> 8;
        buf[3] = offset;
        buf[4] = offset >> 8;
        memcpy(buf + 5, data + offset, n);
        LocalLink_Send(to, buf, 5 + n);
        offset += n;
    } while (offset < size);
}

// To everyone else on the link (`except` aside: a host passing a child's on).
static void ChannelSendAll(u8 type, const u8 *data, u16 size, u16 except)
{
    u8 i;
    if (sA.state == AD_HOST)
    {
        for (i = 0; i < RFU_CHILD_MAX; i++)
            if (sA.children[i].active && !sA.children[i].leaving && sA.children[i].id != except)
                ChannelSendTo(type, sA.children[i].id, data, size);
    }
    else if (sA.state == AD_CHILD && sA.hostId != except)
    {
        ChannelSendTo(type, sA.hostId, data, size);
    }
}

static void ChannelReceive(u8 type, u16 from, const u8 *msg, u32 len)
{
    u16 total, offset;
    u8 i, slot = 0xFF;

    if (len < 4)
        return;
    total = msg[0] | (msg[1] << 8);
    offset = msg[2] | (msg[3] << 8);
    msg += 4;
    len -= 4;
    if (total > CHANNEL_MSG_MAX || offset + len > total)
        return;
    for (i = 0; i < ARRAY_COUNT(sParts); i++)
    {
        if (sParts[i].from == from && sParts[i].type == type)
            slot = i;
        else if (slot == 0xFF && sParts[i].from == 0)
            slot = i;
    }
    if (slot == 0xFF)
        return;
    if (offset == 0)
    {
        sParts[slot].from = from;
        sParts[slot].type = type;
        sParts[slot].total = total;
        sParts[slot].got = 0;
    }
    memcpy(sParts[slot].data + offset, msg, len);
    sParts[slot].got += len;
    if (sParts[slot].got < total)
        return;

    if (type == PEER_BLOCK)
    {
        TRACE("block in from %u: %u bytes", from, total);
        // The host files a child's block under the player index its link
        // layer gives that child's slot (as for the child's frames), and
        // sends it to all its children, the sender too: on the GBA the
        // host's frames carried every player's data, a child's own included.
        if (sA.state == AD_HOST)
        {
            for (i = 0; i < RFU_CHILD_MAX; i++)
                if (sA.children[i].active && sA.children[i].id == from)
                    sParts[slot].data[0] = PlatformRfuPlayerIndexForSlot(i);
            QueuePushMessage(&sBlockQueue, sParts[slot].data, total, 0);
            ChannelSendAll(type, sParts[slot].data, total, 0);
        }
        else
        {
            // Over the radio a block came in frames after the host's ones
            // before it (a block request, say), never ahead of them; here
            // it can overtake them on the way to the game, which may then
            // clear its received blocks (ready for the one it asked for)
            // and lose it. It waits until the frames that came before it,
            // and two more as the radio's shortest block took, are read.
            QueuePushMessage(&sBlockQueue, sParts[slot].data, total, sA.hostFramesIn + 2);
        }
    }
    else
    {
        QueuePushMessage(&sBattleQueue, sParts[slot].data, total, 0);
        if (sA.state == AD_HOST)
            ChannelSendAll(type, sParts[slot].data, total, from);
    }
    sParts[slot].from = 0;
}

static void ClearBlockChannel(void)
{
    sBlockQueue.head = sBlockQueue.count = 0;
    memset(sParts, 0, sizeof(sParts));
}

bool8 PlatformLocalLinkEnabled(void)
{
    return LocalLink_Enabled();
}

bool8 PlatformLocalLinkActive(void)
{
    if (!LocalLink_Enabled())
        return FALSE;
    if (sA.state == AD_HOST)
        return NumChildren() > 0;
    return sA.state == AD_CHILD && sA.connect == CONNECT_ACCEPTED;
}

void PlatformLinkBattleReset(void)
{
    sBattleQueue.head = sBattleQueue.count = 0;
}

void PlatformLinkBattleSend(const void *data, u16 size, bool8 toSelf)
{
    if (toSelf)
        QueuePushMessage(&sBattleQueue, data, size, 0);
    ChannelSendAll(PEER_BATTLE, data, size, 0);
}

u16 PlatformLinkBattleRecv(void *out, u16 max)
{
    if (sBattleQueue.count == 0)
        return 0;
    return QueueTakeMessage(&sBattleQueue, 0, out, max);
}

// A host's own blocks are player 0's and come straight back to it; a child's
// go to the host, which works out whose they are and sends them back to it
// (see ChannelReceive).
void PlatformLinkBlockSend(const void *data, u16 size)
{
    u8 msg[CHANNEL_MSG_MAX];
    if (size > sizeof(msg) - 1)
        size = sizeof(msg) - 1;
    msg[0] = 0;
    memcpy(msg + 1, data, size);
    TRACE("block out: %u bytes", size);
    if (sA.state == AD_HOST)
        QueuePushMessage(&sBlockQueue, msg, 1 + size, 0);
    ChannelSendAll(PEER_BLOCK, msg, 1 + size, 0);
}

u16 PlatformLinkBlockTake(u8 multiplayerId, void *out, u16 max)
{
    static u8 msg[CHANNEL_MSG_MAX];
    u8 n;
    for (n = 0; n < sBlockQueue.count; n++)
    {
        u8 i = (sBlockQueue.head + n) % CHANNEL_QUEUE;
        if (sBlockQueue.len[i] >= 1 && sBlockQueue.data[i][0] == multiplayerId)
        {
            if (sA.state == AD_CHILD && sA.hostFramesOut < sBlockQueue.after[i])
                return 0;
            u16 len = QueueTakeMessage(&sBlockQueue, n, msg, sizeof(msg));
            TRACE("block taken for player %u", multiplayerId);
            len = len - 1 < max ? len - 1 : max;
            memcpy(out, msg + 1, len);
            return len;
        }
    }
    return 0;
}

// A player disconnected (or left the session). Frames that already arrived from
// them are still read first: closing a link, the last one is often the
// "ready to close" that the game waits for (on the GBA it's never overtaken
// by the disconnect).
// A child went away without the game asking: as a real adapter does, it
// tells the game (0x29 with the slot) when it next waits, rather than the
// game only finding out when its own link-loss timeout runs out.
static void ChildLost(u8 slot, u8 reason)
{
    DropChild(slot, FALSE);
    sA.lostChildren |= 1 << slot;
    sA.lostChildrenReason = reason;
}

static void PeerGone(u16 id, u8 reason)
{
    u8 i;
    for (i = 0; i < RFU_CHILD_MAX; i++)
    {
        if (sA.children[i].active && sA.children[i].id == id)
        {
            if (sA.children[i].frames.count)
            {
                sA.children[i].leaving = TRUE;
                sA.children[i].leaveReason = reason;
            }
            else
            {
                ChildLost(i, reason);
            }
        }
    }
    for (i = 0; i < sA.numPending; i++)
        if (sA.pending[i].id == id)
            RemovePending(i--);
    if (sA.hostId == id)
    {
        if (sA.state == AD_CONNECTING)
        {
            sA.connect = CONNECT_REJECTED;
        }
        else if (sA.state == AD_CHILD && sA.fromHost.count)
        {
            sA.hostLeaving = TRUE;
            sA.hostLeavingReason = reason;
        }
        else
        {
            LostHost(reason);
        }
    }
    UpdateAdvert();
}

// Players that left once their last frames have been read.
static void FinishLeaving(void)
{
    u8 i;
    for (i = 0; i < RFU_CHILD_MAX; i++)
    {
        if (sA.children[i].active && sA.children[i].leaving && sA.children[i].frames.count == 0)
        {
            ChildLost(i, sA.children[i].leaveReason);
            UpdateAdvert();
        }
    }
    if (sA.hostLeaving && sA.fromHost.count == 0)
        LostHost(sA.hostLeavingReason);
}

// ---------------------------------------------------------------------------
// The adapter talking first (GBA is the clock slave)

static void DeliverToGame(u8 command, u8 length, u32 param)
{
    struct STWIStatus *st = gSTWIStatus;
    u32 *rx = (u32 *)st->rxPacket;

    TRACE("deliver %02X len %u param %08X", command, length, (unsigned)param);
    rx[0] = 0x99660000 | (length << 8) | command;
    if (length)
        rx[1] = param;
    st->reqLength = length;
    st->reqActiveCommand = command;
    st->ackActiveCommand = command + 0x80;
    st->ackLength = 0;
    st->error = 0;
    // As librfu_intr.c's slave handler: acknowledge, become the master again
    // and tell the library.
    st->msMode = AGB_CLK_MASTER;
    st->state = 0;
    if (st->callbackS != NULL)
        st->callbackS((length << 8) | command);
}

// After a host's send: which children "got" the frame. A host sends its next
// frame only when all have (link_rfu_2.c RfuMain2_Parent), otherwise it
// resends ("RetransmitAndChange") and the game waits a frame. On the GBA a
// child answers within the same frame; over local wireless its answer is a
// round trip away, so a host runs up to MAX_IN_FLIGHT frames ahead of a
// child's answers (which then arrive as if delayed) before it waits. The
// transport resends what the radio loses, so every frame does reach the
// child.
static void SendDataReady(void)
{
    u8 i, ack = 0;
    if (sA.lostChildren)
    {
        DeliverToGame(ID_DISCONNECTED_AND_CHANGE_REQ, 1, sA.lostChildren | (sA.lostChildrenReason << 8));
        sA.lostChildren = 0;
        return;
    }
    for (i = 0; i < RFU_CHILD_MAX; i++)
        if (sA.children[i].active && (sA.children[i].frames.count
         || (!sA.children[i].leaving && (s32)(sA.children[i].sent - sA.children[i].answered) <= MAX_IN_FLIGHT)))
            ack |= 1 << i;
    sA.partialAck = FALSE;
    for (i = 0; i < RFU_CHILD_MAX; i++)
        if (sA.children[i].active && !(ack & (1 << i)))
            sA.partialAck = TRUE;
    DeliverToGame(ID_DATA_READY_AND_CHANGE_REQ, 1, ack);
}

// ---------------------------------------------------------------------------
// Commands

static u32 ConnectStatus(void)
{
    switch (sA.connect)
    {
    case CONNECT_ACCEPTED:
        return MyId() | (sA.slot << 16);
    case CONNECT_PENDING:
        return 0x01000000;
    default:
        return 0x02FF0000; // failed
    }
}

// Children as "id | slot << 16" words, as PollConnections lists them.
static u8 ListChildren(u32 *out)
{
    u8 i, n = 0;
    for (i = 0; i < RFU_CHILD_MAX; i++)
        if (sA.children[i].active)
            out[n++] = sA.children[i].id | (i << 16);
    return n;
}

// Up to 4 hosts, each "id | next slot << 16" and its 6 words of broadcast
// data, in a stable order.
static u8 ListHosts(u32 *out)
{
    struct LocalPeer peers[LOCAL_MAX_PEERS];
    int n = LocalLink_GetPeers(peers, LOCAL_MAX_PEERS);
    int i, j, found = 0;

    for (i = 0; i < n; i++)
        for (j = i + 1; j < n; j++)
            if (peers[j].id < peers[i].id)
            {
                struct LocalPeer t = peers[i];
                peers[i] = peers[j];
                peers[j] = t;
            }
    for (i = 0; i < n && found < 4; i++)
    {
        // Union Room adapters alternate between hosting and searching: keep
        // listing a host for a while after it stops, as a radio search that
        // overlapped its hosting would have found it.
        if (!peers[i].hasAdvert || peers[i].hostedAgoMs > RECENT_HOST_MS)
            continue;
        out[found * 7] = peers[i].id | (peers[i].nextSlot << 16);
        memcpy(&out[found * 7 + 1], peers[i].advert, LOCAL_ADVERT_SIZE);
        found++;
    }
    return found * 7;
}

static void SendData(const u32 *req, u8 reqLength)
{
    u8 frame[1 + MAX_FRAME_BYTES];
    u32 avail = reqLength ? (reqLength - 1) * 4 : 0;
    u32 bytes;
    u8 i;

    if (sA.state == AD_HOST)
    {
        bytes = req[0] & 0x7F;
        frame[0] = bytes;
        memset(frame + 1, 0, bytes);
        memcpy(frame + 1, &req[1], bytes < avail ? bytes : avail);
        for (i = 0; i < RFU_CHILD_MAX; i++)
            if (sA.children[i].active && !sA.children[i].leaving)
            {
                SendPeer(sA.children[i].id, PEER_DATA, frame, 1 + bytes);
                sA.children[i].sent++;
            }
    }
    else if (sA.state == AD_CHILD)
    {
        bytes = (req[0] >> (8 + 5 * sA.slot)) & 0x1F;
        frame[0] = bytes;
        memset(frame + 1, 0, bytes);
        memcpy(frame + 1, &req[1], bytes < avail ? bytes : avail);
        SendPeer(sA.hostId, PEER_DATA, frame, 1 + bytes);
        sA.owesReply = FALSE;
    }
}

// The answer to the host's frame, even if there's nothing to send, so the
// host knows we got it.
static void SendEmptyReply(void)
{
    u8 frame = 0;
    if (sA.state == AD_CHILD && sA.owesReply)
    {
        SendPeer(sA.hostId, PEER_DATA, &frame, 1);
        sA.owesReply = FALSE;
    }
}

// Fills `out` (the words after the header) and returns the word count.
static u8 ReceiveData(u32 *out)
{
    u8 *bytes = (u8 *)&out[1];
    const u8 *data = NULL;
    u32 total = 0, len;
    u8 i;

    out[0] = 0;
    /*
     * A cycle a child was left out of: the game reads none of the frames
     * received in it (link_rfu_2.c RfuMain2_Parent reads them only when
     * every child acknowledged) and doesn't clear their "new data" flags, so
     * the next cycle's frames overwrote them and librfu called it an error
     * (ERR_RECV_DATA_OVERWRITED: the 4-player link failed as its players
     * came into the Colosseum one by one). They wait for the next cycle.
     */
    if (sA.state == AD_HOST && sA.partialAck)
        return 0;
    if (sA.state == AD_HOST)
    {
        for (i = 0; i < RFU_CHILD_MAX; i++)
        {
            if (!sA.children[i].active)
                continue;
            struct FrameQueue *q = &sA.children[i].frames;

            /* Room first: a frame that does not fit stays for the next call. */
            len = q->count ? q->len[q->head] : 0;
            if (len > 31)
                len = 31;
            if (total + len > 0x6C)
                break;
            QueuePop(q, &data);
            out[0] |= len << (8 + 5 * i);
            memcpy(bytes + total, data, len);
            total += len;
        }
    }
    else if (sA.state == AD_CHILD)
    {
        if (sA.fromHost.count)
            sA.hostFramesOut++;
        len = QueuePop(&sA.fromHost, &data);
        if (len > 0x6C)
            len = 0x6C;
        out[0] = len;
        if (len)
            memcpy(bytes, data, len);
        total = len;
    }
    if (out[0] == 0)
        return 0;
    while (total & 3)
        bytes[total++] = 0;
    return 1 + total / 4;
}

static void Disconnect(u8 slots)
{
    u8 i;
    if (sA.state == AD_HOST)
    {
        for (i = 0; i < RFU_CHILD_MAX; i++)
            if ((slots >> i) & 1)
                DropChild(i, TRUE);
        if (NumChildren() == 0 && !sA.hostOpen)
            sA.state = AD_IDLE;
        UpdateAdvert();
    }
    else if (sA.state == AD_CHILD || sA.state == AD_CONNECTING)
    {
        LeaveHost(TRUE);
        sA.state = AD_IDLE;
    }
}

static u8 SystemStatus(void)
{
    switch (sA.state)
    {
    case AD_HOST:
        return sA.hostOpen ? SYS_HOST_OPEN : SYS_HOST_CLOSED;
    case AD_SEARCH:
        return SYS_SEARCHING;
    case AD_CONNECTING:
        return SYS_CONNECTING;
    case AD_CHILD:
        return SYS_CLIENT;
    default:
        return SYS_IDLE;
    }
}

static void CheckGeneration(void)
{
    u32 gen = LocalLink_Generation();
    if (gen != sA.generation)
    {
        // A new session: links from before are gone.
        sA.generation = gen;
        if (sA.state == AD_CHILD)
            LostHost(REASON_LINK_LOSS);
        else
            LeaveHost(FALSE);
        {
            // Lost, not dropped quietly: the session's "left" events find
            // no child any more, and without 0x29 the game only noticed when
            // librfu's link watch ran out.
            u8 i;
            for (i = 0; i < RFU_CHILD_MAX; i++)
            {
                if (sA.state == AD_HOST && sA.children[i].active)
                    ChildLost(i, REASON_LINK_LOSS);
                else
                    DropChild(i, FALSE);
            }
        }
        sA.numPending = 0;
        UpdateAdvert();
    }
}

void PortRfu_Command(void)
{
    struct STWIStatus *st = gSTWIStatus;
    u8 command = st->reqActiveCommand;
    const u32 *req = st->txPacket->rfuPacket32.data;
    u8 reqLength = st->reqLength;
    u32 *rx = (u32 *)st->rxPacket;
    u32 *ack = rx + 1;
    u8 ackLength = 0;
    bool8 reject = FALSE;
    bool8 change = FALSE;
    u8 i;

    CheckGeneration();

    switch (command)
    {
    case ID_RESET_REQ:
        ResetAdapter(TRUE);
        break;
    case ID_LINK_STATUS_REQ:
        ack[0] = 0;
        if (sA.state == AD_HOST)
        {
            for (i = 0; i < RFU_CHILD_MAX; i++)
                if (sA.children[i].active)
                    ack[0] |= SIGNAL_GOOD << (8 * i);
        }
        else if (sA.state == AD_CHILD)
        {
            ack[0] = SIGNAL_GOOD << (8 * sA.slot);
        }
        ackLength = 1;
        break;
    case ID_VERSION_STATUS_REQ:
        ack[0] = 0x00830117;
        ackLength = 1;
        break;
    case ID_SYSTEM_STATUS_REQ:
        ack[0] = (u32)SystemStatus() << 24;
        if (sA.state == AD_HOST || sA.state == AD_CHILD || sA.state == AD_CONNECTING)
            ack[0] |= MyId();
        if (sA.state == AD_CHILD)
            ack[0] |= (1 << sA.slot) << 16;
        ackLength = 1;
        break;
    case ID_SLOT_STATUS_REQ:
        ack[0] = sA.state == AD_HOST ? NextFreeSlot() : 0xFF;
        ackLength = 1 + ListChildren(&ack[1]);
        break;
    case ID_CONFIG_STATUS_REQ:
        memcpy(ack, sA.broadcast, LOCAL_ADVERT_SIZE);
        ack[6] = sA.setup;
        ack[7] = 257;
        ackLength = sA.state == AD_HOST ? 8 : 7;
        break;
    case ID_GAME_CONFIG_REQ:
        if (reqLength >= 6)
        {
            memcpy(sA.broadcast, req, LOCAL_ADVERT_SIZE);
            sA.haveBroadcast = TRUE;
            UpdateAdvert();
        }
        break;
    case ID_SYSTEM_CONFIG_REQ:
        sA.setup = req[0];
        sA.maxChildren = RFU_CHILD_MAX - ((req[0] >> 16) & 3);
        break;
    case ID_SC_START_REQ: // start hosting
        if (sA.state == AD_CHILD || sA.state == AD_CONNECTING)
            LeaveHost(TRUE);
        sA.state = AD_HOST;
        sA.hostOpen = TRUE;
        UpdateAdvert();
        break;
    case ID_SC_POLL_REQ:
        ackLength = sA.state == AD_HOST ? ListChildren(ack) : 0;
        break;
    case ID_SC_END_REQ:
        if (sA.state == AD_HOST)
        {
            sA.hostOpen = FALSE;
            ackLength = ListChildren(ack);
            if (NumChildren() == 0)
                sA.state = AD_IDLE;
            UpdateAdvert();
        }
        break;
    case ID_SP_START_REQ: // start searching
        if (sA.state == AD_HOST && NumChildren() == 0)
            sA.hostOpen = FALSE;
        if (sA.state != AD_HOST || NumChildren() == 0)
            sA.state = AD_SEARCH;
        UpdateAdvert();
        break;
    case ID_SP_POLL_REQ:
        ackLength = ListHosts(ack);
        break;
    case ID_SP_END_REQ:
        ackLength = ListHosts(ack);
        if (sA.state == AD_SEARCH)
            sA.state = AD_IDLE;
        break;
    case ID_CP_START_REQ: // connect to a host
        LeaveHost(TRUE);
        sA.hostId = req[0] & 0xFFFF;
        sA.connect = CONNECT_PENDING;
        sA.state = AD_CONNECTING;
        sA.linkLost = FALSE;
        SendPeer(sA.hostId, PEER_CONNECT, NULL, 0);
        break;
    case ID_CP_POLL_REQ:
        ack[0] = ConnectStatus();
        ackLength = 1;
        break;
    case ID_CP_END_REQ:
        ack[0] = ConnectStatus();
        ackLength = 1;
        if (sA.connect != CONNECT_ACCEPTED)
        {
            LeaveHost(TRUE);
            sA.state = AD_IDLE;
        }
        break;
    case ID_DATA_TX_AND_CHANGE_REQ:
        change = TRUE;
        // fallthrough
    case ID_DATA_TX_REQ:
        SendData(req, reqLength);
        break;
    case ID_DATA_RX_REQ:
        ackLength = ReceiveData(ack);
        break;
    case ID_MS_CHANGE_REQ:
    case ID_UNK35_REQ:
    case ID_RESUME_RETRANSMIT_AND_CHANGE_REQ:
        change = TRUE;
        break;
    case ID_DISCONNECT_REQ:
        Disconnect(req[0]);
        break;
    case ID_TEST_MODE_REQ:
    case ID_CPR_START_REQ:
    case ID_UNK36_REQ:
        break;
    case ID_CPR_POLL_REQ:
    case ID_CPR_END_REQ:
        ack[0] = 1; // reconnecting isn't supported: never recovers
        ackLength = 1;
        break;
    case ID_STOP_MODE_REQ:
        ResetAdapter(TRUE);
        break;
    default:
        reject = TRUE;
        break;
    }

    if (change)
        SendEmptyReply();

    if (reject)
    {
        rx[0] = 0x996601EE;
        rx[1] = 2;
        st->ackLength = 1;
        st->ackActiveCommand = 0xEE;
        st->error = ERR_REQ_CMD_ACK_REJECTION;
        st->state = 4;
        change = FALSE;
    }
    else
    {
        rx[0] = 0x99660000 | (ackLength << 8) | (command | 0x80);
        st->ackLength = ackLength;
        st->ackActiveCommand = command | 0x80;
        st->error = 0;
        if (change)
        {
            st->msMode = AGB_CLK_SLAVE;
            st->state = 5;
        }
        else
        {
            st->state = 4;
        }
    }
    st->sending = 0;
    TRACE("cmd %02X len %u -> %s ack %u [%08X %08X] state %u", command, reqLength, reject ? "REJECT" : "ok",
          ackLength, (unsigned)(ackLength ? ack[0] : 0), (unsigned)(ackLength > 1 ? ack[1] : 0), sA.state);
    if (st->callbackM != NULL)
        st->callbackM(st->reqActiveCommand, st->error);

    if (change && st->msMode == AGB_CLK_SLAVE)
    {
        if (sA.state == AD_HOST)
            SendDataReady();
        else if (sA.state != AD_CHILD)
            sA.idleWaitPending = TRUE;
    }
}

s32 PortRfu_CheckID(void)
{
    return LocalLink_Enabled() ? RFU_ID : 0;
}

// Once a frame, from the vblank (3ds_game_full.c).
void PortRfu_Frame(void)
{
    struct LocalEvent ev;
    struct STWIStatus *st = gSTWIStatus;
    u8 i;

    if (!LocalLink_Enabled())
        return;
    LocalSession_Update();
    if (st == NULL)
        return;
    sA.frame++;
    CheckGeneration();

    while (LocalLink_PollEvent(&ev))
    {
        if (ev.type == LOCAL_EV_RECV)
            HandlePeerMessage(ev.id, ev.data, ev.len);
        else if (ev.type == LOCAL_EV_LEFT)
            PeerGone(ev.id, REASON_LINK_LOSS);
    }
    FinishLeaving();

    for (i = 0; i < sA.numPending; i++)
    {
        if (sA.state == AD_HOST && sA.hostOpen)
        {
            if (!AcceptChild(sA.pending[i].id))
                SendPeer(sA.pending[i].id, PEER_REJECT, NULL, 0);
            RemovePending(i--);
        }
        else if (sA.frame - sA.pending[i].since > PENDING_FRAMES)
        {
            SendPeer(sA.pending[i].id, PEER_REJECT, NULL, 0);
            RemovePending(i--);
        }
    }

    // Waiting for the adapter (clock slave): tell the game what happened.
    if (st->msMode == AGB_CLK_SLAVE && !st->sending)
    {
        if (sA.linkLost)
        {
            sA.linkLost = FALSE;
            sA.idleWaitPending = FALSE;
            DeliverToGame(ID_DISCONNECTED_AND_CHANGE_REQ, 1, (1 << sA.slot) | (sA.linkLostReason << 8));
        }
        else if (sA.state == AD_HOST && sA.lostChildren)
        {
            DeliverToGame(ID_DISCONNECTED_AND_CHANGE_REQ, 1, sA.lostChildren | (sA.lostChildrenReason << 8));
            sA.lostChildren = 0;
        }
        else if (sA.state == AD_CHILD && sA.fromHost.count)
        {
            sA.owesReply = TRUE;
            DeliverToGame(ID_DATA_READY_AND_CHANGE_REQ, 0, 0);
        }
        else if (sA.idleWaitPending && sA.state != AD_CHILD)
        {
            sA.idleWaitPending = FALSE;
            DeliverToGame(ID_DATA_READY_AND_CHANGE_REQ, 0, 0);
        }
    }
}
