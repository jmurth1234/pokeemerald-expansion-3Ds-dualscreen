/*
 * Player and NPC billboards. See voxel_entities.h and NOTICE.md.
 */

/* Before global.h, which redefines abs() as a macro over stdlib's prototype. */
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "global.h"
#include "fieldmap.h"
#include "sprite.h"
#include "event_object_movement.h"
#include "field_player_avatar.h"
#include "gba/io_reg.h"
#include "port_platform.h"

#include "3ds_video.h"
#include "voxel_entities.h"
#include "voxel_relief.h"
#include "voxel_world.h"
#include "voxel_lighting.h"

/* 16 pixels of sprite is one world tile, as in the reference renderer. */
#define VOXEL_PIXELS_PER_TILE 16.0f

/* Largest sprite this atlas can hold, 8bpp: 64*64 bytes of source tiles. */
#define VOXEL_SPRITE_MAX_BYTES (VOXEL_SPRITE_SLOT_DIM * VOXEL_SPRITE_SLOT_DIM)

/* Step durations, matching sStepTimes[] in event_object_movement.c. */
static const int sStepFrames[] = { 16, 8, 6, 4, 2 };

/*
 * What a slot was last decoded from. The animation of a walking object event
 * rewrites the tiles *behind* a fixed tileNum, so the descriptor alone cannot
 * detect a frame change: the source bytes and the palette are kept and
 * compared. That is ~2 KiB of memcmp per object per frame, which is far
 * cheaper than re-decoding one.
 */
typedef struct
{
    bool valid;
    u16 tileNum, paletteNum;
    u8 shape, size;
    bool flipX, flipY, color256;
    int width, height;
    u32 sourceBytes;
    u8 source[VOXEL_SPRITE_MAX_BYTES];
    u16 palette[256];
    u32 paletteBytes;
    /* Extent last written into the atlas cell, so a shrinking sprite clears
     * exactly what it used to cover instead of the whole 64x64 slot. */
    int drawnWidth, drawnHeight;
    /* Empty rows under the feet of the standing pose: the art's feet sit this
     * far up the cell, and the card leaves them out to put them on the
     * ground (VisibleRows). Measured once per picture table (StandingFootPad). */
    int footPad;
    const struct SpriteFrameImage *footPadImages;
} VoxelSpriteSlot;

static VoxelSpriteSlot sSlots[VOXEL_SPRITE_SLOTS];
static int sPlayerVertexFirst = -1;

int VoxelEntities_PlayerVertexFirst(void) { return sPlayerVertexFirst; }

void VoxelEntities_Reset(void)
{
    for (unsigned i = 0; i < VOXEL_SPRITE_SLOTS; ++i)
        sSlots[i].valid = false;
}

/* ── OAM geometry ───────────────────────────────────────────────────────── */

static void GetSpriteDimensions(u8 shape, u8 size, int *w, int *h)
{
    static const u8 square[4][2] = {{8,8},{16,16},{32,32},{64,64}};
    static const u8 wide[4][2]   = {{16,8},{32,8},{32,16},{64,32}};
    static const u8 tall[4][2]   = {{8,16},{8,32},{16,32},{32,64}};
    const u8 (*table)[2];

    if (shape == ST_OAM_SQUARE) table = square;
    else if (shape == ST_OAM_H_RECTANGLE) table = wide;
    else if (shape == ST_OAM_V_RECTANGLE) table = tall;
    else { *w = 16; *h = 32; return; }
    *w = table[size & 3][0];
    *h = table[size & 3][1];
}

/* ── Movement interpolation ─────────────────────────────────────────────── */

/*
 * Sub-tile progress in [0,1]: 0 at previousCoords, 1 at currentCoords.
 *
 * sprite->x/y are screen-space and already carry the camera offset, so they
 * cannot be used for a world position. The step timer can.
 */
