#include <math.h>
#include <stddef.h>
#include <string.h>

#include "voxel_lighting.h"
#include "voxel_tree.h"
#include "voxel_building.h"
#include "voxel_relief.h"
#include "voxel_sign.h"

#if CTR_VOXEL_LIGHTING
/*
 * Casters and finished samples, kept for as long as the world they were read
 * from: VoxelLighting_Reset() is called whenever a live tile changes or the
 * maps on screen move (ctr_voxel.c's world epoch), and never per build. Reset
 * per chunk, as they used to be, every build re-classified the ~300 cells its
 * rays cross, most of which its neighbour had just classified.
 *
 * Direct mapped and exact: a bucket holds one coordinate and a collision only
 * costs a recomputation, never a wrong answer. Ordinary RAM, not linear.
 */
#define CELL_CACHE_SIZE 4096u
typedef struct
{
    int16_t x, z;
    float top;        /* over the base */
    float base;       /* the base of the map the cell is on (VoxelRelief_Base) */
    uint32_t generation;
    int8_t crownPart; /* VoxelTree_Part when the cell is a crown, else -1 */
    bool surface;     /* a drawn map's relief: tested as a height field */
    bool sign;        /* a sign or a lamp: tested against its drawing */
    const uint16_t *mask; /* a model over part of the cell: its footprint */
} LightCell;
static LightCell sCells[CELL_CACHE_SIZE];

/* Adjacent ground quads share their half-tile samples. Cache the final ray/AO
 * result as well as casters; exact coordinates keep walls and sloped crowns
 * distinct even when their hash buckets collide. */
#define SAMPLE_CACHE_SIZE 4096u
typedef struct
{
    float x, y, z, light;
    uint32_t generation;
} LightSample;
static LightSample sSamples[SAMPLE_CACHE_SIZE];
static uint32_t sGeneration;

/*
 * No caster anywhere on screen is taller than this, so a ray that has climbed
 * past it can stop: Occludes() needs a caster whose top is above the ray. It
 * is what most rays reach long before VOXEL_LIGHT_REACH - a street of two-
 * storey houses tops out at a quarter of the reach - and it changes no answer.
 */
static float sCeiling;
#define CROWN_TOP 1.62f
/* The least the ceiling is ever set to: the tallest sign or lamp drawing. */
#define PROP_TOP 2.0f

static int Tile(float n)
{
    int i = (int)n;
    return n < (float)i ? i - 1 : i;
}

static float CasterCeiling(void)
{
    float ceiling = PROP_TOP > CROWN_TOP ? PROP_TOP : CROWN_TOP;
    float buildings = VoxelBuildings_MaxTop(), highest = 0.0f;

    if (buildings > ceiling)
        ceiling = buildings;
    /* over the base of the highest map on screen */
    for (unsigned i = 0; i < VoxelWorld_InstanceCount(); ++i)
    {
        const VoxelMapInstance *inst = VoxelWorld_Instance(i);

        if (inst == NULL || inst->indoor)
            continue;
        if (VoxelRelief_DrawnTop(inst) > ceiling)
            ceiling = VoxelRelief_DrawnTop(inst);
        if (VoxelRelief_Base(inst) > highest)
            highest = VoxelRelief_Base(inst);
    }
    return ceiling + highest;
}

void VoxelLighting_Reset(void)
{
    if (++sGeneration == 0)
    {
        memset(sCells, 0, sizeof(sCells));
        memset(sSamples, 0, sizeof(sSamples));
        sGeneration = 1;
    }
    sCeiling = CasterCeiling();
}

