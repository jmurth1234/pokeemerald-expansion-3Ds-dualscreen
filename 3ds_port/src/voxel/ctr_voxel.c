/*
 * Voxel overworld on Citro3D.
 *
 * This is the only file of the voxel module that knows what a GPU is. It owns
 * the shader, the metatile atlas textures, the chunk cache in VRAM, the staging
 * buffers and the matrices; the map, the camera, the atlas composition and the
 * geometry come from the SDK-free modules beside it.
 *
 * Frame ownership is unchanged: CtrVideo_Present() opens and closes the one
 * Citro3D frame, and nothing here calls C3D_Init, C3D_FrameBegin or
 * C3D_FrameEnd. Update() runs inside the open frame, after FrameBegin has
 * waited for the previous frame's GPU work, so every staging buffer written
 * last frame is free again when it starts.
 *
 * What an Old 3DS can afford decides the shape of everything below:
 *
 *   - nothing is rebuilt, re-hashed or re-read on a frame where nothing
 *     changed. A frame that only walks costs the draw calls and the billboards;
 *   - the work that does come - a chunk entering the view, an atlas, a
 *     building page - is spent against a time budget measured from the frame
 *     itself, nearest first, and done ahead of time when the frame has room;
 *   - no file is read on the render thread while the game waits for it.
 */

#include <3ds.h>
#include <citro3d.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "3ds_log.h"
#include "3ds_platform.h"
#include "3ds_video.h"

#include "ctr_voxel.h"
#include "voxel_arena.h"
#include "voxel_atlas.h"
#include "voxel_camera.h"
#include "voxel_entities.h"
#include "voxel_regions.h"
#include "voxel_mesh_builder.h"
#include "voxel_world.h"
#include "voxel_tree.h"
#include "voxel_building.h"
#include "voxel_relief.h"
#include "voxel_lighting.h"
#include "voxel_sign.h"

#define VOXEL_SHADER_PATH "romfs:/shaders/voxel.shbin"

#ifndef CTR_VOXEL_TRACE
#define CTR_VOXEL_TRACE 0
#endif

/*
 * The depth buffer is 16-bit. At 0.1 the near plane spent its precision on the
 * first tile before the eye, and at the player's distance (8-17 units) a cast
 * shadow 0.03 over the ground no longer told apart from it: striped, and gone
 * next to the feet. Nothing on screen comes within a unit of the eye (the
 * lowest camera is ~4.5 up), and 1.0 gives ten times the resolution.
 */
#define VOXEL_NEAR 1.0f
#define VOXEL_FAR 200.0f

/* The logical surface is a 512x256 texture of which the game uses the top-left
 * 400x240. The 2D compositor reaches that sub-rectangle through its ortho
 * projection; a 3D pass has to fold the same mapping into its own matrix,
 * because the viewport covers the whole texture. */
#define VOXEL_SURFACE_W 512.0f
#define VOXEL_SURFACE_H 256.0f

/* ── GPU vertex format ──────────────────────────────────────────────────────
 *
 * The builder works in VoxelVertex (six floats, 24 bytes). What the GPU reads
 * is this: the texture coordinates stay float, because the building pages are
 * not powers of two and their texel edges must stay exact, while position and
 * shade become shorts - positions in 1/512 tile relative to the draw's own
 * origin (a chunk corner, or the camera for the billboards), shade in 1/16384.
 * voxel.v.pica undoes both scales.
 *
 * A third less of everything: VRAM per chunk, staging, GPU copies and vertex
 * fetch. 1/512 of a tile is 1/32 of a pixel, and the origin a vertex is
 * measured from is always a whole tile, so two chunks quantise a shared edge
 * to exactly the same place.
 */
typedef struct
{
    float u, v;
    int16_t x, y, z, shade;
} VoxelGpuVertex;

#define VOXEL_POS_SCALE 512.0f
#define VOXEL_SHADE_SCALE 16384.0f

/* Vertices a single chunk may produce before the builder refuses more. */
#if CTR_VOXEL_LIGHTING
/* Up to 64 ground quads gain 18 vertices at a shadow boundary. Preserve the
 * original structure budget instead of letting refinement evict walls. */
#define VOXEL_CHUNK_SCRATCH (8192u + 64u * 18u)
#define VOXEL_SHADOW_RESERVE (VOXEL_SPRITE_SLOTS * VOXEL_CAST_SHADOW_VERTICES)
#else
#define VOXEL_CHUNK_SCRATCH 8192u
#define VOXEL_SHADOW_RESERVE 0u
#endif

/*
 * Linear memory the frame's chunk uploads are packed into, in build order.
 * Each queued GPU copy still owns its source until the frame has completed,
 * so the pool is only rewound at the start of the next Update. What does not
 * fit waits for the next frame; a single chunk always fits.
 */
#define VOXEL_STAGING_VERTICES 12288u

/*
 * GPU copies queued per frame for chunks. Inside a frame every
 * C3D_SyncTextureCopy splits the command list and adds up to two entries to
 * Citro3D's GX queue, which holds 32 per frame - and a full queue is not a
 * wait but svcBreak(USERBREAK_PANIC) in gxCmdQueueAdd. The frame's own splits,
 * its display transfer, one atlas and one page slice come on top of these.
 */
#define VOXEL_CHUNK_UPLOADS_MAX 8u

/*
 * Vertices held back for the billboards. Sixteen object events of six vertices
 * each, rounded up. Without the reserve a dense town would leave the player
 * undrawn, which is a far worse failure than a missing wall.
 */
#define VOXEL_SPRITE_RESERVE 128u
#define VOXEL_REFLECTION_RESERVE (VOXEL_SPRITE_SLOTS * VOXEL_REFLECTION_VERTICES)
#define VOXEL_DYNAMIC_VERTICES (VOXEL_SPRITE_RESERVE + VOXEL_SHADOW_RESERVE + VOXEL_REFLECTION_RESERVE)
#define VOXEL_SHADOW_FIRST VOXEL_SPRITE_RESERVE
#define VOXEL_REFLECTION_FIRST (VOXEL_SHADOW_FIRST + VOXEL_SHADOW_RESERVE)

/* One atlas per tileset pair, shared by every map that uses that pair. Six:
 * walking back and forth through a string of towns and routes reaches five
 * or six pairs, and with four they evicted each other on every crossing. */
#define VOXEL_ATLAS_SLOTS 6u

typedef struct
{
    C3D_Tex tex;
    const void *primaryTileset;
    const void *secondaryTileset;
    uint32_t stamp;
    uint32_t generation;
    /* Bumped when ids are added in place (an extending atlas job): old
     * UVs stay valid, only chunks that missed an id need building again. */
    uint32_t extension;
    bool extendPending, uncoveredRetried;
    bool valid;
    VoxelAtlasMap map;
} VoxelAtlasSlot;

static bool sReady;
static DVLB_s *sDvlb;
static shaderProgram_s sProgram;
static int sUniProjection = -1, sUniModelView = -1;
static int sUniShadeTint = -1, sUniTintDiff = -1, sUniGrade = -1, sUniFog = -1;
static VoxelVertex *sScratch;          /* chunk builder output, ordinary heap */
static VoxelVertex *sDynamicScratch;   /* billboard builder output */
static VoxelGpuVertex *sStaging;       /* chunk uploads, linear */
static unsigned sStagingUsed, sChunkUploads;
static VoxelGpuVertex *sDynamic;       /* billboards and shadows, linear */
static VoxelBuilder sBuilder;
static VoxelCamera sCamera;
static VoxelAtlasSlot sAtlases[VOXEL_ATLAS_SLOTS];
static uint32_t sAtlasStamp;
static bool sAtlasCapped;
/* One atlas upload per frame: the queued copy still owns this source after
 * AcquireAtlas returns. Cache hits need no upload and remain unrestricted. */
static uint16_t *sAtlasStaging;
static bool sAtlasUploadPending;
/* One CPU copy of the active atlas. Keeping all six would exhaust Old 3DS
 * memory; inactive atlases are rebased only when they become current again. */
static uint16_t *sAnimShadow;
static VoxelAtlasSlot *sAnimAtlas;
static uint8_t sAnimDirty[128];
static bool sAnimPending, sAnimEverDirty;

void CtrVoxel_NotifyTilesetAnimWrite(unsigned firstTile, unsigned tileCount)
{
    if (firstTile >= 1024) return;
    if (tileCount > 1024 - firstTile) tileCount = 1024 - firstTile;
    for (unsigned t = firstTile; t < firstTile + tileCount; ++t)
        sAnimDirty[t >> 3] |= 1u << (t & 7);
    sAnimPending = sAnimEverDirty = true;
}
static bool AtlasJobBusy(void);
static void AtlasJobCancel(void);
static CtrVoxelStats sStats;
static const char *sStatus = "init";
static uint32_t sFrame;

/*
 * A tileset pair whose atlas could not be built is remembered, because
 * retrying it is the single most expensive thing this module does and doing it
 * once per frame is what turns a broken map into a 30 fps slideshow.
 */
static const void *sFailedPrimary, *sFailedSecondary;
static bool sHasFailed;

/* ── Packing ────────────────────────────────────────────────────────────── */

static void JobCancel(void);

static unsigned sPackErrors;
/* The furthest value that did not fit, and which of x, y, z or shade it was:
 * what the log needs to say which builder produced it. */
static float sPackWorst;
static char sPackAxis;

static int16_t Quantise(float value, float scale)
{
    float scaled = value * scale + 0.5f;
    int whole;

    /*
     * Round half up, never half away from zero: that is what keeps the
     * result of a shared edge independent of the origin it is measured from.
     * floor() by truncation and one correction, not floorf: ARMv6 has no
     * rounding instruction and the library call was a third of a chunk build.
     */
    if (scaled >= 32767.0f || scaled < -32768.0f)
    {
        if (scaled >= 32768.0f || scaled < -32768.0f)
        {
            ++sPackErrors;
            if (fabsf(value) > fabsf(sPackWorst))
                sPackWorst = value;
        }
        return scaled >= 0.0f ? 32767 : -32768;
    }
    whole = (int)scaled;
    if ((float)whole > scaled)
        --whole;
    return (int16_t)whole;
}

static void Pack(const VoxelVertex *src, unsigned count, VoxelGpuVertex *dst)
{
    for (unsigned i = 0; i < count; ++i)
    {
        dst[i].u = src[i].u;
        dst[i].v = src[i].v;
        unsigned before = sPackErrors;

        dst[i].x = Quantise(src[i].x, VOXEL_POS_SCALE);
        dst[i].y = Quantise(src[i].y, VOXEL_POS_SCALE);
        dst[i].z = Quantise(src[i].z, VOXEL_POS_SCALE);
        if (sPackErrors != before && sPackAxis == 0)
            sPackAxis = 'p';
        dst[i].shade = Quantise(src[i].shade, VOXEL_SHADE_SCALE);
        if (sPackErrors != before && sPackAxis == 0)
            sPackAxis = 's';
    }
}

/* Says which mesh held values the vertex format cannot carry, at most once
 * every few seconds: they are clamped, so a steady stream is one fault. */
static void ReportPackErrors(const char *what, int a, int b, int c, int d)
{
    static uint32_t sReported;

    if (sPackErrors != 0 && (sReported == 0 || sFrame - sReported >= 300))
    {
        sReported = sFrame;
        ++sStats.errors;
        CtrLog_Write(CTR_LOG_ERROR, "VOXEL: %u vertices out of packing range in %s %d,%d of %d:%d "
                     "(worst %s %.2f)", sPackErrors, what, a, b, c, d,
                     sPackAxis == 's' ? "shade" : "position", sPackWorst);
    }
    sPackErrors = 0;
    sPackWorst = 0.0f;
    sPackAxis = 0;
}

/* ── Static chunks ───────────────────────────────────────────────────────
 *
 * The map is cut into squares of VOXEL_CHUNK tiles and each square is meshed
 * once, into VRAM, relative to its own corner. Walking brings new squares into
 * view; the ones already built are drawn where they are.
 *
 * Relative to the map rather than to the world, because crossing a border
 * moves every map on screen: the map's position becomes a translation at draw
 * time, so a crossing costs one matrix instead of every chunk of every map.
 *
 * The belt of border metatiles outside every map is cut the same way, on a
 * grid of the current map, and cached like any other chunk. It used to be one
 * mesh of the whole view, rebuilt - shadows and all - whenever the view moved
 * two tiles, which near a town's edge was a spike every other step.
 *
 * A chunk is rebuilt only when what it was built from changes: its tiles and
 * the casters that shade it (a signature of both), the tilesets, or its
 * atlas. The signature itself is only recomputed when the world has changed
 * at all - see the world epoch below.
 */
#define VOXEL_CHUNK 8
#define VOXEL_CHUNK_SLOTS 128u
/*
 * The meshes' own block of VRAM, claimed whole at start-up and cut by
 * voxel_arena.c: taken piece by piece from the console's allocator, a few
 * thousand variable-sized meshes left VRAM in crumbs that no building page or
 * atlas could be placed in. 96K vertices: Rustboro's view alone draws 54K, and
 * the prefetch ring around it has to fit as well.
 */
#define VOXEL_CHUNK_VRAM_BUDGET (1536u * 1024u)

typedef struct
{
    bool used, border;
    int mapGroup, mapNum; /* a border chunk carries the current map's */
    int cx, cy;           /* chunk coordinates, local to the map */
    uint32_t hash, epoch;
    /* Found stale in epoch `staleEpoch` with signature `staleHash`: not
     * hashed again every frame it waits for its rebuild. */
    uint32_t staleHash, staleEpoch;
    const void *primary, *secondary, *layout;
    VoxelAtlasSlot *atlas;
    uint32_t atlasGeneration;
    VoxelGpuVertex *vram;
    unsigned bytes, count, terrainCount;
    unsigned buildingFirst; /* trees are [terrainCount, buildingFirst) */
    int buildingPage;       /* texture page of [buildingFirst, count) */
    uint32_t stamp;         /* last frame the view or the prefetch ring saw it */
    uint32_t viewStamp;     /* last frame it was on screen */
    /* Tiles its geometry covers, relative to the chunk corner: more than the
     * chunk's own square wherever a building or a crown reaches past it. */
    int gx0, gz0, gx1, gz1;
    float buildMs;          /* what its last build cost */
    /* Built with ids its atlas lacked, against this extension of it. */
    bool uncovered;
    uint32_t atlasExtension;
} VoxelChunk;

static VoxelChunk sChunks[VOXEL_CHUNK_SLOTS];
static void *sChunkBlock;
static VoxelArena sChunkArena;
/* One more than the slots: a rebuild takes its new block before it frees the
 * old one. */
static VoxelArenaPiece sChunkPieces[VOXEL_CHUNK_SLOTS + 1];

/* What is drawn this frame. */
#define VOXEL_DRAW_MAX 160u

typedef struct
{
    const VoxelChunk *chunk;
    int worldX, worldZ; /* the chunk corner's place in the world this frame */
} VoxelChunkDraw;

static VoxelChunkDraw sDraws[VOXEL_DRAW_MAX];
static unsigned sDrawCount;

/*
 * How far the camera reaches, in tiles, at a 40-degree pitch and a 35-degree
 * field of view. It looks north, so it sees more than twice as far ahead of
 * the player as behind. Getting these wrong northwards is the one visible
 * failure: the far end of the view turns black.
 */
#define VOXEL_VIEW_NORTH 14
#define VOXEL_VIEW_SOUTH 6
#define VOXEL_VIEW_SIDE  14
/* Tiles of view beyond what the camera reaches. */
#define VOXEL_WINDOW_SLACK 1
/*
 * Chunks this far outside the view are built ahead of time, with whatever the
 * frame has to spare, so that by the time a step brings them in they are
 * already there. One chunk: the player never outwalks it.
 */
#define VOXEL_PREFETCH VOXEL_CHUNK

/*
 * World epoch. Bumped when a live tile changes (the digest of the live grid
 * moves) or the set of maps on screen does (a crossing, a warp). Everything
 * cached against the world - chunk signatures, the lighting caches - is
 * checked again only when it has moved. A chunk remembers the epoch it was
 * last verified in.
 */
static uint32_t sEpoch = 1;
static uint32_t sLiveDigest, sInstanceSignature;

static int sMeshMapGroup = -1, sMeshMapNum = -1;
/* Worst overflow logged so far: reporting only the first one hid how far
 * over budget a town really goes, and whether a change has helped. */