static float GetMovementProgress(const struct ObjectEvent *obj, const struct Sprite *sprite)
{
    int speed, timer, stepLen;
    float t;

    /* Idle: ShiftStillObjectEventCoords has made previous == current. */
    if (!obj->singleMovementActive && !obj->heldMovementActive)
        return 1.0f;

    speed = (int)(u16)sprite->data[4];
    timer = (int)(u16)sprite->data[5];
    if (speed < 0 || speed >= (int)ARRAY_COUNT(sStepFrames))
        return 1.0f;
    stepLen = sStepFrames[speed];
    if (stepLen <= 0)
        return 1.0f;

    t = (float)timer / (float)stepLen;
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    return t;
}

static bool GetObjectWorldPos(const struct ObjectEvent *obj, const struct Sprite *sprite,
                              float *outX, float *outZ)
{
    float prevX = (float)(obj->previousCoords.x - MAP_OFFSET);
    float prevZ = (float)(obj->previousCoords.y - MAP_OFFSET);
    float curX = (float)(obj->currentCoords.x - MAP_OFFSET);
    float curZ = (float)(obj->currentCoords.y - MAP_OFFSET);
    float t = GetMovementProgress(obj, sprite);

    *outX = prevX + (curX - prevX) * t;
    *outZ = prevZ + (curZ - prevZ) * t;
    return true;
}

void VoxelEntities_GetPlayerWorldPos(float *worldX, float *worldZ)
{
    const struct ObjectEvent *obj = &gObjectEvents[gPlayerAvatar.objectEventId];
    float x = 0.0f, z = 0.0f;

    if (!obj->active || obj->spriteId >= MAX_SPRITES)
    {
        /* That helper already resolves to world space on its own. */
        VoxelWorld_GetPlayerWorldCoords(worldX, worldZ);
        return;
    }
    GetObjectWorldPos(obj, &gSprites[obj->spriteId], &x, &z);

    if (VoxelWorld_InstanceCount() > 0)
    {
        const VoxelMapInstance *inst = VoxelWorld_Instance(0);

        x += (float)inst->originX;
        z += (float)inst->originY;
    }
    /* The camera follows the player where the relief puts it. */
    z += VoxelRelief_ShiftAt(x + 0.5f, z + 0.5f);
    if (worldX != NULL) *worldX = x;
    if (worldZ != NULL) *worldZ = z;
}

/* ── Slot decoding ──────────────────────────────────────────────────────── */

/*
 * Gathers the source tiles of a sprite into `dest`, in reading order, using
 * the same address arithmetic the 2D compositor uses for OBJ: 1D or 2D
 * character mapping per DISPCNT bit 6, two VRAM units per tile at 8bpp.
 */
static u32 GatherSource(const struct Sprite *sprite, int w, int h, bool color256, u8 *dest)
{
    const u8 *vram = VRAM_ + 0x10000;
    bool mapping1d = (REG_DISPCNT & DISPCNT_OBJ_1D_MAP) != 0;
    unsigned bytesPerTile = color256 ? 64u : 32u;
    u32 written = 0;

    for (int ty = 0; ty < h / 8; ++ty)
    {
        for (int tx = 0; tx < w / 8; ++tx)
        {
            unsigned tile = CtrVideo_ObjTile(sprite->oam.tileNum, (unsigned)tx, (unsigned)ty,
                                             (unsigned)w, color256, mapping1d);
            unsigned offset = tile * 32;

            /* OBJ character data is the last 32 KiB of the 96 KiB VRAM bank. */
            if (offset + bytesPerTile > 0x8000)
                offset = 0;
            memcpy(dest + written, vram + offset, bytesPerTile);
            written += bytesPerTile;
        }
    }
    return written;
}

