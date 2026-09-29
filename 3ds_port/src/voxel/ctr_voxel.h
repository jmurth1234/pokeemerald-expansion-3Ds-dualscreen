#ifndef CTR_VOXEL_H
#define CTR_VOXEL_H

#include <stdbool.h>
#include <citro3d.h>

bool CtrVoxel_Init(void);
void CtrVoxel_Shutdown(void);

/* Cheap and side-effect free: decides which renderer composes this frame. */
bool CtrVoxel_IsAvailable(void);

/* Called after C3D_FrameBegin(), with the previous frame's GPU work finished:
 * everything it writes goes to linear memory that C3D_FrameEnd() then flushes. */
bool CtrVoxel_Update(void);
/* Game VRAM animation transfer: tile numbers are relative to BG_VRAM. */
void CtrVoxel_NotifyTilesetAnimWrite(unsigned firstTile, unsigned tileCount);

/* Draws into the given target. Never opens or closes a frame. */
void CtrVoxel_Draw(C3D_RenderTarget *target, float eyeOffset);
/* The GBA brightness effect (BLDY) of the frame about to be drawn, as the
 * compositor reads it: on the backgrounds and on the sprites, towards white
 * or black. The palette fade is read by the voxel module itself. */
void CtrVoxel_SetBrightness(float backgrounds, float sprites, bool white);

typedef struct
{
    unsigned instances;
    unsigned chunks;
    unsigned visibleChunks;
    unsigned vertices;
    unsigned atlasRebuilds;
    unsigned meshRebuilds;
    unsigned spriteUpdates;
    unsigned animationUploads, animatedMetatiles;
    unsigned reflections;
    unsigned errors;
    /* Vertices the last chunk build could not fit. */
    unsigned dropped;
    /*
     * Chunks the view needed this frame and did not get, because the per-frame
     * build budget ran out before reaching them. A steady non-zero number is
     * the cache thrashing rather than filling, which on screen is a black
     * square-edged hole - so it is worth a number of its own.
     */
    unsigned chunksMissing;
    /* Milliseconds the last frame spent visiting the view and building
     * chunks and atlases, and the worst seen. The steady frame is fine; what
     * the overlay could not show was the spike. */
    float meshMs, meshPeakMs;
    /* The whole update, billboards and page streaming included. */
    float updateMs, updatePeakMs;
    /* Where the rest of the update went: the maps on screen and the lighting
     * reset that follows a change to them (region layouts come off RomFS
     * there), the atlas job, and the billboards. */
    float worldMs, atlasMs, spritesMs;
    /* Chunks built this frame, and still waiting for a later one. */
    unsigned frameBuilds, pendingBuilds;
    /* Bytes still free in the two pools this competes for. VRAM is here
     * because it is almost entirely unused, which is the whole argument for
     * moving the mesh into it. */
    unsigned long linearFree, vramFree;
} CtrVoxelStats;

const CtrVoxelStats *CtrVoxel_GetStats(void);

/*
 * Why the last frame did or did not take the voxel path, as a short word for
 * the debug overlay: off, nomap, noatlas, nomesh, on.
 */
const char *CtrVoxel_Status(void);

/*
 * Gives back the VRAM of atlases the overworld is not using right now - every
 * tileset pair but the current map's. For the 2D compositor, on a frame the
 * overworld does not draw. Returns the bytes released.
 */
unsigned long CtrVoxel_ReleaseIdleVram(void);

/* A map just entered by a cut is still being built (see VOXEL_WARMUP_MS). */
bool CtrVoxel_IsWarmingUp(void);

#endif
