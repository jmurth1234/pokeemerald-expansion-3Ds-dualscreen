/*
 * ARM11 implementation of the portable bridge hooks that are not resource
 * lookups: logging, debug channels, fonts, script/text pointers, save storage
 * and the audio debug channel. Every deferred behaviour is logged with the
 * plan's stub tag; none of them returns a fake success.
 */

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "port_platform.h"
#include "port_log.h"
#include "3ds_data.h"
#include "3ds_assets.h"
#include "3ds_platform.h"
#include "3ds_audio.h"
#include "gba/flash_internal.h"

/*
 * global.h redirects these four GBA registers to RAM under the bridge; this is
 * the storage the emulated registers live in, shared by every translation unit
 * that sees the bridge.
 */
volatile unsigned short gPortEmuIME;
volatile unsigned short gPortEmuIE;
volatile unsigned short gPortEmuIF;
volatile unsigned short gPortEmuDISPSTAT;

/* ── Logging bridge ─────────────────────────────────────────────────────── */

unsigned char g_PortLogActive = 1;

void Port_Log_Printf(const char *fmt, ...)
{
    char line[256];
    va_list args;
    size_t length;

    va_start(args, fmt);
    vsnprintf(line, sizeof(line), fmt, args);
    va_end(args);
    /* The game's messages end in '\n'; the 3DS logger adds its own. */
    length = strlen(line);
    while (length != 0 && (line[length - 1] == '\n' || line[length - 1] == '\r'))
        line[--length] = '\0';
    CtrLog_Write(CTR_LOG_GAME, "%s", line);
}

void Port_Log_Init(void) {}
void Port_Log_SetEnabled(unsigned char enabled) { g_PortLogActive = enabled; }
unsigned char Port_Log_IsEnabled(void) { return g_PortLogActive; }
void Port_Log_VBlankProtect(void) {}

void CtrAssets_Fatal(const char *detail)
{
    static char message[192];

    snprintf(message, sizeof(message), "[FS] %s", detail != NULL ? detail : "resource failure");
    CtrPlatform_Fatal(message);
}

/* ── Debug channels ─────────────────────────────────────────────────────── */

volatile u16 gPortDbgBirchTag, gPortDbgBirchA, gPortDbgBirchB, gPortDbgBirchC, gPortDbgBirchD;
volatile u16 gPortDbgDmaTag, gPortDbgDmaA, gPortDbgDmaB, gPortDbgDmaC, gPortDbgDmaD;
volatile u16 gPortDbgDma2Tag, gPortDbgDma2A, gPortDbgDma2B, gPortDbgDma2C, gPortDbgDma2D;
volatile u16 gPortDbgAudioTag, gPortDbgAudioA, gPortDbgAudioB, gPortDbgAudioC, gPortDbgAudioD;
volatile u16 gPortDbgAssetTag, gPortDbgAssetA, gPortDbgAssetB, gPortDbgAssetC;

void Port_SetBirchDebugEx(u16 tag, u16 a, u16 b, u16 c, u16 d)
{
    gPortDbgBirchTag = tag; gPortDbgBirchA = a; gPortDbgBirchB = b;
    gPortDbgBirchC = c; gPortDbgBirchD = d;
}

void Port_SetBirchDebug(u16 tag, u16 a, u16 b, u16 c) { Port_SetBirchDebugEx(tag, a, b, c, 0); }

void Port_SetDmaDebugEx(u16 tag, u16 a, u16 b, u16 c, u16 d)
{
    gPortDbgDmaTag = tag; gPortDbgDmaA = a; gPortDbgDmaB = b; gPortDbgDmaC = c; gPortDbgDmaD = d;
}

void Port_SetDmaDebug(u16 tag, u16 a, u16 b, u16 c) { Port_SetDmaDebugEx(tag, a, b, c, 0); }

void Port_SetDmaDebug2(u16 tag, u16 a, u16 b, u16 c, u16 d)
{
    gPortDbgDma2Tag = tag; gPortDbgDma2A = a; gPortDbgDma2B = b; gPortDbgDma2C = c; gPortDbgDma2D = d;
}

/*
 * The mixer already marks its own frame through this channel: 0x0D00 is the
 * first statement of RunMixerFrame and 0x0D09 the last, after the CGB channels
 * have been generated. Timing those two is the whole mixer cost, and it costs
 * nothing to measure at a point the code was going to call anyway.
 */
void Port_SetAudioDebugEx(u16 tag, u16 a, u16 b, u16 c, u16 d)
{
    static uint64_t mixStart;

    gPortDbgAudioTag = tag; gPortDbgAudioA = a; gPortDbgAudioB = b;
    gPortDbgAudioC = c; gPortDbgAudioD = d;

    if (tag == 0x0D00)
    {
        mixStart = CtrPlatform_Ticks();
    }
    else if (tag == 0x0D09 && mixStart != 0)
    {
        CtrAudio_Stats()->mixMs = CtrPlatform_TickMs(CtrPlatform_Ticks() - mixStart);
        mixStart = 0;
    }
}