static unsigned sWorstDropped;
static bool sOverflowReported;

/* Time the previous Update spent on builds, so the next budget can tell the
 * frame's own cost from what this module added to it. */
static float sLastBuildMs;

static C3D_Tex sSpriteAtlas;
static C3D_Tex sTreeAtlas;
/* The modelled buildings' own art, ground made transparent. Empty when
 * buildings.bin is absent, and then the region extrusion draws them. */
static bool sHaveBuildings;
static unsigned sSpriteVertices;
static unsigned sReflectionVertices;
#if CTR_VOXEL_LIGHTING
static unsigned sShadowVertices;
#endif
static int sDynamicX, sDynamicZ; /* the billboards' origin this frame */

/* ── Building pages ─────────────────────────────────────────────────────────
 *
 * The buildings' texture pages, one per map, in VRAM. The pages of the maps on
 * screen are what is needed - the current map and its connections - so four
 * are cached and the least recently used is reused, never one drawn in the
 * last two frames (its draws may still be queued).
 *
 * A page is up to 512 KiB of RomFS. Read on the render thread, as it used to
 * be, a slice of it stalled the frame for as long as the SD card took, which
 * an emulator never shows. A worker thread reads the slices instead, into a
 * linear buffer the GPU then copies into the page: VRAM is not the CPU's to
 * write (a data abort on hardware), and the render thread only ever queues
 * the copy. The worker runs below the game's priority, so it takes the CPU
 * only while the game is waiting for the display - and a read spends almost
 * all of its time waiting for the card anyway.
 */
#define VOXEL_BUILDING_PAGES 4
#define VOXEL_PAGE_SLICE (64u * 1024u) /* texels per read: 128 KiB */
#define VOXEL_PAGE_VRAM_BUDGET (768u * 1024u)
/* A page that found no room is asked for again only after this long. */
#define VOXEL_PAGE_RETRY_FRAMES 120u

typedef struct
{
    int page;           /* -1: empty */
    unsigned w, h;
    unsigned loaded;    /* texels copied so far; w*h when ready */
    C3D_Tex tex;
    uint32_t used;      /* frame last needed */
} BuildingPageSlot;
static BuildingPageSlot sPageSlots[VOXEL_BUILDING_PAGES];

enum { STREAM_IDLE, STREAM_READING, STREAM_DONE, STREAM_FAILED };

static struct
{
    Thread thread;
    LightEvent wake;
    volatile bool quit;
    volatile int state;
    /* The request, written by the render thread before it wakes the worker. */
    BuildingPageSlot *slot;
    int page;
    unsigned first, count;
    uint16_t *buffer;   /* linear, VOXEL_PAGE_SLICE texels */
    bool copyQueued;    /* the buffer is the source of this frame's copy */
} sStream;

/* Pages that failed to read are not asked for again: retrying one every
 * frame would only log the same error for as long as its map is on screen.
 * One flag a page, for as many pages as buildings.bin can name. */
#define VOXEL_MAX_PAGES 256
static bool sBadPages[VOXEL_MAX_PAGES];

/*
 * The pages' block of VRAM, claimed whole at start-up like the meshes' (see
 * VOXEL_CHUNK_VRAM_BUDGET): the 512 KiB page of a city needs 512 KiB in one
 * piece, which a VRAM shared with everything else stopped having. Pages of
 * 256 KiB and more are placed from the bottom, smaller ones from the top, so
 * the small ones never strand a large gap.
 */
static void *sPageBlock;
static VoxelArena sPageArena;
static VoxelArenaPiece sPagePieces[VOXEL_BUILDING_PAGES];
static uint32_t sPageRetry[VOXEL_MAX_PAGES];
static bool sPageNoRoomLogged[VOXEL_MAX_PAGES];

/* A page texture over a piece of the arena. Never C3D_TexDelete'd: that
 * would hand the piece to the console's allocator, which never gave it. */
static void PageTexInit(C3D_Tex *tex, void *data, unsigned w, unsigned h)
{
    memset(tex, 0, sizeof(*tex));
    tex->data = data;
    tex->fmt = GPU_RGBA5551;
    tex->size = w * h * sizeof(uint16_t);
    tex->width = (u16)w;
    tex->height = (u16)h;
    tex->param = GPU_TEXTURE_MODE(GPU_TEX_2D);
    C3D_TexSetFilter(tex, GPU_NEAREST, GPU_NEAREST);
    C3D_TexSetWrap(tex, GPU_CLAMP_TO_EDGE, GPU_CLAMP_TO_EDGE);
}

static void PageFree(BuildingPageSlot *slot)
{
    VoxelArena_Free(&sPageArena, slot->tex.data);
    memset(&slot->tex, 0, sizeof(slot->tex));
    slot->page = -1;
    slot->loaded = 0;
}

/* Could this slot's page be dropped now? Not if drawn in the last two
 * frames (its draws may still be queued), nor while the stream uses it. */
static bool PageDroppable(const BuildingPageSlot *slot)
{
    return slot->tex.data != NULL && sFrame - slot->used >= 2
        && !(sStream.state != STREAM_IDLE && sStream.slot == slot)
        && !(sStream.copyQueued && sStream.slot == slot);
}

/*
 * The worker also reads region layouts ahead of need (see
 * VoxelRegions_ReadDetached): one request at a time, handed over like a page
 * slice - the render thread writes the request, the worker the result.
 */
enum { AHEAD_IDLE, AHEAD_READING, AHEAD_DONE };

static struct
{
    volatile int state;
    unsigned layout;
    void *result;
    unsigned failed;    /* the last layout that would not read: not asked again */
} sAhead;

static void StreamWorker(void *arg)
{
    (void)arg;
    for (;;)
    {
        LightEvent_Wait(&sStream.wake);
        if (sStream.quit)
            break;
        if (sStream.state == STREAM_READING)
        {
            bool ok = VoxelBuildings_ReadPage((unsigned)sStream.page, sStream.first,
                                              sStream.count, sStream.buffer);

            __sync_synchronize();
            sStream.state = ok ? STREAM_DONE : STREAM_FAILED;
        }
        if (sAhead.state == AHEAD_READING)
        {
            sAhead.result = VoxelRegions_ReadDetached(sAhead.layout);
            __sync_synchronize();
            sAhead.state = AHEAD_DONE;
        }
    }
}

/* Installs what the worker read, and asks it for the next layout one
 * crossing away that is not held yet. */
static void ReadAhead(void)
{
    unsigned layouts[16], count;

    if (sStream.thread == NULL)
        return;
    if (sAhead.state == AHEAD_DONE)
    {
        __sync_synchronize();
        if (sAhead.result == NULL)
            sAhead.failed = sAhead.layout;
        VoxelRegions_Adopt(sAhead.result);
        sAhead.result = NULL;
        sAhead.state = AHEAD_IDLE;
    }
    if (sAhead.state != AHEAD_IDLE)
        return;
    count = VoxelWorld_NextLayouts(layouts, 16);
    for (unsigned i = 0; i < count; ++i)
        if (layouts[i] != sAhead.failed && VoxelRegions_Wanted(layouts[i]))
        {
            sAhead.layout = layouts[i];
            __sync_synchronize();
            sAhead.state = AHEAD_READING;
            LightEvent_Signal(&sStream.wake);
            return;
        }
}

static void StreamStart(void)
{
    s32 priority = 0x30;

    memset(&sStream, 0, sizeof(sStream));
    memset(sBadPages, 0, sizeof(sBadPages));
    sStream.buffer = linearAlloc(VOXEL_PAGE_SLICE * sizeof(uint16_t));
    if (sStream.buffer == NULL)
    {
        CtrLog_Write(CTR_LOG_ERROR, "VOXEL: no linear memory for the page stream; "
                     "pages borrow the atlas staging");
        return;
    }
    LightEvent_Init(&sStream.wake, RESET_ONESHOT);
    svcGetThreadPriority(&priority, CUR_THREAD_HANDLE);
    sStream.thread = threadCreate(StreamWorker, NULL, 16 * 1024,
                                  priority < 0x3F ? priority + 1 : 0x3F, -2, false);
    /* Without the worker the reads stay on this thread, one slice a frame:
     * slower to arrive, but still never more than a slice per frame. */
    if (sStream.thread == NULL)
        CtrLog_Write(CTR_LOG_ERROR, "VOXEL: no page stream thread; reading on the render thread");
}

static void StreamStop(void)
{
    if (sStream.thread != NULL)
    {
        sStream.quit = true;
        LightEvent_Signal(&sStream.wake);
        threadJoin(sStream.thread, U64_MAX);
        threadFree(sStream.thread);
    }
    /* A layout the worker finished reading is still installed; one it never
     * started is simply not read. */
    if (sAhead.state == AHEAD_DONE)
        VoxelRegions_Adopt(sAhead.result);
    memset(&sAhead, 0, sizeof(sAhead));
    linearFree(sStream.buffer);
    memset(&sStream, 0, sizeof(sStream));
}

static BuildingPageSlot *FindPage(int page)
{
    for (unsigned i = 0; i < VOXEL_BUILDING_PAGES; ++i)
        if (sPageSlots[i].page == page && sPageSlots[i].tex.data != NULL)
            return &sPageSlots[i];
    return NULL;
}

/* Claims a slot for a page some chunk needs; it is filled by StreamPages. */
static void WantPage(int page)
{
    BuildingPageSlot *victim = NULL;
    unsigned w, h;

    if (page < 0 || page >= VOXEL_MAX_PAGES || sBadPages[page] || sFrame < sPageRetry[page])
        return;
    if ((victim = FindPage(page)) != NULL)
    {
        victim->used = sFrame;
        return;
    }
    for (unsigned i = 0; i < VOXEL_BUILDING_PAGES; ++i)
    {
        BuildingPageSlot *slot = &sPageSlots[i];

        if (slot->page < 0)
        {
            victim = slot;
            break;
        }
        if (!PageDroppable(slot))
            continue;
        if (victim == NULL || slot->used < victim->used)
            victim = slot;
    }
    if (victim == NULL || !VoxelBuildings_PageSize((unsigned)page, &w, &h))
        return;
    if (victim->tex.data != NULL && (victim->w != w || victim->h != h))
        PageFree(victim);
    /*
     * Room in the arena, dropping the least recently needed pages that are
     * safe to drop until the piece fits. When it cannot fit - everything else
     * resident is on screen - the page is asked for again a while later, not
     * every frame: that retry, and its error line, each frame is what once
     * brought a city to a crawl.
     */
    while (victim->tex.data == NULL)
    {
        unsigned bytes = w * h * sizeof(uint16_t);
        void *block = VoxelArena_Alloc(&sPageArena, bytes, bytes < 256u * 1024u);
        BuildingPageSlot *drop = NULL;

        if (block != NULL)
        {
            PageTexInit(&victim->tex, block, w, h);
            victim->w = w;
            victim->h = h;
            break;
        }
        for (unsigned i = 0; i < VOXEL_BUILDING_PAGES; ++i)
        {
            BuildingPageSlot *slot = &sPageSlots[i];

            if (slot != victim && PageDroppable(slot) && (drop == NULL || slot->used < drop->used))
                drop = slot;
        }
        if (drop == NULL)
        {
            /* A page not needed this frame - the map left behind by a warp -
             * is droppable in a frame or two: ask again then, not seconds
             * later, or the new map stands without its buildings meanwhile. */
            bool soon = false;

            for (unsigned i = 0; i < VOXEL_BUILDING_PAGES; ++i)
                if (&sPageSlots[i] != victim && sPageSlots[i].tex.data != NULL
                 && sPageSlots[i].used != sFrame)
                    soon = true;
            victim->page = -1;
            sPageRetry[page] = sFrame + (soon ? 2u : VOXEL_PAGE_RETRY_FRAMES);
            if (!soon)
            {
                if (!sPageNoRoomLogged[page])
                {
                    sPageNoRoomLogged[page] = true;
                    ++sStats.errors;
                    CtrLog_Write(CTR_LOG_ERROR, "VOXEL: no room for the %ux%u building page %d "
                                 "(largest gap %lu of %lu KiB); retrying later",
                                 w, h, page, (unsigned long)VoxelArena_LargestGap(&sPageArena),
                                 (unsigned long)(sPageArena.size >> 10));
                }
            }
            return;
        }
        PageFree(drop);
    }
    victim->page = page;
    victim->loaded = 0;
    victim->used = sFrame;
}

static void MarkBadPage(int page)
{
    ++sStats.errors;
    CtrLog_Write(CTR_LOG_ERROR, "VOXEL: building page %d unreadable", page);
    if (page >= 0 && page < VOXEL_MAX_PAGES)
        sBadPages[page] = true;
}

/* The most recently needed page that is still coming in. */
static BuildingPageSlot *NextPageSlot(void)
{
    BuildingPageSlot *next = NULL;

    for (unsigned i = 0; i < VOXEL_BUILDING_PAGES; ++i)
    {
        BuildingPageSlot *slot = &sPageSlots[i];

        if (slot->page >= 0 && slot->tex.data != NULL && slot->loaded < slot->w * slot->h
         && (next == NULL || slot->used > next->used))
            next = slot;
    }
    return next;
}

static unsigned SliceOf(const BuildingPageSlot *slot)
{
    unsigned count = slot->w * slot->h - slot->loaded;

    return count > VOXEL_PAGE_SLICE ? VOXEL_PAGE_SLICE : count;
}

static void QueuePageCopy(BuildingPageSlot *slot, uint16_t *source, unsigned count)
{
    GSPGPU_FlushDataCache(source, count * sizeof(uint16_t));
    C3D_SyncTextureCopy((u32 *)source, 0,
                        (u32 *)((uint16_t *)slot->tex.data + slot->loaded), 0,
                        count * sizeof(uint16_t), 8);
    slot->loaded += count;
    if (slot->loaded == slot->w * slot->h)
        CtrLog_Write(CTR_LOG_VIDEO, "VOXEL building page %d loaded (%ux%u)",
                     slot->page, slot->w, slot->h);
}

/* One step of the page stream per frame: hand a finished slice to the GPU,
 * then ask the worker for the next one. */
static void StreamPages(void)
{
    BuildingPageSlot *next;

    /* FrameBegin waited for last frame's copy: the buffer is free again. */
    sStream.copyQueued = false;
    if (sStream.buffer == NULL)
    {
        /* No buffer of its own: borrow the atlas staging on a frame that is
         * not uploading an atlas, and read here, one slice at a time. */
        if (sAtlasUploadPending || AtlasJobBusy() || sAtlasStaging == NULL
         || (next = NextPageSlot()) == NULL)
            return;
        if (!VoxelBuildings_ReadPage((unsigned)next->page, next->loaded, SliceOf(next),
                                     sAtlasStaging))
        {
            MarkBadPage(next->page);
            next->page = -1;
            return;
        }
        QueuePageCopy(next, sAtlasStaging, SliceOf(next));
        sAtlasUploadPending = true;
        return;
    }

    if (sStream.state == STREAM_DONE || sStream.state == STREAM_FAILED)
    {
        BuildingPageSlot *slot = sStream.slot;
        bool current = slot->page == sStream.page && slot->loaded == sStream.first;

        __sync_synchronize();
        if (sStream.state == STREAM_FAILED)
        {
            MarkBadPage(sStream.page);
            if (current)
                slot->page = -1;
        }
        else if (current)
        {
            QueuePageCopy(slot, sStream.buffer, sStream.count);
            sStream.copyQueued = true;
        }
        sStream.state = STREAM_IDLE;
    }
    if (sStream.state != STREAM_IDLE || sStream.copyQueued || (next = NextPageSlot()) == NULL)
        return;
    sStream.slot = next;
    sStream.page = next->page;
    sStream.first = next->loaded;
    sStream.count = SliceOf(next);
    if (sStream.thread != NULL)
    {
        sStream.state = STREAM_READING;
        __sync_synchronize();
        LightEvent_Signal(&sStream.wake);
    }
    else
    {
        /* Picked up at the top of the next frame, like the worker's result. */
        sStream.state = VoxelBuildings_ReadPage((unsigned)sStream.page, sStream.first,
                                                sStream.count, sStream.buffer)
                      ? STREAM_DONE : STREAM_FAILED;
    }
}

/* The page's texture, or NULL while it is still coming in. */
static C3D_Tex *BuildingPage(int page)
{
    BuildingPageSlot *slot = page >= 0 ? FindPage(page) : NULL;

    if (slot == NULL || slot->loaded < slot->w * slot->h)
        return NULL;
    slot->used = sFrame;
    return &slot->tex;
}