/* Writes the gathered tiles into the slot's 64x64 cell of the atlas. */
static void DecodeSlot(VoxelSpriteSlot *slot, unsigned index, uint16_t *atlas)
{
    unsigned baseX = (index % VOXEL_SPRITE_COLUMNS) * VOXEL_SPRITE_SLOT_DIM;
    unsigned baseY = (index / VOXEL_SPRITE_COLUMNS) * VOXEL_SPRITE_SLOT_DIM;
    unsigned bytesPerTile = slot->color256 ? 64u : 32u;
    int tilesX = slot->width / 8;
    /* Clearing only what was drawn before, union the new extent, keeps a
     * walking sprite's per-frame cost at its own size rather than 64x64. */
    int clearW = slot->drawnWidth > slot->width ? slot->drawnWidth : slot->width;
    int clearH = slot->drawnHeight > slot->height ? slot->drawnHeight : slot->height;

    for (int y = 0; y < clearH; ++y)
        for (int x = 0; x < clearW; ++x)
            atlas[CtrVideo_Texel(baseX + (unsigned)x, baseY + (unsigned)y,
                                 VOXEL_SPRITE_ATLAS_DIM)] = 0;
    slot->drawnWidth = slot->width;
    slot->drawnHeight = slot->height;

    for (int ty = 0; ty < slot->height / 8; ++ty)
    {
        for (int tx = 0; tx < tilesX; ++tx)
        {
            const u8 *tile = slot->source + (unsigned)(ty * tilesX + tx) * bytesPerTile;

            for (unsigned py = 0; py < 8; ++py)
            {
                for (unsigned px = 0; px < 8; ++px)
                {
                    unsigned index8 = py * 8 + px;
                    unsigned colorIdx = slot->color256
                        ? tile[index8]
                        : ((tile[index8 / 2] >> ((px & 1) * 4)) & 0xF);
                    int outX = tx * 8 + (int)px;
                    int outY = ty * 8 + (int)py;

                    /* Colour index 0 is transparent; the alpha bit carries it. */
                    if (colorIdx == 0)
                        continue;
                    if (slot->flipX) outX = slot->width - 1 - outX;
                    if (slot->flipY) outY = slot->height - 1 - outY;
                    atlas[CtrVideo_Texel(baseX + (unsigned)outX, baseY + (unsigned)outY,
                                         VOXEL_SPRITE_ATLAS_DIM)] =
                        CtrVideo_RGBA5551(slot->palette[colorIdx]);
                }
            }
        }
    }
}

/*
 * Empty rows under the feet of an object's standing pose: frame 0 of its
 * picture table, facing south. Not of the frame showing: the art moves the
 * whole body between frames, a walk's steps a row lower than the standing
 * pose and a run's standing pose a row higher than its strides, and that
 * row is the bob of the walk. Standing on the ground, the pose seen most
 * meets its shadow; every other frame keeps its offset from it, as on the
 * GBA (VisibleRows).
 */
static int StandingFootPad(const struct ObjectEventGraphicsInfo *info)
{
    const struct SpriteFrameImage *frame = &info->images[0];
    int tilesX = info->width / 8, tilesY = info->height / 8;
    u32 size = Port_GetSpriteFrameSize(frame->data, frame->size);
    const u8 *tiles = Port_ResolveSpriteFramePointer(frame->data, size, frame->offset);

    if (tiles == NULL || tilesX <= 0 || tilesY <= 0
     || size < (u32)(tilesX * tilesY) * TILE_SIZE_4BPP)
        return 0;
    /* Object event pictures are 4bpp, tiles in rows (1D mapping). Bottom up,
     * the first row with an opaque pixel is the feet. */
    for (int y = info->height - 1; y >= 0; --y)
    {
        for (int tx = 0; tx < tilesX; ++tx)
        {
            const u8 *row = tiles + (u32)((y / 8) * tilesX + tx) * TILE_SIZE_4BPP + (y % 8) * 4;

            if (row[0] | row[1] | row[2] | row[3])
                return info->height - 1 - y;
        }
    }
    return 0;
}

/*
 * Brings a slot up to date with its sprite. Returns true if it was re-decoded,
 * which only happens when something the picture depends on actually changed.
 */
