"""Check production compressed sheet sizing against prefix and short payloads."""
import argparse
from pathlib import Path
import subprocess
import tempfile
from pokedex_lifetime_test import function

p = argparse.ArgumentParser()
p.add_argument('--tree', type=Path, required=True)
p.add_argument('--cc', default='gcc')
a = p.parse_args()
source = (a.tree / 'src/decompress.c').read_text()
h = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
struct CompressedSpriteSheet { const void *data; unsigned size, tag; };
struct SpriteSheet { const void *data; unsigned size, tag; };
static unsigned payloadSize, expectedSize, usedTiles;
static void *malloc_and_decompress(const void *data, u32 *size) {
    (void)data; *size = payloadSize;
    void *p = malloc(payloadSize); memset(p, 0x5a, payloadSize); return p;
}
static void Free(void *p) { free(p); }
static u32 LoadSpriteSheet(const struct SpriteSheet *s) {
    assert(s->size == expectedSize && s->size <= payloadSize);
    unsigned char *copy = malloc(s->size);
    memcpy(copy, s->data, s->size);
    for (unsigned i = 0; i < s->size; ++i) assert(copy[i] == 0x5a);
    free(copy); usedTiles += s->size / 32;
    return 7;
}
'''
h += function(source, 'u32 LoadCompressedSpriteSheet(const struct CompressedSpriteSheet *src)\n{')
h += r'''
static void check(unsigned declared, unsigned actual, unsigned expected) {
    struct CompressedSpriteSheet s = {NULL, declared, 42};
    payloadSize = actual; expectedSize = expected;
    assert(LoadCompressedSpriteSheet(&s) == 7);
}
int main(void) {
    /* Four health bars share an 0x800 blank image but need only 34 tiles. */
    for (unsigned i = 0; i < 2; ++i) {
        check(0x100, 0x800, 0x100);
        check(0x120, 0x800, 0x120);
    }
    assert(usedTiles == 34);
    check(0x1000, 0x800, 0x800); /* Existing short healthbox protection. */
    check(0x800, 0x800, 0x800);
    check(0, 0x800, 0x800);
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / 'test.c').write_text(h)
    subprocess.run([a.cc, '-std=c99', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g', str(t / 'test.c'),
                    '-o', str(t / 'test')], check=True)
    subprocess.run([str(t / 'test')], check=True)
print('PASS: compressed sheet prefixes, four healthbars, short payloads and unspecified size')