void Port_SetAssetDebugEx(u16 tag, u16 a, u16 b, u16 c, u16 d)
{
    (void)d;
    gPortDbgAssetTag = tag; gPortDbgAssetA = a; gPortDbgAssetB = b; gPortDbgAssetC = c;
}

void Port_SetAssetDebug(u16 tag, u16 a, u16 b, u16 c) { Port_SetAssetDebugEx(tag, a, b, c, 0); }

void Port_SetGfxTilesetDebug(u8 secondary, u16 size, u8 nonzero)
{
    (void)secondary; (void)size; (void)nonzero;
}

void Port_SetGfxPaletteDebug(u8 secondary, u8 nonzero, u8 resolved)
{
    (void)secondary; (void)nonzero; (void)resolved;
}

void Port_SetGfxMetatileDebug(u16 id, u8 resolved, u16 first, u16 second)
{
    (void)id; (void)resolved; (void)first; (void)second;
}

/* Safe point between main-loop callbacks: the only place payloads may retire. */
void Port_RecordMainLoopIteration(void)
{
    CtrAssets_Collect();
}

/* ── Latin fonts ────────────────────────────────────────────────────────── */

extern const u16 gFontSmallNarrowLatinGlyphs[];
extern const u16 gFontSmallLatinGlyphs[];
extern const u16 gFontNarrowLatinGlyphs[];
extern const u16 gFontShortLatinGlyphs[];
extern const u16 gFontNormalLatinGlyphs[];

/*
 * The stubs of the five latin fonts are packed almost back to back, so an
 * interior pointer derived from one of them aliases another family. Callers
 * must resolve the base first and apply glyph offsets to the loaded buffer.
 */
static struct
{
    const u16 *stubBase;
    const char *path;
    u8 *raw;
    u32 size;
} sLatinFonts[] = {
#ifdef PORT_EXPANSION
    /* The expansion generates its fonts under build/assets/ with the source
     * name kept; these are the same glyph sets the vanilla names point at. */
    { gFontSmallNarrowLatinGlyphs, "build/assets/graphics/fonts/latin_small_narrow.png.latfont", NULL, 0 },
    { gFontSmallLatinGlyphs,       "build/assets/graphics/fonts/latin_small.png.latfont",        NULL, 0 },
    { gFontNarrowLatinGlyphs,      "build/assets/graphics/fonts/latin_narrow.png.latfont",       NULL, 0 },
    { gFontShortLatinGlyphs,       "build/assets/graphics/fonts/latin_short.png.latfont",        NULL, 0 },
    { gFontNormalLatinGlyphs,      "build/assets/graphics/fonts/latin_normal.png.latfont",       NULL, 0 },
#else
    { gFontSmallNarrowLatinGlyphs, "graphics/fonts/small_narrow.latfont", NULL, 0 },
    { gFontSmallLatinGlyphs,       "graphics/fonts/small.latfont",        NULL, 0 },
    { gFontNarrowLatinGlyphs,      "graphics/fonts/narrow.latfont",       NULL, 0 },
    { gFontShortLatinGlyphs,       "graphics/fonts/short.latfont",        NULL, 0 },
    { gFontNormalLatinGlyphs,      "graphics/fonts/normal.latfont",       NULL, 0 },
#endif
};

static bool LoadLatinFont(unsigned index)
{
    FILE *file;
    long size;

    if (sLatinFonts[index].raw != NULL)
        return true;
    file = CtrData_Open(sLatinFonts[index].path);
    if (file == NULL)
    {
        PORT_LOG("[ERROR] font missing: %s\n", sLatinFonts[index].path);
        return false;
    }
    if (fseek(file, 0, SEEK_END) != 0 || (size = ftell(file)) <= 0
     || fseek(file, 0, SEEK_SET) != 0)
    {
        fclose(file);
        return false;
    }
    sLatinFonts[index].raw = malloc((size_t)size);
    if (sLatinFonts[index].raw == NULL
     || fread(sLatinFonts[index].raw, 1, (size_t)size, file) != (size_t)size)
    {
        free(sLatinFonts[index].raw);
        sLatinFonts[index].raw = NULL;
        fclose(file);
        PORT_LOG("[ERROR] font unreadable: %s\n", sLatinFonts[index].path);
        return false;
    }
    fclose(file);
    sLatinFonts[index].size = (u32)size;
    return true;
}