static bool RefreshSlot(unsigned index, const struct Sprite *sprite, uint16_t *atlas)
{
    VoxelSpriteSlot *slot = &sSlots[index];
    /* Static, not automatic: this runs in the VBlank handler and 4.5 KiB of
     * stack frame there is not worth the convenience. */
    static u8 source[VOXEL_SPRITE_MAX_BYTES];
    static u16 palette[256];
    int w, h;
    bool color256 = sprite->oam.bpp != 0;
    bool flipX = sprite->oam.affineMode == ST_OAM_AFFINE_OFF
              && (sprite->oam.matrixNum & 0x08) != 0;
    bool flipY = sprite->oam.affineMode == ST_OAM_AFFINE_OFF
              && (sprite->oam.matrixNum & 0x10) != 0;
    u32 sourceBytes, paletteBytes;
    bool changed;

    GetSpriteDimensions(sprite->oam.shape, sprite->oam.size, &w, &h);
    if (w > (int)VOXEL_SPRITE_SLOT_DIM || h > (int)VOXEL_SPRITE_SLOT_DIM)
        return false;

    sourceBytes = GatherSource(sprite, w, h, color256, source);
    paletteBytes = color256 ? 256 * 2 : 16 * 2;
    /* OBJ palettes are the second half of PLTT; 8bpp uses it as one bank. */
    memcpy(palette, PLTT + 0x200 + (color256 ? 0 : sprite->oam.paletteNum * 32), paletteBytes);

    changed = !slot->valid
           || slot->width != w || slot->height != h
           || slot->color256 != color256
           || slot->flipX != flipX || slot->flipY != flipY
           || slot->tileNum != sprite->oam.tileNum
           || slot->paletteNum != sprite->oam.paletteNum
           || slot->sourceBytes != sourceBytes
           || memcmp(slot->source, source, sourceBytes) != 0
           || memcmp(slot->palette, palette, paletteBytes) != 0;
    if (!changed)
        return false;

    slot->valid = true;
    slot->width = w;
    slot->height = h;
    slot->color256 = color256;
    slot->flipX = flipX;
    slot->flipY = flipY;
    slot->tileNum = sprite->oam.tileNum;
    slot->paletteNum = sprite->oam.paletteNum;
    slot->shape = sprite->oam.shape;
    slot->size = sprite->oam.size;
    slot->sourceBytes = sourceBytes;
    slot->paletteBytes = paletteBytes;
    memcpy(slot->source, source, sourceBytes);
    memcpy(slot->palette, palette, paletteBytes);
    DecodeSlot(slot, index, atlas);
    return true;
}

/* ── Billboards ─────────────────────────────────────────────────────────── */

/*
 * Rows of the sprite the card shows: all but the standing pose's empty rows
 * under the feet, so the card ends at the feet and they are on the ground.
 * Cut off rather than sunk under the ground: a step's feet, a row lower, would
 * be hidden by the terrain there, and the player's x-ray pass (ctr_voxel.c)
 * draws exactly what terrain hides.
 */
static int VisibleRows(const VoxelSpriteSlot *slot)
{
    int pad = slot->footPad < slot->height ? slot->footPad : slot->height - 1;

    return slot->height - pad;
}

static void EmitBillboard(VoxelBuilder *builder, const VoxelSpriteSlot *slot, unsigned index,
                          float worldX, float worldZ, float rightX, float rightZ, float stretch,
                          float shade)
{
    unsigned baseX = (index % VOXEL_SPRITE_COLUMNS) * VOXEL_SPRITE_SLOT_DIM;
    unsigned baseY = (index / VOXEL_SPRITE_COLUMNS) * VOXEL_SPRITE_SLOT_DIM;
    float u0 = baseX / (float)VOXEL_SPRITE_ATLAS_DIM;
    float u1 = (baseX + slot->width) / (float)VOXEL_SPRITE_ATLAS_DIM;
    int rows = VisibleRows(slot);
    /* Row 0 in memory is v=1, the convention the whole port uses. */
    float v0 = 1.0f - baseY / (float)VOXEL_SPRITE_ATLAS_DIM;
    float v1 = 1.0f - (baseY + rows) / (float)VOXEL_SPRITE_ATLAS_DIM;
    float halfW = slot->width / VOXEL_PIXELS_PER_TILE * 0.5f;
    float height = rows / VOXEL_PIXELS_PER_TILE * stretch;
    /* Standing on the centre of its tile, feet on the ground. */
    float cx = worldX + 0.5f, cz = worldZ + 0.5f;
    /* On relief the sprite stands where its cell was lifted to, and rides
     * the lattice between cells, so a flight of stairs is climbed. */
    float lift = VoxelRelief_LiftAt(cx, cz), shift = VoxelRelief_ShiftAt(cx, cz);
    float ax = cx - rightX * halfW, az = cz - rightZ * halfW + shift;
    float bx = cx + rightX * halfW, bz = cz + rightZ * halfW + shift;

    VoxelBuilder_Quad(builder,
        &(VoxelVertex){ax, lift,          az, u0, v1, shade},
        &(VoxelVertex){bx, lift,          bz, u1, v1, shade},
        &(VoxelVertex){bx, lift + height, bz, u1, v0, shade},
        &(VoxelVertex){ax, lift + height, az, u0, v0, shade});
}