/* ── Chunk cache ────────────────────────────────────────────────────────── */

static void ReleaseChunk(VoxelChunk *chunk)
{
    VoxelArena_Free(&sChunkArena, chunk->vram);
    memset(chunk, 0, sizeof(*chunk));
}

static void ReleaseAllChunks(void)
{
    for (unsigned i = 0; i < VOXEL_CHUNK_SLOTS; ++i)
        ReleaseChunk(&sChunks[i]);
    sDrawCount = 0;
}

static VoxelChunk *FindChunk(bool border, int mapGroup, int mapNum, int cx, int cy)
{
    for (unsigned i = 0; i < VOXEL_CHUNK_SLOTS; ++i)
    {
        const VoxelChunk *c = &sChunks[i];

        if (c->used && c->cx == cx && c->cy == cy && c->border == border
         && c->mapGroup == mapGroup && c->mapNum == mapNum)
            return &sChunks[i];
    }
    return NULL;
}

/*
 * The chunk that has gone longest without being needed. A build for the view
 * may take anything not on screen this frame; a build ahead of time may take
 * only what this frame has not touched at all, or the ring would evict itself.
 */
static VoxelChunk *OldestChunk(bool forView)
{
    VoxelChunk *victim = NULL;

    for (unsigned i = 0; i < VOXEL_CHUNK_SLOTS; ++i)
    {
        VoxelChunk *c = &sChunks[i];

        if (!c->used || c->viewStamp == sFrame || (!forView && c->stamp == sFrame))
            continue;
        if (victim == NULL || c->stamp < victim->stamp)
            victim = c;
    }
    return victim;
}

static VoxelChunk *FreeChunkSlot(bool forView)
{
    for (unsigned i = 0; i < VOXEL_CHUNK_SLOTS; ++i)
        if (!sChunks[i].used)
            return &sChunks[i];
    return OldestChunk(forView);
}

/* VRAM for one chunk, evicting the least recently needed until there is room. */
static VoxelGpuVertex *ChunkVram(unsigned bytes, bool forView)
{
    for (;;)
    {
        VoxelChunk *victim;
        void *block;

        block = VoxelArena_Alloc(&sChunkArena, bytes, false);
        if (block != NULL)
            return block;
        victim = OldestChunk(forView);
        if (victim == NULL)
            return NULL;
        ReleaseChunk(victim);
    }
}

/* ── Init / shutdown ────────────────────────────────────────────────────── */

bool CtrVoxel_Init(void)
{
    const char *step = "open " VOXEL_SHADER_PATH;
    FILE *file = fopen(VOXEL_SHADER_PATH, "rb");
    long size;
    void *blob = NULL;

    if (file == NULL)
        goto fail;
    if (fseek(file, 0, SEEK_END) != 0 || (size = ftell(file)) <= 0
     || fseek(file, 0, SEEK_SET) != 0)
    {
        fclose(file);
        step = "read voxel.shbin";
        goto fail;
    }
    blob = malloc((size_t)size);
    if (blob == NULL || fread(blob, 1, (size_t)size, file) != (size_t)size)
    {
        fclose(file);
        free(blob);
        blob = NULL;
        step = "read voxel.shbin";
        goto fail;
    }
    fclose(file);

    step = "parse voxel.shbin";
    sDvlb = DVLB_ParseFile(blob, (u32)size);
    /* DVLB_ParseFile keeps pointers into the blob, so it is never freed. */
    if (sDvlb == NULL || sDvlb->numDVLE == 0)
        goto fail;
    shaderProgramInit(&sProgram);
    if (R_FAILED(shaderProgramSetVsh(&sProgram, &sDvlb->DVLE[0])))
    {
        step = "bind vertex shader";
        goto fail;
    }
    sUniProjection = shaderInstanceGetUniformLocation(sProgram.vertexShader, "projection");
    sUniModelView = shaderInstanceGetUniformLocation(sProgram.vertexShader, "modelView");
    sUniShadeTint = shaderInstanceGetUniformLocation(sProgram.vertexShader, "shadeTint");
    sUniTintDiff = shaderInstanceGetUniformLocation(sProgram.vertexShader, "tintDiff");
    sUniGrade = shaderInstanceGetUniformLocation(sProgram.vertexShader, "grade");
    sUniFog = shaderInstanceGetUniformLocation(sProgram.vertexShader, "fog");
    if (sUniProjection < 0 || sUniModelView < 0 || sUniShadeTint < 0
     || sUniTintDiff < 0 || sUniGrade < 0 || sUniFog < 0)
    {
        step = "locate shader uniforms";
        goto fail;
    }

    /* First, while VRAM is still in one piece. */
    step = "chunk mesh VRAM";
    sChunkBlock = vramAlloc(VOXEL_CHUNK_VRAM_BUDGET);
    if (sChunkBlock == NULL)
        goto fail;
    VoxelArena_Init(&sChunkArena, sChunkBlock, VOXEL_CHUNK_VRAM_BUDGET, 16,
                    sChunkPieces, VOXEL_CHUNK_SLOTS + 1);
    /* Not fatal: without it the houses fall back to the region path, exactly
     * as without buildings.bin. */
    sPageBlock = vramAlloc(VOXEL_PAGE_VRAM_BUDGET);
    VoxelArena_Init(&sPageArena, sPageBlock, sPageBlock != NULL ? VOXEL_PAGE_VRAM_BUDGET : 0,
                    128, sPagePieces, VOXEL_BUILDING_PAGES);
    memset(sPageRetry, 0, sizeof(sPageRetry));
    memset(sPageNoRoomLogged, 0, sizeof(sPageNoRoomLogged));

    step = "chunk build scratch";
    sScratch = malloc(VOXEL_CHUNK_SCRATCH * sizeof(VoxelVertex));
    if (sScratch == NULL)
        goto fail;
    sDynamicScratch = malloc(VOXEL_DYNAMIC_VERTICES * sizeof(VoxelVertex));
    if (sDynamicScratch == NULL)
        goto fail;
    step = "chunk staging in linear memory";
    sStaging = linearAlloc(VOXEL_STAGING_VERTICES * sizeof(VoxelGpuVertex));
    if (sStaging == NULL)
        goto fail;
    step = "billboard buffer in linear memory";
    sDynamic = linearAlloc(VOXEL_DYNAMIC_VERTICES * sizeof(VoxelGpuVertex));
    if (sDynamic == NULL)
        goto fail;
    VoxelRegions_Init();
    VoxelRelief_Init();
    VoxelSign_Init();
    VoxelCamera_Init(&sCamera);

    step = "atlas staging buffer in linear memory";
    sAtlasStaging = linearAlloc(VOXEL_ATLAS_PIXELS * sizeof(uint16_t));
    if (sAtlasStaging == NULL)
        goto fail;

    step = "sprite atlas in linear memory";
    if (!C3D_TexInit(&sSpriteAtlas, VOXEL_SPRITE_ATLAS_DIM, VOXEL_SPRITE_ATLAS_DIM, GPU_RGBA5551))
        goto fail;
    C3D_TexSetFilter(&sSpriteAtlas, GPU_NEAREST, GPU_NEAREST);
    C3D_TexSetWrap(&sSpriteAtlas, GPU_CLAMP_TO_EDGE, GPU_CLAMP_TO_EDGE);
    memset(sSpriteAtlas.data, 0, VOXEL_SPRITE_PIXELS * sizeof(uint16_t));
    VoxelEntities_Reset();

    step = "tree texture " VOXEL_TREE_TEXTURE_PATH;
    if (!C3D_TexInit(&sTreeAtlas, VOXEL_TREE_TEXTURE_DIM, VOXEL_TREE_TEXTURE_DIM, GPU_RGBA5551))
        goto fail;
    file = fopen(VOXEL_TREE_TEXTURE_PATH, "rb");
    if (file == NULL)
        goto fail;
    {
        size_t bytes = VOXEL_TREE_TEXTURE_DIM * VOXEL_TREE_TEXTURE_DIM * sizeof(uint16_t);
        bool loaded = fread(sTreeAtlas.data, 1, bytes, file) == bytes;
        fclose(file);
        if (!loaded)
            goto fail;
    }
    C3D_TexSetFilter(&sTreeAtlas, GPU_NEAREST, GPU_NEAREST);
    C3D_TexSetWrap(&sTreeAtlas, GPU_CLAMP_TO_EDGE, GPU_CLAMP_TO_EDGE);

    /* Not fatal: without models the houses fall back to the region path. */
    for (unsigned i = 0; i < VOXEL_BUILDING_PAGES; ++i)
        sPageSlots[i].page = -1;
    sHaveBuildings = sPageBlock != NULL && VoxelBuildings_Init();
    if (sHaveBuildings)
        StreamStart();

    /* Optional: animation only. Failure must not prevent the overworld from
     * loading when homebrew linear memory is tighter than on Azahar. */
    sAnimShadow = linearAlloc(VOXEL_ATLAS_PIXELS * sizeof(uint16_t));
    if (sAnimShadow == NULL)
        CtrLog_Write(CTR_LOG_ERROR, "VOXEL: no linear memory for animated atlas shadow");
    sAnimAtlas = NULL;
    sReady = true;
    CtrLog_Write(CTR_LOG_VIDEO, "VOXEL lighting: %s",
                 CTR_VOXEL_LIGHTING ? "fixed sun + baked shadows + cast sprite shadows" : "disabled");
    CtrLog_Write(CTR_LOG_VIDEO,
                 "VOXEL init: %u staging + %u billboard vertices (%lu KiB linear), up to %u VRAM "
                 "atlases of %lu KiB (%ux%u, %u slots), page stream %s, linear free=%lu, VRAM free=%lu",
                 VOXEL_STAGING_VERTICES, VOXEL_DYNAMIC_VERTICES,
                 (unsigned long)((VOXEL_STAGING_VERTICES + VOXEL_DYNAMIC_VERTICES)
                                 * sizeof(VoxelGpuVertex) >> 10),
                 VOXEL_ATLAS_SLOTS,
                 (unsigned long)(VOXEL_ATLAS_PIXELS * sizeof(uint16_t) >> 10),
                 VOXEL_ATLAS_W, VOXEL_ATLAS_H, VOXEL_ATLAS_MAX_SLOTS,
                 sStream.thread != NULL ? "threaded" : "inline",
                 (unsigned long)linearSpaceFree(), (unsigned long)vramSpaceFree());
    return true;

fail:
    CtrLog_Write(CTR_LOG_ERROR, "VOXEL init failed: %s (linear free=%lu)",
                 step, (unsigned long)linearSpaceFree());
    free(blob);
    CtrVoxel_Shutdown();
    return false;
}

void CtrVoxel_Shutdown(void)
{
    StreamStop();
    AtlasJobCancel();
    ReleaseAllChunks();
    VoxelRegions_Shutdown();
    VoxelSign_Shutdown();
    linearFree(sAnimShadow);
    sAnimShadow = NULL;
    sAnimAtlas = NULL;
    memset(sAnimDirty, 0, sizeof(sAnimDirty));
    sAnimPending = sAnimEverDirty = false;
    linearFree(sAtlasStaging);
    sAtlasStaging = NULL;
    for (unsigned i = 0; i < VOXEL_ATLAS_SLOTS; ++i)
    {
        if (sAtlases[i].tex.data != NULL)
            C3D_TexDelete(&sAtlases[i].tex);
        memset(&sAtlases[i], 0, sizeof(sAtlases[i]));
    }
    if (sSpriteAtlas.data != NULL)
    {
        C3D_TexDelete(&sSpriteAtlas);
        memset(&sSpriteAtlas, 0, sizeof(sSpriteAtlas));
    }
    if (sTreeAtlas.data != NULL)
    {
        C3D_TexDelete(&sTreeAtlas);
        memset(&sTreeAtlas, 0, sizeof(sTreeAtlas));
    }
    for (unsigned i = 0; i < VOXEL_BUILDING_PAGES; ++i)
    {
        memset(&sPageSlots[i], 0, sizeof(sPageSlots[i]));
        sPageSlots[i].page = -1;
    }
    memset(&sPageArena, 0, sizeof(sPageArena));
    memset(&sChunkArena, 0, sizeof(sChunkArena));
    if (sPageBlock != NULL)
        vramFree(sPageBlock);
    if (sChunkBlock != NULL)
        vramFree(sChunkBlock);
    sPageBlock = sChunkBlock = NULL;
    sHaveBuildings = false;
    VoxelBuildings_Shutdown();
    VoxelRelief_Shutdown();
    free(sScratch);
    sScratch = NULL;
    free(sDynamicScratch);
    sDynamicScratch = NULL;
    JobCancel();
    linearFree(sStaging);
    sStaging = NULL;
    linearFree(sDynamic);
    sDynamic = NULL;
    if (sDvlb != NULL)
    {
        shaderProgramFree(&sProgram);
        DVLB_Free(sDvlb);
        sDvlb = NULL;
    }
    sUniProjection = sUniModelView = -1;
    sAtlasCapped = false;
    sMeshMapGroup = sMeshMapNum = -1;
    sReady = false;
}

bool CtrVoxel_IsAvailable(void)
{
#if CTR_VOXEL_ENABLED
    if (sReady && VoxelWorld_IsMapAvailable())
        return true;
    /* Not an overworld frame: the 2D compositor owns it. Recorded so the
     * overlay can tell "not the overworld" from "the voxel path broke". */
    sStatus = sReady ? "2d" : "off";
    return false;
#else
    return false;
#endif
}

const CtrVoxelStats *CtrVoxel_GetStats(void) { return &sStats; }

/* ── Atlas cache ────────────────────────────────────────────────────────── */

static unsigned CountAllocatedAtlases(void)
{
    unsigned count = 0;

    for (unsigned i = 0; i < VOXEL_ATLAS_SLOTS; ++i)
        if (sAtlases[i].tex.data != NULL)
            ++count;
    return count;
}

/*
 * Least recently used slot that already owns a texture and was not claimed
 * during this update.
 *
 * That last condition is the whole point. Several maps are visible at once,
 * and if more tileset pairs are on screen than there are atlases, an
 * unguarded LRU would evict the atlas the previous instance just built and
 * rebuild it again next frame, for ever. Returning NULL instead drops one map
 * from the frame, which is visible but cheap and self-correcting.
 */
static uint32_t sRebuildStamp;

static VoxelAtlasSlot *LeastRecentlyUsedAllocated(void)
{
    VoxelAtlasSlot *lru = NULL;

    for (unsigned i = 0; i < VOXEL_ATLAS_SLOTS; ++i)
        if (sAtlases[i].tex.data != NULL && sAtlases[i].stamp <= sRebuildStamp
         && (lru == NULL || sAtlases[i].stamp < lru->stamp))
            lru = &sAtlases[i];
    return lru;
}

static VoxelAtlasSlot *FindAtlas(const VoxelMapInstance *inst)
{
    for (unsigned i = 0; i < VOXEL_ATLAS_SLOTS; ++i)
    {
        VoxelAtlasSlot *slot = &sAtlases[i];

        if (slot->valid && slot->primaryTileset == inst->primaryTileset
         && slot->secondaryTileset == inst->secondaryTileset)
            return slot;
    }
    return NULL;
}

/*
 * The one atlas being composed, over as many frames as it takes (see
 * VoxelAtlas_JobBegin). Into the staging buffer, which it owns until it ends;
 * its table is installed with the upload, never before.
 */
static struct
{
    VoxelAtlasJob job;
    VoxelAtlasSlot *slot;
    bool forView;       /* a map on screen waits for it */
} sAtlasJob;

static bool AtlasJobBusy(void)
{
    return sAtlasJob.job.active;
}

static void AtlasJobCancel(void)
{
    if (sAtlasJob.job.active)
        VoxelAtlas_JobCancel(&sAtlasJob.job);
    sAtlasJob.slot = NULL;
}

static void AtlasFailed(int mapGroup, int mapNum, const void *primary, const void *secondary)
{
    ++sStats.errors;
    sHasFailed = true;
    sFailedPrimary = primary;
    sFailedSecondary = secondary;
    CtrLog_Write(CTR_LOG_ERROR, "VOXEL: atlas for %d:%d has no composable metatiles",
                 mapGroup, mapNum);
}

/*
 * The atlas of a map, or NULL while it is still being composed. `mayEvict` is
 * false for a map that is only in the prefetch ring: it may take an empty
 * slot, never the atlas of something else - which could be a map on screen.
 */
