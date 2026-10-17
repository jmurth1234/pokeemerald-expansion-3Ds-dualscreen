/*
 * Reader for voxel/relief.bin (game data); see voxel_relief.h and the generator,
 * scripts/gen_voxel_relief.py.
 *
 * Layout (little endian):
 *   "VXL4", u16 layouts, u16 side (5)
 *   layouts x 14: u16 layout id, u16 cells, u16 width (bit 15: level, its
 *                 heights lift it straight up with no depth), u16 height (bit 15:
 *                 drawn; bit 14: heights in units of 2 pixels; bits 10-13:
 *                 the belt's courses, see VoxelRelief_Belt), u32 offset,
 *                 s16 base (pixels: the level the whole map stands at)
 *   cells x (2 + 25): u8 x, u8 y, int8 heights[25] (row major, over the base)
 *
 * then the cut tiles (see VoxelRelief_Cut): u16 variants, u16 cells;
 *   variants x 36: u16 layout drawing the tileset, u16 metatile, u16 rows[16]
 *                  (bit set: background)
 *   cells x 14:    u16 layout, u8 x, u8 y, u16 variant, s16 foot (pixels),
 *                  u16 metatile of the ground behind it (0xFFFF: none),
 *                  u16 metatile its cliff walls are drawn with (0xFFFF: none),
 *                  u8 sides (bits 0-3: stretches of its west edge column, 4-7:
 *                  of its east one, that have rock for a wall to hang from),
 *                  u8 flags, `sides` bits 8-15 to VoxelRelief_Cut (1: all its
 *                  background is the plain ground behind it - written, no
 *                  longer read; 2: VOXEL_RELIEF_GOES_ON; 4:
 *                  VOXEL_RELIEF_BLOCK_WALLS; 8: VOXEL_RELIEF_THIN_DECK)
 *   and last u32 the table's offset, "CUTS".
 *
 * A point's depth is its height ((u, h, v + h)), so the file does not carry
 * it; on a level map (bit 15 of its width) it has none. The heights are decoded to pixels at load: a drawn mountain can stand
 * taller than a signed byte of pixels.
 */
#include <math.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

/* Host tests define PORT_LOG away rather than link the console's logger. */
#ifndef PORT_LOG
#include "port_log.h"
#endif

#include "voxel_relief.h"
#include "voxel_file.h"

#ifndef VOXEL_RELIEF_PATH
#define VOXEL_RELIEF_PATH "voxel/relief.bin"
#endif
#define GRID (VOXEL_RELIEF_SIDE * VOXEL_RELIEF_SIDE)
#define CELL_BYTES (2 + GRID)
#define ROW_BYTES 14

/*
 * Cells are searched, not indexed: every map with a ledge has a few dozen
 * lifted cells, and a dense index of each map's every cell cost more heap
 * than all of them. The generator writes them row by row, x within y.
 */
typedef struct
{
    uint16_t layoutId, width, height, count;
    int16_t base;          /* pixels */
    bool drawn;            /* the relief is all of this map's terrain */
    bool level;            /* heights with no depth: lifted straight up */
    uint8_t belt;          /* courses the border stands, as drawn (0: as ever) */
    const uint8_t *cells;  /* into sBlob: x, y and the stored heights */
    int16_t *heights;      /* GRID per cell, pixels */
    /* A drawn map's surface where it stands, for the sun to be stopped by:
     * its height (pixels) every lattice step of world X and Z, not of the
     * drawing's rows - a point of the drawing lies as far south as it is
     * high, so the table runs SURFACE_SPAN cells past the map's south edge. */
    int8_t *surface;       /* in units of 2 pixels */
    uint16_t surfaceW, surfaceH;
    float top;             /* its highest point, tiles */
} ReliefLayout;

#define SURFACE_SPAN 6

/*
 * The cells last found, by their place in an 8x8 tiling of the layout: a
 * ground cell asks for itself, its eight neighbours (the face terms), its depth,
 * lift and shift and its walls' neighbours, a dozen and more searches of the
 * same few cells, and a ray over a mountain asks again cell by cell. Exact: a
 * cell is known by its layout and place, and a miss only searches. Cleared
 * with the layouts it points at (VoxelRelief_Shutdown).
 */
