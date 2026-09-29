/*
 * CPU geometry for the voxel overworld. See voxel_mesh_builder.h.
 *
 * The shapes, heights, face culling rules and the camera-side cutaway are
 * ported from the MIT-licensed src/platform/voxel/voxel_mesh.c of
 * pokeemerald-multiplatform (see NOTICE.md). The immediate-mode emission is
 * replaced by the builder, and the reference's 2048x2048 "wall consumed" array
 * is not ported at all: nothing here remembers what it has already emitted, so
 * a cell can be built without building its neighbours first.
 */

#include <stddef.h>
#include <string.h>

#include "voxel_mesh_builder.h"
#include "voxel_atlas.h"
#include "voxel_tree.h"
#include "voxel_lighting.h"
#include "voxel_sign.h"
#include "voxel_building.h"
#include "voxel_relief.h"

/*
 * Face shades, from the greys the reference gives its untextured side faces.
 * Here every face is textured and the shade multiplies the sample, so the
 * ordering is what matters: a lit top, a bright south face towards the camera,
 * a dark north face away from it.
 */
#define SHADE_TOP   1.00f
#define SHADE_SOUTH 0.60f
#define SHADE_NORTH 0.50f
#define SHADE_WEST  0.65f
#define SHADE_EAST  0.55f

/*
 * Solid furniture parts barely vary with facing. The shades above are meant
 * for metatile art, which is bright; a flat colour that is already dark goes
 * to black when multiplied by 0.55, which is how the first build turned every
 * television into a hole in the floor. Enough variation to read as a box, no
 * more.
 */
#define SOLID_LIT   1.00f
#define SOLID_FRONT 0.94f
#define SOLID_BACK  0.88f
#define SOLID_SIDE  0.91f

static int sWindowX0, sWindowY0, sWindowW, sWindowH;

/*
 * Classification of each tile in the window, computed at most once per
 * rebuild. 0 means "not yet", otherwise shape + 1.
 *
 * VoxelWorld_ClassifyTile is not cheap - it reads the block, the collision of
 * up to five neighbours and the metatile behaviour, which reaches into the
 * asset layer - and the rebuild asks for the same tile about five times: once
 * for itself, and once as the neighbour of each of its four neighbours when
 * they decide which faces to cull.
 */
static uint8_t sShapes[VOXEL_WINDOW_MAX * VOXEL_WINDOW_MAX];

VoxelVisualShape VoxelMesh_Classify(int x, int y)
{
    int cx = x - sWindowX0, cy = y - sWindowY0;
    unsigned slot;
    VoxelVisualShape shape;

    if (cx < 0 || cx >= sWindowW || cy < 0 || cy >= sWindowH)
        return VoxelWorld_ClassifyTile(x, y);
    slot = (unsigned)(cy * VOXEL_WINDOW_MAX + cx);
    if (sShapes[slot] != 0)
        return (VoxelVisualShape)(sShapes[slot] - 1);
    shape = VoxelWorld_ClassifyTile(x, y);
    sShapes[slot] = (uint8_t)(shape + 1);
    return shape;
}

void VoxelBuilder_Init(VoxelBuilder *builder, VoxelVertex *storage, unsigned capacity)
{
    builder->vertices = storage;
    builder->capacity = capacity;
    builder->count = 0;
    builder->dropped = 0;
    builder->atlas = NULL;
    builder->uncovered = 0;
    builder->originX = 0.0f;
    builder->originZ = 0.0f;
    builder->lift = 0.0f;
    builder->shift = 0.0f;
    builder->base = 0.0f;
    builder->artShaded = false;
    builder->lighting = false;
    builder->lightingRefine = true;
    builder->lightingConstant = -1.0f;
}

void VoxelBuilder_SetOrigin(VoxelBuilder *builder, int originX, int originZ)
{
    builder->originX = (float)originX;
    builder->originZ = (float)originZ;
}

/* Does not clear `uncovered`: a rebuild walks several instances and several
 * atlases, and the caller wants the total. VoxelBuilder_Init clears it. */
void VoxelBuilder_SetAtlas(VoxelBuilder *builder, const struct VoxelAtlasMap *atlas)
{
    builder->atlas = atlas;
}

void VoxelBuilder_Tri(VoxelBuilder *builder, const VoxelVertex *a,
                      const VoxelVertex *b, const VoxelVertex *c)
{
    if (builder->count + 3 > builder->capacity)
    {
        ++builder->dropped;
        return;
    }
    const VoxelVertex *v[3] = { a, b, c };

    for (int i = 0; i < 3; ++i)
    {
        VoxelVertex *out = &builder->vertices[builder->count++];

        *out = *v[i];
        out->x -= builder->originX;
        out->y += builder->lift + builder->base;
        out->z += builder->shift - builder->originZ;
    }
}

void VoxelBuilder_Quad(VoxelBuilder *builder, const VoxelVertex *a, const VoxelVertex *b,
                       const VoxelVertex *c, const VoxelVertex *d)
{
#if CTR_VOXEL_LIGHTING
    if (builder->lighting)
    {
        VoxelLighting_Quad(builder, a, b, c, d);
        return;
    }
#endif
    VoxelBuilder_Tri(builder, a, b, c);
    VoxelBuilder_Tri(builder, a, c, d);
}