static VoxelAtlasSlot *AcquireAtlas(const VoxelMapInstance *inst, bool mayEvict)
{
    VoxelAtlasSlot *victim = &sAtlases[0];
    VoxelAtlasSlot *hit = FindAtlas(inst);

    if (hit != NULL)
    {
        hit->stamp = ++sAtlasStamp;
        /* A cached atlas has no current CPU shadow after another tileset pair
         * used it. Recompose it as an extension before applying live VRAM
         * animations; existing slot numbers and chunk meshes remain valid. */
        const VoxelMapInstance *current = VoxelWorld_Instance(0);
        if (sAnimShadow != NULL && sAnimEverDirty && sAnimAtlas != hit
         && current != NULL && current->primaryTileset == inst->primaryTileset
         && current->secondaryTileset == inst->secondaryTileset
         && !AtlasJobBusy() && !sAtlasUploadPending && sAtlasStaging != NULL)
        {
            if (VoxelAtlas_JobBegin(&sAtlasJob.job, inst, sAtlasStaging, &hit->map))
            {
                sAtlasJob.slot = hit;
                sAtlasJob.forView = true;
            }
            else
            {
                CtrLog_Write(CTR_LOG_ERROR, "VOXEL: animation rebase lacks heap; disabling atlas animation");
                linearFree(sAnimShadow);
                sAnimShadow = NULL;
                sAnimAtlas = NULL;
            }
        }
        /*
         * A chunk met ids this atlas was not built with - a neighbour that
         * came into view, a metatile a script placed. The atlas grows in place
         * instead of being rebuilt: rebuilt, it had a new generation and every
         * chunk drawn from it went black at once until each was built again.
         */
        if (hit->extendPending && !AtlasJobBusy() && !sAtlasUploadPending
         && sAtlasStaging != NULL)
        {
            hit->extendPending = false;
            hit->uncoveredRetried = true;
            if (VoxelAtlas_JobBegin(&sAtlasJob.job, inst, sAtlasStaging, &hit->map))
            {
                sAtlasJob.slot = hit;
                sAtlasJob.forView = true; /* its chunks are missing tiles */
            }
        }
        return hit;
    }
    /* Being composed: a map on screen makes the job urgent. */
    if (AtlasJobBusy() && !sAtlasJob.job.extend
     && sAtlasJob.job.primary == inst->primaryTileset
     && sAtlasJob.job.secondary == inst->secondaryTileset)
    {
        sAtlasJob.slot->stamp = ++sAtlasStamp;
        sAtlasJob.forView = sAtlasJob.forView || mayEvict;
        return NULL;
    }
    if (sHasFailed && inst->primaryTileset == sFailedPrimary
     && inst->secondaryTileset == sFailedSecondary)
        return NULL;
    /* A map on screen does not wait behind one composed ahead of time. */
    if (AtlasJobBusy() && !sAtlasJob.forView && mayEvict)
        AtlasJobCancel();
    /* One at a time: the job owns the staging buffer, and an upload queued
     * from it this frame has not been made yet. */
    if (AtlasJobBusy() || sAtlasUploadPending || sAtlasStaging == NULL)
        return NULL;

    /* An unused slot first, then the least recently used one. The atlas of
     * the current map is stamped every frame, so it is never the victim. */
    for (unsigned i = 0; i < VOXEL_ATLAS_SLOTS; ++i)
    {
        VoxelAtlasSlot *slot = &sAtlases[i];

        if (!slot->valid)
        {
            if (victim->valid || slot->stamp < victim->stamp)
                victim = slot;
        }
        else if (victim->valid && slot->stamp < victim->stamp)
        {
            victim = slot;
        }
    }

    if (!mayEvict && victim->valid)
        return NULL;
    if (victim->valid && victim->stamp > sRebuildStamp)
    {
        victim = LeastRecentlyUsedAllocated();
        if (victim == NULL)
            return NULL;
    }

    /*
     * The atlases live in VRAM, composed in the linear staging buffer first
     * and copied over by the GPU: VRAM is device memory, and writing a 512x256
     * atlas into it texel by texel from the CPU would cost far more than the
     * copy does (and faults outright on hardware).
     */
    if (victim->tex.data == NULL
     && !C3D_TexInitVRAM(&victim->tex, VOXEL_ATLAS_W, VOXEL_ATLAS_H, GPU_RGBA5551))
    {
        /* Still not a failure: the cache simply holds fewer atlases and
         * recycles the least recently used one. */
        if (!sAtlasCapped)
        {
            sAtlasCapped = true;
            CtrLog_Write(CTR_LOG_VIDEO, "VOXEL: atlas cache capped at %u (VRAM free=%lu)",
                         CountAllocatedAtlases(), (unsigned long)vramSpaceFree());
        }
        if (!mayEvict)
            return NULL;
        victim = LeastRecentlyUsedAllocated();
        if (victim == NULL)
        {
            /*
             * Not a failure to remember: the VRAM is usually held by the 2D
             * compositor's depth planes, left from a screen shown in 3D. They
             * are given back before the next frame and the atlas tried again
             * then - marked as failed, the map stayed in 2D for good.
             */
            static bool sReported;

            CtrVideo_RequestPlaneRelease();
            if (!sReported)
            {
                sReported = true;
                ++sStats.errors;
                CtrLog_Write(CTR_LOG_ERROR, "VOXEL: no VRAM for a %ux%u atlas (free=%lu); "
                             "asking for the depth planes back",
                             VOXEL_ATLAS_W, VOXEL_ATLAS_H, (unsigned long)vramSpaceFree());
            }
            return NULL;
        }
    }
    if (victim->tex.data != NULL && !victim->valid)
    {
        C3D_TexSetFilter(&victim->tex, GPU_NEAREST, GPU_NEAREST);
        C3D_TexSetWrap(&victim->tex, GPU_CLAMP_TO_EDGE, GPU_CLAMP_TO_EDGE);
    }
    if (victim->primaryTileset != inst->primaryTileset
     || victim->secondaryTileset != inst->secondaryTileset)
        victim->uncoveredRetried = false;
    /* Out of use from now on: what it held is being replaced. */
    victim->valid = false;
    if (sAnimAtlas == victim) sAnimAtlas = NULL;
    victim->primaryTileset = inst->primaryTileset;
    victim->secondaryTileset = inst->secondaryTileset;
    victim->stamp = ++sAtlasStamp;
    if (!VoxelAtlas_JobBegin(&sAtlasJob.job, inst, sAtlasStaging, NULL))
    {
        AtlasFailed(inst->mapGroup, inst->mapNum, inst->primaryTileset, inst->secondaryTileset);
        return NULL;
    }
    sAtlasJob.slot = victim;
    sAtlasJob.forView = mayEvict;
    return NULL;
}

/*
 * Advances the atlas job within `budget` ms of `started`, and uploads it when
 * it ends. A map on screen gets at least one step a frame, however the frame
 * is going, so that it always arrives.
 */
static void RunAtlasJob(uint64_t started, float budget)
{
    VoxelAtlasJob *job = &sAtlasJob.job;
    VoxelAtlasSlot *slot = sAtlasJob.slot;
    bool done = false;
    unsigned steps = 0;

    if (!job->active)
        return;
    /* The slot must still be the one the job is for. */
    if (slot == NULL || slot->primaryTileset != job->primary
     || slot->secondaryTileset != job->secondary || slot->valid != job->extend)
    {
        AtlasJobCancel();
        return;
    }
    while (!done)
    {
        float elapsed = (float)((svcGetSystemTick() - started) * 1000.0 / SYSCLOCK_ARM11);

        if (elapsed >= budget && !(sAtlasJob.forView && steps == 0))
            return;
        done = VoxelAtlas_JobStep(job, 24);
        ++steps;
    }

    sAtlasJob.slot = NULL;
    if (!job->ok)
    {
        if (!job->extend)
            AtlasFailed(job->mapGroup, job->mapNum, job->primary, job->secondary);
        return;
    }
    /*
     * Staging to VRAM, by the GPU. The atlas is already in the swizzled layout
     * the PICA samples, so this is a straight byte copy - flags 8 is the raw
     * texture copy, with no format or tiling conversion asked of it. Queued
     * ahead of this frame's draws, so the table installed with it is never
     * drawn with the pixels it replaced.
     */
    GSPGPU_FlushDataCache(sAtlasStaging, VOXEL_ATLAS_PIXELS * sizeof(uint16_t));
    C3D_SyncTextureCopy((u32 *)sAtlasStaging, 0, (u32 *)slot->tex.data, 0,
                        VOXEL_ATLAS_PIXELS * sizeof(uint16_t), 8);
    sAtlasUploadPending = true;
    slot->map = job->map;
    {
        const VoxelMapInstance *current = VoxelWorld_Instance(0);
        if (sAnimShadow != NULL && current != NULL
         && current->primaryTileset == job->primary
         && current->secondaryTileset == job->secondary)
        {
            memcpy(sAnimShadow, sAtlasStaging, VOXEL_ATLAS_PIXELS * sizeof(uint16_t));
            sAnimAtlas = slot;
        }
    }
    if (job->extend)
    {
        /* Nothing new placed - a full atlas - leaves every chunk as it is. */
        if (job->added != 0)
            ++slot->extension;
        CtrLog_Write(CTR_LOG_VIDEO, "VOXEL atlas for %d:%d extended to %u/%u slots%s",
                     job->mapGroup, job->mapNum, slot->map.used, VOXEL_ATLAS_MAX_SLOTS,
                     slot->map.overflowed ? " OVERFLOW" : "");
        return;
    }
    ++slot->generation;
    slot->valid = true;
    ++sStats.atlasRebuilds;
    CtrLog_Write(CTR_LOG_VIDEO, "VOXEL atlas for %d:%d: %u/%u slots%s (rebuilds=%u, linear free=%lu)",
                 job->mapGroup, job->mapNum, slot->map.used, VOXEL_ATLAS_MAX_SLOTS,
                 slot->map.overflowed ? " OVERFLOW" : "", sStats.atlasRebuilds,
                 (unsigned long)linearSpaceFree());
}

/* ── Building ───────────────────────────────────────────────────────────── */

/* What one chunk stands for: a square of a map, or of the border belt. */
typedef struct
{
    const VoxelMapInstance *inst; /* the map; for the belt, the current map */
    bool border;
    int cx, cy;
    int x0, y0, x1, y1;  /* world tiles it covers, clipped to its map */
    int baseX, baseY;    /* world tile its vertices are measured from */
} ChunkSite;

static void SiteOf(ChunkSite *site, const VoxelMapInstance *inst, bool border, int cx, int cy)
{
    site->inst = inst;
    site->border = border;
    site->cx = cx;
    site->cy = cy;
    site->baseX = (border ? 0 : inst->originX) + cx * VOXEL_CHUNK;
    site->baseY = (border ? 0 : inst->originY) + cy * VOXEL_CHUNK;
    site->x0 = site->baseX;
    site->y0 = site->baseY;
    site->x1 = site->x0 + VOXEL_CHUNK;
    site->y1 = site->y0 + VOXEL_CHUNK;
    if (!border)
    {
        /* In one place because the build and the signature must agree. */
        if (site->x1 > inst->originX + inst->width) site->x1 = inst->originX + inst->width;
        if (site->y1 > inst->originY + inst->height) site->y1 = inst->originY + inst->height;
    }
}

/*
 * Everything a chunk's geometry reads: its tiles, the margin columns and face
 * culling look into, and with lighting the casters that shade it.
 */
static uint32_t SiteHash(const ChunkSite *site)
{
#if CTR_VOXEL_LIGHTING
    if (!site->inst->indoor)
        return VoxelLighting_Hash(site->x0, site->y0, site->x1, site->y1);
#endif
    return VoxelWorld_BlockHash(site->x0 - 1, site->y0 - VOXEL_CHUNK_MARGIN_NORTH - 1,
                                site->x1 + 1, site->y1 + 2);
}

/*
 * ── Building, a few rows at a time ─────────────────────────────────────────
 *
 * A chunk of a dense town costs 10-30 ms on an Old 3DS: far more than any
 * frame has to spare, so built in one go every new chunk was a dropped frame.
 * It is built as a job instead - ground rows in VoxelMesh_EmitInstance's
 * order, tree rows, then the models a few dozen triangles at a time - a few
 * slices per frame against the frame's budget, and uploaded when done.
 * One job at a time: the mesh builder's classification window is its own.
 *
 * The world it reads must not move under it. A job remembers the epoch and
 * the atlas generation it started in and is dropped - never finished with
 * mixed data - if either changes; nothing of the chunk it was rebuilding has
 * been touched by then.
 *
 * The classification cache is opened over the square plus a margin, because
 * a face is culled by looking at the neighbour beyond it and what stands on a
 * cell can depend on the rows north of it: the build reads past its own edges
 * even though it writes nothing there.
 */
typedef enum { BUILD_OK, BUILD_NO_STAGING, BUILD_NO_VRAM } BuildResult;

enum
{
    JOB_GROUND, JOB_TREES, JOB_MODELS, /* map chunks */
    JOB_BORDER, JOB_BORDER_TREES,      /* belt chunks */
    JOB_DONE
};

static struct
{
    bool active;
    ChunkSite site;
    VoxelAtlasSlot *atlas;
    uint32_t atlasGeneration, epoch, hash;
    /* The atlas's extension when the job started: its table may grow while
     * the job runs, and a chunk that missed ids in its early rows must still
     * count as built against the smaller one. */
    uint32_t atlasExtension;
    VoxelChunk *chunk;      /* the stale chunk being rebuilt, or NULL */
    bool forView;
    int phase, row;
    unsigned terrainCount, buildingFirst;
    VoxelBuildingCursor models;
    uint64_t ticks;         /* spent on it, over every frame it took */
    uint32_t firstFrame;
} sJob;

static void JobCancel(void)
{
    sJob.active = false;
}

static void JobStart(const ChunkSite *site, VoxelAtlasSlot *atlas, uint32_t hash,
                     VoxelChunk *chunk, bool forView)
{
    const VoxelMapInstance *inst = site->inst;

    memset(&sJob, 0, sizeof(sJob));
    sJob.active = true;
    sJob.site = *site;
    sJob.atlas = atlas;
    sJob.atlasGeneration = atlas->generation;
    sJob.atlasExtension = atlas->extension;
    sJob.epoch = sEpoch;
    sJob.hash = hash;
    sJob.chunk = chunk;
    sJob.forView = forView;
    sJob.phase = site->border ? JOB_BORDER : JOB_GROUND;
    sJob.row = site->y0;
    sJob.firstFrame = sFrame;
    VoxelBuilder_Init(&sBuilder, sScratch, VOXEL_CHUNK_SCRATCH);
    VoxelBuilder_SetAtlas(&sBuilder, &atlas->map);
    VoxelBuilder_SetOrigin(&sBuilder, site->baseX, site->baseY);
    /* The whole map stands at its base (voxel_relief.h), the belt round the
     * world at the base of the map it rings. */
    sBuilder.base = VoxelRelief_Base(inst);
#if CTR_VOXEL_LIGHTING
    sBuilder.lighting = !inst->indoor;
    /* The belt keeps the fixed cost of the old one: corner shading only. */
    sBuilder.lightingRefine = !site->border;
#else
    (void)inst;
#endif
    VoxelMesh_BeginWindow(site->x0 - 1, site->y0 - VOXEL_CHUNK_MARGIN_NORTH,
                          site->x1 + 1, site->y1 + 1);
}

/* Still building from the world it started in? */
static bool JobValid(void)
{
    const VoxelAtlasSlot *atlas = sJob.atlas;

    return sJob.epoch == sEpoch && atlas->valid && atlas->generation == sJob.atlasGeneration
        && atlas->primaryTileset == sJob.site.inst->primaryTileset
        && atlas->secondaryTileset == sJob.site.inst->secondaryTileset;
}

#define VOXEL_MODEL_SLICE_TRIANGLES 64u