#define FIND_MEMO_SIDE 8u
typedef struct
{
    const ReliefLayout *layout;
    int16_t x, y;
    int k;
} FindMemo;
static FindMemo sFindMemo[FIND_MEMO_SIDE * FIND_MEMO_SIDE];

static int Find(const ReliefLayout *l, const VoxelMapInstance *inst, int x, int y);
static bool BuildSurface(ReliefLayout *l);

static uint8_t *sBlob;
static ReliefLayout *sLayouts;
static unsigned sLayoutCount;
/* The layouts by id (their entry + 1, 0 for none): every height, lift and
 * shadow-ray step asks which layout a map is, and a scan of them all was the
 * bulk of what a step on a mountain cost. */
static uint16_t *sLayoutIndex;
static unsigned sLayoutIndexSize;
static const uint8_t *sCutVariants, *sCutCells;
static unsigned sCutVariantCount, sCutCellCount;
#define CUT_VARIANT_BYTES 36
#define CUT_CELL_BYTES 14

/* The cut cells last looked up, tiled as sFindMemo: a relief cell asks about
 * itself and its neighbours several times a build (its walls, its ground,
 * the cells beside it), each a search of every map's cut cells. Exact, and
 * cleared with the table (VoxelRelief_Shutdown). */
typedef struct
{
    bool used;
    int layoutId;
    int16_t x, y;
    int record;            /* index into sCutCells, -1: not cut */
} CutMemo;
static CutMemo sCutMemo[FIND_MEMO_SIDE * FIND_MEMO_SIDE];

/* floorf as a conversion and one correction: the library call it replaces was
 * made at every step of every shadow ray over a mountain. */
static inline int FloorI(float v)
{
    int i = (int)v;

    return (float)i > v ? i - 1 : i;
}

static unsigned U16(const uint8_t *p) { return (unsigned)p[0] | ((unsigned)p[1] << 8); }
static uint32_t U32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