static const LightCell *Cell(int x, int z)
{
    unsigned key = ((uint32_t)x * 73856093u ^ (uint32_t)z * 19349663u)
                 & (CELL_CACHE_SIZE - 1);
    LightCell *cell = &sCells[key];
    const VoxelMapInstance *inst;
    int metatile;

    if (cell->generation == sGeneration && cell->x == x && cell->z == z)
        return cell;
    cell->generation = sGeneration;
    cell->x = (int16_t)x;
    cell->z = (int16_t)z;
    cell->top = 0.0f;
    cell->crownPart = -1;
    cell->surface = false;
    cell->sign = false;
    cell->mask = NULL;
    inst = VoxelWorld_GetInstanceAt(x, z);
    cell->base = VoxelRelief_Base(inst);
    if (inst == NULL || inst->indoor)
        return cell;
    metatile = VoxelWorld_GetMetatileId(x, z);
    if (VoxelWorld_UsesTreeSprites(inst))
    {
        int part = VoxelTree_Part(metatile);
        if (part >= 0)
        {
            /* A rounded crown proxy, rather than the opaque rectangular card.
             * Match the replacement tree's two-tile footprint and 1.62 height. */
            cell->crownPart = (int8_t)part;
            cell->top = CROWN_TOP;
            return cell;
        }
        if (VoxelTree_GroundMetatile(metatile) != metatile)
            return cell; /* canopy fringe removed by the tree renderer */
    }
    /* A modelled building casts from its own solid, cell by cell - and a
     * railing from its line, not from its whole cell. */
    if (VoxelBuildings_CellAt(inst, x, z, NULL, &cell->top))
    {
        cell->mask = VoxelBuildings_Footprint(inst, x, z);
        return cell;
    }
    /* A sign or a lamp casts its own drawing, not a box. */
    cell->top = VoxelSign_CasterTop(inst, x, z);
    if (cell->top > 0.0f)
    {
        cell->sign = true;
        return cell;
    }
    /* A map read off its drawing casts from its relief as it stands - the
     * mountain on the grass, a terrace on the one below - tested as a height
     * field, so a slope facing the sun is not shadowed by its own cell. */
    if (VoxelRelief_IsDrawn(inst))
    {
        cell->top = VoxelRelief_SurfaceTop(inst, x, z);
        cell->surface = cell->top > 0.05f;
        return cell;
    }
    /* Anything else is ground: relief elsewhere (a rock band, a ledge's lip)
     * is a sheet, not a box, and nothing is raised from a guess, so nothing
     * casts from one. The only casters are what was modelled, above. */
    return cell;
}

/* A ray this far under a drawn map's surface is under it, not grazing it. */
#define SURFACE_BIAS 0.06f

static bool CellOccludes(const LightCell *cell, float x, float y, float z)
{
    /* Everything a cell casts from stands on its map's base. */
    y -= cell->base;
    /* Level ground stops no sun, even for a point below it: a beach under
     * its cliff (Route 104) is lit where nothing stands over it. */
    if (cell->top <= y || (cell->top <= 0.0f && !cell->surface))
        return false;
    if (cell->surface)
        return y < VoxelRelief_SurfaceAt(x, z) - SURFACE_BIAS;
    if (cell->mask != NULL)
    {
        int px = (int)((x - (float)cell->x) * 16.0f), pz = (int)((z - (float)cell->z) * 16.0f);

        px = px < 0 ? 0 : px > 15 ? 15 : px;
        pz = pz < 0 ? 0 : pz > 15 ? 15 : pz;
        return (cell->mask[pz] >> px) & 1u;
    }
    if (cell->sign)
        return VoxelSign_Occludes(VoxelWorld_GetInstanceAt(cell->x, cell->z), cell->x, cell->z,
                                  x, y, z);
    if (cell->crownPart == VOXEL_TREE_SMALL)
    {
        /* Half the large proxy across, kept on its own cell: in a wood the
         * cell to the north is the next small tree and carries its own. */
        float dx = (x - ((float)cell->x + 0.5f)) / 0.48f;
        float dz = (z - ((float)cell->z + 0.35f)) / 0.55f;
        float dy = (y - 0.80f) / 0.62f;
        return dx * dx + dz * dz + dy * dy < 1.0f;
    }
    if (cell->crownPart >= 0)
    {
        float crownX = (float)(cell->x - (cell->crownPart & 1)) + 1.0f;
        float crownZ = (float)(cell->z - (cell->crownPart >> 1)) + 0.65f;
        float dx = (x - crownX) / 0.95f;
        float dz = (z - crownZ) / 0.65f;
        float dy = (y - 0.95f) / 0.67f;
        return dx * dx + dz * dz + dy * dy < 1.0f;
    }
    return true;
}

static bool Occludes(float x, float y, float z)
{
    return CellOccludes(Cell(Tile(x), Tile(z)), x, y, z);
}

/*
 * Where step `step` of a ray puts it. One expression, used by the march and by
 * the cell skip alike, so both see exactly the same points.
 */
static void RayPoint(float x, float y, float z, int step, float *rx, float *ry, float *rz)
{
    float rise = (float)step * 0.25f;

    *rx = x - VOXEL_SUN_DX * rise;
    *ry = y + rise + 0.08f;
    *rz = z - VOXEL_SUN_DZ * rise;
}