/* One slice of the job: a row of one pass, or one of the small passes. */
static void JobStep(void)
{
    const ChunkSite *site = &sJob.site;
    const VoxelMapInstance *inst = site->inst;

    switch (sJob.phase)
    {
    case JOB_GROUND:
        VoxelMesh_EmitGroundRow(&sBuilder, inst, site->x0, site->x1, sJob.row);
        break;
    case JOB_TREES:
        if (sJob.row == site->y0)
            sJob.terrainCount = sBuilder.count;
        VoxelTree_EmitInstance(&sBuilder, inst, site->x0, sJob.row, site->x1, sJob.row + 1);
        if (++sJob.row < site->y1)
            return;
        sJob.row = site->y0;
        sJob.buildingFirst = sBuilder.count;
        sJob.phase = sHaveBuildings ? JOB_MODELS : JOB_DONE;
        return;
    case JOB_MODELS:
        /* A few dozen lit triangles a slice: one large model is thousands. */
        if (VoxelBuildings_EmitSome(&sBuilder, inst, site->x0, site->y0, site->x1, site->y1,
                                    &sJob.models, VOXEL_MODEL_SLICE_TRIANGLES))
            sJob.phase = JOB_DONE;
        return;
    case JOB_BORDER:
        VoxelMesh_EmitBorder(&sBuilder, site->x0, site->y0, site->x1, site->y1);
        sJob.terrainCount = sBuilder.count;
        sJob.phase = JOB_BORDER_TREES;
        return;
    case JOB_BORDER_TREES:
        VoxelTree_EmitBorder(&sBuilder, site->x0, site->y0, site->x1, site->y1);
        sJob.buildingFirst = sBuilder.count;
        sJob.phase = JOB_DONE;
        return;
    default:
        return;
    }
    /* The ground row pass. */
    if (++sJob.row >= site->y1)
    {
        sJob.row = site->y0;
        ++sJob.phase;
    }
}

/*
 * Uploads a finished job into its chunk. On NO_STAGING or NO_VRAM nothing is
 * touched and the finished geometry waits for another frame.
 */
static BuildResult JobFinish(VoxelChunk *chunk)
{
    const ChunkSite *site = &sJob.site;
    const VoxelMapInstance *inst = site->inst;
    VoxelAtlasSlot *atlas = sJob.atlas;
    unsigned bytes;
    VoxelGpuVertex *staging;

    if (sStagingUsed + sBuilder.count > VOXEL_STAGING_VERTICES)
        return BUILD_NO_STAGING; /* next frame, with the pool rewound */
    bytes = (sBuilder.count * sizeof(VoxelGpuVertex) + 15u) & ~15u;
    if (bytes != 0 && (chunk->vram == NULL || chunk->bytes < bytes))
    {
        /*
         * The new block before the old one goes: a stale chunk may already be
         * on this frame's draw list, and if there is no room it must stay
         * exactly as it was. Never evict the slot itself to make the room.
         */
        VoxelGpuVertex *block;

        if (chunk->used)
            chunk->stamp = chunk->viewStamp = sFrame;
        block = ChunkVram(bytes, sJob.forView);
        if (block == NULL)
            return BUILD_NO_VRAM;
        VoxelArena_Free(&sChunkArena, chunk->vram);
        chunk->vram = block;
        chunk->bytes = bytes;
    }

    if (sBuilder.dropped != 0 && sBuilder.dropped > sWorstDropped)
    {
        sWorstDropped = sBuilder.dropped;
        ++sStats.errors;
        CtrLog_Write(CTR_LOG_ERROR,
                     "VOXEL: chunk scratch full, %u triangles refused at %d,%d of %d:%d%s"
                     " (%u of %u vertices)",
                     sBuilder.dropped, site->cx, site->cy, inst->mapGroup, inst->mapNum,
                     site->border ? " border" : "", sBuilder.count, VOXEL_CHUNK_SCRATCH);
    }
    sStats.dropped = sBuilder.dropped;
    if (bytes != 0)
    {
        /*
         * Staged in linear memory and copied by the GPU, as the atlases are.
         * SyncTextureCopy only QUEUES the transfer inside an open frame, so
         * the staging slice stays reserved until the next Update. Flags 8 is
         * the raw copy, with no format conversion.
         */
        staging = sStaging + sStagingUsed;
        Pack(sScratch, sBuilder.count, staging);
        ReportPackErrors(site->border ? "border chunk" : "chunk", site->cx, site->cy,
                         inst->mapGroup, inst->mapNum);
        sStagingUsed += (bytes + sizeof(VoxelGpuVertex) - 1) / sizeof(VoxelGpuVertex);
        GSPGPU_FlushDataCache(staging, bytes);
        C3D_SyncTextureCopy((u32 *)staging, 0, (u32 *)chunk->vram, 0, bytes, 8);
        ++sChunkUploads;
    }

    chunk->used = true;
    chunk->border = site->border;
    chunk->mapGroup = inst->mapGroup;
    chunk->mapNum = inst->mapNum;
    chunk->cx = site->cx;
    chunk->cy = site->cy;
    chunk->hash = sJob.hash;
    chunk->epoch = sEpoch;
    chunk->staleEpoch = 0;
    chunk->primary = inst->primaryTileset;
    chunk->secondary = inst->secondaryTileset;
    chunk->layout = inst->layout;
    chunk->atlas = atlas;
    chunk->atlasGeneration = atlas->generation;
    chunk->atlasExtension = sJob.atlasExtension;
    chunk->uncovered = sBuilder.uncovered != 0;
    chunk->count = sBuilder.count;
    chunk->terrainCount = sJob.terrainCount;
    chunk->buildingFirst = sJob.buildingFirst;
    chunk->gx0 = chunk->gz0 = 0;
    chunk->gx1 = chunk->gz1 = VOXEL_CHUNK;
    for (unsigned i = 0; i < sBuilder.count; ++i)
    {
        const VoxelVertex *v = &sScratch[i];

        if (v->x < chunk->gx0) chunk->gx0 = (int)floorf(v->x);
        if (v->z < chunk->gz0) chunk->gz0 = (int)floorf(v->z);
        if (v->x > chunk->gx1) chunk->gx1 = (int)ceilf(v->x);
        if (v->z > chunk->gz1) chunk->gz1 = (int)ceilf(v->z);
    }
    chunk->buildingPage = sHaveBuildings && !site->border ? VoxelBuildings_PageOf(inst) : -1;
    chunk->stamp = sFrame;
    if (sJob.forView)
        chunk->viewStamp = sFrame;
    if (chunk->count > chunk->buildingFirst)
        WantPage(chunk->buildingPage);
    /* Ids the atlas has never seen: it grows to take them (see AcquireAtlas). */
    if (sBuilder.uncovered != 0 && !atlas->uncoveredRetried)
        atlas->extendPending = true;
    if (CTR_VOXEL_TRACE && sStats.meshRebuilds < 48)
        CtrLog_Write(CTR_LOG_VIDEO, "VOXEL chunk %d,%d of %d:%d%s tiles %d,%d..%d,%d -> %u verts",
                     site->cx, site->cy, inst->mapGroup, inst->mapNum,
                     site->border ? " border" : "", site->x0, site->y0, site->x1, site->y1,
                     sBuilder.count);
    ++sStats.meshRebuilds;
    return BUILD_OK;
}

/* ── The view ───────────────────────────────────────────────────────────── */

/*
 * Chunks that need building this frame, most urgent first: a hole in the view
 * before a stale square that can still be drawn as it is, both before
 * anything ahead of time, and within each class the nearest to the player.
 */
#define VOXEL_REQUESTS_MAX 192u

enum
{
    NEED_HOLE,     /* in view, nothing to draw in its place */
    NEED_STALE,    /* in view, the old mesh stands meanwhile */
    NEED_AHEAD,    /* in the prefetch ring */
    NEED_AHEAD_STALE
};

typedef struct
{
    ChunkSite site;
    VoxelAtlasSlot *atlas;
    VoxelChunk *chunk;  /* the stale chunk, or NULL for a new one */
    uint32_t hash;
    bool hashKnown;     /* else computed if and when it is built */
    bool done;          /* taken as a job, or being built by the running one */
    unsigned key;       /* class, then distance */
} BuildRequest;

static BuildRequest sRequests[VOXEL_REQUESTS_MAX];
static unsigned sRequestCount;

static bool IsChunkOf(const VoxelChunk *chunk, const ChunkSite *site)
{
    return chunk->used && chunk->border == site->border
        && chunk->mapGroup == site->inst->mapGroup && chunk->mapNum == site->inst->mapNum
        && chunk->cx == site->cx && chunk->cy == site->cy;
}

static bool Overlaps(int ax0, int ay0, int ax1, int ay1, int bx0, int by0, int bx1, int by1)
{
    return ax0 < bx1 && ax1 > bx0 && ay0 < by1 && ay1 > by0;
}

static void Draw(const VoxelChunk *chunk, int worldX, int worldZ)
{
    if (chunk->count == 0 || sDrawCount >= VOXEL_DRAW_MAX)
        return;
    sDraws[sDrawCount].chunk = chunk;
    sDraws[sDrawCount].worldX = worldX;
    sDraws[sDrawCount].worldZ = worldZ;
    ++sDrawCount;
    sStats.vertices += chunk->count;
}

static void Request(const ChunkSite *site, VoxelAtlasSlot *atlas, VoxelChunk *chunk,
                    const uint32_t *hash, unsigned need, float playerX, float playerZ)
{
    BuildRequest *r;
    float dx = (site->x0 + site->x1) * 0.5f - playerX;
    float dz = (site->y0 + site->y1) * 0.5f - playerZ;

    if (sRequestCount >= VOXEL_REQUESTS_MAX)
        return;
    r = &sRequests[sRequestCount++];
    r->site = *site;
    r->atlas = atlas;
    r->chunk = chunk;
    r->hashKnown = hash != NULL;
    r->done = false;
    r->hash = hash != NULL ? *hash : 0;
    r->key = need * 100000u + (unsigned)(dx * dx + dz * dz);
}

/*
 * One square of the view or of the ring around it: drawn if it can be, asked
 * for if it has to be built.
 */
static void VisitSite(const ChunkSite *site, VoxelAtlasSlot *atlas, const int view[4],
                      float playerX, float playerZ, unsigned *missing)
{
    const VoxelMapInstance *inst = site->inst;
    VoxelChunk *chunk = FindChunk(site->border, inst->mapGroup, inst->mapNum,
                                  site->cx, site->cy);
    bool drawable, stale = false, hashKnown = false;
    uint32_t hash = 0;
    /*
     * On screen if its tiles are, or - once built - if anything it drew is:
     * a building is emitted by the chunk of its top-left cell and stands
     * south of it, so a house whose anchor is past the north edge of the view
     * still has its front in it.
     */
    bool inView = Overlaps(site->x0, site->y0, site->x1, site->y1,
                           view[0], view[1], view[2], view[3])
               || (chunk != NULL && chunk->count != 0
                   && Overlaps(site->baseX + chunk->gx0, site->baseY + chunk->gz0,
                               site->baseX + chunk->gx1, site->baseY + chunk->gz1,
                               view[0], view[1], view[2], view[3]));

    if (chunk == NULL)
    {
        if (atlas != NULL)
            Request(site, atlas, NULL, NULL, inView ? NEED_HOLE : NEED_AHEAD,
                    playerX, playerZ);
        if (inView)
            ++*missing;
        return;
    }

    chunk->stamp = sFrame;
    if (inView)
        chunk->viewStamp = sFrame;
    /* An old UV range cannot be drawn with recycled atlas pixels: keep old
     * geometry only while its material is still the one it was built for. */
    drawable = atlas != NULL && chunk->atlas == atlas
            && chunk->atlasGeneration == atlas->generation;
    if (atlas == NULL)
    {
        if (inView)
            ++*missing;
        return;
    }
    if (!drawable || chunk->layout != inst->layout
     || chunk->primary != inst->primaryTileset || chunk->secondary != inst->secondaryTileset)
        stale = true;
    else if (chunk->uncovered && chunk->atlasExtension != atlas->extension)
        stale = true; /* the atlas grew the ids it was missing; still drawable */
    else if (chunk->staleEpoch == sEpoch)
    {
        stale = hashKnown = true;
        hash = chunk->staleHash;
    }
    else if (chunk->epoch != sEpoch)
    {
        /* The world moved since this chunk was last verified: the one time
         * its signature is worth computing. */
        hash = SiteHash(site);
        hashKnown = true;
        if (hash == chunk->hash)
            chunk->epoch = sEpoch;
        else
        {
            stale = true;
            chunk->staleHash = hash;
            chunk->staleEpoch = sEpoch;
        }
    }
    if (stale)
        Request(site, atlas, chunk, hashKnown ? &hash : NULL,
                inView ? (drawable ? NEED_STALE : NEED_HOLE)
                       : (drawable ? NEED_AHEAD_STALE : NEED_AHEAD),
                playerX, playerZ);
    if (!inView)
        return;
    if (drawable)
        Draw(chunk, site->baseX, site->baseY);
    else
        ++*missing;
}

/* Most belt squares lie wholly inside one map and are rejected here, without
 * asking which map owns each of their 64 tiles. */
static bool InsideOneMap(const ChunkSite *site)
{
    for (unsigned i = 0; i < VoxelWorld_InstanceCount(); ++i)
    {
        const VoxelMapInstance *inst = VoxelWorld_Instance(i);

        if (site->x0 >= inst->originX && site->x1 <= inst->originX + inst->width
         && site->y0 >= inst->originY && site->y1 <= inst->originY + inst->height)
            return true;
    }
    return false;
}

/* Walks every chunk of every map, and of the belt, that reaches into the ring. */
static void VisitView(int vx0, int vy0, int vx1, int vy1, float playerX, float playerZ,
                      unsigned *missing, bool atlasAhead)
{
    int px0 = vx0 - VOXEL_PREFETCH, py0 = vy0 - VOXEL_PREFETCH;
    int px1 = vx1 + VOXEL_PREFETCH, py1 = vy1 + VOXEL_PREFETCH;
    const int view[4] = { vx0, vy0, vx1, vy1 };
    unsigned instances = VoxelWorld_InstanceCount();
    const VoxelMapInstance *current = VoxelWorld_Instance(0);
    VoxelAtlasSlot *atlases[MAX_VOXEL_MAP_INSTANCES];
    bool inRing[MAX_VOXEL_MAP_INSTANCES];

    if (instances > MAX_VOXEL_MAP_INSTANCES)
        instances = MAX_VOXEL_MAP_INSTANCES;
    /*
     * Atlases first, the maps on screen before the ones only in the ring: an
     * on-screen map must never find its slot, or the frame's one atlas
     * upload, taken by a map nobody can see yet. A ring map gets its atlas
     * ahead of time only into an empty slot, and only when the frame can
     * afford the build.
     */
    for (unsigned pass = 0; pass < 2; ++pass)
        for (unsigned i = 0; i < instances; ++i)
        {
            const VoxelMapInstance *inst = VoxelWorld_Instance(i);
            int ex = inst->originX + inst->width, ey = inst->originY + inst->height;
            bool inView = Overlaps(inst->originX, inst->originY, ex, ey, vx0, vy0, vx1, vy1);

            if (pass == 0)
            {
                inRing[i] = Overlaps(inst->originX, inst->originY, ex, ey, px0, py0, px1, py1);
                atlases[i] = inView ? AcquireAtlas(inst, true) : NULL;
            }
            else if (inRing[i] && !inView)
            {
                atlases[i] = FindAtlas(inst);
                if (atlases[i] != NULL)
                    atlases[i]->stamp = ++sAtlasStamp; /* not recycled this update */
                else if (atlasAhead)
                    atlases[i] = AcquireAtlas(inst, false);
            }
        }

    for (unsigned i = 0; i < instances; ++i)
    {
        const VoxelMapInstance *inst = VoxelWorld_Instance(i);
        VoxelAtlasSlot *atlas = atlases[i];
        int ix0, iy0, ix1, iy1;

        if (!inRing[i])
            continue;
        ix0 = px0 > inst->originX ? px0 : inst->originX;
        iy0 = py0 > inst->originY ? py0 : inst->originY;
        ix1 = px1 < inst->originX + inst->width ? px1 : inst->originX + inst->width;
        iy1 = py1 < inst->originY + inst->height ? py1 : inst->originY + inst->height;
        for (int cy = (iy0 - inst->originY) / VOXEL_CHUNK;
             cy <= (iy1 - 1 - inst->originY) / VOXEL_CHUNK; ++cy)
            for (int cx = (ix0 - inst->originX) / VOXEL_CHUNK;
                 cx <= (ix1 - 1 - inst->originX) / VOXEL_CHUNK; ++cx)
            {
                ChunkSite site;

                SiteOf(&site, inst, false, cx, cy);
                VisitSite(&site, atlas, view,
                          playerX, playerZ, missing);
            }
    }

    /* The belt, on the current map's grid: only squares some tile of which
     * belongs to no map at all. Indoors there is none; the clear is black. */
    if (current == NULL || current->indoor)
        return;
    {
        VoxelAtlasSlot *atlas = FindAtlas(current);
        int cy0 = (int)floorf(py0 / (float)VOXEL_CHUNK), cy1 = (int)floorf((py1 - 1) / (float)VOXEL_CHUNK);
        int cx0 = (int)floorf(px0 / (float)VOXEL_CHUNK), cx1 = (int)floorf((px1 - 1) / (float)VOXEL_CHUNK);

        for (int cy = cy0; cy <= cy1; ++cy)
            for (int cx = cx0; cx <= cx1; ++cx)
            {
                ChunkSite site;
                bool open = false;

                SiteOf(&site, current, true, cx, cy);
                if (InsideOneMap(&site))
                    continue;
                for (int y = site.y0; y < site.y1 && !open; ++y)
                    for (int x = site.x0; x < site.x1 && !open; ++x)
                        open = VoxelWorld_GetInstanceAt(x, y) == NULL;
                if (!open)
                    continue;
                VisitSite(&site, atlas, view,
                          playerX, playerZ, missing);
            }
    }
}