const void *Port_ResolveFontPointer(const void *ptr)
{
    unsigned i;

    for (i = 0; i < ARRAY_COUNT(sLatinFonts); i++)
    {
        if (ptr != sLatinFonts[i].stubBase)
            continue;
        if (!LoadLatinFont(i))
            return ptr;
        return sLatinFonts[i].raw;
    }
    return ptr;
}

void Port_PreloadLatinFonts(void)
{
    unsigned i;

    for (i = 0; i < ARRAY_COUNT(sLatinFonts); i++)
        LoadLatinFont(i);
}

void Port_TextPreload(void)
{
    Port_PreloadLatinFonts();
}

bool Port_FontProbe_ReadNormalLatinGlyph(u16 glyphId, u8 *outBuf, u32 outSize)
{
    const u8 *raw;
    u32 offset = (u32)glyphId * 0x40;

    if (outBuf == NULL || outSize < 0x40 || !LoadLatinFont(4))
        return false;
    raw = sLatinFonts[4].raw;
    if (offset + 0x40 > sLatinFonts[4].size)
        return false;
    memcpy(outBuf, raw + offset, 0x40);
    return true;
}

const void *Port_FontProbe_GetNormalLatinStubBase(void)
{
    return sLatinFonts[4].stubBase;
}

u16 Port_FontProbe_CompareNormalLatinGlyph(u16 glyphId)
{
    u8 glyph[0x40];

    return Port_FontProbe_ReadNormalLatinGlyph(glyphId, glyph, sizeof(glyph)) ? 0 : 0xFFFF;
}

/* ── Save storage ───────────────────────────────────────────────────────────
 *
 * The image is mirrored in FLASH_BASE so reads and verification stay cheap, and each write
 * puts *only its own range* into the file. Rewriting all 128 KiB per sector
 * would be fourteen times the traffic per save and would destroy the other
 * rotating slot on a half-finished write.
 *
 * The directory is created by CtrFs_Init, which runs before the game starts.
 */

#define CTR_SAVE_PATH CTR_DATA_DIR "emerald3ds.sav"
/* Where earlier builds kept the save; moved once on first start. */
#define CTR_OLD_SAVE_PATH "sdmc:/3ds/pokeemerald/pokeemerald.sav"

static bool sSaveAvailable;

static void MigrateOldSave(void)
{
    FILE *probe = fopen(CTR_SAVE_PATH, "rb");

    if (probe != NULL)
    {
        fclose(probe);
        return;
    }
    if (rename(CTR_OLD_SAVE_PATH, CTR_SAVE_PATH) == 0)
        CtrLog_Write(CTR_LOG_FS, "save moved from %s", CTR_OLD_SAVE_PATH);
}

void Port_SaveInit(void)
{
    FILE *file;
    long size;
    bool loaded;

    memset(FLASH_BASE, 0xFF, sizeof(FLASH_BASE));
    sSaveAvailable = false;

    MigrateOldSave();
    file = fopen(CTR_SAVE_PATH, "rb");
    if (file == NULL)
    {
        /* Created by the first real save, not while starting a new game. */
        sSaveAvailable = errno == ENOENT;
        CtrLog_Write(sSaveAvailable ? CTR_LOG_FS : CTR_LOG_ERROR,
                     "save %s: %s (errno=%d)",
                     sSaveAvailable ? "will be created" : "unavailable",
                     CTR_SAVE_PATH, errno);
        return;
    }

    size = -1;
    if (fseek(file, 0, SEEK_END) == 0)
        size = ftell(file);
    loaded = size >= 0 && (u32)size <= sizeof(FLASH_BASE)
          && fseek(file, 0, SEEK_SET) == 0
          && fread(FLASH_BASE, 1, (size_t)size, file) == (size_t)size && !ferror(file);
    if (fclose(file) != 0)
        loaded = false;
    if (!loaded)
    {
        memset(FLASH_BASE, 0xFF, sizeof(FLASH_BASE));
        CtrLog_Write(CTR_LOG_ERROR, "save read failed: %s (size=%ld)", CTR_SAVE_PATH, size);
        return;
    }

    /* A short file still holds whole sectors; Emerald recovers the older slot. */
    sSaveAvailable = true;
    CtrLog_Write(CTR_LOG_FS, "save loaded: %s (%ld bytes)", CTR_SAVE_PATH, size);
}

bool Port_SaveIsAvailable(void)
{
    return sSaveAvailable;
}