#ifdef VOXEL_LIGHTING_TESTS
bool gVoxelLightingStepEveryPoint; /* the reference march, for the tests */
#endif

float VoxelLighting_Sample(float x, float y, float z)
{
    unsigned key = ((uint32_t)Tile(x * 2.0f) * 73856093u
                  ^ (uint32_t)Tile(z * 2.0f) * 19349663u
                  ^ (uint32_t)Tile(y * 16.0f) * 83492791u) & (SAMPLE_CACHE_SIZE - 1);
    LightSample *sample = &sSamples[key];
    float light = 1.0f;

    if (sGeneration == 0)
        VoxelLighting_Reset();
    if (sample->generation == sGeneration
     && sample->x == x && sample->y == y && sample->z == z)
        return sample->light;
    /*
     * Bounded ray through the height proxies, a quarter tile of rise per step.
     * This runs during meshing, never per fragment. Bias avoids self-shadowing
     * of a flat roof.
     *
     * The ray only climbs, so a box cell that does not stop it at the first
     * point it has inside cannot stop it at any later one: the march jumps to
     * the first point in the next cell. That is five to seven cells for a ray
     * that used to take twenty-six points, with the same answer - only crowns,
     * which are round, are still tested point by point.
     */
    for (int step = 1; step <= VOXEL_LIGHT_REACH * 4;)
    {
        float rx, ry, rz;
        int tx, tz;
        const LightCell *cell;

        RayPoint(x, y, z, step, &rx, &ry, &rz);
        if (ry >= sCeiling)
            break;
        tx = Tile(rx);
        tz = Tile(rz);
        cell = Cell(tx, tz);
        if (CellOccludes(cell, rx, ry, rz))
        {
            light = 0.70f;
            break;
        }
        ++step;
#ifdef VOXEL_LIGHTING_TESTS
        if (gVoxelLightingStepEveryPoint)
            continue;
#endif
        if ((cell->crownPart >= 0 || cell->surface || cell->sign || cell->mask != NULL)
         && cell->top + cell->base > ry)
            continue;  /* not a box: point by point while it is above the ray */
        /* Leave the cell. rx and rz only fall, so it is left when either
         * drops below the cell's corner. The estimate starts a step short
         * of the crossing and the exact points settle it. */
        {
            float ex = (x - (float)tx) / (VOXEL_SUN_DX * 0.25f);
            float ez = (z - (float)tz) / (VOXEL_SUN_DZ * 0.25f);
            int guess = (int)(ex < ez ? ex : ez) - 1;

            if (guess > step)
                step = guess;
            for (; step <= VOXEL_LIGHT_REACH * 4; ++step)
            {
                RayPoint(x, y, z, step, &rx, &ry, &rz);
                if (Tile(rx) != tx || Tile(rz) != tz || ry >= sCeiling)
                    break;
            }
        }
    }
    /* Small contact AO at the bases of solid neighbours; leave roof/crown art
     * alone. Ambient light keeps the original pixel art legible in shadow. */
    if (y - Cell(Tile(x), Tile(z))->base <= 0.41f)
    {
        unsigned covered = 0;
        covered += Occludes(x - 0.28f, y + 0.3f, z);
        covered += Occludes(x + 0.28f, y + 0.3f, z);
        covered += Occludes(x, y + 0.3f, z - 0.28f);
        covered += Occludes(x, y + 0.3f, z + 0.28f);
        light *= 1.0f - 0.035f * (float)covered;
    }
    *sample = (LightSample){x, y, z, light, sGeneration};
    return light;
}