#if CTR_VOXEL_LIGHTING
/*
 * The billboard's own picture laid on the ground along the sun: the feet stay
 * where they are and every row of the sprite slides away from the sun by its
 * height, which is exactly where the fixed sun would put the shadow of a flat
 * card standing there. Same texture and texels as the sprite, so it walks,
 * turns and animates with it; one quad per object and no clipping work.
 *
 * The strength goes out in `shade`, which the shadow pass reads as alpha
 * (ctr_voxel.c). An object already in the scenery's shadow has no sun to
 * cast with, so its shadow fades out with the light it receives.
 */
#define VOXEL_CAST_SHADOW_ALPHA 0.36f
#define VOXEL_CAST_SHADOW_LIFT 0.03f    /* over decals (0.02) */
#define VOXEL_CAST_SHADOW_LIT 0.70f     /* sample at or below: no sun */

static void EmitCastShadow(VoxelBuilder *shadows, const VoxelSpriteSlot *slot, unsigned index,
                           float worldX, float worldZ, float rightX, float rightZ, float stretch,
                           float light)
{
    unsigned baseX = (index % VOXEL_SPRITE_COLUMNS) * VOXEL_SPRITE_SLOT_DIM;
    unsigned baseY = (index / VOXEL_SPRITE_COLUMNS) * VOXEL_SPRITE_SLOT_DIM;
    float u0 = baseX / (float)VOXEL_SPRITE_ATLAS_DIM;
    float u1 = (baseX + slot->width) / (float)VOXEL_SPRITE_ATLAS_DIM;
    /* The rows the card shows (VisibleRows), from the feet: they meet their
     * own shadow. */
    int rows = VisibleRows(slot);
    float v0 = 1.0f - baseY / (float)VOXEL_SPRITE_ATLAS_DIM;
    float v1 = 1.0f - (baseY + rows) / (float)VOXEL_SPRITE_ATLAS_DIM;
    float halfW = slot->width / VOXEL_PIXELS_PER_TILE * 0.5f;
    float height = rows / VOXEL_PIXELS_PER_TILE * stretch;
    float cx = worldX + 0.5f, cz = worldZ + 0.5f;
    float ax = cx - rightX * halfW, az = cz - rightZ * halfW;
    float bx = cx + rightX * halfW, bz = cz + rightZ * halfW;
    float sx = VOXEL_SUN_DX * height, sz = VOXEL_SUN_DZ * height;
    /* Each corner on the ground under it: at a face's foot the ground under
     * the shadow's far end is not the ground under the feet. */
    float ya = VoxelRelief_LiftAt(ax, az) + VOXEL_CAST_SHADOW_LIFT;
    float yb = VoxelRelief_LiftAt(bx, bz) + VOXEL_CAST_SHADOW_LIFT;
    float yc = VoxelRelief_LiftAt(bx + sx, bz + sz) + VOXEL_CAST_SHADOW_LIFT;
    float yd = VoxelRelief_LiftAt(ax + sx, az + sz) + VOXEL_CAST_SHADOW_LIFT;
    float za = VoxelRelief_ShiftAt(ax, az), zb = VoxelRelief_ShiftAt(bx, bz);
    float zc = VoxelRelief_ShiftAt(bx + sx, bz + sz), zd = VoxelRelief_ShiftAt(ax + sx, az + sz);
    float strength = (light - VOXEL_CAST_SHADOW_LIT) / (1.0f - VOXEL_CAST_SHADOW_LIT);

    if (strength <= 0.0f)
        return;
    if (strength > 1.0f)
        strength = 1.0f;
    strength *= VOXEL_CAST_SHADOW_ALPHA;
    VoxelBuilder_Quad(shadows,
        &(VoxelVertex){ax,      ya, az + za,      u0, v1, strength},
        &(VoxelVertex){bx,      yb, bz + zb,      u1, v1, strength},
        &(VoxelVertex){bx + sx, yc, bz + sz + zc, u1, v0, strength},
        &(VoxelVertex){ax + sx, yd, az + sz + zd, u0, v0, strength});
}
#endif

