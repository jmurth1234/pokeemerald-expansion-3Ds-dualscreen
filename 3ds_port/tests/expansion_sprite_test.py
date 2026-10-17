"""Exercise category icon creation and animation through the real sprite engine."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tree', required=True, type=Path)
    parser.add_argument('--cc', default='gcc')
    args = parser.parse_args()
    sprites = (args.tree / 'src/sprite.c').read_text()
    summary = (args.tree / 'src/pokemon_summary_screen.c').read_text()
    start = summary.index('#define TAG_MOVE_SELECTOR ')
    end = summary.index('static const struct OamData sOamData_MoveTypes', start)
    source = r'''
#include <assert.h>
#include "gba/types.h"
#include "sprite.h"
#define TESTING 0
#define PLATFORM_3DS 1
#define ANIM_SPRITES_START 10000
#define ANIM_TAG_COUNT 20000
#define OW_GFX_COMPRESS 1
static unsigned copies;
struct Sprite gSprites[MAX_SPRITES + 1];
static unsigned gCtrSpriteId[MAX_SPRITES], gCtrSpriteNextId;
static const u32 gCategoryIcons_Gfx[] = {0};
static const u16 gCategoryIcons_Pal[] = {0};
static const union AnimCmd dummy[] = {ANIMCMD_END};
const union AnimCmd *const gDummySpriteAnimTable[] = {dummy};
static const union AffineAnimCmd dummyAffine[] = {AFFINEANIMCMD_END};
const union AffineAnimCmd *const gDummySpriteAffineAnimTable[] = {dummyAffine};
static const struct Sprite sDummySprite = {
    .anims = gDummySpriteAnimTable, .affineAnims = gDummySpriteAffineAnimTable
};
static void ResetSprite(struct Sprite *sprite);
static bool32 IsGfxLoaded(u16 tag) { (void)tag; return TRUE; }
static bool32 IsPalLoaded(u16 tag) { (void)tag; return TRUE; }
#define assertf(ok, ...) assert(ok)
void SpriteCallbackDummy(struct Sprite *s) { (void)s; }
void CalcCenterToCornerVec(struct Sprite *s, u8 shape, u8 size, u8 mode)
{ (void)s; (void)shape; (void)size; (void)mode; }
s16 AllocSpriteTiles(u16 count) { assert(count > 0); return 0; }
u16 GetSpriteTileStartByTag(u16 tag) { assert(tag == 30004); return 100; }
u32 IndexOfSpritePaletteTag(u16 tag) { assert(tag == 30004); return 2; }
void InitSpriteAffineAnim(struct Sprite *s) { (void)s; assert(0); }
void SetSpriteOamFlipBits(struct Sprite *s, u8 h, u8 v) { (void)s; (void)h; (void)v; }
void RequestSpriteFrameImageCopy(u16 index, u16 tile, const struct SpriteFrameImage *images)
{ (void)index; (void)tile; (void)images; ++copies; }
'''
    source += summary[start:end]
    signatures = [
        'void ResetSprite(', 'u32 CreateSpriteAt(', 'void SetSpriteSheetFrameTileNum(',
        'void StartSpriteAnim(', 'void BeginAnim(', 'void ContinueAnim(',
        'void AnimCmd_frame(', 'void AnimCmd_end(', 'void AnimCmd_jump(',
        'void AnimCmd_loop(', 'void BeginAnimLoop(', 'void ContinueAnimLoop(',
        'void JumpToTopOfAnimLoop(', 'void DecrementAnimDelayCounter(',
    ]
    # Definitions have a newline before their opening brace; skip declarations.
    bodies = []
    for signature in signatures:
        pos = 0
        while True:
            pos = sprites.index(signature, pos)
            if sprites.index('{', pos) < sprites.index(';', pos):
                break
            pos += len(signature)
        body = function(sprites[pos:], signature)
        bodies.append(body)
        source += body[:body.index('{')].rstrip() + ';\n'
    source += r'''
static void (*const sAnimCmdFuncs[])(struct Sprite *) = {
    AnimCmd_loop, AnimCmd_jump, AnimCmd_end, AnimCmd_frame
};
'''
    source += '\n'.join(bodies)
    source += r'''
int main(void)
{
    /* Summary, battle move details, move relearner and HGSS Pokedex all use
     * this template. Creation touches animation 0 before StartSpriteAnim. */
    for (unsigned screen = 0; screen < 4; ++screen)
        for (unsigned category = 0; category <= 3; ++category)
            for (unsigned repeat = 0; repeat < 10; ++repeat)
            {
                unsigned id = CreateSpriteAt(0, &gSpriteTemplate_CategoryIcons, 48, 128, 0);
                assert(id == 0 && gSprites[id].oam.tileNum == 100);
                StartSpriteAnim(&gSprites[id], category);
                BeginAnim(&gSprites[id]);
                assert(!gSprites[id].animBeginning);
                unsigned offset = category ? (category - 1) * 4 : 0;
                assert(gSprites[id].oam.tileNum == 100 + offset);
                assert(!gSprites[id].animEnded);
                ContinueAnim(&gSprites[id]);
                assert(gSprites[id].animEnded);
                assert(gSprites[id].oam.paletteNum == 2);
            }
    assert(copies == 0);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        (path / 'test.c').write_text(source)
        subprocess.run([args.cc, '-std=gnu99', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-g',
                        '-iquote', str(args.tree.resolve() / 'include'), str(path / 'test.c'),
                        '-o', str(path / 'test')], check=True)
        subprocess.run([str(path / 'test')], check=True)
    print('PASS: category icon creation, all categories, animation completion, repeated screen entry')


if __name__ == '__main__':
    main()