u16 Port_WriteFlash(u32 offset, const void *data, u32 size)
{
    FILE *file;
    long fileSize;
    bool ok;

    if (!sSaveAvailable || data == NULL || offset > sizeof(FLASH_BASE)
     || size > sizeof(FLASH_BASE) - offset)
        return 1;

    file = fopen(CTR_SAVE_PATH, "r+b");
    if (file == NULL && errno == ENOENT)
        file = fopen(CTR_SAVE_PATH, "w+b");
    if (file == NULL)
        goto fail;

    /* Writing past the end of a short file would leave a hole, so the image is
     * grown from the mirror first and only then is the range written. */
    ok = fseek(file, 0, SEEK_END) == 0;
    fileSize = ok ? ftell(file) : -1;
    ok = fileSize >= 0 && (u32)fileSize <= sizeof(FLASH_BASE);
    if (ok && (u32)fileSize < sizeof(FLASH_BASE))
    {
        u32 remaining = sizeof(FLASH_BASE) - (u32)fileSize;

        ok = fwrite(FLASH_BASE + fileSize, 1, remaining, file) == remaining;
    }
    if (ok)
        ok = fseek(file, (long)offset, SEEK_SET) == 0 && fwrite(data, 1, size, file) == size;
    if (ok)
        ok = fflush(file) == 0;
    if (fclose(file) != 0)
        ok = false;
    if (!ok)
        goto fail;

    memmove(FLASH_BASE + offset, data, size);
    return 0;

fail:
    CtrLog_Write(CTR_LOG_ERROR, "save write failed: offset=%lu size=%lu errno=%d",
                 (unsigned long)offset, (unsigned long)size, errno);
    return 1;
}

/* ── Entry points the excluded GBA link/multiboot units would provide ───── */

u32 JoyBusRead(void) { return 0; }
void JoyBusWrite(void) {}
void CB2_InitBerryFixProgram(void) { CTR_STUB("BERRY_FIX", "GBA berry-fix program not applicable"); }
void CreateEReaderTask(void) { CTR_STUB("EREADER", "e-Reader link not supported"); }
void CB2_InitMysteryGift(void) { CTR_STUB("MYSTERY_GIFT", "link menu not supported"); }
void CB2_InitEReader(void) { CTR_STUB("EREADER", "e-Reader menu not supported"); }
void CB2_InitMysteryEventMenu(void) { CTR_STUB("MYSTERY_EVENT", "link menu not supported"); }

/*
 * Save-image verification. The GBA original relocated its comparison loop into
 * IWRAM because the cartridge bus needed it; here the save image is ordinary
 * RAM. VerifyFlashSector_Core is compiled out under PORTABLE (it returns 0
 * unconditionally), so this compares the bytes itself instead of reporting a
 * match that was never checked.
 */
static u32 CompareSaveBytes(const u8 *src, const u8 *target, u32 size)
{
    while (size-- != 0)
    {
        if (*target++ != *src++)
            return (u32)(target - 1);
    }
    return 0;
}

u32 VerifyFlashSectorNBytes(u16 sectorNum, u8 *src, u32 n)
{
    u32 base;

    if (gFlash == NULL || sectorNum >= gFlash->sector.count || n == 0
     || n > gFlash->sector.size)
        return 1;
    base = (u32)sectorNum << gFlash->sector.shift;
    if (base >= sizeof(FLASH_BASE) || n > sizeof(FLASH_BASE) - base)
        return 1;
    return CompareSaveBytes(src, FLASH_BASE + base, n);
}

u32 VerifyFlashSector(u16 sectorNum, u8 *src)
{
    return VerifyFlashSectorNBytes(sectorNum, src, gFlash != NULL ? gFlash->sector.size : 0);
}

#ifdef PORT_EXPANSION
/*
 * The expansion's game tree references a few symbols the vanilla tree either
 * defined elsewhere or never needed, and excludes the GBA-only mgba print
 * library (its logs go through the port backend). Keep the link complete; the
 * ones on debug/assert paths are deliberately inert on the 3DS.
 */
bool32 CheckHeap(void) { return TRUE; }
void ReInitializeEWRAM(void) {}

bool32 MgbaOpen(void) { return TRUE; }
void MgbaPrintf(s32 level, const char *pBuf, ...) { (void)level; (void)pBuf; }
void MgbaAssert(const char *pFile, s32 nLine, const char *pExpression, bool32 nStopProgram)
{
    (void)nStopProgram;
    Port_Log_Printf("ASSERT %s:%d %s", pFile != NULL ? pFile : "?", (int)nLine,
                    pExpression != NULL ? pExpression : "?");
}

/* Only assertf.c's crash screen calls this, and the port has its own. */
void BitUnPack(const void *src, void *dest, const void *args)
{
    (void)src;
    (void)dest;
    (void)args;
}

/*
 * expansion's ld_script_modern.ld defines the initial callback and the segment
 * bounds; the port's linker template has no equivalent, so declare them here.
 */
void CB2_InitCopyrightScreenAfterBootup(void);
void gInitialMainCB2(void) { CB2_InitCopyrightScreenAfterBootup(); }
char __iwram_end[1];
u8 __rom_end[1];
#endif