bool VoxelRelief_Init(void)
{
    FILE *file;
    long size;

    VoxelRelief_Shutdown();
    file = VoxelFile_Open(VOXEL_RELIEF_PATH);
    if (file == NULL)
    {
        PORT_LOG("[VIDEO] VOXEL relief: %s absent, terrain stays level\n", VOXEL_RELIEF_PATH);
        return false;
    }
    fseek(file, 0, SEEK_END);
    size = ftell(file);
    fseek(file, 0, SEEK_SET);
    sBlob = size > 8 ? malloc((size_t)size) : NULL;
    if (sBlob == NULL || fread(sBlob, 1, (size_t)size, file) != (size_t)size
     || memcmp(sBlob, "VXL4", 4) != 0 || U16(sBlob + 6) != VOXEL_RELIEF_SIDE
     || memcmp(sBlob + size - 4, "CUTS", 4) != 0)
        goto fail;
    {
        uint32_t at = U32(sBlob + size - 8);

        if ((long)at + 4 > size - 8)
            goto fail;
        sCutVariantCount = U16(sBlob + at);
        sCutCellCount = U16(sBlob + at + 2);
        sCutVariants = sBlob + at + 4;
        sCutCells = sCutVariants + CUT_VARIANT_BYTES * sCutVariantCount;
        if ((long)(sCutCells - sBlob) + (long)(CUT_CELL_BYTES * sCutCellCount) > size - 8)
            goto fail;
    }
    sLayoutCount = U16(sBlob + 4);
    if (8 + (unsigned long)ROW_BYTES * sLayoutCount > (unsigned long)size)
        goto fail;
    sLayouts = calloc(sLayoutCount ? sLayoutCount : 1, sizeof(*sLayouts));
    if (sLayouts == NULL)
        goto fail;
    for (unsigned i = 0; i < sLayoutCount; ++i)
    {
        const uint8_t *row = sBlob + 8 + ROW_BYTES * i;
        ReliefLayout *l = &sLayouts[i];
        unsigned cells = U16(row + 2);
        uint32_t offset = U32(row + 8);

        l->layoutId = (uint16_t)U16(row);
        l->base = (int16_t)U16(row + 12);
        l->width = (uint16_t)(U16(row + 4) & 0x03FF);
        l->level = (U16(row + 4) & 0x8000) != 0;
        unsigned unit = (U16(row + 6) & 0x4000) ? 2 : 1;

        l->height = (uint16_t)(U16(row + 6) & 0x03FF);
        l->drawn = (U16(row + 6) & 0x8000) != 0;
        l->belt = (uint8_t)((U16(row + 6) >> 10) & 0x0F);
        l->cells = sBlob + offset;
        l->count = (uint16_t)cells;
        if (offset > (uint32_t)size || (uint32_t)cells * CELL_BYTES > (uint32_t)size - offset)
            goto fail;
        for (unsigned k = 1; k < cells; ++k)
        {
            const uint8_t *a = l->cells + (k - 1) * CELL_BYTES, *c = a + CELL_BYTES;
            if (a[1] > c[1] || (a[1] == c[1] && a[0] >= c[0]))
                goto fail;  /* not in row order: the search would miss cells */
        }
        l->heights = malloc((size_t)(cells ? cells : 1) * GRID * sizeof(int16_t));
        if (l->heights == NULL)
            goto fail;
        for (unsigned k = 0; k < cells; ++k)
            for (unsigned g = 0; g < GRID; ++g)
                l->heights[k * GRID + g] =
                    (int16_t)((int8_t)l->cells[k * CELL_BYTES + 2 + g] * (int)unit);
    }
    fclose(file);
    for (unsigned i = 0; i < sLayoutCount; ++i)
        if (sLayouts[i].drawn && !BuildSurface(&sLayouts[i]))
        {
    PORT_LOG("[ERROR] VOXEL relief: no memory for a drawn map's surface\n");
            VoxelRelief_Shutdown();
            return false;
        }
    {
        unsigned most = 0;

        for (unsigned i = 0; i < sLayoutCount; ++i)
            if (sLayouts[i].layoutId > most)
                most = sLayouts[i].layoutId;
        sLayoutIndex = calloc((size_t)most + 1u, sizeof(*sLayoutIndex));
        if (sLayoutIndex != NULL)
        {
            sLayoutIndexSize = most + 1u;
            /* backwards: the first of two layouts of one id answers, as the scan did */
            for (unsigned i = sLayoutCount; i-- > 0;)
                sLayoutIndex[sLayouts[i].layoutId] = (uint16_t)(i + 1u);
        }
    }
    PORT_LOG("[VIDEO] VOXEL relief: %u layouts\n", sLayoutCount);
    return true;

fail:
    fclose(file);
    PORT_LOG("[ERROR] VOXEL relief: %s is truncated or malformed\n", VOXEL_RELIEF_PATH);
    VoxelRelief_Shutdown();
    return false;
}

void VoxelRelief_Shutdown(void)
{
    for (unsigned i = 0; sLayouts != NULL && i < sLayoutCount; ++i)
    {
        free(sLayouts[i].surface);
        free(sLayouts[i].heights);
    }
    memset(sFindMemo, 0, sizeof(sFindMemo));
    memset(sCutMemo, 0, sizeof(sCutMemo));
    free(sLayouts);
    free(sLayoutIndex);
    sLayoutIndex = NULL;
    sLayoutIndexSize = 0;
    free(sBlob);
    sLayouts = NULL;
    sBlob = NULL;
    sLayoutCount = 0;
    sCutVariants = sCutCells = NULL;
    sCutVariantCount = sCutCellCount = 0;
}

unsigned VoxelRelief_CutCount(void)
{
    return sCutVariantCount;
}

bool VoxelRelief_CutVariant(unsigned i, unsigned *layout, unsigned *metatile,
                            const uint8_t **rows)
{
    const uint8_t *v;

    if (i >= sCutVariantCount)
        return false;
    v = sCutVariants + CUT_VARIANT_BYTES * i;
    *layout = U16(v);
    *metatile = U16(v + 2);
    *rows = v + 4;
    return true;
}

static const int8_t kCutDir[8][2] = { { 0, -1 }, { 0, 1 }, { -1, 0 }, { 1, 0 },
                                      { -1, -1 }, { 1, -1 }, { -1, 1 }, { 1, 1 } };

/* The rock's eight sides (its hull: a*i + b*j <= c over its pixels), and
 * whether it is that hull: a polygon. */
