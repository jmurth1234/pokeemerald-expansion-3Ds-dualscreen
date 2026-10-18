"""Exercise expansion Pokédex cleanup across heap reuse and a later capture."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--cc', default='gcc')
    args = parser.parse_args()
    source = (args.tree / 'src/pokedex.c').read_text()
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef uint8_t u8;
typedef bool bool8;
typedef bool bool32;
#define DEX_MODE_HOENN 0
#define TRACKS_ALL 0xffff
#define REG_OFFSET_DISPCNT 0
#define DISPCNT_OBJ_1D_MAP 0x40
#define DISPCNT_OBJ_ON 0x1000
#define tIsShiny data[13]
#define FREE_AND_SET_NULL(p) do { Free(p); (p) = NULL; } while (0)
static struct { bool active; } gPaletteFade;
static struct { struct { int mode, order; } pokedex; } save, *gSaveBlock2Ptr = &save;
static struct View { int dexMode, dexOrder; } reused, *sPokedexView;
static struct { int data[16]; } gTasks[1];
static int gMPlayInfo_BGM, windowsFreed, destroyed, viewFrees, battleFrees;
static int owner; /* 1 = Pokédex view; 2 = battle data reusing that allocation */
static int bg[2], bgFrees;
static bool national, shiny;
static void (*callback)(void);
static void Free(void *p) {
    if (p == &reused) {
        assert(owner != 0);
        if (owner == 1) ++viewFrees;
        else ++battleFrees;
        owner = 0;
    } else {
        assert(p == &bg[0] || p == &bg[1]);
        ++bgFrees;
    }
}
static bool IsNationalPokedexEnabled(void) { return national; }
static void ClearMonSprites(void) {}
static void FreeWindowAndBgBuffers(void) { ++windowsFreed; }
static void FreeAllWindowBuffers(void) { ++windowsFreed; }
static void DestroyTask(u8 id) { assert(id == 0); ++destroyed; }
static void CB2_ReturnToFieldWithOpenMenu(void) {}
static void SetMainCallback2(void (*cb)(void)) { callback = cb; }
static void m4aMPlayVolumeControl(void *p, int tracks, int volume) {
    assert(p == &gMPlayInfo_BGM && tracks == TRACKS_ALL && volume == 0x100);
}
static void SetGpuReg(int reg, int value) { (void)reg; (void)value; }
static void *GetBgTilemapBuffer(int id) { assert(id == 2 || id == 3); return &bg[id - 2]; }
static void LoadDexMonPalette(unsigned id, bool32 isShiny) { assert(id == 0); shiny = isShiny; }
'''
    harness += function(source, 'static void Task_ClosePokedex(u8 taskId)\n{')
    harness += function(source, 'static void Task_ExitCaughtMonPage(u8 taskId)\n{')
    harness += r'''
int main(void) {
    for (int cycle = 0; cycle < 4; ++cycle) {
        national = cycle & 1;
        owner = 1;
        sPokedexView = &reused;
        reused = (struct View){1, 3};
        gPaletteFade.active = true;
        Task_ClosePokedex(0);
        assert(owner == 1 && sPokedexView == &reused);
        gPaletteFade.active = false;
        Task_ClosePokedex(0);
        assert(owner == 0 && viewFrees == cycle + 1);
        assert(save.pokedex.mode == (national ? 1 : DEX_MODE_HOENN));
        assert(save.pokedex.order == 3 && callback == CB2_ReturnToFieldWithOpenMenu);

        /* Battle startup resets/reuses the game heap. Normal capture does not
         * allocate sPokedexView, unlike the HGSS capture screen. */
        owner = 2;
        gTasks[0].tIsShiny = cycle & 1;
        gPaletteFade.active = true;
        Task_ExitCaughtMonPage(0);
        assert(owner == 2);
        gPaletteFade.active = false;
        Task_ExitCaughtMonPage(0);
        assert(battleFrees == 0 && owner == 2);
        assert(sPokedexView == NULL && shiny == (bool)(cycle & 1));
        assert(bgFrees == 2 * (cycle + 1));
    }
    /* HGSS capture owns a fresh view and must still release it. */
    owner = 1;
    sPokedexView = &reused;
    Task_ExitCaughtMonPage(0);
    assert(owner == 0 && sPokedexView == NULL && viewFrees == 5);
    assert(battleFrees == 0 && destroyed == 9 && windowsFreed == 9);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        (path / 'test.c').write_text(harness)
        subprocess.run([args.cc, '-std=c99', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-g', str(path / 'test.c'),
                        '-o', str(path / 'test')], check=True)
        subprocess.run([str(path / 'test')], check=True)
    print('PASS: Pokédex close, heap reuse, repeated normal captures, HGSS cleanup, fade gates')


if __name__ == '__main__':
    main()