/* Require every map cell under the actual, relief-shifted quad to be
 * reflective. Checking only the first south cell let a long reflection spill
 * across shorelines into grass. The small AABB is conservative when yawed. */
static bool IsReflectiveFootprint(float ax, float az, float bx, float bz, float length)
{
    float minX = fminf(ax, bx), maxX = fmaxf(ax, bx);
    float minZ = fminf(az, bz), maxZ = fmaxf(az, bz) + length;
    int firstX = (int)floorf(minX + 0.0001f);
    int lastX = (int)floorf(maxX - 0.0001f);
    int firstZ = (int)floorf(minZ + 0.0001f);
    int lastZ = (int)floorf(maxZ - 0.0001f);

    for (int z = firstZ; z <= lastZ; ++z)
    {
        for (int x = firstX; x <= lastX; ++x)
        {
            if (!VoxelWorld_IsVisibleReflectiveSurface(x, z))
                return false;
        }
    }
    return true;
}

/* One quad per reflected object. Grow only through cells that the world marks
 * as water or ice; the original ground effect merely says water is nearby. */
static void EmitReflection(VoxelBuilder *reflections, const VoxelSpriteSlot *slot,
                           unsigned index, float worldX, float worldZ,
                           float rightX, float rightZ, float stretch)
{
    unsigned baseX = (index % VOXEL_SPRITE_COLUMNS) * VOXEL_SPRITE_SLOT_DIM;
    unsigned baseY = (index / VOXEL_SPRITE_COLUMNS) * VOXEL_SPRITE_SLOT_DIM;
    float u0 = baseX / (float)VOXEL_SPRITE_ATLAS_DIM;
    float u1 = (baseX + slot->width) / (float)VOXEL_SPRITE_ATLAS_DIM;
    float v0 = 1.0f - baseY / (float)VOXEL_SPRITE_ATLAS_DIM;
    float v1 = 1.0f - (baseY + slot->height) / (float)VOXEL_SPRITE_ATLAS_DIM;
    float halfW = slot->width / VOXEL_PIXELS_PER_TILE * 0.5f;
    float cardLength = slot->height / VOXEL_PIXELS_PER_TILE * stretch;
    float fullLength = cardLength;
    float length = 0.0f;
    float cx = worldX + 0.5f, nearZ = worldZ + 1.0f;
    float shift = VoxelRelief_ShiftAt(cx, nearZ + 0.5f);
    float ax = cx - rightX * halfW, az = nearZ - rightZ * halfW + shift;
    float bx = cx + rightX * halfW, bz = nearZ + rightZ * halfW + shift;
    float y = VoxelRelief_LiftAt(cx, nearZ + 0.5f) + 0.035f;

    if (fullLength > 1.75f) fullLength = 1.75f;
    for (float next = 0.25f; next <= fullLength + 0.0001f; next += 0.25f)
    {
        if (!IsReflectiveFootprint(ax, az, bx, bz, next))
            break;
        length = next;
    }
    if (length < 0.25f)
        return;
    float vFar = v1 + (v0 - v1) * (length / cardLength);
    VoxelBuilder_Quad(reflections,
        &(VoxelVertex){ax, y, az, u0, v1, 1.0f},
        &(VoxelVertex){bx, y, bz, u1, v1, 1.0f},
        &(VoxelVertex){bx, y, bz + length, u1, vFar, 1.0f},
        &(VoxelVertex){ax, y, az + length, u0, vFar, 1.0f});
}