static int CompareRequests(const void *a, const void *b)
{
    unsigned ka = ((const BuildRequest *)a)->key, kb = ((const BuildRequest *)b)->key;

    return ka < kb ? -1 : ka > kb;
}

/*
 * How long this frame may spend building. The game's own share of the frame
 * is read off the last one (its work minus what this module built in it); a
 * hole in the view may take what is left up to a ceiling, and always gets at
 * least one slice. Work ahead of time only takes what would otherwise idle.
 *
 * The target leaves room for what the frame's work does not count - the
 * command list's submission, the display transfer, a late VBlank - which on
 * an Old 3DS is two to three milliseconds: at 14 ms, frames whose work
 * measured 16.5 ms kept missing their VBlank.
 */
#define VOXEL_WORK_TARGET_MS 13.0f
#define VOXEL_HOLE_MIN_MS 2.0f
#define VOXEL_HOLE_MAX_MS 9.0f
#define VOXEL_AHEAD_MAX_MS 8.0f
#define VOXEL_AHEAD_MARGIN_MS 1.5f
/*
 * A map change turns every belt square in view into a hole at once, and gets
 * a little more than the usual hole budget to fill them. Not much more: the
 * game spends 30-60 ms of that same frame loading the map it crossed into, so
 * the frame is late already, and every millisecond added here is a further
 * frame lost.
 */
#define VOXEL_CROSSING_MS 8.0f
#define VOXEL_AHEAD_BACKOFF_FRAMES 30u

static uint32_t sAheadBackoff;

/*
 * After a cut - a warp, a door, a loaded game - the new map is built in full
 * before anything else, whatever it costs: the frames go by behind the fade
 * anyway. Spread over frames like any other work, the first frames of the
 * new map had nothing to draw, and the video fell back to the 2D compositor
 * until they had - the plain GBA picture flashing up before the 3D one.
 * Ends once the view has no hole left, or after a few seconds regardless.
 */
#define VOXEL_WARMUP_MS 250.0f
#define VOXEL_WARMUP_FRAMES 180u
static uint32_t sWarmupUntil;
/* Set when atlases were given back to the 2D compositor: the overworld warms
 * up again when it returns, as after a cut. */
static bool sResumeWarmup;

static float Clamp(float value, float lo, float hi)
{
    return value < lo ? lo : value > hi ? hi : value;
}

static void NoteBuildCost(VoxelChunk *chunk, const ChunkSite *site, float ms, uint32_t frames)
{
    static unsigned sSlowLogged;

    chunk->buildMs = ms;
    /* Where the time goes on hardware, which an emulator cannot say. */
    if (ms >= 8.0f && sSlowLogged < 64)
    {
        ++sSlowLogged;
        CtrLog_Write(CTR_LOG_VIDEO, "VOXEL slow chunk %d,%d of %d:%d%s: %.1f ms over %lu frames, %u verts",
                     site->cx, site->cy, site->inst->mapGroup, site->inst->mapNum,
                     site->border ? " border" : "", ms, (unsigned long)frames, chunk->count);
    }
}

static float SpareMs(void)
{
    const CtrTiming *timing = CtrPlatform_GetTiming();
    float fixed = timing->workMs - sLastBuildMs;

    if (fixed < 0.0f)
        fixed = 0.0f;
    return VOXEL_WORK_TARGET_MS - fixed;
}

/*
 * What one slice of each phase is expected to cost, and at JOB_DONE the
 * upload. Per phase because they differ several times over: a row of ground
 * against a dense chunk's models. Each rises at once to an expensive slice and
 * forgets it slowly. Capped below the ahead budget (VOXEL_AHEAD_MAX_MS): an
 * estimate at that budget would never let an ahead-of-time slice run again,
 * and so never come down - one slice that waited on the card would freeze the
 * prefetch ring for good.
 */
static float sPhaseMs[JOB_DONE + 1];
#define VOXEL_SLICE_CAP_MS 5.0f

static void NotePhaseCost(int phase, float ms)
{
    float *estimate = &sPhaseMs[phase];

    if (ms > VOXEL_SLICE_CAP_MS)
        ms = VOXEL_SLICE_CAP_MS;
    *estimate = ms > *estimate ? ms : *estimate * 0.95f + ms * 0.05f;
}

static bool SameSite(const ChunkSite *a, const ChunkSite *b)
{
    return a->border == b->border && a->cx == b->cx && a->cy == b->cy
        && a->inst->mapGroup == b->inst->mapGroup && a->inst->mapNum == b->inst->mapNum;
}

static bool IsDrawn(const VoxelChunk *chunk)
{
    for (unsigned i = 0; i < sDrawCount; ++i)
        if (sDraws[i].chunk == chunk)
            return true;
    return false;
}

/* Takes the most urgent request the frame can afford as the next job. */
static bool StartNextJob(float elapsed, float holeMs, float aheadMs)
{
    for (unsigned i = 0; i < sRequestCount; ++i)
    {
        BuildRequest *r = &sRequests[i];
        const VoxelMapInstance *inst = r->site.inst;
        unsigned need = r->key / 100000u;
        bool forView = need == NEED_HOLE || need == NEED_STALE;

        if (r->done)
            continue;
        if (elapsed >= (forView ? holeMs : aheadMs))
            return false; /* sorted: nothing after it is more urgent */
        /* The slot may have been recycled for another tileset this update. */
        if (!r->atlas->valid || r->atlas->primaryTileset != inst->primaryTileset
         || r->atlas->secondaryTileset != inst->secondaryTileset)
            continue;
        if (!r->hashKnown)
            r->hash = SiteHash(&r->site);
        r->done = true;
        JobStart(&r->site, r->atlas, r->hash, r->chunk, forView);
        return true;
    }
    return false;
}

static void UpdateView(float playerX, float playerZ, bool crossed, bool cut)
{
    int px = (int)floorf(playerX), pz = (int)floorf(playerZ);
    int vx0 = px - VOXEL_VIEW_SIDE - VOXEL_WINDOW_SLACK;
    int vy0 = pz - VOXEL_VIEW_NORTH - VOXEL_WINDOW_SLACK;
    int vx1 = px + VOXEL_VIEW_SIDE + VOXEL_WINDOW_SLACK + 1;
    int vy1 = pz + VOXEL_VIEW_SOUTH + VOXEL_WINDOW_SLACK + 1;
    uint64_t started = svcGetSystemTick();
    float spare = SpareMs();
    float aheadMs = sFrame < sAheadBackoff ? 0.0f
                  : Clamp(spare - VOXEL_AHEAD_MARGIN_MS, 0.0f, VOXEL_AHEAD_MAX_MS);
    float holeMs = Clamp(spare, VOXEL_HOLE_MIN_MS, VOXEL_HOLE_MAX_MS);
    unsigned missing = 0, built = 0, slices = 0;
    bool holes = false;
    bool warmup;

    /* A crossing changes which map the belt belongs to: see VOXEL_CROSSING_MS. */
    if (crossed && holeMs < VOXEL_CROSSING_MS)
        holeMs = VOXEL_CROSSING_MS;
    if (cut || sResumeWarmup)
        sWarmupUntil = sFrame + VOXEL_WARMUP_FRAMES;
    sResumeWarmup = false;
    warmup = sFrame < sWarmupUntil;
    if (warmup)
        holeMs = VOXEL_WARMUP_MS;
    /* The atlas being composed goes first: no chunk of its map can be built
     * before it, and a map on screen without one is a hole of its own. */
    RunAtlasJob(started, sAtlasJob.forView ? holeMs : aheadMs);
    for (unsigned pass = 0;; ++pass)
    {
        sRebuildStamp = sAtlasStamp;
        sDrawCount = 0;
        sRequestCount = 0;
        sStats.vertices = 0;
        missing = 0;
        VisitView(vx0, vy0, vx1, vy1, playerX, playerZ, &missing, aheadMs > 0.0f);
        /* Warming up, an atlas the visit has just asked for is composed now,
         * and the view visited again with it - one per pass, one job at a
         * time, for each tileset pair on screen. */
        if (!warmup || !AtlasJobBusy() || !sAtlasJob.forView || pass >= 4)
            break;
        RunAtlasJob(started, 1000.0f);
    }
    sStats.atlasMs = (float)((svcGetSystemTick() - started) * 1000.0 / SYSCLOCK_ARM11);
    if (sRequestCount > 1)
        qsort(sRequests, sRequestCount, sizeof(sRequests[0]), CompareRequests);
    for (unsigned i = 0; i < sRequestCount; ++i)
    {
        holes = holes || sRequests[i].key / 100000u == NEED_HOLE;
        /* The square the running job is building is not asked for again. */
        if (sJob.active && SameSite(&sRequests[i].site, &sJob.site))
            sRequests[i].done = true;
    }

    /* A job that no longer matches the world is dropped; one working ahead
     * of time gives way the moment the view has a hole. */
    if (sJob.active && (!JobValid() || (!sJob.forView && holes)))
        sJob.active = false;

    for (;;)
    {
        float elapsed = (float)((svcGetSystemTick() - started) * 1000.0 / SYSCLOCK_ARM11);
        float budget;
        uint64_t sliceStart;

        if (!sJob.active && !StartNextJob(elapsed, holeMs, aheadMs))
            break;
        budget = sJob.forView ? holeMs : aheadMs;
        /*
         * A slice starts only if it should also end inside the budget. The
         * exception is a hole on screen, which gets one slice a frame however
         * the frame is going, so that it is always filled in the end.
         */
        if (!(sJob.forView && slices == 0) && elapsed + sPhaseMs[sJob.phase] > budget)
            break;
        if (sJob.phase != JOB_DONE)
        {
            int phase = sJob.phase;
            uint64_t ticks;

            sliceStart = svcGetSystemTick();
            JobStep();
            ticks = svcGetSystemTick() - sliceStart;
            sJob.ticks += ticks;
            NotePhaseCost(phase, (float)(ticks * 1000.0 / SYSCLOCK_ARM11));
            ++slices;
            continue;
        }

        /* Finished: upload it into its chunk. */
        {
            VoxelChunk *chunk = sJob.chunk;
            BuildResult result;

            if (VOXEL_STAGING_VERTICES - sStagingUsed < sBuilder.count
             || sChunkUploads >= VOXEL_CHUNK_UPLOADS_MAX)
                break; /* next frame, the geometry stays in the scratch */
            /* An earlier upload may have evicted the chunk this job was
             * rebuilding, and even handed its slot to another square. */
            if (chunk != NULL && !IsChunkOf(chunk, &sJob.site))
                chunk = NULL;
            if (chunk == NULL)
            {
                chunk = FreeChunkSlot(sJob.forView);
                if (chunk == NULL)
                {
                    sJob.active = false;
                    continue;
                }
                ReleaseChunk(chunk);
            }
            {
                uint64_t finishStart = svcGetSystemTick(), ticks;

                result = JobFinish(chunk);
                ticks = svcGetSystemTick() - finishStart;
                if (result == BUILD_OK)
                {
                    sJob.ticks += ticks;
                    NotePhaseCost(JOB_DONE, (float)(ticks * 1000.0 / SYSCLOCK_ARM11));
                }
            }
            if (result == BUILD_NO_STAGING)
                break;
            if (result == BUILD_NO_VRAM)
            {
                /* Nothing more fits. Ahead of time, stop trying for a while:
                 * the ring would otherwise build a chunk and discard it for as
                 * long as the view holds the cache full. */
                if (!sJob.forView)
                    sAheadBackoff = sFrame + VOXEL_AHEAD_BACKOFF_FRAMES;
                sJob.active = false;
                break;
            }
            sJob.active = false;
            ++built;
            NoteBuildCost(chunk, &sJob.site, (float)(sJob.ticks * 1000.0 / SYSCLOCK_ARM11),
                          sFrame - sJob.firstFrame + 1);
            /* A stale chunk is already on the draw list, which points at its
             * slot; a hole joins it now, if it is on screen. */
            if (!IsDrawn(chunk)
             && (Overlaps(sJob.site.x0, sJob.site.y0, sJob.site.x1, sJob.site.y1,
                          vx0, vy0, vx1, vy1)
                 || Overlaps(sJob.site.baseX + chunk->gx0, sJob.site.baseY + chunk->gz0,
                             sJob.site.baseX + chunk->gx1, sJob.site.baseY + chunk->gz1,
                             vx0, vy0, vx1, vy1)))
            {
                Draw(chunk, sJob.site.baseX, sJob.site.baseY);
                if (missing > 0)
                    --missing;
            }
        }
    }

    if (warmup && missing == 0 && !AtlasJobBusy())
        sWarmupUntil = 0;
    sLastBuildMs = (float)((svcGetSystemTick() - started) * 1000.0 / SYSCLOCK_ARM11);
    sStats.meshMs = sLastBuildMs;
    if (sStats.meshMs > sStats.meshPeakMs)
        sStats.meshPeakMs = sStats.meshMs;
    sStats.chunksMissing = missing;
    sStats.visibleChunks = sDrawCount;
    sStats.frameBuilds = built;
    sStats.pendingBuilds = sRequestCount;
}

/* ── Map changes ────────────────────────────────────────────────────────── */

/*
 * Where each map sat in world space before this frame. Crossing a border makes
 * the neighbour the new origin, so everything that was on screen moves by that
 * neighbour's old offset.
 */
static struct
{
    int mapGroup, mapNum, originX, originY;
} sPreviousOrigins[MAX_VOXEL_MAP_INSTANCES];
static unsigned sPreviousOriginCount;

static void RememberInstanceOrigins(void)
{
    unsigned count = VoxelWorld_InstanceCount();

    sPreviousOriginCount = 0;
    for (unsigned i = 0; i < count && i < MAX_VOXEL_MAP_INSTANCES; ++i)
    {
        const VoxelMapInstance *inst = VoxelWorld_Instance(i);

        sPreviousOrigins[sPreviousOriginCount].mapGroup = inst->mapGroup;
        sPreviousOrigins[sPreviousOriginCount].mapNum = inst->mapNum;
        sPreviousOrigins[sPreviousOriginCount].originX = inst->originX;
        sPreviousOrigins[sPreviousOriginCount].originY = inst->originY;
        ++sPreviousOriginCount;
    }
}

/*
 * Walking from Littleroot to Route 101 is not a teleport: the player keeps
 * moving in a straight line and only the coordinate system changes underneath.
 * If the new map was one of the connections we were already drawing, the
 * camera is shifted by that map's old offset and the view does not move at
 * all. Anything else - a warp, a door, a new game - is a genuine cut, and
 * there the camera snaps.
 */
/* True for a crossing, false for a cut. */
static bool HandleMapChange(int mapGroup, int mapNum, float playerX, float playerZ)
{
    for (unsigned i = 0; i < sPreviousOriginCount; ++i)
    {
        if (sPreviousOrigins[i].mapGroup != mapGroup || sPreviousOrigins[i].mapNum != mapNum)
            continue;
        if (sPreviousOrigins[i].originX == 0 && sPreviousOrigins[i].originY == 0)
            break; /* already the origin: nothing moved */
        VoxelCamera_Shift(&sCamera, (float)-sPreviousOrigins[i].originX,
                          (float)-sPreviousOrigins[i].originY);
        CtrLog_Write(CTR_LOG_VIDEO, "VOXEL: crossed into %d:%d, camera shifted by %d,%d",
                     mapGroup, mapNum, -sPreviousOrigins[i].originX,
                     -sPreviousOrigins[i].originY);
        return true;
    }
    VoxelCamera_SetGround(&sCamera, VoxelRelief_LiftAt(playerX + 0.5f, playerZ + 0.5f), 1);
    VoxelCamera_Snap(&sCamera, playerX, playerZ);
    return false;
}