static bool CutHull(const uint8_t *rows, int c[8])
{
    int rock = 0;

    for (int k = 0; k < 8; ++k)
        c[k] = INT16_MIN;
    for (int j = 0; j < 16; ++j)
    {
        unsigned bits = (unsigned)rows[2 * j] | ((unsigned)rows[2 * j + 1] << 8);

        for (int i = 0; i < 16; ++i)
            if (!((bits >> i) & 1u))
            {
                ++rock;
                for (int k = 0; k < 8; ++k)
                    if (kCutDir[k][0] * i + kCutDir[k][1] * j > c[k])
                        c[k] = kCutDir[k][0] * i + kCutDir[k][1] * j;
            }
    }
    if (rock == 0 || rock == 256)
        return false;
    for (int j = 0; j < 16; ++j)
    {
        unsigned bits = (unsigned)rows[2 * j] | ((unsigned)rows[2 * j + 1] << 8);

        for (int i = 0; i < 16; ++i)
        {
            bool in = true;

            for (int k = 0; k < 8 && in; ++k)
                in = kCutDir[k][0] * i + kCutDir[k][1] * j <= c[k];
            if (in == (((bits >> i) & 1u) != 0))
                return false;
        }
    }
    return true;
}

void VoxelRelief_CutDiagonals(const uint8_t *rows, VoxelCutDiagonals *out)
{
    int c[8];

    out->count = 0;
    if (rows == NULL || !CutHull(rows, c))
        return;
    for (int k = 4; k < 8; ++k)
    {
        /* its value at the corner of the rock's rectangle (its horizontal
         * and vertical sides): a side that clips nothing more is none */
        int a = kCutDir[k][0], b = kCutDir[k][1];
        int corner = a * (a > 0 ? c[3] : -c[2]) + b * (b > 0 ? c[1] : -c[0]);

        if (c[k] >= corner)
            continue;
        out->a[out->count] = (int8_t)a;
        out->b[out->count] = (int8_t)b;
        /* through the outer corners of the rock's pixels along it */
        out->c[out->count] = (int16_t)(c[k] + (a > 0) + (b > 0));
        ++out->count;
    }
}

void VoxelRelief_CutClear(const uint8_t *rows, uint16_t clear[16])
{
    VoxelCutDiagonals d;
    int c[8];

    for (int j = 0; j < 16; ++j)
        clear[j] = (uint16_t)((unsigned)rows[2 * j] | ((unsigned)rows[2 * j + 1] << 8));
    VoxelRelief_CutDiagonals(rows, &d);
    if (d.count == 0)
        return;
    CutHull(rows, c);
    for (int j = 0; j < 16; ++j)
        for (int i = 0; i < 16; ++i)
        {
            bool in = true;

            if (!((clear[j] >> i) & 1u))
                continue;
            /* a background pixel the polygon (its sides through the outer
             * corners of the rock's pixels) takes in part of: drawn */
            for (int k = 0; k < 8 && in; ++k)
            {
                int a = kCutDir[k][0], b = kCutDir[k][1];
                int least = a * (a > 0 ? i : i + 1) + b * (b > 0 ? j : j + 1);

                in = least < c[k] + (a > 0) + (b > 0);
            }
            if (in)
                clear[j] &= (uint16_t)~(1u << i);
        }
}

int VoxelRelief_CutFringe(const uint8_t *rows, const uint16_t clear[16], int x, int y)
{
    static const int8_t kAround[8][2] = {{-1, 0}, {1, 0}, {0, -1}, {0, 1},
                                         {-1, -1}, {1, -1}, {-1, 1}, {1, 1}};
    unsigned row = (unsigned)rows[2 * y] | ((unsigned)rows[2 * y + 1] << 8);

    if (!((row >> x) & 1u) || ((clear[y] >> x) & 1u))
        return -1;
    for (int k = 0; k < 8; ++k)
    {
        int nx = x + kAround[k][0], ny = y + kAround[k][1];
        unsigned nrow;

        if (nx < 0 || nx > 15 || ny < 0 || ny > 15)
            continue;
        nrow = (unsigned)rows[2 * ny] | ((unsigned)rows[2 * ny + 1] << 8);
        if (!((nrow >> nx) & 1u))
            return ny * 16 + nx;
    }
    return -1;
}