/* ── Window bookkeeping ─────────────────────────────────────────────────── */

static void WindowReset(int x0, int y0, int x1, int y1)
{
    sWindowX0 = x0;
    sWindowY0 = y0;
    sWindowW = x1 - x0;
    sWindowH = y1 - y0;
    if (sWindowW > VOXEL_WINDOW_MAX) sWindowW = VOXEL_WINDOW_MAX;
    if (sWindowH > VOXEL_WINDOW_MAX) sWindowH = VOXEL_WINDOW_MAX;
    /* Row by row rather than the whole 6.4 KiB: a chunk build opens a
     * rectangle of about 10x18, and clearing the other 6 KiB of a cache it
     * will never touch costs more than the classifications it saves. */
    for (int row = 0; row < sWindowH; ++row)
        memset(&sShapes[row * VOXEL_WINDOW_MAX], 0, (size_t)sWindowW);
}

/* ── UV lookup ──────────────────────────────────────────────────────────── */

/* Returns false when the atlas has no pixels for this tile's metatile; the
 * caller emits nothing, and `uncovered` has already asked for a rebuild. */
/* UVs of an explicit metatile id in the bound atlas. */
static bool MetatileUV(VoxelBuilder *builder, int metatileId,
                       float *u0, float *v0, float *u1, float *v1)
{
    unsigned slot;

    if (metatileId < 0)
        return false;
    slot = builder->atlas->slotOf[metatileId];
    if (slot == 0)
    {
        ++builder->uncovered;
        return false;
    }
    if (slot == VOXEL_SLOT_ABSENT)
        return false;
    VoxelAtlas_SlotUV(slot - 1, u0, v0, u1, v1);
    return true;
}

bool VoxelMesh_TileUV(VoxelBuilder *builder, int x, int y,
                   float *u0, float *v0, float *u1, float *v1)
{
    unsigned slot;
    int metatile;

    /*
     * Nothing stands on a tile that belongs to no map on screen, and indoors
     * VOID is the black out-of-bounds filler. Asking either for its metatile
     * gives an id the atlas was never built with, which used to be counted as
     * a gap - so a wall whose top reached past the building had the atlas
     * rebuilt, every frame, for geometry that must not be drawn at all.
     */
    if (VoxelMesh_Classify(x, y) == VOXEL_SHAPE_VOID)
        return false;
    metatile = VoxelWorld_GetMetatileId(x, y);
    if (VoxelWorld_UsesTreeSprites(VoxelWorld_GetInstanceAt(x, y)))
        metatile = VoxelTree_GroundMetatile(metatile);
    slot = builder->atlas->slotOf[metatile];

    if (slot == 0)
    {
        ++builder->uncovered;
        return false;
    }
    if (slot == VOXEL_SLOT_ABSENT)
        return false;
    VoxelAtlas_SlotUV(slot - 1, u0, v0, u1, v1);
    return true;
}

/* ── Face emission ──────────────────────────────────────────────────────── */

/* Horizontal face at height `h`, spanning the tile inset from its edges. */
void VoxelMesh_Top(VoxelBuilder *b, float wx, float wz, float h, float inset,
                    float u0, float v0, float u1, float v1, float shade)
{
    float x0 = wx + inset, x1 = wx + 1.0f - inset;
    float z0 = wz + inset, z1 = wz + 1.0f - inset;

    VoxelBuilder_Quad(b,
        &(VoxelVertex){x0, h, z0, u0, v0, shade},
        &(VoxelVertex){x1, h, z0, u1, v0, shade},
        &(VoxelVertex){x1, h, z1, u1, v1, shade},
        &(VoxelVertex){x0, h, z1, u0, v1, shade});
}

void VoxelMesh_South(VoxelBuilder *b, float wx, float wz, float yBottom, float yTop,
                      float inset, float u0, float v0, float u1, float v1)
{
    float x0 = wx + inset, x1 = wx + 1.0f - inset, z = wz + 1.0f - inset;

    VoxelBuilder_Quad(b,
        &(VoxelVertex){x0, yBottom, z, u0, v1, SHADE_SOUTH},
        &(VoxelVertex){x1, yBottom, z, u1, v1, SHADE_SOUTH},
        &(VoxelVertex){x1, yTop,    z, u1, v0, SHADE_SOUTH},
        &(VoxelVertex){x0, yTop,    z, u0, v0, SHADE_SOUTH});
}

void VoxelMesh_North(VoxelBuilder *b, float wx, float wz, float yBottom, float yTop,
                      float inset, float u0, float v0, float u1, float v1)
{
    float x0 = wx + inset, x1 = wx + 1.0f - inset, z = wz + inset;

    VoxelBuilder_Quad(b,
        &(VoxelVertex){x1, yBottom, z, u0, v1, SHADE_NORTH},
        &(VoxelVertex){x0, yBottom, z, u1, v1, SHADE_NORTH},
        &(VoxelVertex){x0, yTop,    z, u1, v0, SHADE_NORTH},
        &(VoxelVertex){x1, yTop,    z, u0, v0, SHADE_NORTH});
}