/* Everything about the maps on screen that cached geometry depends on. */
static uint32_t InstanceSignature(void)
{
    uint32_t hash = 2166136261u;
    unsigned count = VoxelWorld_InstanceCount();

    hash = (hash ^ count) * 16777619u;
    for (unsigned i = 0; i < count; ++i)
    {
        const VoxelMapInstance *inst = VoxelWorld_Instance(i);
        const uint32_t fields[] = {
            (uint32_t)inst->mapGroup, (uint32_t)inst->mapNum,
            (uint32_t)inst->originX, (uint32_t)inst->originY,
            (uint32_t)inst->width, (uint32_t)inst->height,
            (uint32_t)(uintptr_t)inst->layout,
            (uint32_t)(uintptr_t)inst->primaryTileset,
            (uint32_t)(uintptr_t)inst->secondaryTileset,
            inst->indoor ? 1u : 0u,
        };

        for (unsigned f = 0; f < sizeof(fields) / sizeof(fields[0]); ++f)
            hash = (hash ^ fields[f]) * 16777619u;
    }
    return hash;
}

/* ── Frame update ───────────────────────────────────────────────────────── */

bool CtrVoxel_Update(void)
{
    const VoxelMapInstance *inst;
    float playerX = 0.0f, playerZ = 0.0f;
    float smoothX = 0.0f, smoothZ = 0.0f;
    int mapGroup = -1, mapNum = -1;
    bool mapChanged, cut = false;
    uint64_t started;

    if (!sReady)
    {
        sStatus = "off";
        return false;
    }
    started = svcGetSystemTick();
    ++sFrame;

    /* CtrVideo_Present has opened the frame and waited for the previous GPU
     * queue. Only now may the sources of last frame's uploads be reused. */
    sStagingUsed = 0;
    sChunkUploads = 0;
    sAtlasUploadPending = false;
    /* Nothing retires an asset payload between here and the end of the
     * update, so resolved pointers may be reused throughout. */
    VoxelWorld_BeginBatch();
    VoxelWorld_BuildInstances();
    sStats.instances = VoxelWorld_InstanceCount();
    ReadAhead();
    inst = VoxelWorld_Instance(0);
    if (inst == NULL)
    {
        sStatus = "nomap";
        return false;
    }

    {
        uint32_t digest = VoxelWorld_LiveDigest(), signature = InstanceSignature();

        if (digest != sLiveDigest || signature != sInstanceSignature)
        {
            sLiveDigest = digest;
            sInstanceSignature = signature;
            ++sEpoch;
#if CTR_VOXEL_LIGHTING
            VoxelLighting_Reset();
#endif
        }
    }

    VoxelWorld_GetLocation(&mapGroup, &mapNum);
    /*
     * Two different positions on purpose. The camera follows the interpolated
     * one, or it lurches a whole tile per step; the view uses the tile-aligned
     * one, because that is the granularity at which the geometry changes.
     */
    VoxelWorld_GetPlayerWorldCoords(&playerX, &playerZ);
    VoxelEntities_GetPlayerWorldPos(&smoothX, &smoothZ);

    mapChanged = mapGroup != sMeshMapGroup || mapNum != sMeshMapNum;
    if (mapChanged)
    {
        for (unsigned a = 0; a < VOXEL_ATLAS_SLOTS; ++a)
            sAtlases[a].uncoveredRetried = false;
        cut = !HandleMapChange(mapGroup, mapNum, smoothX, smoothZ);
    }
    else
    {
        VoxelCamera_SetGround(&sCamera, VoxelRelief_LiftAt(smoothX + 0.5f, smoothZ + 0.5f), 0);
        VoxelCamera_Update(&sCamera, smoothX, smoothZ);
    }
    /* Recorded after the shift, for the next crossing. */
    RememberInstanceOrigins();
    sMeshMapGroup = mapGroup;
    sMeshMapNum = mapNum;

    sStats.worldMs = (float)((svcGetSystemTick() - started) * 1000.0 / SYSCLOCK_ARM11);
    UpdateView(playerX, playerZ, mapChanged && !cut, cut);
    for (unsigned d = 0; d < sDrawCount; ++d)
        if (sDraws[d].chunk->count > sDraws[d].chunk->buildingFirst)
            WantPage(sDraws[d].chunk->buildingPage);
    StreamPages();
    /* Animation writes arrived during the game's VBlank. Recompose only the
     * metatiles that reference those 8x8 tiles and upload once, never per tile. */
    if (sAnimPending && sAnimShadow != NULL && !sAtlasUploadPending
     && !AtlasJobBusy())
    {
        VoxelAtlasSlot *active = FindAtlas(inst);
        if (active != NULL && active == sAnimAtlas)
        {
            unsigned changed = VoxelAtlas_RefreshAnimated(inst, &active->map,
                                                            sAnimShadow, CtrVideo_GetBgVram(),
                                                            sAnimDirty);
            if (changed != 0)
            {
                ++sStats.animationUploads;
                sStats.animatedMetatiles += changed;
                GSPGPU_FlushDataCache(sAnimShadow, VOXEL_ATLAS_PIXELS * sizeof(uint16_t));
                C3D_SyncTextureCopy((u32 *)sAnimShadow, 0, (u32 *)active->tex.data, 0,
                                    VOXEL_ATLAS_PIXELS * sizeof(uint16_t), 8);
                sAtlasUploadPending = true;
            }
            memset(sAnimDirty, 0, sizeof(sAnimDirty));
            sAnimPending = false;
        }
    }

    /*
     * Billboards last, rebuilt every frame: they interpolate between tiles, so
     * they move on frames where nothing else does. Built around the camera's
     * tile, which keeps their coordinates small enough to pack.
     */
    {
        uint64_t spritesStart = svcGetSystemTick();
        VoxelBuilder sprites, reflections;
        VoxelBuilder *shadowBuilder = NULL;
        VoxelVertex *dynamic = sDynamicScratch;
#if CTR_VOXEL_LIGHTING
        VoxelBuilder shadows;
#endif

        sDynamicX = (int)floorf(sCamera.targetX);
        sDynamicZ = (int)floorf(sCamera.targetZ);
        VoxelBuilder_Init(&sprites, dynamic, VOXEL_SPRITE_RESERVE);
        VoxelBuilder_SetOrigin(&sprites, sDynamicX, sDynamicZ);
        VoxelBuilder_Init(&reflections, dynamic + VOXEL_REFLECTION_FIRST,
                          VOXEL_REFLECTION_RESERVE);
        VoxelBuilder_SetOrigin(&reflections, sDynamicX, sDynamicZ);
#if CTR_VOXEL_LIGHTING
        VoxelBuilder_Init(&shadows, dynamic + VOXEL_SHADOW_FIRST, VOXEL_SHADOW_RESERVE);
        VoxelBuilder_SetOrigin(&shadows, sDynamicX, sDynamicZ);
        shadowBuilder = &shadows;
#endif
        sStats.spriteUpdates += VoxelEntities_Emit(&sprites, (uint16_t *)sSpriteAtlas.data,
                                                   &sCamera, shadowBuilder, &reflections);
        sReflectionVertices = reflections.count;
        sStats.reflections = reflections.count / VOXEL_REFLECTION_VERTICES;
        Pack(dynamic + VOXEL_REFLECTION_FIRST, reflections.count,
             sDynamic + VOXEL_REFLECTION_FIRST);
        if (reflections.dropped != 0)
        {
            ++sStats.errors;
            CtrLog_Write(CTR_LOG_ERROR, "VOXEL: reflection staging overflow");
        }
        sSpriteVertices = sprites.count;
        Pack(dynamic, sprites.count, sDynamic);
#if CTR_VOXEL_LIGHTING
        sShadowVertices = shadows.count;
        Pack(dynamic + VOXEL_SHADOW_FIRST, shadows.count, sDynamic + VOXEL_SHADOW_FIRST);
        if (shadows.dropped != 0)
        {
            ++sStats.errors;
            CtrLog_Write(CTR_LOG_ERROR, "VOXEL: cast shadow staging overflow");
        }
#endif
        if (sprites.dropped != 0 && !sOverflowReported)
        {
            sOverflowReported = true;
            ++sStats.errors;
            CtrLog_Write(CTR_LOG_ERROR, "VOXEL: no staging room for billboards");
        }
        sStats.spritesMs = (float)((svcGetSystemTick() - spritesStart) * 1000.0 / SYSCLOCK_ARM11);
    }
    ReportPackErrors("the billboards around", sDynamicX, sDynamicZ, mapGroup, mapNum);

    sStats.chunks = 0;
    for (unsigned i = 0; i < VOXEL_CHUNK_SLOTS; ++i)
        if (sChunks[i].used)
            ++sStats.chunks;
    sStats.vertices += sSpriteVertices + sReflectionVertices;
#if CTR_VOXEL_LIGHTING
    sStats.vertices += sShadowVertices;
#endif
    sStats.linearFree = (unsigned long)linearSpaceFree();
    sStats.vramFree = (unsigned long)vramSpaceFree();
    sStats.updateMs = (float)((svcGetSystemTick() - started) * 1000.0 / SYSCLOCK_ARM11);
    if (sStats.updateMs > sStats.updatePeakMs)
        sStats.updatePeakMs = sStats.updateMs;
    sStatus = sDrawCount != 0 ? "on" : "nomesh";
    return sDrawCount != 0;
}

/*
 * Called by the 2D compositor, on a frame the overworld is not drawing, when
 * it cannot find VRAM for its depth planes. The atlases of every tileset pair
 * but the current map's are released; they are composed again, over frames,
 * when their maps are next on screen.
 */
bool CtrVoxel_IsWarmingUp(void)
{
    return sReady && sFrame < sWarmupUntil;
}

unsigned long CtrVoxel_ReleaseIdleVram(void)
{
    const VoxelMapInstance *current = VoxelWorld_Instance(0);
    unsigned long freed = 0;

    if (!sReady)
        return 0;
    AtlasJobCancel();
    for (unsigned i = 0; i < VOXEL_ATLAS_SLOTS; ++i)
    {
        VoxelAtlasSlot *slot = &sAtlases[i];

        if (slot->tex.data == NULL)
            continue;
        if (current != NULL && slot->valid && slot->primaryTileset == current->primaryTileset
         && slot->secondaryTileset == current->secondaryTileset)
            continue;
        C3D_TexDelete(&slot->tex);
        if (sAnimAtlas == slot) sAnimAtlas = NULL;
        memset(&slot->tex, 0, sizeof(slot->tex));
        slot->valid = false;
        freed += VOXEL_ATLAS_PIXELS * sizeof(uint16_t);
    }
    if (freed != 0)
    {
        sAtlasCapped = false;
        sResumeWarmup = true;
        CtrLog_Write(CTR_LOG_VIDEO, "VOXEL: %lu KiB of idle atlases released (VRAM free=%lu)",
                     freed >> 10, (unsigned long)vramSpaceFree());
    }
    return freed;
}

const char *CtrVoxel_Status(void)
{
    return sReady ? sStatus : "off";
}

/* ── Draw ───────────────────────────────────────────────────────────────── */

/*
 * Folds "render into the top-left 400x240 of a 512x256 surface" into the
 * projection: a clip-space scale and bias, applied before the perspective
 * divide so it costs nothing at run time.
 */
static void FitToLogicalSurface(C3D_Mtx *mtx)
{
    float sx = CTR_GAME_WIDTH / VOXEL_SURFACE_W;
    float tx = sx - 1.0f;
    float sy = CTR_GAME_HEIGHT / VOXEL_SURFACE_H;
    float ty = 1.0f - sy;

    for (int i = 0; i < 4; ++i)
    {
        mtx->r[0].c[i] = sx * mtx->r[0].c[i] + tx * mtx->r[3].c[i];
        mtx->r[1].c[i] = sy * mtx->r[1].c[i] + ty * mtx->r[3].c[i];
    }
}

static void SetModelView(const C3D_Mtx *view, int worldX, int worldZ)
{
    C3D_Mtx model;

    Mtx_Copy(&model, view);
    Mtx_Translate(&model, (float)worldX, 0.0f, (float)worldZ, true);
    C3D_FVUnifMtx4x4(GPU_VERTEX_SHADER, sUniModelView, &model);
}

/*
 * Fetched again for every draw, not hoisted: C3D_GetBufInfo is what marks the
 * buffer configuration dirty, so writing through a pointer taken once leaves
 * every draw after the first reading the first buffer's vertices.
 */
static void BindVertices(const VoxelGpuVertex *vertices)
{
    C3D_BufInfo *buf = C3D_GetBufInfo();

    BufInfo_Init(buf);
    BufInfo_Add(buf, vertices, sizeof(VoxelGpuVertex), 2, 0x10);
}

/*
 * Colour grade and distance haze, both worked out per vertex by the shader
 * from what the chunks already carry: no extra pass, texture or target, and
 * nothing rebuilt when the values change.
 *
 * The baked shade only ever darkened a texel towards grey. Graded, the same
 * number picks a colour: full sun (1.0) is slightly warm and slightly above
 * the art, the baked shadow (0.70) and the dark sides of structures (0.68)
 * lean blue and a little darker. The art keeps its hues; lit against shaded
 * reads further apart than before.
 *
 * The haze starts a little beyond the player and grows towards the top of the
 * screen, which with this camera is the distance: the far rows fade towards a
 * pale sky colour and the ground reads as receding. It is relative to the
 * camera's own distance, so a wider map framed from further away hazes the
 * same share of the screen.
 *
 * Indoors, and with lighting compiled out, both are the identity.
 */
#define VOXEL_GRADE_SHADE_LOW 0.62f     /* at or below: fully the shadow tint */
#define VOXEL_HAZE_START 1.05f          /* x the eye-to-player distance */
#define VOXEL_HAZE_RAMP 0.60f           /* ... to full haze this much further */
#define VOXEL_HAZE_MAX 0.30f
/* 0xAABBGGRR, as the texture environment takes it. */
#define VOXEL_HAZE_COLOUR 0xFFECDECEu

static void SetGrade(bool indoor)
{
    float sun[3] = {1.08f, 1.03f, 0.93f}, shade[3] = {0.80f, 0.86f, 1.02f};
    float fogScale = 0.0f, fogStart = 0.0f, fogMax = 0.0f;

#if CTR_VOXEL_LIGHTING
    if (!indoor)
    {
        float eye = sCamera.distance / cosf(C3D_AngleFromDegrees(sCamera.pitch));
        float haze = VOXEL_HAZE_MAX;

        switch (VoxelWorld_Weather())
        {
        case VOXEL_WEATHER_SUN:
            sun[0] = 1.14f; sun[1] = 1.06f; sun[2] = 0.88f;
            haze = 0.24f;
            break;
        case VOXEL_WEATHER_RAIN:
            sun[0] = 0.81f; sun[1] = 0.88f; sun[2] = 0.98f;
            shade[0] = 0.67f; shade[1] = 0.77f; shade[2] = 0.93f;
            haze = 0.39f;
            break;
        case VOXEL_WEATHER_FOG:
            sun[0] = 0.93f; sun[1] = 0.96f; sun[2] = 0.98f;
            haze = 0.52f;
            break;
        case VOXEL_WEATHER_PARTICLES:
            sun[0] = 0.91f; sun[1] = 0.90f; sun[2] = 0.88f;
            haze = 0.42f;
            break;
        case VOXEL_WEATHER_SHADE:
            sun[0] = 0.91f; sun[1] = 0.94f; sun[2] = 0.97f;
            haze = 0.35f;
            break;
        default:
            break;
        }
        fogStart = eye * VOXEL_HAZE_START;
        fogScale = haze / (eye * VOXEL_HAZE_RAMP);
        fogMax = haze;
    }
    else
#endif
    {
        (void)indoor;
        for (int i = 0; i < 3; ++i)
            sun[i] = shade[i] = 1.0f;
    }
    /* Halved: texture environment 0 scales by two. */
    C3D_FVUnifSet(GPU_VERTEX_SHADER, sUniShadeTint,
                  shade[0] * 0.5f, shade[1] * 0.5f, shade[2] * 0.5f, 0.0f);
    C3D_FVUnifSet(GPU_VERTEX_SHADER, sUniTintDiff,
                  (sun[0] - shade[0]) * 0.5f, (sun[1] - shade[1]) * 0.5f,
                  (sun[2] - shade[2]) * 0.5f, 0.0f);
    C3D_FVUnifSet(GPU_VERTEX_SHADER, sUniGrade, 1.0f / (1.0f - VOXEL_GRADE_SHADE_LOW),
                  -VOXEL_GRADE_SHADE_LOW / (1.0f - VOXEL_GRADE_SHADE_LOW), 0.0f, 0.0f);
    /* haze = (-viewZ - start) * scale = viewZ * -scale - start * scale */
    C3D_FVUnifSet(GPU_VERTEX_SHADER, sUniFog, -fogScale, -fogStart * fogScale, fogMax, 0.0f);
}