uint32_t VoxelLighting_Hash(int x0, int z0, int x1, int z1)
{
    /* Rays now reach northwest. Also include the chunk's own north margin
     * and neighbours on the opposite boundary for AO and face visibility. */
    int northReach = VOXEL_LIGHT_REACH > VOXEL_CHUNK_MARGIN_NORTH
                   ? VOXEL_LIGHT_REACH : VOXEL_CHUNK_MARGIN_NORTH;
    int hx0 = x0 - VOXEL_LIGHT_REACH - 1, hz0 = z0 - northReach - 1;
    int hx1 = x1 + 1, hz1 = z1 + 2;
    uint32_t hash = VoxelWorld_BlockHash(hx0, hz0, hx1, hz1);

    /* Equal tile IDs in a different tileset/layout do not mean equal casters.
     * Relative origins preserve the hash when the entire world is rebased. */
    for (unsigned i = 0; i < VoxelWorld_InstanceCount(); ++i)
    {
        const VoxelMapInstance *inst = VoxelWorld_Instance(i);
        uint32_t tag;
        if (inst->originX >= hx1 || inst->originY >= hz1
         || inst->originX + inst->width <= hx0 || inst->originY + inst->height <= hz0)
            continue;
        tag = (uint32_t)inst->layoutId * 16777619u;
        tag ^= (uint32_t)(inst->originX - x0) * 73856093u;
        tag ^= (uint32_t)(inst->originY - z0) * 19349663u;
        tag ^= (uint32_t)(uintptr_t)inst->primaryTileset;
        tag ^= (uint32_t)(uintptr_t)inst->secondaryTileset;
        tag ^= inst->indoor ? 0x80000000u : 0;
        hash ^= tag; /* instance ordering changes on a map crossing */
    }
    return hash;
}

static VoxelVertex Mix(VoxelVertex a, VoxelVertex b, float t)
{
    VoxelVertex v;
    v.x = a.x + (b.x - a.x) * t;
    v.y = a.y + (b.y - a.y) * t;
    v.z = a.z + (b.z - a.z) * t;
    v.u = a.u + (b.u - a.u) * t;
    v.v = a.v + (b.v - a.v) * t;
    v.shade = a.shade + (b.shade - a.shade) * t;
    return v;
}

static void RawQuad(VoxelBuilder *builder, const VoxelVertex *a,
                    const VoxelVertex *b, const VoxelVertex *c, const VoxelVertex *d)
{
    VoxelBuilder_Tri(builder, a, b, c);
    VoxelBuilder_Tri(builder, a, c, d);
}

void VoxelLighting_Quad(VoxelBuilder *builder, const VoxelVertex *a,
                         const VoxelVertex *b, const VoxelVertex *c,
                         const VoxelVertex *d)
{
    VoxelVertex corners[4] = {*a, *b, *c, *d};
    /* Existing face colours carry the art's directional shading. Add a modest
     * fixed-sun term to the south/east faces without brightening dark art. */
    float ux = b->x - a->x, uy = b->y - a->y, uz = b->z - a->z;
    float vx = c->x - a->x, vy = c->y - a->y, vz = c->z - a->z;
    float nx = uy * vz - uz * vy, nz = ux * vy - uy * vx;
    float face = (nx > 0.01f || nz > 0.01f) && a->y != c->y && !builder->artShaded
               ? 0.90f : 1.0f;

    /* An object lit as a whole (see VoxelSign_Emit): its one sample, and the
     * face term, for every corner. */
    if (builder->lightingConstant >= 0.0f)
    {
        for (unsigned i = 0; i < 4; ++i)
            corners[i].shade *= face * builder->lightingConstant;
        RawQuad(builder, &corners[0], &corners[1], &corners[2], &corners[3]);
        return;
    }

    /* Only ground-sized horizontal faces need extra samples. Four sub-quads
     * resolve diagonal shadow edges without multiplying all wall geometry. */
    if (builder->lightingRefine
     && a->y == b->y && a->y == c->y && a->y == d->y && (a->y <= 0.41f || builder->artShaded)
     && fabsf(a->x - c->x) >= 0.99f && fabsf(a->z - c->z) >= 0.99f
     && fabsf(a->x - c->x) <= 1.01f && fabsf(a->z - c->z) <= 1.01f)
    {
        VoxelVertex grid[9];
        float lo = 1.0f, hi = 0.0f;
        for (int row = 0; row < 3; ++row)
            for (int col = 0; col < 3; ++col)
            {
                float t = col * 0.5f, s = row * 0.5f;
                VoxelVertex v = Mix(Mix(*a, *b, t), Mix(*d, *c, t), s);
                float light = VoxelLighting_Sample(v.x, v.y, v.z);
                if (light < lo) lo = light;
                if (light > hi) hi = light;
                v.shade *= light;
                grid[row * 3 + col] = v;
            }
        if (hi - lo > 0.02f && builder->count + 24 <= builder->capacity)
        {
            for (int row = 0; row < 2; ++row)
                for (int col = 0; col < 2; ++col)
                {
                    int i = row * 3 + col;
                    RawQuad(builder, &grid[i], &grid[i + 1], &grid[i + 4], &grid[i + 3]);
                }
        }
        else
            RawQuad(builder, &grid[0], &grid[2], &grid[8], &grid[6]);
        return;
    }
    for (unsigned i = 0; i < 4; ++i)
        corners[i].shade *= face * VoxelLighting_Sample(corners[i].x, corners[i].y, corners[i].z);
    RawQuad(builder, &corners[0], &corners[1], &corners[2], &corners[3]);
}