void VoxelMesh_West(VoxelBuilder *b, float wx, float wz, float yBottom, float yTop,
                     float inset, float u0, float v0, float u1, float v1)
{
    float x = wx + inset, z0 = wz + inset, z1 = wz + 1.0f - inset;

    VoxelBuilder_Quad(b,
        &(VoxelVertex){x, yBottom, z0, u0, v1, SHADE_WEST},
        &(VoxelVertex){x, yBottom, z1, u1, v1, SHADE_WEST},
        &(VoxelVertex){x, yTop,    z1, u1, v0, SHADE_WEST},
        &(VoxelVertex){x, yTop,    z0, u0, v0, SHADE_WEST});
}

void VoxelMesh_East(VoxelBuilder *b, float wx, float wz, float yBottom, float yTop,
                     float inset, float u0, float v0, float u1, float v1)
{
    float x = wx + 1.0f - inset, z0 = wz + inset, z1 = wz + 1.0f - inset;

    VoxelBuilder_Quad(b,
        &(VoxelVertex){x, yBottom, z1, u0, v1, SHADE_EAST},
        &(VoxelVertex){x, yBottom, z0, u1, v1, SHADE_EAST},
        &(VoxelVertex){x, yTop,    z0, u1, v0, SHADE_EAST},
        &(VoxelVertex){x, yTop,    z1, u0, v0, SHADE_EAST});
}

/* ── Single tiles ───────────────────────────────────────────────────────── */

/* Height of the block a shape is drawn as, or 0 for a flat tile. Negative
 * recesses it. The values are the reference's. Terrain has none: what stands
 * up outdoors is modelled or read off its drawing, never raised from here. */
static float ShapeHeight(VoxelVisualShape shape)
{
    switch (shape)
    {
    case VOXEL_SHAPE_DECAL:   return 0.02f; /* rugs and mats, just off the floor */
    case VOXEL_SHAPE_WATER:   return -0.10f;
    case VOXEL_SHAPE_COUNTER: return 0.70f;
    default:                  return 0.0f;
    }
}

/* ── Furniture ──────────────────────────────────────────────────────────── */

/* A box of one flat colour, inset from the tile edges. Used for the parts the
 * reference draws untextured: table sides and legs, television bodies. */
static void EmitSolidBox(VoxelBuilder *b, float wx, float wz, VoxelSolidColor color,
                         float x0, float z0, float x1, float z1, float yBottom, float yTop)
{
    float u0, v0, u1, v1;

    /* Written out rather than routed through the face helpers, which span a
     * whole tile; a leg is a tenth of one. */
    VoxelAtlas_SolidUV(color, &u0, &v0, &u1, &v1);
    VoxelBuilder_Quad(b,
        &(VoxelVertex){wx + x0, yTop, wz + z0, u0, v0, SOLID_LIT},
        &(VoxelVertex){wx + x1, yTop, wz + z0, u1, v0, SOLID_LIT},
        &(VoxelVertex){wx + x1, yTop, wz + z1, u1, v1, SOLID_LIT},
        &(VoxelVertex){wx + x0, yTop, wz + z1, u0, v1, SOLID_LIT});
    /* South */
    VoxelBuilder_Quad(b,
        &(VoxelVertex){wx + x0, yBottom, wz + z1, u0, v1, SOLID_FRONT},
        &(VoxelVertex){wx + x1, yBottom, wz + z1, u1, v1, SOLID_FRONT},
        &(VoxelVertex){wx + x1, yTop,    wz + z1, u1, v0, SOLID_FRONT},
        &(VoxelVertex){wx + x0, yTop,    wz + z1, u0, v0, SOLID_FRONT});
    /* North */
    VoxelBuilder_Quad(b,
        &(VoxelVertex){wx + x1, yBottom, wz + z0, u0, v1, SOLID_BACK},
        &(VoxelVertex){wx + x0, yBottom, wz + z0, u1, v1, SOLID_BACK},
        &(VoxelVertex){wx + x0, yTop,    wz + z0, u1, v0, SOLID_BACK},
        &(VoxelVertex){wx + x1, yTop,    wz + z0, u0, v0, SOLID_BACK});
    /* West */
    VoxelBuilder_Quad(b,
        &(VoxelVertex){wx + x0, yBottom, wz + z0, u0, v1, SOLID_SIDE},
        &(VoxelVertex){wx + x0, yBottom, wz + z1, u1, v1, SOLID_SIDE},
        &(VoxelVertex){wx + x0, yTop,    wz + z1, u1, v0, SOLID_SIDE},
        &(VoxelVertex){wx + x0, yTop,    wz + z0, u0, v0, SOLID_SIDE});
    /* East */
    VoxelBuilder_Quad(b,
        &(VoxelVertex){wx + x1, yBottom, wz + z1, u0, v1, SOLID_SIDE},
        &(VoxelVertex){wx + x1, yBottom, wz + z0, u1, v1, SOLID_SIDE},
        &(VoxelVertex){wx + x1, yTop,    wz + z0, u1, v0, SOLID_SIDE},
        &(VoxelVertex){wx + x1, yTop,    wz + z1, u0, v0, SOLID_SIDE});
}

/*
 * Furniture spanning several tiles is drawn as one piece: a table only grows
 * legs at the outer corners of the group, and the sides it shares with a
 * neighbouring table tile are left out.
 */
