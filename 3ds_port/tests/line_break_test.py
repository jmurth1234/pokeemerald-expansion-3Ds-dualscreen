"""Run production automatic wrapping with poisoned allocations and bounded strings."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--cc', default='gcc')
    args = parser.parse_args()
    harness = r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "global.h"
#include "line_break.h"
static unsigned allocations;
void *Alloc(size_t size) {
    void *p = malloc(size);
    assert(p);
    memset(p, 0x99, size);
    ++allocations;
    return p;
}
void Free(void *p) { assert(allocations); --allocations; free(p); }
s32 GetGlyphWidth(u16 glyph, bool32 japanese, u8 font) {
    (void)japanese; (void)font;
    return glyph == CHAR_SPACE ? 4 : 6;
}
static void check(const char *text, unsigned width, unsigned lines, enum ToggleScrollPrompt prompt) {
    size_t len = strlen(text);
    u8 *str = malloc(len + 1);
    for (size_t i = 0; i < len; ++i) str[i] = text[i] == ' ' ? CHAR_SPACE : text[i];
    str[len] = EOS;
    BreakStringAutomatic(str, width, lines, 0, prompt);
    assert(str[len] == EOS && allocations == 0);
    for (size_t i = 0; i < len; ++i) {
        if (text[i] == ' ')
            assert(str[i] == CHAR_SPACE || str[i] == CHAR_NEWLINE || str[i] == CHAR_PROMPT_SCROLL);
        else
            assert(str[i] == (u8)text[i]);
    }
    if (!strcmp(text, "abcdefghij")) assert(CountLineBreaks(str) == 0);
    if (!strcmp(text, "aa bb cc") && width == 20 && lines == 2) {
        assert(str[2] == CHAR_NEWLINE);
        assert(str[5] == (prompt == SHOW_SCROLL_PROMPT ? CHAR_PROMPT_SCROLL : CHAR_NEWLINE));
    }
    free(str);
}
int main(void) {
    /* One 60-pixel word: estimate reserves a second line, layout uses one. */
    check("abcdefghij", 40, 2, SHOW_SCROLL_PROMPT);
    const char *samples[] = {"", "a", "aa bb cc", "abcdefghij", "abcdefghij a",
        "a abcdefghij", "one two three four five six", "a b c d e f g h i j"};
    for (unsigned i = 0; i < sizeof(samples)/sizeof(samples[0]); ++i)
        for (unsigned width = 1; width <= 240; ++width)
            for (unsigned lines = 1; lines <= 3; ++lines)
                for (unsigned prompt = 0; prompt < 2; ++prompt)
                    check(samples[i], width, lines, prompt);
    u8 manual[] = {'a', CHAR_NEWLINE, 'b', CHAR_PROMPT_SCROLL, 'c', EOS};
    u8 original[sizeof(manual)];
    memcpy(original, manual, sizeof(manual));
    BreakStringAutomatic(manual, 1, 2, 0, SHOW_SCROLL_PROMPT);
    assert(memcmp(manual, original, sizeof(manual)) == 0);
    u8 pages[] = {'a', 'b', 'c', CHAR_PROMPT_CLEAR, 'd', 'e', 'f', EOS};
    BreakStringAutomatic(pages, 1, 2, 0, SHOW_SCROLL_PROMPT);
    assert(pages[3] == CHAR_PROMPT_CLEAR && pages[7] == EOS && allocations == 0);
    puts("PASS: automatic wrapping, overestimated lines, long words, scrolling, manual breaks, pages and bounds");
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        (path / 'global.h').write_text('''#pragma once
#include <stdint.h>
#include <stddef.h>
#include "constants/characters.h"
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int32_t s32;
typedef uint32_t bool32;
#define TRUE 1
#define FALSE 0
''')
        (path / 'text.h').write_text('#include "global.h"\ns32 GetGlyphWidth(u16, bool32, u8);\n')
        (path / 'malloc.h').write_text('#include <stddef.h>\nvoid *Alloc(size_t);\nvoid Free(void *);\n')
        (path / 'line_break.c').write_text((args.tree / 'src/line_break.c').read_text())
        (path / 'test.c').write_text(harness)
        subprocess.run([args.cc, '-std=c99', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fsanitize=address,undefined', '-g',
                        '-I', str(path), '-I', str(args.tree.resolve() / 'include'),
                        str(path / 'line_break.c'), str(path / 'test.c'), '-o', str(path / 'test')], check=True)
        subprocess.run([str(path / 'test')], check=True)


if __name__ == '__main__':
    main()