int VoxelRelief_Cut(const VoxelMapInstance *inst, int x, int y, float *foot, int *ground,
                    int *wall, unsigned *sides)
{
    unsigned lo = 0, hi = sCutCellCount, key;
    const uint8_t *c;
    CutMemo *memo;

    if (inst == NULL || sCutCells == NULL)
        return -1;
    x -= inst->originX;
    y -= inst->originY;
    if (x < 0 || y < 0 || x > 255 || y > 255)
        return -1;
    memo = &sCutMemo[((unsigned)y & (FIND_MEMO_SIDE - 1u)) * FIND_MEMO_SIDE
                     + ((unsigned)x & (FIND_MEMO_SIDE - 1u))];
    if (!memo->used || memo->layoutId != inst->layoutId || memo->x != x || memo->y != y)
    {
        key = ((unsigned)inst->layoutId << 16) | ((unsigned)x << 8) | (unsigned)y;
        memo->used = true;
        memo->layoutId = inst->layoutId;
        memo->x = (int16_t)x;
        memo->y = (int16_t)y;
        memo->record = -1;
        while (lo < hi)
        {
            unsigned mid = (lo + hi) / 2;
            unsigned at;

            c = sCutCells + CUT_CELL_BYTES * mid;
            at = (U16(c) << 16) | ((unsigned)c[2] << 8) | c[3];
            if (at == key)
            {
                memo->record = (int)mid;
                break;
            }
            if (at < key)
                lo = mid + 1;
            else
                hi = mid;
        }
    }
    if (memo->record < 0)
        return -1;
    c = sCutCells + CUT_CELL_BYTES * (unsigned)memo->record;
    *foot = (int16_t)U16(c + 6) / 16.0f;
    *ground = U16(c + 8) == 0xFFFFu ? -1 : (int)U16(c + 8);
    *wall = U16(c + 10) == 0xFFFFu ? -1 : (int)U16(c + 10);
    *sides = (unsigned)c[12] | ((unsigned)c[13] << 8);
    return (int)U16(c + 4);
}

static const ReliefLayout *LayoutOf(const VoxelMapInstance *inst)
{
    if (inst == NULL)
        return NULL;
    if (sLayoutIndex != NULL)
    {
        unsigned k = (unsigned)inst->layoutId < sLayoutIndexSize ? sLayoutIndex[inst->layoutId] : 0u;

        return k != 0 ? &sLayouts[k - 1u] : NULL;
    }
    for (unsigned i = 0; i < sLayoutCount; ++i)
        if (sLayouts[i].layoutId == (unsigned)inst->layoutId)
            return &sLayouts[i];
    return NULL;
}

float VoxelRelief_Base(const VoxelMapInstance *inst)
{
    const ReliefLayout *l = LayoutOf(inst);
    return l != NULL ? l->base / 16.0f : 0.0f;
}

const int16_t *VoxelRelief_Cell(const VoxelMapInstance *inst, int x, int y)
{
    const ReliefLayout *l = LayoutOf(inst);
    int k = Find(l, inst, x, y);
    return k >= 0 ? l->heights + (unsigned)k * GRID : NULL;
}

const int16_t *VoxelRelief_Depth(const VoxelMapInstance *inst, int x, int y)
{
    static const int16_t kNone[GRID];
    const ReliefLayout *l = LayoutOf(inst);
    int k = Find(l, inst, x, y);

    if (k < 0)
        return NULL;
    return l->level ? kNone : l->heights + (unsigned)k * GRID;
}