static void EmitFurniture(VoxelBuilder *builder, int x, int y, VoxelVisualShape shape,
                          float u0, float v0, float u1, float v1)
{
    float wx = (float)x, wz = (float)y;
    bool sameNorth = VoxelMesh_Classify(x, y - 1) == shape;
    bool sameSouth = VoxelMesh_Classify(x, y + 1) == shape;
    bool sameEast = VoxelMesh_Classify(x + 1, y) == shape;
    bool sameWest = VoxelMesh_Classify(x - 1, y) == shape;

    if (shape == VOXEL_SHAPE_TABLE)
    {
        const float top = 0.5f, thick = 0.1f, leg = 0.1f;

        VoxelMesh_Top(builder, wx, wz, top, 0.0f, u0, v0, u1, v1, SHADE_TOP);
        /* The table top's rim, only where the surface actually ends. */
        if (!sameNorth)
            VoxelMesh_North(builder, wx, wz, top - thick, top, 0.0f, u0, v0, u1, v1);
        if (!sameSouth)
            VoxelMesh_South(builder, wx, wz, top - thick, top, 0.0f, u0, v0, u1, v1);
        if (!sameEast)
            VoxelMesh_East(builder, wx, wz, top - thick, top, 0.0f, u0, v0, u1, v1);
        if (!sameWest)
            VoxelMesh_West(builder, wx, wz, top - thick, top, 0.0f, u0, v0, u1, v1);
        /* Legs at the corners of the whole group, not of every tile. */
        if (!sameNorth && !sameWest)
            EmitSolidBox(builder, wx, wz, VOXEL_SOLID_WOOD_DARK,
                         0.0f, 0.0f, leg, leg, 0.0f, top - thick);
        if (!sameNorth && !sameEast)
            EmitSolidBox(builder, wx, wz, VOXEL_SOLID_WOOD_DARK,
                         1.0f - leg, 0.0f, 1.0f, leg, 0.0f, top - thick);
        if (!sameSouth && !sameWest)
            EmitSolidBox(builder, wx, wz, VOXEL_SOLID_WOOD_DARK,
                         0.0f, 1.0f - leg, leg, 1.0f, 0.0f, top - thick);
        if (!sameSouth && !sameEast)
            EmitSolidBox(builder, wx, wz, VOXEL_SOLID_WOOD_DARK,
                         1.0f - leg, 1.0f - leg, 1.0f, 1.0f, 0.0f, top - thick);
        return;
    }

    if (shape == VOXEL_SHAPE_FURNITURE)
    {
        /* A television: a stand, a screen facing south, and a dark body. */
        const float stand = 0.3f, top = 1.0f;

        EmitSolidBox(builder, wx, wz, VOXEL_SOLID_GREY,
                     0.0f, 0.0f, 1.0f, 1.0f, 0.0f, stand);
        EmitSolidBox(builder, wx, wz, VOXEL_SOLID_BLACK,
                     0.0f, 0.3f, 1.0f, 0.8f, stand, top);
        /* The metatile's own art is the screen. */
        VoxelBuilder_Quad(builder,
            &(VoxelVertex){wx,        stand, wz + 0.79f, u0, v1, SHADE_SOUTH},
            &(VoxelVertex){wx + 1.0f, stand, wz + 0.79f, u1, v1, SHADE_SOUTH},
            &(VoxelVertex){wx + 1.0f, top,   wz + 0.79f, u1, v0, SHADE_SOUTH},
            &(VoxelVertex){wx,        top,   wz + 0.79f, u0, v0, SHADE_SOUTH});
        return;
    }

    if (shape == VOXEL_SHAPE_BED)
    {
        /*
         * The reference classifies beds and then draws nothing: its
         * DrawFurniture has branches for tables and televisions only, so beds
         * vanish into the floor. A mattress-height block with the metatile's
         * own art on top is the smallest thing that makes the room read.
         */
        const float mattress = 0.35f;

        VoxelMesh_Top(builder, wx, wz, mattress, 0.0f, u0, v0, u1, v1, SHADE_TOP);
        if (!sameNorth)
            VoxelMesh_North(builder, wx, wz, 0.0f, mattress, 0.0f, u0, v0, u1, v1);
        if (!sameSouth)
            VoxelMesh_South(builder, wx, wz, 0.0f, mattress, 0.0f, u0, v0, u1, v1);
        if (!sameEast)
            VoxelMesh_East(builder, wx, wz, 0.0f, mattress, 0.0f, u0, v0, u1, v1);
        if (!sameWest)
            VoxelMesh_West(builder, wx, wz, 0.0f, mattress, 0.0f, u0, v0, u1, v1);
        return;
    }
}

/* A sign or a potted plant: the metatile stood upright across the tile. */
static void EmitUpright(VoxelBuilder *builder, int x, int y,
                        float u0, float v0, float u1, float v1)
{
    float wx = (float)x, wz = (float)y + 0.5f;
    const float height = 1.0f;

    VoxelBuilder_Quad(builder,
        &(VoxelVertex){wx,        0.0f,   wz, u0, v1, SHADE_TOP},
        &(VoxelVertex){wx + 1.0f, 0.0f,   wz, u1, v1, SHADE_TOP},
        &(VoxelVertex){wx + 1.0f, height, wz, u1, v0, SHADE_TOP},
        &(VoxelVertex){wx,        height, wz, u0, v0, SHADE_TOP});
}

