/* Include the real world implementation so fixtures can arrange overlapping
 * instances and compare its batched hash with its scalar raw-block reader. */
#include <assert.h>
#include "../src/voxel/voxel_world.c"

struct MapHeader gMapHeader;
struct BackupMapLayout gBackupMapLayout;
struct SaveBlock1 *gSaveBlock1Ptr;
struct Main gMain;
struct ObjectEvent gObjectEvents[OBJECT_EVENTS_COUNT];
struct PlayerAvatar gPlayerAvatar;
const struct Tileset gTileset_General = {0};
static u16 sFortreeAttributes[280] = {
    [628 - NUM_METATILES_IN_PRIMARY] = MB_PUDDLE,
    [657 - NUM_METATILES_IN_PRIMARY] = MB_PUDDLE,
};
const struct Tileset gTileset_Fortree = {.metatileAttributes = sFortreeAttributes};
bool8 MetatileBehavior_IsReflective(u8 b) { return b == MB_PUDDLE || b == MB_POND_WATER; }
bool8 MetatileBehavior_IsIce(u8 b) { return b == MB_ICE; }
const struct Tileset gTileset_GenericBuilding = {0};
bool VoxelAtlas_IsVoid(const VoxelMapInstance *inst, int metatile) { (void)inst; (void)metatile; return false; }

const void *Port_ResolveAssetPointer(const void *p) { return p; }
u32 Port_GetAssetSizeExact(const void *p) { (void)p; return 0; }
void LZDecompressWram(const u32 *src, void *dst) { (void)src; (void)dst; }
#ifdef PORT_EXPANSION
/* The expansion's decompress.c and metatile_behavior.c are the ROM's; the test
 * links nothing from them, so it supplies the calls the voxel units make. */
void FastLZ77UnCompWram(const u32 *src, void *dst) { (void)src; (void)dst; }
bool8 MetatileBehavior_IsSurfableWaterOrUnderwater(u8 b) { (void)b; return FALSE; }
bool8 MetatileBehavior_IsPuddle(u8 b) { return b == MB_PUDDLE; }
bool8 MetatileBehavior_IsShallowFlowingWater(u8 b) { (void)b; return FALSE; }
bool8 MetatileBehavior_IsCounter(u8 b) { (void)b; return FALSE; }
bool8 MetatileBehavior_IsPC(u8 b) { (void)b; return FALSE; }
bool8 MetatileBehavior_IsSecretBasePC(u8 b) { (void)b; return FALSE; }
bool8 MetatileBehavior_IsPlayerRoomPCOn(u8 b) { (void)b; return FALSE; }
#endif
void CB2_Overworld(void) {}
void CB2_OverworldBasic(void) {}
const struct MapHeader *const GetMapHeaderFromConnection(const struct MapConnection *c)
{ (void)c; return NULL; }
unsigned VoxelRegions_RoleAt(unsigned id, int x, int y)
{ (void)id; (void)x; (void)y; return VOXEL_ROLE_FLOOR; }
VoxelVisualShape Voxel_BehaviorShape(unsigned b) { (void)b; return VOXEL_SHAPE_COUNT; }
int VoxelTree_Part(int id) { (void)id; return -1; }
int VoxelTree_GroundMetatile(int id) { return id; }
bool VoxelRelief_IsDrawn(const VoxelMapInstance *inst) { (void)inst; return false; }

static void Check(int x0, int y0, int x1, int y1)
{
    uint32_t expected = 2166136261u;
    for (int y = y0; y < y1; ++y)
        for (int x = x0; x < x1; ++x)
            expected = (expected ^ GetRawBlock(x, y, NULL)) * 16777619u;
    assert(VoxelWorld_BlockHash(x0, y0, x1, y1) == expected);
}

int main(void)
{
    u16 live[32 * 32], a[16 * 16], b[16 * 16];
    struct MapLayout layouts[3] = {{.map = a}, {.map = b}, {.map = NULL}};
    for (unsigned i = 0; i < 32 * 32; ++i) live[i] = (u16)(i * 7919u);
    for (unsigned i = 0; i < 16 * 16; ++i)
    { a[i] = (u16)(i * 31u); b[i] = (u16)(i * 97u); }
    gBackupMapLayout.map = live;
    gBackupMapLayout.width = gBackupMapLayout.height = 32;
    sInstanceCount = 4;
    sInstances[0] = (VoxelMapInstance){.width = 16, .height = 16};
    sInstances[1] = (VoxelMapInstance){.layout = &layouts[0], .originX = -8,
        .originY = -4, .width = 16, .height = 16};
    sInstances[2] = (VoxelMapInstance){.layout = &layouts[1], .originX = 12,
        .originY = 10, .width = 16, .height = 16};
    sInstances[3] = (VoxelMapInstance){.layout = &layouts[2], .originX = -12,
        .originY = 2, .width = 16, .height = 16};
    VoxelWorld_BeginBatch();
    for (int y = -16; y < 30; y += 3)
        for (int x = -20; x < 30; x += 3)
        {
            Check(x, y, x + 18, y + 26);
            Check(x, y, x + 80, y + 2);
            Check(x, y, x + 81, y + 2); /* scalar fallback */
            Check(x, y, x, y);
        }
    /* Incomplete live grids must zero their overlap rather than expose a
     * lower-priority neighbour; collision/elevation edits change the hash. */
    gBackupMapLayout.width = gBackupMapLayout.height = 12;
    Check(-20, -16, 40, 32);
    uint32_t before = VoxelWorld_BlockHash(0, 0, 4, 4);
    live[MAP_OFFSET * 12 + MAP_OFFSET] ^= 0xFC00;
    assert(before != VoxelWorld_BlockHash(0, 0, 4, 4));
    Check(0, 0, 4, 4);
    /* The live digest sees the same edits, and only them. */
    uint32_t digest = VoxelWorld_LiveDigest();
    assert(digest == VoxelWorld_LiveDigest());
    live[3] ^= 1;
    assert(digest != VoxelWorld_LiveDigest());
    live[3] ^= 1;
    assert(digest == VoxelWorld_LiveDigest());
    sInstanceCount = 0;
    Check(-20, -20, 40, 40);
    /* Both cells have MB_PUDDLE, but Fortree's 657 is drawn as grass. */
    gBackupMapLayout.width = gBackupMapLayout.height = 12;
    live[MAP_OFFSET + MAP_OFFSET * 12] = 657;
    live[MAP_OFFSET + 1 + MAP_OFFSET * 12] = 628;
    sInstanceCount = 1;
    sInstances[0] = (VoxelMapInstance){.width = 2, .height = 1,
                                     .secondaryTileset = &gTileset_Fortree};
    VoxelWorld_BeginBatch();
    assert(!VoxelWorld_IsVisibleReflectiveSurface(0, 0));
    assert(VoxelWorld_IsVisibleReflectiveSurface(1, 0));
    puts("PASS world hash: scalar equivalence, live digest, overlaps, negative origins, missing maps, live edits, clipping; Fortree puddle art");
    return 0;
}
