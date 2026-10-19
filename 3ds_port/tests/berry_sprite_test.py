"""Exercise dynamic berry frame descriptors with dirty/reused heap contents."""
import argparse
from pathlib import Path
import subprocess
import tempfile
from pokedex_lifetime_test import function


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--cc', default='gcc')
    args = parser.parse_args()
    icons = (args.tree / 'src/item_menu_icons.c').read_text()
    sprites = (args.tree / 'src/sprite.c').read_text()
    header = (args.tree / 'include/sprite.h').read_text()
    descriptor = header[header.index('struct SpriteFrameImage\n'):]
    descriptor = descriptor[:descriptor.index('};') + 2]
    harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int32_t s32;
typedef u8 bool8;
#define PORT_BRIDGE 1
#define FALSE 0
#define BERRY_SPRITE_SIZE 2048
#define BERRY_ICON_GFX_PTR_DATA_ID 6
#define MAX_SPRITE_COPY_REQUESTS 8
#define TILE_SIZE_4BPP 32
'''
    harness += descriptor
    harness += r'''
struct BerryDynamicGfx { u8 gfx[BERRY_SPRITE_SIZE]; struct SpriteFrameImage images[1]; };
static struct BerryDynamicGfx reused;
struct SpriteTemplate { u16 paletteTag; const struct SpriteFrameImage *images; };
static struct { u16 data[8]; } gSprites[1];
static u8 vram[BERRY_SPRITE_SIZE];
#define OBJ_VRAM0 vram
static struct { const void *src; void *dest; u32 size; } sSpriteCopyRequests[8];
static u8 sSpriteCopyRequestCount;
static u32 Port_GetSpriteFrameSize(const void *p, u32 size) { (void)p; return size; }
static const void *Port_ResolveSpriteFramePointer(const void *p, u32 size, u32 offset) {
    assert(p == reused.gfx && size == BERRY_SPRITE_SIZE);
    assert(offset == 0); /* Dirty offset caused crash dump 41. */
    return (const u8 *)p + offset;
}
'''
    harness += function(sprites, 'void RequestSpriteFrameImageCopy(u16 index, u16 tileNum, const struct SpriteFrameImage *images)\n{')
    harness += r'''
static struct BerryDynamicGfx *LoadBerryGfx(u8 id) {
    /* Alloc may return old graphics bytes, not zero-filled memory. */
    memset(&reused, id, sizeof(reused));
    return &reused;
}
static u32 CreateSprite(const struct SpriteTemplate *t, s32 x, s32 y, u8 priority) {
    assert(x == 12 && y == 34 && priority == 0);
    assert(!t->images[0].relativeFrames);
    RequestSpriteFrameImageCopy(0, 0, t->images);
    return 0;
}
static void StoreWordInTwoHalfwords(u16 *p, u32 value) { (void)p; (void)value; }
'''
    harness += function(icons, 'static u32 CreateBerrySprite(const struct SpriteTemplate *sprTemplate, u32 berryId, s32 x, s32 y)\n{')
    harness += r'''
int main(void) {
    const struct SpriteTemplate t = {100, NULL};
    for (unsigned fill = 1; fill < 256; ++fill) {
        sSpriteCopyRequestCount = 0;
        assert(CreateBerrySprite(&t, fill, 12, 34) == 0);
        /* Repeated animation uses the same descriptor, including in Blender. */
        RequestSpriteFrameImageCopy(0, 0, reused.images);
        assert(sSpriteCopyRequestCount == 2);
        for (unsigned i = 0; i < sSpriteCopyRequestCount; ++i) {
            assert(sSpriteCopyRequests[i].size == sizeof(vram));
            memcpy(sSpriteCopyRequests[i].dest, sSpriteCopyRequests[i].src,
                   sSpriteCopyRequests[i].size);
            for (unsigned j = 0; j < sizeof(vram); ++j) assert(vram[j] == fill);
        }
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        (path / 'test.c').write_text(harness)
        subprocess.run([args.cc, '-std=c99', '-Wall', '-Wextra', '-Werror',
                        '-Wno-pointer-to-int-cast', '-fsanitize=address,undefined',
                        '-g', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
        subprocess.run([str(path / 'test')], check=True)
    print('PASS: berry creation and repeated frame copies across 255 dirty heap patterns')


if __name__ == '__main__':
    main()
