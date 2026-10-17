"""The 3D battle's sprite slots on the host: SlotsPack and its skyline,
taken from src/3ds_video.c. Every packed slot inside its region and apart
from every other; the cases the battle over the voxel world met fit."""
import argparse
import re
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


def block(source, first, last):
    start = source.index(first)
    return source[start:source.index(last, start) + len(last)] + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cc', default='gcc')
    args = parser.parse_args()
    video = (ROOT / 'src/3ds_video.c').read_text(encoding='utf-8')
    zoom = re.search(r'#define SCENE_ZOOM (\S+)', video).group(1)
    source = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define SCENE_ZOOM ''' + zoom + r'''
typedef struct { unsigned short width, height; } C3D_Tex;
typedef struct C3D_RenderTarget C3D_RenderTarget;
''' + block(video, 'typedef struct\n{\n    C3D_RenderTarget *target;', '} SlotRegion;') + r'''
static SlotRegion sRegions[2];
static unsigned sRegionCount;
''' + block(video, 'typedef struct\n{\n    uint32_t members[4];', '} BattleSlot;') + r'''
static BattleSlot sSlots[128];
static unsigned sSlotCount;
static uint8_t sPlaceOrder[128];
''' + block(video, 'typedef struct\n{\n    int16_t x, w, y;\n} SlotRun;', 'static unsigned sRunCount[2];') \
        + function(video, 'static bool SlotRunFind(') + function(video, 'static void SlotRunCover(') \
        + function(video, 'static bool SlotsPack(') + r'''
static int sFails, sCases;

static void Fail(const char *what, const char *why)
{
    printf("FAIL %s: %s\n", what, why);
    ++sFails;
}

/* Every slot inside its region, none over another, the order by region. */
static void Valid(const char *what)
{
    for (unsigned i = 0; i < sSlotCount; ++i)
    {
        const BattleSlot *a = &sSlots[i];
        const SlotRegion *r = &sRegions[a->region];
        int aw = (a->w + 2) * (int)SCENE_ZOOM, ah = (a->h + 2) * (int)SCENE_ZOOM;

        if (a->region >= sRegionCount || a->sx < r->column || a->sx + aw > r->end || a->sy < 0
            || a->sy + ah > r->tex->height)
            Fail(what, "a slot outside its region");
        if (i && sSlots[i - 1].region > a->region) Fail(what, "not ordered by region");
        for (unsigned j = i + 1; j < sSlotCount; ++j)
        {
            const BattleSlot *b = &sSlots[j];
            int bw = (b->w + 2) * (int)SCENE_ZOOM, bh = (b->h + 2) * (int)SCENE_ZOOM;

            if (a->region == b->region && a->sx < b->sx + bw && b->sx < a->sx + aw
                && a->sy < b->sy + bh && b->sy < a->sy + ah)
                Fail(what, "two slots over each other");
        }
    }
    {
        bool seen[128] = {false};

        for (unsigned i = 0; i < sSlotCount; ++i)
        {
            if (sPlaceOrder[i] >= sSlotCount || seen[sPlaceOrder[i]]) Fail(what, "draw order not each slot once");
            else seen[sPlaceOrder[i]] = true;
        }
    }
}

static C3D_Tex sScene = {1024, 256}, sSlotTex = {512, 256};

static bool Pack(const char *what, int column, bool second, const int (*boxes)[2], unsigned count, bool fits)
{
    bool packed;

    ++sCases;
    sRegionCount = 0;
    if (second) sRegions[sRegionCount++] = (SlotRegion){NULL, &sSlotTex, 0, 512};
    sRegions[sRegionCount++] = (SlotRegion){NULL, &sScene, column, 1024};
    sSlotCount = count;
    for (unsigned i = 0; i < count; ++i)
    {
        memset(&sSlots[i], 0, sizeof(sSlots[i]));
        sSlots[i].w = (int16_t)boxes[i][0];
        sSlots[i].h = (int16_t)boxes[i][1];
        sSlots[i].first = (uint8_t)i;
    }
    packed = SlotsPack();
    if (packed != fits) Fail(what, fits ? "did not fit" : "fitted past the surface");
    if (packed) Valid(what);
    return packed;
}

int main(void)
{
    /* The second battle's opening (hw_campo2d, frame 11257): 652 columns as
     * shelves, beside the world's 408 there are 616. */
    static const int opening[][2] = {{128, 32}, {128, 126}, {64, 64}};
    static const int single[][2] = {{64, 64}, {64, 64}, {128, 32}, {128, 32}};
    static const int doubles[][2] = {{64, 64}, {64, 64}, {64, 64}, {64, 64}, {128, 32}, {128, 32}, {128, 32},
                                     {128, 32}};
    static const int tall[][2] = {{128, 128}};
    static const int wide[][2] = {{200, 40}, {200, 40}};

    Pack("opening beside the world", 408, false, opening, 3, true);
    Pack("opening, whole surface", 0, false, opening, 3, true);
    Pack("single battle beside the world", 408, false, single, 4, true);
    Pack("single battle beside the scenery", 408 + 584, false, single, 4, false);
    Pack("double battle, whole surface", 0, false, doubles, 8, true);
    Pack("double battle, 2D, both surfaces", 600, true, doubles, 8, true);
    Pack("taller than the surface", 0, false, tall, 1, false);
    Pack("wider than the room", 1024 - 300, false, wide, 2, false);
    /* The most slots there can be, each a run of its own: the skyline's
     * capacity (2 * 128 + 2 runs). */
    {
        static int narrow[128][2];

        for (unsigned i = 0; i < 128; ++i)
        {
            narrow[i][0] = 1 + (int)(i % 3);
            narrow[i][1] = 1 + (int)(i % 50);
        }
        Pack("128 narrow slots", 0, false, (const int (*)[2])narrow, 128, true);
    }
    /* Many small ones and random sets: always apart and inside. */
    srand(89);
    for (int round = 0; round < 2000; ++round)
    {
        int boxes[24][2];
        unsigned count = 1 + (unsigned)(rand() % 24);
        char what[32];

        for (unsigned i = 0; i < count; ++i)
        {
            boxes[i][0] = 8 + rand() % 121;
            boxes[i][1] = 8 + rand() % 119;
        }
        sprintf(what, "random set %d", round);
        ++sCases;
        sRegionCount = 0;
        if (round & 1) sRegions[sRegionCount++] = (SlotRegion){NULL, &sSlotTex, 0, 512};
        sRegions[sRegionCount++] = (SlotRegion){NULL, &sScene, rand() % 800, 1024};
        sSlotCount = count;
        for (unsigned i = 0; i < count; ++i)
        {
            memset(&sSlots[i], 0, sizeof(sSlots[i]));
            sSlots[i].w = (int16_t)boxes[i][0];
            sSlots[i].h = (int16_t)boxes[i][1];
        }
        if (SlotsPack()) Valid(what);
    }
    if (sFails)
        return 1;
    printf("PASS slot pack: %d cases (inside, apart, the voxel battle's openings)\n", sCases);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'slot_pack_test.c'
        binary = Path(tmp) / 'slot_pack_test'
        path.write_text(source, encoding='utf-8')
        subprocess.run([args.cc, '-std=gnu99', '-O1', '-Wall', '-Wno-unused-function', str(path), '-o', str(binary)],
                       check=True)
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