/*
 * The rim of a recessed tile (water): down from the ground round it to the
 * sunken top, wherever the neighbour is not recessed with it. Without it the
 * gap showed the clear colour, a black line along every shore. Each rim wears
 * the strip of the tile's own drawing along that edge, the drawn bank folded
 * down, so it reads as the shore the artist drew.
 */
/* What a neighbour of recessed water is drawn as: water drawn as relief (the
 * shoulder of a rock in the sea, surfed behind) stands on its lattice, not
 * recessed, so the rim is wanted against it too. */
static VoxelVisualShape RimNeighbour(int x, int y)
{
    VoxelVisualShape shape = VoxelMesh_Classify(x, y);

    if (shape == VOXEL_SHAPE_WATER
     && VoxelRelief_Cell(VoxelWorld_GetInstanceAt(x, y), x, y) != NULL)
        return VOXEL_SHAPE_FLAT;
    return shape;
}

static void EmitRim(VoxelBuilder *b, int x, int y, VoxelVisualShape shape, float h,
                    float u0, float v0, float u1, float v1)
{
    float wx = (float)x, wz = (float)y;
    float du = (u1 - u0) * -h, dv = (v1 - v0) * -h;
    VoxelVisualShape n = RimNeighbour(x, y - 1), s = RimNeighbour(x, y + 1);
    VoxelVisualShape w = RimNeighbour(x - 1, y), e = RimNeighbour(x + 1, y);

    if (n != shape && n != VOXEL_SHAPE_VOID) /* faces south, at the north edge */
        VoxelBuilder_Quad(b,
            &(VoxelVertex){wx,        h,    wz, u0, v0,      SHADE_SOUTH},
            &(VoxelVertex){wx + 1.0f, h,    wz, u1, v0,      SHADE_SOUTH},
            &(VoxelVertex){wx + 1.0f, 0.0f, wz, u1, v0 + dv, SHADE_SOUTH},
            &(VoxelVertex){wx,        0.0f, wz, u0, v0 + dv, SHADE_SOUTH});
    if (s != shape && s != VOXEL_SHAPE_VOID) /* faces north, at the south edge */
        VoxelBuilder_Quad(b,
            &(VoxelVertex){wx + 1.0f, h,    wz + 1.0f, u1, v1,      SHADE_NORTH},
            &(VoxelVertex){wx,        h,    wz + 1.0f, u0, v1,      SHADE_NORTH},
            &(VoxelVertex){wx,        0.0f, wz + 1.0f, u0, v1 - dv, SHADE_NORTH},
            &(VoxelVertex){wx + 1.0f, 0.0f, wz + 1.0f, u1, v1 - dv, SHADE_NORTH});
    if (w != shape && w != VOXEL_SHAPE_VOID) /* faces east, at the west edge */
        VoxelBuilder_Quad(b,
            &(VoxelVertex){wx, h,    wz + 1.0f, u0,      v1, SHADE_EAST},
            &(VoxelVertex){wx, h,    wz,        u0,      v0, SHADE_EAST},
            &(VoxelVertex){wx, 0.0f, wz,        u0 + du, v0, SHADE_EAST},
            &(VoxelVertex){wx, 0.0f, wz + 1.0f, u0 + du, v1, SHADE_EAST});
    if (e != shape && e != VOXEL_SHAPE_VOID) /* faces west, at the east edge */
        VoxelBuilder_Quad(b,
            &(VoxelVertex){wx + 1.0f, h,    wz,        u1,      v0, SHADE_WEST},
            &(VoxelVertex){wx + 1.0f, h,    wz + 1.0f, u1,      v1, SHADE_WEST},
            &(VoxelVertex){wx + 1.0f, 0.0f, wz + 1.0f, u1 - du, v1, SHADE_WEST},
            &(VoxelVertex){wx + 1.0f, 0.0f, wz,        u1 - du, v0, SHADE_WEST});
}

static void EmitTile(VoxelBuilder *builder, int x, int y, VoxelVisualShape shape)
{
    float wx = (float)x, wz = (float)y;
    float u0, v0, u1, v1;
    float h = ShapeHeight(shape);

    if (!VoxelMesh_TileUV(builder, x, y, &u0, &v0, &u1, &v1))
        return;

    switch (shape)
    {
    case VOXEL_SHAPE_TABLE:
    case VOXEL_SHAPE_FURNITURE:
    case VOXEL_SHAPE_BED:
        EmitFurniture(builder, x, y, shape, u0, v0, u1, v1);
        return;
    case VOXEL_SHAPE_SIGN:
        EmitUpright(builder, x, y, u0, v0, u1, v1);
        return;
    default:
        break;
    }

    VoxelMesh_Top(builder, wx, wz, h, 0.0f, u0, v0, u1, v1, SHADE_TOP);
    if (h < 0.0f)
    {
        EmitRim(builder, x, y, shape, h, u0, v0, u1, v1);
        return;
    }
    if (h == 0.0f)
        return;

    {
        VoxelVisualShape south = VoxelMesh_Classify(x, y + 1);
        VoxelVisualShape north = VoxelMesh_Classify(x, y - 1);
        VoxelVisualShape west = VoxelMesh_Classify(x - 1, y);
        VoxelVisualShape east = VoxelMesh_Classify(x + 1, y);

        /* A face is hidden by the void and by a neighbour of the same height. */
        if (south != VOXEL_SHAPE_VOID && south != shape)
            VoxelMesh_South(builder, wx, wz, 0.0f, h, 0.0f, u0, v0, u1, v1);
        if (north != VOXEL_SHAPE_VOID && north != shape)
            VoxelMesh_North(builder, wx, wz, 0.0f, h, 0.0f, u0, v0, u1, v1);
        if (west != VOXEL_SHAPE_VOID && west != shape)
            VoxelMesh_West(builder, wx, wz, 0.0f, h, 0.0f, u0, v0, u1, v1);
        if (east != VOXEL_SHAPE_VOID && east != shape)
            VoxelMesh_East(builder, wx, wz, 0.0f, h, 0.0f, u0, v0, u1, v1);
    }
}