static int Find(const ReliefLayout *l, const VoxelMapInstance *inst, int x, int y)
{
    unsigned lo = 0, hi, key;
    FindMemo *memo;

    if (l == NULL)
        return -1;
    x -= inst->originX;
    y -= inst->originY;
    if (x < 0 || y < 0 || x >= l->width || y >= l->height)
        return -1;
    memo = &sFindMemo[((unsigned)y & (FIND_MEMO_SIDE - 1u)) * FIND_MEMO_SIDE
                      + ((unsigned)x & (FIND_MEMO_SIDE - 1u))];
    if (memo->layout == l && memo->x == x && memo->y == y)
        return memo->k;
    key = ((unsigned)y << 8) | (unsigned)x;
    hi = l->count;
    memo->layout = l;
    memo->x = (int16_t)x;
    memo->y = (int16_t)y;
    memo->k = -1;
    while (lo < hi)
    {
        unsigned mid = (lo + hi) / 2;
        const uint8_t *c = l->cells + mid * CELL_BYTES;
        unsigned at = ((unsigned)c[1] << 8) | c[0];

        if (at == key)
        {
            memo->k = (int)mid;
            return (int)mid;
        }
        if (at < key)
            lo = mid + 1;
        else
            hi = mid;
    }
    return -1;
}

bool VoxelRelief_IsDrawn(const VoxelMapInstance *inst)
{
    const ReliefLayout *l = LayoutOf(inst);
    return l != NULL && l->drawn;
}

unsigned VoxelRelief_Belt(const VoxelMapInstance *inst)
{
    const ReliefLayout *l = LayoutOf(inst);
    return l != NULL ? l->belt : 0u;
}

bool VoxelRelief_IsSlope(const int16_t *grid)
{
    for (unsigned i = 1; grid != NULL && i < VOXEL_RELIEF_SIDE * VOXEL_RELIEF_SIDE; ++i)
        if (grid[i] != grid[0])
            return true;
    return false;
}

float VoxelRelief_CellLift(const VoxelMapInstance *inst, int x, int y)
{
    const int16_t *g = VoxelRelief_Cell(inst, x, y);
    return g ? g[2 * VOXEL_RELIEF_SIDE + 2] / 16.0f : 0.0f;
}

float VoxelRelief_CellShift(const VoxelMapInstance *inst, int x, int y)
{
    const int16_t *g = VoxelRelief_Depth(inst, x, y);
    return g ? g[2 * VOXEL_RELIEF_SIDE + 2] / 16.0f : 0.0f;
}

static float Sample(const int16_t *g, float worldX, float worldZ);

float VoxelRelief_LiftAt(float worldX, float worldZ)
{
    int x = FloorI(worldX), y = FloorI(worldZ);
    const VoxelMapInstance *inst = VoxelWorld_GetInstanceAt(x, y);

    return VoxelRelief_Base(inst) + Sample(VoxelRelief_Cell(inst, x, y), worldX, worldZ);
}

float VoxelRelief_ShiftAt(float worldX, float worldZ)
{
    int x = FloorI(worldX), y = FloorI(worldZ);
    return Sample(VoxelRelief_Depth(VoxelWorld_GetInstanceAt(x, y), x, y), worldX, worldZ);
}

static float Sample(const int16_t *g, float worldX, float worldZ)
{
    int x = FloorI(worldX), y = FloorI(worldZ);
    float fx, fy, a, b;
    int i, j;

    if (g == NULL)
        return 0.0f;
    fx = (worldX - x) * (VOXEL_RELIEF_SIDE - 1);
    fy = (worldZ - y) * (VOXEL_RELIEF_SIDE - 1);
    i = (int)fx;
    j = (int)fy;
    if (i > VOXEL_RELIEF_SIDE - 2) i = VOXEL_RELIEF_SIDE - 2;
    if (j > VOXEL_RELIEF_SIDE - 2) j = VOXEL_RELIEF_SIDE - 2;
    fx -= i;
    fy -= j;
    a = g[j * VOXEL_RELIEF_SIDE + i] * (1 - fx) + g[j * VOXEL_RELIEF_SIDE + i + 1] * fx;
    b = g[(j + 1) * VOXEL_RELIEF_SIDE + i] * (1 - fx) + g[(j + 1) * VOXEL_RELIEF_SIDE + i + 1] * fx;
    return (a * (1 - fy) + b * fy) / 16.0f;
}