/* Contact shadows are clipped to each receiving ground tile: no floating blob
 * across a wall, void, water edge or step. Like the current billboard renderer,
 * this uses visual mesh heights, not the GBA collision-elevation byte. */
static bool Receiver(int x, int z, float *height)
{
    const VoxelMapInstance *inst = VoxelWorld_GetInstanceAt(x, z);
    int metatile;
    if (inst == NULL || inst->indoor)
        return false;
    metatile = VoxelWorld_GetMetatileId(x, z);
    if (VoxelWorld_UsesTreeSprites(inst)
     && (VoxelTree_Part(metatile) >= 0 || VoxelTree_GroundMetatile(metatile) != metatile))
    {
        *height = 0.0f;
        return true;
    }
    {
        float top;

        /* Ground the model leaves uncovered receives like any other. */
        if (VoxelBuildings_CellAt(inst, x, z, NULL, &top))
        {
            *height = 0.0f;
            return top <= 0.0f;
        }
    }
    /* Nor is there a flat top on a slope for a shadow to lie on. */
    if (VoxelRelief_IsSlope(VoxelRelief_Cell(inst, x, z)))
        return false;
    switch (VoxelWorld_ClassifyTile(x, z))
    {
    case VOXEL_SHAPE_FLAT: *height = 0.0f; return true;
    case VOXEL_SHAPE_DECAL: *height = 0.02f; return true;
    case VOXEL_SHAPE_WATER: *height = -0.10f; return true;
    default: return false;
    }
}

/* Sutherland-Hodgman, clipping a convex polygon against one tile edge. An
 * octagon clipped by four planes has at most twelve vertices. */
static unsigned Clip(const VoxelVertex *in, unsigned count, VoxelVertex *out,
                      bool axisZ, float edge, bool greater)
{
    unsigned written = 0;
    if (count == 0) return 0;
    VoxelVertex prev = in[count - 1];
    float pd = (axisZ ? prev.z : prev.x) - edge;
    if (!greater) pd = -pd;
    for (unsigned i = 0; i < count; ++i)
    {
        VoxelVertex cur = in[i];
        float cd = (axisZ ? cur.z : cur.x) - edge;
        if (!greater) cd = -cd;
        if ((pd < 0 && cd > 0) || (pd > 0 && cd < 0))
            out[written++] = Mix(prev, cur, pd / (pd - cd));
        if (cd >= 0) out[written++] = cur;
        prev = cur;
        pd = cd;
    }
    return written;
}

void VoxelLighting_Contact(VoxelBuilder *builder, float x, float z)
{
    static const float ring[8][2] = {
        {-1,0}, {-0.707107f,-0.707107f}, {0,-1}, {0.707107f,-0.707107f},
        {1,0}, {0.707107f,0.707107f}, {0,1}, {-0.707107f,0.707107f}
    };
    VoxelVertex polygon[8], a[16], b[16];
    /* Slight southeast displacement, away from the northwest sun. */
    float cx = x + 0.10f, cz = z + 0.06f;
    for (unsigned i = 0; i < 8; ++i)
        polygon[i] = (VoxelVertex){cx + ring[i][0] * 0.36f, 0,
                                   cz + ring[i][1] * 0.23f, 0, 0, 0};
    for (int tz = Tile(cz - 0.23f); tz <= Tile(cz + 0.23f); ++tz)
        for (int tx = Tile(cx - 0.36f); tx <= Tile(cx + 0.36f); ++tx)
        {
            float height;
            unsigned n;
            if (!Receiver(tx, tz, &height)) continue;
            n = Clip(polygon, 8, a, false, (float)tx, true);
            n = Clip(a, n, b, false, (float)(tx + 1), false);
            n = Clip(b, n, a, true, (float)tz, true);
            n = Clip(a, n, b, true, (float)(tz + 1), false);
            for (unsigned i = 0; i < n; ++i) b[i].y = height + 0.012f;
            for (unsigned i = 1; i + 1 < n; ++i)
                VoxelBuilder_Tri(builder, &b[0], &b[i], &b[i + 1]);
        }
}
#endif