/*
 * A lifted cell: its own metatile laid on its relief lattice, every point of
 * the drawing at (u, h, v + h). Level cells need one quad; slopes take the
 * 4-pixel lattice. Shade 1: the drawing's own light is already in it.
 */
static void EmitRelief(VoxelBuilder *b, int x, int y, const int16_t *g, const int16_t *s,
                       int artY)
{
    const int n = VOXEL_RELIEF_SIDE - 1;
    float u0, v0, u1, v1;

    if (!VoxelMesh_TileUV(b, x, artY, &u0, &v0, &u1, &v1))
        return;
    /* The drawing's own light is in it, a face's included: shadows only. */
    b->artShaded = true;
    if (!VoxelRelief_IsSlope(g) && !VoxelRelief_IsSlope(s))
    {
        float h = g[0] / 16.0f, d = s[0] / 16.0f;

        /* Through the lit quad, as all ground is: what stands in the sun
         * (a house beside the mountain) shades it. */
        VoxelBuilder_Quad(b, &(VoxelVertex){x, h, y + d, u0, v0, SHADE_TOP},
                          &(VoxelVertex){x + 1, h, y + d, u1, v0, SHADE_TOP},
                          &(VoxelVertex){x + 1, h, y + 1 + d, u1, v1, SHADE_TOP},
                          &(VoxelVertex){x, h, y + 1 + d, u0, v1, SHADE_TOP});
        b->artShaded = false;
        return;
    }
    for (int j = 0; j < n; ++j)
        for (int i = 0; i < n; ++i)
        {
            VoxelVertex p[4];
            static const int di[4] = { 0, 1, 1, 0 }, dj[4] = { 0, 0, 1, 1 };

            for (int k = 0; k < 4; ++k)
            {
                int a = i + di[k], c = j + dj[k];
                float h = g[c * VOXEL_RELIEF_SIDE + a] / 16.0f;
                float d = s[c * VOXEL_RELIEF_SIDE + a] / 16.0f;

                p[k] = (VoxelVertex){ x + a / (float)n, h, y + c / (float)n + d,
                                      u0 + (u1 - u0) * a / n, v0 + (v1 - v0) * c / n, SHADE_TOP };
            }
            VoxelBuilder_Quad(b, &p[0], &p[1], &p[2], &p[3]);
        }
    b->artShaded = false;
}

/* ── Entry point ────────────────────────────────────────────────────────── */

void VoxelMesh_BeginWindow(int x0, int y0, int x1, int y1)
{
    WindowReset(x0, y0, x1, y1);
}

/*
 * Where the world does not close up, two maps meet at different levels
 * (voxel_relief.h: a map stands at its base) and the higher one's edge is a
 * step down to the other. It is closed by a skirt of the edge cell's own
 * drawing, hanging from the edge to the ground beyond it, so the seam is a
 * low wall and never a gap onto the clear colour.
 */