/* A lattice point of a layout's drawing: its height and its depth, pixels. */
static void Point(const ReliefLayout *l, unsigned i, unsigned j, int *h, int *d)
{
    const unsigned n = VOXEL_RELIEF_SIDE - 1;
    unsigned x = i / n, y = j / n, a, b;
    int k;
    VoxelMapInstance inst;

    if (x >= l->width) x = l->width - 1u;
    if (y >= l->height) y = l->height - 1u;
    a = i - x * n;
    b = j - y * n;
    memset(&inst, 0, sizeof(inst));
    inst.layoutId = l->layoutId;
    k = Find(l, &inst, (int)x, (int)y);
    *h = k >= 0 ? l->heights[(unsigned)k * GRID + b * VOXEL_RELIEF_SIDE + a] : 0;
    *d = l->level ? 0 : *h;
}

/* Each column of the drawing, walked down its rows, runs south through the
 * world as v + d: the height at every world lattice row it passes is read off
 * between the two points either side of it (the drawing never folds over). */
static bool BuildSurface(ReliefLayout *l)
{
    const unsigned n = VOXEL_RELIEF_SIDE - 1, step = 16 / n;
    unsigned w = l->width * n + 1u, rows = (l->height + SURFACE_SPAN) * n + 1u;
    int top = 0;

    l->surface = calloc((size_t)w * rows, 1);
    if (l->surface == NULL)
        return false;
    l->surfaceW = (uint16_t)w;
    l->surfaceH = (uint16_t)rows;
    for (unsigned i = 0; i < w; ++i)
    {
        int h0, d0;
        unsigned k = 0;

        Point(l, i, 0, &h0, &d0);
        for (unsigned j = 1; j <= l->height * n; ++j)
        {
            int h1, d1;
            float z0 = (float)((j - 1) * step) + d0, z1;

            Point(l, i, j, &h1, &d1);
            z1 = (float)(j * step) + d1;
            for (; k < rows && (float)(k * step) <= z1; ++k)
            {
                float t = z1 > z0 ? ((float)(k * step) - z0) / (z1 - z0) : 1.0f;
                int v;

                if (t < 0.0f) t = 0.0f;
                v = (int)(h0 + (h1 - h0) * t + 0.5f);
                l->surface[k * w + i] = (int8_t)(v >= 0 ? (v + 1) / 2 : -((1 - v) / 2));
                if (v > top) top = v;
            }
            h0 = h1;
            d0 = d1;
        }
    }
    l->top = top / 16.0f;
    return true;
}

static float SurfacePoint(const ReliefLayout *l, int i, int k)
{
    if (i < 0 || k < 0 || i >= l->surfaceW || k >= l->surfaceH)
        return 0.0f;
    return 2.0f * l->surface[k * l->surfaceW + i];
}

float VoxelRelief_SurfaceAt(float worldX, float worldZ)
{
    const int n = VOXEL_RELIEF_SIDE - 1;
    int x = FloorI(worldX), z = FloorI(worldZ);
    const VoxelMapInstance *inst = VoxelWorld_GetInstanceAt(x, z);
    const ReliefLayout *l = LayoutOf(inst);
    float fx, fz;
    int i, k;

    if (l == NULL || l->surface == NULL)
        return 0.0f;
    fx = (worldX - inst->originX) * n;
    fz = (worldZ - inst->originY) * n;
    i = FloorI(fx);
    k = FloorI(fz);
    fx -= i;
    fz -= k;
    return ((SurfacePoint(l, i, k) * (1 - fx) + SurfacePoint(l, i + 1, k) * fx) * (1 - fz)
          + (SurfacePoint(l, i, k + 1) * (1 - fx) + SurfacePoint(l, i + 1, k + 1) * fx) * fz)
           / 16.0f;
}

float VoxelRelief_SurfaceTop(const VoxelMapInstance *inst, int x, int z)
{
    const ReliefLayout *l = LayoutOf(inst);
    const int n = VOXEL_RELIEF_SIDE - 1;
    float top = 0.0f;

    if (l == NULL || l->surface == NULL)
        return 0.0f;
    x -= inst->originX;
    z -= inst->originY;
    for (int k = z * n; k <= z * n + n; ++k)
        for (int i = x * n; i <= x * n + n; ++i)
            if (SurfacePoint(l, i, k) > top)
                top = SurfacePoint(l, i, k);
    return top / 16.0f;
}

float VoxelRelief_DrawnTop(const VoxelMapInstance *inst)
{
    const ReliefLayout *l = LayoutOf(inst);
    return l != NULL && l->surface != NULL ? l->top : 0.0f;
}