unsigned VoxelEntities_Emit(VoxelBuilder *builder, uint16_t *atlas, const VoxelCamera *camera,
                            VoxelBuilder *shadows, VoxelBuilder *reflections)
{
    /* The billboards turn about the vertical axis to face the camera. With the
     * default yaw of 0 this is (1,0,0), the same plane the reference uses.
     * The card stands upright and the camera looks down on it, so on screen
     * its height comes out cos(pitch) short. That is made up half on each
     * side: the width comes down by sqrt(cos(pitch)) and the height goes up
     * by as much, so on screen both are sqrt(cos(pitch)) of the sprite's
     * (0.875 at 40 degrees). The proportions are the sprite's, and so is the
     * area on screen of the plain card. All of it off the width left the
     * characters small, all of it on the height big. Still upright, so it
     * sorts against walls as before. Shadow and reflection are the card's
     * own, so they take the same size. */
    float yawRad = camera->yaw * (3.14159265358979323846f / 180.0f);
    float narrow = sqrtf(cosf(camera->pitch * (3.14159265358979323846f / 180.0f)));
    float stretch = 1.0f / narrow;
    float rightX = cosf(yawRad) * narrow, rightZ = -sinf(yawRad) * narrow;
    unsigned updates = 0;
    sPlayerVertexFirst = -1;

    /* The lighting caches are the chunks' own, valid until the world
     * changes (ctr_voxel.c resets them then), so they are not reset here. */
#if !CTR_VOXEL_LIGHTING
    (void)shadows;
#endif
    for (unsigned i = 0; i < VOXEL_SPRITE_SLOTS && i < OBJECT_EVENTS_COUNT; ++i)
    {
        const struct ObjectEvent *obj = &gObjectEvents[i];
        const struct Sprite *sprite;
        float worldX = 0.0f, worldZ = 0.0f;
        float shade = 1.0f;

        if (!obj->active)
        {
            sSlots[i].valid = false;
            continue;
        }
        if (obj->spriteId >= MAX_SPRITES)
            continue;
        sprite = &gSprites[obj->spriteId];
        /*
         * Not sprite->invisible: the game also sets that for an object that
         * has left the 2D field of view (offScreen), and the 3D camera sees
         * twice as far north. Only the object's own flag hides it here.
         */
        if (obj->invisible)
            continue;

        GetObjectWorldPos(obj, sprite, &worldX, &worldZ);
        /* Object coordinates are map-local; the mesh is in world space. */
        if (VoxelWorld_InstanceCount() > 0)
        {
            const VoxelMapInstance *inst = VoxelWorld_Instance(0);

            worldX += (float)inst->originX;
            worldZ += (float)inst->originY;
        }
        /* Active events may be dozens of tiles beyond the camera, including
         * across map connections. They cannot appear in this view, and packing
         * them relative to the camera's tile overflows the 16-bit format. */
        if (fabsf(worldX - camera->targetX) > 24.0f
         || fabsf(worldZ - camera->targetZ) > 24.0f)
            continue;
        if (RefreshSlot(i, sprite, atlas))
            ++updates;
        if (!sSlots[i].valid)
            continue;
        {
            const struct ObjectEventGraphicsInfo *info = GetObjectEventGraphicsInfo(obj->graphicsId);

            if (sSlots[i].footPadImages != info->images)
            {
                sSlots[i].footPadImages = info->images;
                sSlots[i].footPad = info->images != NULL ? StandingFootPad(info) : 0;
            }
        }
#if CTR_VOXEL_LIGHTING
        {
            const VoxelMapInstance *inst = VoxelWorld_GetInstanceAt((int)floorf(worldX + 0.5f),
                                                                   (int)floorf(worldZ + 0.5f));
            if (inst != NULL && !inst->indoor)
            {
                shade = VoxelLighting_Sample(worldX + 0.5f, 0.75f, worldZ + 0.5f);
                if (shadows != NULL)
                    EmitCastShadow(shadows, &sSlots[i], i, worldX, worldZ, rightX, rightZ,
                                   stretch, shade);
            }
        }
#endif
        if (reflections != NULL && obj->hasReflection)
            EmitReflection(reflections, &sSlots[i], i, worldX, worldZ, rightX, rightZ, stretch);
        unsigned first = builder->count;
        EmitBillboard(builder, &sSlots[i], i, worldX, worldZ, rightX, rightZ, stretch, shade);
        if (i == gPlayerAvatar.objectEventId && builder->count == first + 6)
            sPlayerVertexFirst = (int)first;
    }
    return updates;
}