/*
 * Environment 0: texture x the graded colour, doubled; alpha is the texture's
 * alone, so the haze weight the shader puts in the vertex alpha never reaches
 * the alpha test. Environment 1: towards the haze colour by that weight.
 */
static void TerrainTexEnv(void)
{
    C3D_TexEnv *env = C3D_GetTexEnv(0);

    C3D_TexEnvInit(env);
    C3D_TexEnvSrc(env, C3D_RGB, GPU_TEXTURE0, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR);
    C3D_TexEnvFunc(env, C3D_RGB, GPU_MODULATE);
    C3D_TexEnvScale(env, C3D_RGB, GPU_TEVSCALE_2);
    C3D_TexEnvSrc(env, C3D_Alpha, GPU_TEXTURE0, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR);
    C3D_TexEnvFunc(env, C3D_Alpha, GPU_REPLACE);
}

static void HazeTexEnv(uint32_t colour)
{
    C3D_TexEnv *env = C3D_GetTexEnv(1);

    C3D_TexEnvInit(env);
    C3D_TexEnvSrc(env, C3D_RGB, GPU_PREVIOUS, GPU_CONSTANT, GPU_PRIMARY_COLOR);
    C3D_TexEnvOpRgb(env, GPU_TEVOP_RGB_SRC_COLOR, GPU_TEVOP_RGB_SRC_COLOR,
                    GPU_TEVOP_RGB_SRC_ALPHA);
    C3D_TexEnvFunc(env, C3D_RGB, GPU_INTERPOLATE);
    C3D_TexEnvSrc(env, C3D_Alpha, GPU_PREVIOUS, GPU_PREVIOUS, GPU_PREVIOUS);
    C3D_TexEnvFunc(env, C3D_Alpha, GPU_REPLACE);
    C3D_TexEnvColor(env, colour);
}

/*
 * Screen fades. On the GBA a fade rewrites the shown palette, and the world's
 * textures are baked from the tilesets' own palettes, so the world has to be
 * faded here: environment 2 blends everything drawn from those textures (and
 * the constant colours drawn over them, the cast shadows and the player's
 * silhouette) towards the fade colour. Sprites are decoded from the shown
 * palette and are faded already; they only take the brightness effect, and a
 * haze colour faded with the world so a distant sprite fades with it.
 */
typedef struct
{
    float amount, rgb[3];
} VoxelFade;
static VoxelFade sWorldFade, sSpriteFade;
static float sBrightBg, sBrightObj;
static bool sBrightWhite;

void CtrVoxel_SetBrightness(float backgrounds, float sprites, bool white)
{
    sBrightBg = backgrounds;
    sBrightObj = sprites;
    sBrightWhite = white;
}

/* Blends of a then b, as one: (1-a)(1-b)x + (a(1-b)A + bB). */
static VoxelFade FadeThen(VoxelFade first, float amount, float target)
{
    VoxelFade out = first;
    float total = 1.0f - (1.0f - first.amount) * (1.0f - amount);

    if (amount <= 0.0f)
        return out;
    out.amount = total;
    for (int c = 0; c < 3; ++c)
        out.rgb[c] = (first.amount * (1.0f - amount) * first.rgb[c] + amount * target) / total;
    return out;
}

static uint32_t FadeColour(float amount, const float rgb[3])
{
    unsigned a = (unsigned)(amount * 255.0f + 0.5f);
    unsigned r = (unsigned)(rgb[0] * 255.0f + 0.5f);
    unsigned g = (unsigned)(rgb[1] * 255.0f + 0.5f);
    unsigned b = (unsigned)(rgb[2] * 255.0f + 0.5f);

    return a << 24 | b << 16 | g << 8 | r;
}

static void PrepareFades(void)
{
    VoxelFade none = {0.0f, {0.0f, 0.0f, 0.0f}};
    float target = sBrightWhite ? 1.0f : 0.0f;

    sWorldFade = none;
    VoxelWorld_ScreenFade(&sWorldFade.amount, sWorldFade.rgb);
    sWorldFade = FadeThen(sWorldFade, sBrightBg, target);
    sSpriteFade = FadeThen(none, sBrightObj, target);
}

/* The haze colour a sprite pass uses: the world's, faded as the world is. */
static uint32_t SpriteHaze(void)
{
    const float haze[3] = {(VOXEL_HAZE_COLOUR & 255) / 255.0f,
                           ((VOXEL_HAZE_COLOUR >> 8) & 255) / 255.0f,
                           ((VOXEL_HAZE_COLOUR >> 16) & 255) / 255.0f};
    float rgb[3];

    for (int c = 0; c < 3; ++c)
        rgb[c] = haze[c] + (sWorldFade.rgb[c] - haze[c]) * sWorldFade.amount;
    return FadeColour(1.0f, rgb);
}

static void FadeTexEnv(const VoxelFade *fade)
{
    C3D_TexEnv *env = C3D_GetTexEnv(2);

    C3D_TexEnvInit(env);
    if (fade->amount <= 0.0f)
        return;
    /* constant x a + previous x (1 - a) */
    C3D_TexEnvSrc(env, C3D_RGB, GPU_CONSTANT, GPU_PREVIOUS, GPU_CONSTANT);
    C3D_TexEnvOpRgb(env, GPU_TEVOP_RGB_SRC_COLOR, GPU_TEVOP_RGB_SRC_COLOR,
                    GPU_TEVOP_RGB_SRC_ALPHA);
    C3D_TexEnvFunc(env, C3D_RGB, GPU_INTERPOLATE);
    C3D_TexEnvSrc(env, C3D_Alpha, GPU_PREVIOUS, GPU_PREVIOUS, GPU_PREVIOUS);
    C3D_TexEnvFunc(env, C3D_Alpha, GPU_REPLACE);
    C3D_TexEnvColor(env, FadeColour(fade->amount, fade->rgb));
}

/* Environments 1 and 2 for what is drawn next: the world's or a sprite's. */
static void FadeFor(bool sprites)
{
    HazeTexEnv(sprites ? SpriteHaze() : VOXEL_HAZE_COLOUR);
    FadeTexEnv(sprites ? &sSpriteFade : &sWorldFade);
}

void CtrVoxel_Draw(C3D_RenderTarget *target, float eyeOffset)
{
    C3D_Mtx projection, view;
    C3D_AttrInfo *attr;
    C3D_TexEnv *env;
    const VoxelMapInstance *current = VoxelWorld_Instance(0);
    bool indoor;

    /* Stereoscopy is V8; the first milestone renders one eye. */
    (void)eyeOffset;
    if (!sReady || sDrawCount == 0)
        return;

    C3D_FrameDrawOn(target);

    Mtx_Persp(&projection, C3D_AngleFromDegrees(sCamera.fov),
              (float)CTR_GAME_WIDTH / (float)CTR_GAME_HEIGHT,
              VOXEL_NEAR, VOXEL_FAR, false);
    FitToLogicalSurface(&projection);
    Mtx_LookAt(&view,
               FVec3_New(sCamera.x, sCamera.y, sCamera.z),
               FVec3_New(sCamera.targetX, sCamera.targetY, sCamera.targetZ),
               FVec3_New(0.0f, 1.0f, 0.0f), false);

    C3D_BindProgram(&sProgram);

    /* VoxelGpuVertex: texcoord (2 floats), then position + shade (4 shorts). */
    attr = C3D_GetAttrInfo();
    AttrInfo_Init(attr);
    AttrInfo_AddLoader(attr, 1, GPU_FLOAT, 2);
    AttrInfo_AddLoader(attr, 0, GPU_SHORT, 4);

    /* texture0 x graded colour, then the distance haze, then the fade. */
    PrepareFades();
    TerrainTexEnv();
    FadeFor(false);
    for (int i = 3; i < 6; ++i)
        C3D_TexEnvInit(C3D_GetTexEnv(i));

    C3D_CullFace(GPU_CULL_NONE);
    C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
    C3D_ColorLogicOp(GPU_LOGICOP_COPY);

    C3D_FVUnifMtx4x4(GPU_VERTEX_SHADER, sUniProjection, &projection);
    indoor = current != NULL && current->indoor;
    SetGrade(indoor);

    /*
     * Ordinary terrain first, then the tree material with alpha test, then
     * the modelled buildings. The transparent corners of the crowns must write
     * neither colour nor depth. Each chunk keeps all three ranges in the same
     * static VRAM buffer, measured from its own corner, so every draw carries
     * that corner's place in the world in its model-view.
     */
    for (unsigned pass = 0; pass < 3; ++pass)
    {
        const C3D_Tex *boundTex = NULL;

        C3D_AlphaTest(pass != 0, GPU_GREATER, 0);

        for (unsigned i = 0; i < sDrawCount; ++i)
        {
            const VoxelChunk *chunk = sDraws[i].chunk;
            unsigned first = pass == 0 ? 0
                           : pass == 1 ? chunk->terrainCount : chunk->buildingFirst;
            unsigned count = pass == 0 ? chunk->terrainCount
                           : pass == 1 ? chunk->buildingFirst - chunk->terrainCount
                           : chunk->count - chunk->buildingFirst;
            C3D_Tex *tex;

            if (count == 0)
                continue;
            tex = pass == 0 ? &chunk->atlas->tex
                : pass == 1 ? &sTreeAtlas : BuildingPage(chunk->buildingPage);
            if (tex == NULL)
                continue;

            SetModelView(&view, sDraws[i].worldX, sDraws[i].worldZ);
            if (boundTex != tex)
            {
                boundTex = tex;
                C3D_TexBind(0, tex);
            }
            BindVertices(chunk->vram);
            C3D_DrawArrays(GPU_TRIANGLES, (int)first, (int)count);
        }
    }

    /* Billboards and shadows: the linear buffer, around the camera's tile. */
    SetModelView(&view, sDynamicX, sDynamicZ);
    BindVertices(sDynamic);

    if (sReflectionVertices != 0)
    {
        /* Mirrored sprite art lies just above reflective ground. One draw for
         * every visible reflection, blended without writing depth. */
        SetGrade(true);
        C3D_TexBind(0, &sSpriteAtlas);
        C3D_AlphaTest(true, GPU_GREATER, 0);
        C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_COLOR);
        C3D_AlphaBlend(GPU_BLEND_ADD, GPU_BLEND_ADD,
                       GPU_SRC_ALPHA, GPU_ONE_MINUS_SRC_ALPHA, GPU_ZERO, GPU_ONE);
        env = C3D_GetTexEnv(0);
        C3D_TexEnvSrc(env, C3D_Alpha, GPU_TEXTURE0, GPU_CONSTANT, GPU_CONSTANT);
        C3D_TexEnvFunc(env, C3D_Alpha, GPU_MODULATE);
        C3D_TexEnvColor(env, 0x60FFFFFFu);
        FadeFor(true);
        C3D_DrawArrays(GPU_TRIANGLES, VOXEL_REFLECTION_FIRST, sReflectionVertices);
        C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
        TerrainTexEnv();
        FadeFor(false);
        SetGrade(indoor);
    }

#if CTR_VOXEL_LIGHTING
    /*
     * Cast shadows: each object's own sprite laid on the ground (see
     * EmitCastShadow), so the silhouette is the texture's alpha. Colour is a
     * constant dark blue; alpha is texture alpha x the strength the vertex
     * carries in shade, which reaches the primary colour unchanged under the
     * identity grade (half of it, doubled back by the alpha scale). Depth
     * tested against the finished terrain and crowns, never written: a shadow
     * must not occlude a sprite. One draw for all of them.
     */
    if (sShadowVertices != 0)
    {
        SetGrade(true);
        C3D_TexBind(0, &sSpriteAtlas);
        C3D_AlphaTest(true, GPU_GREATER, 0);
        C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_COLOR);
        C3D_AlphaBlend(GPU_BLEND_ADD, GPU_BLEND_ADD,
                       GPU_SRC_ALPHA, GPU_ONE_MINUS_SRC_ALPHA, GPU_ZERO, GPU_ONE);
        env = C3D_GetTexEnv(0);
        C3D_TexEnvInit(env);
        C3D_TexEnvSrc(env, C3D_RGB, GPU_CONSTANT, GPU_CONSTANT, GPU_CONSTANT);
        C3D_TexEnvFunc(env, C3D_RGB, GPU_REPLACE);
        C3D_TexEnvSrc(env, C3D_Alpha, GPU_TEXTURE0, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR);
        C3D_TexEnvOpAlpha(env, GPU_TEVOP_A_SRC_ALPHA, GPU_TEVOP_A_SRC_R, GPU_TEVOP_A_SRC_ALPHA);
        C3D_TexEnvFunc(env, C3D_Alpha, GPU_MODULATE);
        C3D_TexEnvScale(env, C3D_Alpha, GPU_TEVSCALE_2);
        C3D_TexEnvColor(env, 0xFF281408u);
        C3D_DrawArrays(GPU_TRIANGLES, VOXEL_SHADOW_FIRST, sShadowVertices);
        C3D_ColorLogicOp(GPU_LOGICOP_COPY);
        C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
        TerrainTexEnv();
        SetGrade(indoor);
    }
#endif

    /*
     * Billboards last. The atlas is RGBA5551, so transparency is one bit and
     * an alpha test is enough - no blending, and depth is written only where
     * the sprite is actually opaque, which is what makes a wall occlude an NPC
     * without the NPC's empty corners punching a hole in it.
     */
    if (sSpriteVertices != 0)
    {
        int playerFirst = VoxelEntities_PlayerVertexFirst();

        C3D_AlphaTest(true, GPU_GREATER, 0);
        C3D_TexBind(0, &sSpriteAtlas);
        if (playerFirst >= 0)
        {
            /* Reversed depth: LESS selects only the player's pixels hidden by
             * terrain. Texture alpha preserves the sprite outline. One quad,
             * colour-only writes; neither roofs nor their depth are modified. */
            C3D_DepthTest(true, GPU_LESS, GPU_WRITE_COLOR);
            C3D_AlphaBlend(GPU_BLEND_ADD, GPU_BLEND_ADD,
                           GPU_SRC_ALPHA, GPU_ONE_MINUS_SRC_ALPHA, GPU_ZERO, GPU_ONE);
            env = C3D_GetTexEnv(0);
            C3D_TexEnvSrc(env, C3D_RGB, GPU_CONSTANT, GPU_CONSTANT, GPU_CONSTANT);
            C3D_TexEnvFunc(env, C3D_RGB, GPU_REPLACE);
            C3D_TexEnvSrc(env, C3D_Alpha, GPU_TEXTURE0, GPU_CONSTANT, GPU_CONSTANT);
            C3D_TexEnvFunc(env, C3D_Alpha, GPU_MODULATE);
            C3D_TexEnvScale(env, C3D_RGB, GPU_TEVSCALE_1);
            C3D_TexEnvColor(env, 0xA0D8E8F0u);
            C3D_DrawArrays(GPU_TRIANGLES, playerFirst, 6);
            C3D_ColorLogicOp(GPU_LOGICOP_COPY);
            C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
            /* DrawArrays consumed the dirty flag. Reacquire the environment
             * so Citro3D uploads the restored sprite colours on the next draw. */
            TerrainTexEnv();
        }
        FadeFor(true);
        C3D_DrawArrays(GPU_TRIANGLES, 0, sSpriteVertices);
    }
    C3D_AlphaTest(false, GPU_ALWAYS, 0);
    /* The 2D compositor sets up stage 0 only. */
    C3D_TexEnvInit(C3D_GetTexEnv(1));
    C3D_TexEnvInit(C3D_GetTexEnv(2));
}