static void EmitSeamSkirt(VoxelBuilder *b, const VoxelMapInstance *inst, int x, int y)
{
    static const int kDir[4][2] = { {0, -1}, {0, 1}, {-1, 0}, {1, 0} };
    float top = VoxelRelief_CellLift(inst, x, y), shift = VoxelRelief_CellShift(inst, x, y);
    float wx = (float)x, wz = (float)y + shift;
    float u0, v0, u1, v1;
    bool uv = false;

    for (int d = 0; d < 4; ++d)
    {
        int nx = x + kDir[d][0], ny = y + kDir[d][1];
        const VoxelMapInstance *other;
        float low, h, du, dv;

        if (nx >= inst->originX && ny >= inst->originY
         && nx < inst->originX + inst->width && ny < inst->originY + inst->height)
            continue;
        other = VoxelWorld_GetInstanceAt(nx, ny);
        if (other == NULL || other == inst || other->indoor)
            continue;
        low = VoxelRelief_LiftAt((float)nx + 0.5f, (float)ny + 0.5f) - VoxelRelief_Base(inst);
        h = top - low;
        if (h <= 0.05f)
            continue;
        if (!uv && !(uv = VoxelMesh_TileUV(b, x, y, &u0, &v0, &u1, &v1)))
            return;
        du = (u1 - u0) * (h < 1.0f ? h : 1.0f);
        dv = (v1 - v0) * (h < 1.0f ? h : 1.0f);
        if (d == 0) /* faces north, at the north edge */
            VoxelBuilder_Quad(b,
                &(VoxelVertex){wx + 1.0f, top, wz, u1, v0,      SHADE_NORTH},
                &(VoxelVertex){wx,        top, wz, u0, v0,      SHADE_NORTH},
                &(VoxelVertex){wx,        low, wz, u0, v0 + dv, SHADE_NORTH},
                &(VoxelVertex){wx + 1.0f, low, wz, u1, v0 + dv, SHADE_NORTH});
        else if (d == 1) /* faces south, at the south edge */
            VoxelBuilder_Quad(b,
                &(VoxelVertex){wx,        top, wz + 1.0f, u0, v1,      SHADE_SOUTH},
                &(VoxelVertex){wx + 1.0f, top, wz + 1.0f, u1, v1,      SHADE_SOUTH},
                &(VoxelVertex){wx + 1.0f, low, wz + 1.0f, u1, v1 - dv, SHADE_SOUTH},
                &(VoxelVertex){wx,        low, wz + 1.0f, u0, v1 - dv, SHADE_SOUTH});
        else if (d == 2) /* faces west, at the west edge */
            VoxelBuilder_Quad(b,
                &(VoxelVertex){wx, top, wz,        u0,      v0, SHADE_WEST},
                &(VoxelVertex){wx, top, wz + 1.0f, u0,      v1, SHADE_WEST},
                &(VoxelVertex){wx, low, wz + 1.0f, u0 + du, v1, SHADE_WEST},
                &(VoxelVertex){wx, low, wz,        u0 + du, v0, SHADE_WEST});
        else /* faces east, at the east edge */
            VoxelBuilder_Quad(b,
                &(VoxelVertex){wx + 1.0f, top, wz + 1.0f, u1,      v1, SHADE_EAST},
                &(VoxelVertex){wx + 1.0f, top, wz,        u1,      v0, SHADE_EAST},
                &(VoxelVertex){wx + 1.0f, low, wz,        u1 - du, v0, SHADE_EAST},
                &(VoxelVertex){wx + 1.0f, low, wz + 1.0f, u1 - du, v1, SHADE_EAST});
    }
}

/* One row of the ground pass over [x0,x1), already clipped to the instance. */
void VoxelMesh_EmitGroundRow(VoxelBuilder *builder, const VoxelMapInstance *inst,
                             int x0, int x1, int y)
{
    for (int x = x0; x < x1; ++x)
        if (x == inst->originX || x == inst->originX + inst->width - 1
         || y == inst->originY || y == inst->originY + inst->height - 1)
            EmitSeamSkirt(builder, inst, x, y);
    for (int x = x0; x < x1; ++x)
    {
        VoxelVisualShape shape = VoxelMesh_Classify(x, y);

        /* The tree pass supplies both the trunk's ground and its crown. */
        if (VoxelWorld_UsesTreeSprites(inst)
         && VoxelTree_Part(VoxelWorld_GetMetatileId(x, y)) >= 0)
            continue;
        if (shape == VOXEL_SHAPE_VOID)
            continue;
        /* A lifted cell lays its own drawing on its relief. A slope - rock,
         * stairs - is nothing but that; a level lifted cell still carries
         * whatever stands on it, drawn below with the same lift. */
        {
            const int16_t *relief = VoxelRelief_Cell(inst, x, y);

            /* A building's cells are its model's: a mountain's foot that
             * runs on under a roof is not drawn over it. */
            if (relief != NULL && VoxelBuildings_CellAt(inst, x, y, NULL, NULL))
                relief = NULL;
            if (relief != NULL)
            {
                /* A signpost's drawing is the sign: its relief is laid with
                 * the ground south of it, and the sign stands without its
                 * own. */
                bool sign = VoxelSign_IsCell(inst, x, y);

                EmitRelief(builder, x, y, relief, VoxelRelief_Depth(inst, x, y),
                           sign ? y + 1 : y);
                if (VoxelRelief_IsSlope(relief))
                {
                    /* A signpost at the foot of a face stands on the ground
                     * under its board, three quarters of the way down the
                     * cell (voxel_sign.c's EmitSign). */
                    const int16_t *depth = VoxelRelief_Depth(inst, x, y);
                    const int under = 3 * VOXEL_RELIEF_SIDE + 2;

                    builder->lift = relief[under] / 16.0f;
                    builder->shift = depth[under] / 16.0f;
                    VoxelSign_EmitStanding(builder, inst, x, y);
                    builder->lift = 0.0f;
                    builder->shift = 0.0f;
                    continue;
                }
                builder->lift = VoxelRelief_CellLift(inst, x, y);
                builder->shift = VoxelRelief_CellShift(inst, x, y);
                VoxelSign_EmitStanding(builder, inst, x, y);
                builder->lift = 0.0f;
                builder->shift = 0.0f;
                continue;
            }
        }
        /* A modelled building brings its own walls and roof; what is
         * left of its cells is the ground its drawing was painted over. */
        {
            int ground;
            float u0, v0, u1, v1;

            if (VoxelBuildings_CellAt(inst, x, y, &ground, NULL))
            {
                if (MetatileUV(builder, ground, &u0, &v0, &u1, &v1))
                    VoxelMesh_Top(builder, (float)x, (float)y, 0.0f, 0.0f,
                                  u0, v0, u1, v1, SHADE_TOP);
                continue;
            }
        }
        if (VoxelSign_EmitCell(builder, inst, x, y))
            continue;
        /* A lamp's lantern stands on its post; under it, the ground. */
        {
            int ground;
            float u0, v0, u1, v1;

            if (VoxelSign_HeadGround(inst, x, y, &ground))
            {
                if (MetatileUV(builder, ground, &u0, &v0, &u1, &v1))
                    VoxelMesh_Top(builder, (float)x, (float)y, 0.0f, 0.0f,
                                  u0, v0, u1, v1, SHADE_TOP);
                continue;
            }
        }
        EmitTile(builder, x, y, shape);
    }
}

