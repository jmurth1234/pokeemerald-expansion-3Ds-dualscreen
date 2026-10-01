/*
 * The port's own settings, kept on the SD card next to the save
 * (sdmc:/3ds/emerald3ds/settings.txt) and written as soon as one changes.
 *
 * They are not part of the game's save: the GBA save block has no room for
 * them, and a setting that only sticks once the game is saved is not what a
 * player expects from a console option. One "key=value" per line; unknown
 * keys are ignored, so older and newer builds can share the file.
 */
#include <stdio.h>
#include <string.h>
#include "3ds_platform.h"

#define SETTINGS_FILE "settings.txt"

/* The voxel overworld is opt-in: the classic 2D field is the default. */
static bool sVoxel = false;

/*
 * The voxel camera: a few degrees either way of its 40 degree pitch, and a
 * few zoom steps (percent of the adapted distance, higher is closer). Only
 * these values are offered: further out, or flatter, the view would reach
 * past the squares the renderer keeps built around the player.
 */
static const int sPitches[] = {34, 37, 40, 43, 46};
static const int sZooms[] = {90, 100, 110, 120};
#define COUNT(a) ((int)(sizeof(a) / sizeof((a)[0])))
static int sPitch = 2, sZoom = 1;
/* The HD-2D tilt-shift blur over the voxel picture (3ds_video.c): on unless
 * turned off. */
static bool sVoxelBlur = true;

static int Find(const int *values, int count, int value, int fallback)
{
    for (int i = 0; i < count; ++i)
        if (values[i] == value)
            return i;
    return fallback;
}

void CtrSettings_Load(void)
{
    FILE *file = CtrFs_OpenData(SETTINGS_FILE, "r");
    char line[64];

    if (file == NULL)
    {
        CtrLog_Write(CTR_LOG_FS, "settings: none yet, defaults (voxel=0)");
        return;
    }
    while (fgets(line, sizeof(line), file))
    {
        int value;

        if (strncmp(line, "voxel=", 6) == 0)
            sVoxel = line[6] == '1';
        else if (sscanf(line, "voxel_pitch=%d", &value) == 1)
            sPitch = Find(sPitches, COUNT(sPitches), value, sPitch);
        else if (sscanf(line, "voxel_zoom=%d", &value) == 1)
            sZoom = Find(sZooms, COUNT(sZooms), value, sZoom);
        else if (strncmp(line, "voxel_blur=", 11) == 0)
            sVoxelBlur = line[11] != '0';
    }
    fclose(file);
    CtrLog_Write(CTR_LOG_FS, "settings: voxel=%d pitch=%d zoom=%d blur=%d", sVoxel ? 1 : 0,
                 sPitches[sPitch], sZooms[sZoom], sVoxelBlur ? 1 : 0);
}

static void Save(void)
{
    FILE *file = CtrFs_OpenData(SETTINGS_FILE, "w");

    if (file == NULL)
        return;
    fprintf(file, "voxel=%d\nvoxel_pitch=%d\nvoxel_zoom=%d\nvoxel_blur=%d\n", sVoxel ? 1 : 0,
            sPitches[sPitch], sZooms[sZoom], sVoxelBlur ? 1 : 0);
    fclose(file);
}

bool CtrSettings_Voxel(void)
{
    return sVoxel;
}

void CtrSettings_SetVoxel(bool on)
{
    if (sVoxel == on)
        return;
    sVoxel = on;
    Save();
    CtrLog_Write(CTR_LOG_FS, "settings: voxel=%d", on ? 1 : 0);
}

int CtrSettings_VoxelPitch(void)
{
    return sPitches[sPitch];
}

int CtrSettings_VoxelZoom(void)
{
    return sZooms[sZoom];
}

/* One step up or down, wrapping round like the game's own options. */
static void Step(int *index, int count, int direction)
{
    *index = (*index + (direction < 0 ? count - 1 : 1)) % count;
    Save();
}

void CtrSettings_StepVoxelPitch(int direction)
{
    Step(&sPitch, COUNT(sPitches), direction);
}

void CtrSettings_StepVoxelZoom(int direction)
{
    Step(&sZoom, COUNT(sZooms), direction);
}

bool CtrSettings_VoxelBlur(void)
{
    return sVoxelBlur;
}

void CtrSettings_SetVoxelBlur(bool on)
{
    if (sVoxelBlur == on)
        return;
    sVoxelBlur = on;
    Save();
}