void VoxelMesh_EmitInstance(VoxelBuilder *builder, const VoxelMapInstance *inst,
                            int x0, int y0, int x1, int y1)
{
    if (inst == NULL || builder->atlas == NULL)
        return;
    if (x0 < inst->originX) x0 = inst->originX;
    if (y0 < inst->originY) y0 = inst->originY;
    if (x1 > inst->originX + inst->width) x1 = inst->originX + inst->width;
    if (y1 > inst->originY + inst->height) y1 = inst->originY + inst->height;
    if (x1 <= x0 || y1 <= y0)
        return;

    /*
     * The ground, with the relief it is lifted on and the small things that
     * stand on it cell by cell (signs, furniture). The staging buffer is
     * finite, and this goes first so that it is never what gets refused:
     * losing the floor leaves a hole straight through the world to the clear
     * colour. Trees and modelled buildings follow in passes of their own.
     */
    for (int y = y0; y < y1; ++y)
        VoxelMesh_EmitGroundRow(builder, inst, x0, x1, y);
}

/*
 * The world past the last map.
 *
 * The window reaches beyond every instance on purpose, and until now those
 * tiles were VOID and drew nothing, so a town ended on a plate over the clear
 * colour and the horizon was a black wedge. The 2D game tiles the layout's 2x2
 * border block outwards for ever; this stands the same block up.
 *
 * Two courses, because that is what the block's own drawing measures: it is a
 * 2x2 pattern tiled, so a column through it reads one row, the other, the
 * first again - two distinct drawings, by the same rule every other column
 * here is heighted with. Laid flat instead, a border of trees reads as a leaf
 * carpet.
 *
 * Instance 0 only: the border belongs to the map the player is standing on,
 * and running it for every connection would stack four copies of it on the
 * same tiles.
 */
#define VOXEL_BORDER_COURSES 2

static bool BorderAt(int x, int y)
{
    const VoxelMapInstance *inst = VoxelWorld_GetInstanceAt(x, y);
    if (inst == NULL)
        return true;
    /* Outdoor classifiers never produce VOID inside an instance. Do not read
     * metatiles, attributes and neighbours just to discover there is no belt. */
    if (!inst->indoor)
        return false;
    return VoxelMesh_Classify(x, y) == VOXEL_SHAPE_VOID;
}

void VoxelMesh_EmitBorder(VoxelBuilder *builder, int x0, int y0, int x1, int y1)
{
    float u0, v0, u1, v1;

    if (builder->atlas == NULL)
        return;
    /* Indoors the border is the black filler outside the rooms: stood up it
     * was a black wall two tiles tall round the room, and the south one hid
     * the doorway. The 2D game shows black there, and so does the clear. */
    if (VoxelWorld_Instance(0) != NULL && VoxelWorld_Instance(0)->indoor)
        return;
    for (int y = y0; y < y1; ++y)
        for (int x = x0; x < x1; ++x)
        {
            float wx = (float)x, wz = (float)y;
            /* Only where the border meets something else: the inside of a
             * belt that reaches the whole way to the horizon is never seen,
             * and drawing it is most of the tiles in the window. */
            bool south, east, west;

            if (!BorderAt(x, y))
                continue;
            if (VoxelWorld_UsesTreeSprites(VoxelWorld_Instance(0))
             && VoxelTree_Part(VoxelWorld_BorderMetatile(x, y)) >= 0)
                continue; /* flat trunks and tilted crowns are appended later */
            south = !BorderAt(x, y + 1);
            east = !BorderAt(x + 1, y);
            west = !BorderAt(x - 1, y);
            if (MetatileUV(builder, VoxelWorld_BorderMetatile(x, y - VOXEL_BORDER_COURSES + 1),
                           &u0, &v0, &u1, &v1))
                VoxelMesh_Top(builder, wx, wz, (float)VOXEL_BORDER_COURSES, 0.0f,
                              u0, v0, u1, v1, SHADE_TOP);
            if (!south && !east && !west)
                continue;
            for (int h = 0; h < VOXEL_BORDER_COURSES; ++h)
            {
                float yBottom = (float)h, yTop = (float)(h + 1);

                if (!MetatileUV(builder, VoxelWorld_BorderMetatile(x, y - h),
                                &u0, &v0, &u1, &v1))
                    continue;
                if (south) VoxelMesh_South(builder, wx, wz, yBottom, yTop, 0.0f, u0, v0, u1, v1);
                if (east) VoxelMesh_East(builder, wx, wz, yBottom, yTop, 0.0f, u0, v0, u1, v1);
                if (west) VoxelMesh_West(builder, wx, wz, yBottom, yTop, 0.0f, u0, v0, u1, v1);
            }
        }
}
