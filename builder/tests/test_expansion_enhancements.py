"""Execute the real expansion adapters with host-side game state fixtures."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GAME = ROOT / 'build/expansion/src'
CC = shutil.which('cc')


def function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


@unittest.skipUnless(CC and (GAME / 'wild_encounter.c').exists(), 'requires bootstrapped expansion and C compiler')
class ExpansionEnhancementTests(unittest.TestCase):
    def run_c(self, source, extra_sources=()):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'test.c').write_text(source)
            subprocess.run([CC, '-std=c11', '-Wall', '-Werror', '-DPLATFORM_3DS',
                            '-DPORT_EXPANSION', '-I', str(ROOT / '3ds_port/include'), str(path / 'test.c'),
                            *map(str, extra_sources), '-lm', '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)

    def test_party_exp_setting_and_native_item_flag(self):
        actual = function((GAME / 'battle_util.c').read_text(), 'bool32 IsGen6ExpShareEnabled(void)')
        self.run_c('''
#include <assert.h>
#include <string.h>
typedef int bool32;
#define TRUE 1
#define FALSE 0
#define I_EXP_SHARE_FLAG 32
#define TEMP_FLAGS_END 31
static int setting, nativeFlag;
static int CtrExtras_Get(const char *key) { assert(!strcmp(key, "exp_share")); return setting; }
static int FlagGet(int flag) { assert(flag == I_EXP_SHARE_FLAG); return nativeFlag; }
''' + actual + '''
int main(void) {
 assert(!IsGen6ExpShareEnabled());
 setting=1; assert(IsGen6ExpShareEnabled());
 setting=0; nativeFlag=1; assert(IsGen6ExpShareEnabled());
 nativeFlag=0; assert(!IsGen6ExpShareEnabled());
}
''')

    def test_visible_contact_respects_setting_water_and_lifecycle(self):
        actual = function((GAME / 'wild_encounter.c').read_text(), 'bool8 VisibleWild_StepOnto(void)')
        self.run_c('''
#include <assert.h>
typedef int bool8; typedef short s16; typedef unsigned u32;
#define TRUE 1
#define FALSE 0
#define VW_MAX 6
#define VW_NONE 64
#define PLAYER_AVATAR_FLAG_SURFING 8
static int enabled=1, surfing, battles, sWildEncountersDisabled;
#define VISIBLE_MOST() enabled
static struct ObjectEvent { struct { s16 x,y; } currentCoords; } gObjectEvents[1] = {{{10,20}}};
static struct { int objectEventId; } gPlayerAvatar;
static struct VisibleWild { int active,vanishing,spriteId,water,x,y,progress,dx,dy; } sVisible[VW_MAX];
static int TestPlayerAvatarFlags(int flag) { assert(flag==8); return surfing; }
static void VisibleStartBattle(struct VisibleWild *v) { assert(v==sVisible); battles++; }
''' + actual + '''
int main(void) {
 sVisible[0]=(struct VisibleWild){.active=1,.spriteId=2,.x=10,.y=20};
 assert(VisibleWild_StepOnto() && battles==1);
 enabled=0; assert(!VisibleWild_StepOnto()); enabled=1;
 sWildEncountersDisabled=1; assert(!VisibleWild_StepOnto()); sWildEncountersDisabled=0;
 sVisible[0].vanishing=1; assert(!VisibleWild_StepOnto()); sVisible[0].vanishing=0;
 sVisible[0].spriteId=VW_NONE; assert(!VisibleWild_StepOnto()); sVisible[0].spriteId=2;
 surfing=1; assert(!VisibleWild_StepOnto()); sVisible[0].water=1;
 sVisible[0].x=9; sVisible[0].dx=1; sVisible[0].progress=1;
 assert(VisibleWild_StepOnto() && battles==2);
 sVisible[0].progress=0; assert(!VisibleWild_StepOnto());
}
''')

    def test_visible_battle_keeps_identity_and_initializes_moves(self):
        actual = function((GAME / 'wild_encounter.c').read_text(), 'static void VisibleStartBattle(struct VisibleWild *v)')
        self.run_c('''
#include <assert.h>
#define B_TRAINER_OPPONENT_A 0
#define OTID_STRUCT_PLAYER_ID 0
#define USE_RANDOM_IVS 32
#define CTR_LOG_GAME 0
static int gParties[1][6], gIsSurfingEncounter, sequence;
struct VisibleWild { int species,level,water,personality,shiny; };
typedef int bool32;
#define MON_DATA_IS_SHINY 1
static void SetMonData(int *mon, int field, const void *value) { assert(mon==&gParties[0][0] && field==1 && *(const bool32*)value==1); }
static void ZeroEnemyPartyMons(void) { assert(sequence++==0); }
static void CreateMonWithIVs(int *mon,int species,int level,int personality,int ot,int ivs) {
 assert(sequence++==1 && mon==&gParties[0][0] && species==270 && level==7 && personality==1234 && ot==0 && ivs==32);
}
static void GiveMonInitialMoveset(int *mon) { assert(sequence++==2 && mon==&gParties[0][0]); }
#define CtrLog_Write(...) ((void)0)
static void VisibleRemove(struct VisibleWild *v) { assert(sequence++==3 && v->species==270); }
static void BattleSetup_StartWildBattle(void) { assert(sequence++==4); }
''' + actual + '''
int main(void) {
 struct VisibleWild v={.species=270,.level=7,.water=1,.personality=1234,.shiny=1};
 VisibleStartBattle(&v); assert(sequence==5 && gIsSurfingEncounter==1);
}
''')

    def test_every_gameplay_toggle_has_an_expansion_reader(self):
        # UI registration alone must not qualify as a working feature.
        hooks = {
            'exp_catch': 'battle_main.c', 'exp_share': 'battle_util.c',
            'trade_evo': 'pokemon.c', 'visible_wild': 'wild_encounter.c',
            'ivs_evs': 'pokemon_summary_screen.c', 'repel_prompt': 'sprays.c',
            'infinite_tms': 'party_menu.c', 'run_indoors': 'bike.c',
            'encounter_rate': 'wild_encounter.c', 'shiny_odds': 'pokemon.c',
            'always_catch': 'battle_script_commands.c', 'instant_victory': 'battle_main.c',
            'fast_eggs': 'daycare.c', 'infinite_money': 'money.c',
            'follower': 'event_object_movement.c',
        }
        shared = {'speed', 'audio_clock', 'voxel_stereo', 'voxel_depth',
                  'day_night', 'clock_speed', 'timed_encounters', 'show_clock',
                  'dex_hoenn', 'dex_national_on', 'dex_national',
                  'give_open', 'give_back', 'give_pocket', 'give_item', 'give_count', 'give_now'}
        registered = set(re.findall(r'\), "([a-z_]+)",', (ROOT / '3ds_port/src/3ds_extras.c').read_text()))
        self.assertEqual(registered, set(hooks) | shared | {'field_hms', 'move_split'})
        for key, file in hooks.items():
            with self.subTest(key=key):
                self.assertIn('CtrExtras_Get("' + key + '")', (GAME / file).read_text())
        for file in ('scrcmd.c', 'field_player_avatar.c'):
            self.assertIn('CtrExtras_FieldHm(', (GAME / file).read_text())
        self.assertIn('CanUseFieldHmFromBag', (GAME / 'item_use.c').read_text())
        self.assertIn('CtrEncounters_Land(', (GAME / 'wild_encounter.c').read_text())
        self.assertIn('CtrDayNight_Time(', (GAME / 'overworld.c').read_text())
        self.assertIn('CtrDayNight_TakeBerryMinutes(', (GAME / 'clock.c').read_text())
        self.assertIn('CtrClockStore_Save(', (GAME / 'save.c').read_text())
        self.assertIn('CtrClockStore_Load(', (GAME / 'save.c').read_text())

    def test_hm_override_does_not_cover_other_moves(self):
        actual = function((ROOT / '3ds_port/src/3ds_extras.c').read_text(), 'bool CtrExtras_FieldHm(uint16_t move)')
        self.run_c('''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
enum { MOVE_CUT=1, MOVE_FLY, MOVE_SURF, MOVE_STRENGTH, MOVE_FLASH, MOVE_ROCK_SMASH, MOVE_WATERFALL, MOVE_DIVE };
static int enabled;
static int CtrExtras_Get(const char *key) { assert(!strcmp(key,"field_hms")); return enabled; }
''' + actual + '''
int main(void) {
 for (int move=1; move<=8; ++move) assert(!CtrExtras_FieldHm(move));
 enabled=1; for (int move=1; move<=8; ++move) assert(CtrExtras_FieldHm(move));
 assert(!CtrExtras_FieldHm(0) && !CtrExtras_FieldHm(9) && !CtrExtras_FieldHm(65535));
}
''')

    def test_shiny_settings_and_forced_flags(self):
        actual = function((GAME / 'pokemon.c').read_text(), 'bool32 ComputePlayerShinyOdds(u32 personality, u32 value)')
        self.run_c('''
#include <assert.h>
#include <stddef.h>
#include <string.h>
typedef unsigned u32; typedef int bool32;
#define TRUE 1
#define FALSE 0
#define ARRAY_COUNT(a) (sizeof(a)/sizeof((a)[0]))
#define GET_SHINY_VALUE(ot,pid) ((ot)^(pid))
#define SHINY_ODDS 8
#define P_FLAG_FORCE_NO_SHINY 1
#define P_FLAG_FORCE_SHINY 2
#define P_ONLY_OBTAINABLE_SHINIES 0
#define PYRAMID_LOCATION_NONE 0
#define WE_FLAG_NO_CATCHING 3
#define P_NO_SHINIES_WITHOUT_POKEBALLS 0
#define FLAG_SYS_POKEDEX_GET 4
#define ITEM_SHINY_CHARM 5
#define I_SHINY_CHARM_ADDITIONAL_ROLLS 2
#define LURE_STEP_COUNT 0
static int setting, forceFlag, gDexNavSpecies;
static int FlagGet(int flag) { return flag==forceFlag; }
static int CtrExtras_Get(const char *key) { assert(!strcmp(key,"shiny_odds")); return setting; }
static int CurrentBattlePyramidLocation(void) { return 0; }
static int HasAtLeastOnePokeBall(void) { return 1; }
static int CheckBagHasItem(int item,int count) { (void)item;(void)count;return 0; }
static int CalculateChainFishingShinyRolls(void) { return 0; }
static int CalculateDexNavShinyRolls(void) { return 0; }
static u32 Random32(void) { return 0; }
''' + actual + '''
int main(void) {
 const unsigned thresholds[]={8,16,64,256,1024,65536};
 for(setting=0;setting<6;setting++) {
  assert(ComputePlayerShinyOdds(thresholds[setting]-1,0));
  if(setting<5) assert(!ComputePlayerShinyOdds(thresholds[setting],0));
 }
 setting=5;forceFlag=P_FLAG_FORCE_NO_SHINY;assert(!ComputePlayerShinyOdds(0,0));
 setting=0;forceFlag=P_FLAG_FORCE_SHINY;assert(ComputePlayerShinyOdds(65535,0));
}
''')

    def test_money_cheat_preserves_encryption_and_other_wallets(self):
        actual = function((GAME / 'money.c').read_text(), 'u32 GetMoney(u32 *moneyPtr)')
        self.run_c('''
#include <assert.h>
#include <string.h>
typedef unsigned u32;
#define MAX_MONEY 999999
static struct { u32 money; } save1;
static struct { u32 encryptionKey; } save2={0x12345678};
#define gSaveBlock1Ptr (&save1)
#define gSaveBlock2Ptr (&save2)
static int enabled;
static int CtrExtras_Get(const char *key) { assert(!strcmp(key,"infinite_money"));return enabled; }
''' + actual + '''
int main(void) {
 save1.money=100^save2.encryptionKey;assert(GetMoney(&save1.money)==100);
 enabled=1;assert(GetMoney(&save1.money)==MAX_MONEY);
 assert(save1.money==(MAX_MONEY^save2.encryptionKey));
 u32 other=23^save2.encryptionKey;assert(GetMoney(&other)==23);
}
''')

    def test_expansion_clock_drains_bonus_once(self):
        actual = function((GAME / 'clock.c').read_text(), 'static void UpdatePerMinute(struct Time *localTime)\n{')
        self.run_c('''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "3ds_daynight.h"
struct Time { int days,hours,minutes,seconds; };
static struct { struct Time lastBerryTreeUpdate; } save;
#define gSaveBlock2Ptr (&save)
static unsigned totalMinutes, seconds;
static uint64_t now;
uint64_t CtrPlatform_Milliseconds(void) { return now; }
int64_t CtrPlatform_WallSeconds(void) { return 1700000000; }
bool CtrGame_IsOutdoor(void) { return true; }
bool CtrGame_LocalTimeSeconds(unsigned *value) { *value=seconds; return true; }
int CtrSettings_GetInt(const char *key,int fallback) {
 if (!strcmp(key,"clock_speed")) return 5;
 if (!strcmp(key,"show_clock")) return 1;
 return fallback;
}
static void CalcTimeDifference(struct Time *difference,const struct Time *start,const struct Time *end) {
 *difference=(struct Time){.minutes=end->minutes-start->minutes};
}
static void BerryTreeTimeUpdate(unsigned minutes) { totalMinutes+=minutes; }
''' + actual + '''
int main(void) {
 CtrDayNight_Reset();CtrDayNight_Update();
 now=120000;seconds=120;struct Time local={.minutes=2};
 CtrDayNight_Update();UpdatePerMinute(&local);assert(totalMinutes==240);
 UpdatePerMinute(&local);assert(totalMinutes==240);
}
''', [ROOT / '3ds_port/src/3ds_daynight.c'])

    def test_category_drawing_stays_inside_three_expansion_icons(self):
        actual = function((ROOT / '3ds_port/src/3ds_bottom_ui.c').read_text(), 'static void DrawCategoryIcon(u16 move, int x, int y)')
        self.run_c('''
#include <assert.h>
#include <stddef.h>
typedef unsigned short u16;
#define MOVES_COUNT 3
static unsigned char tiles[3*16*16/2];
static struct { unsigned char *categoryTiles; struct { int c[16]; } categoryPal; } sRes={.categoryTiles=tiles};
static int drawn;
static int BottomMoveSplitEnabled(void) { return 1; }
static int MoveCategory(u16 move) { return move; }
static void DrawSprite(const unsigned char *src,int w,int h,int x,int y,const int *pal) {
 assert(src>=tiles && src+w*h*32<=tiles+sizeof(tiles));
 assert(w==2 && h==2 && x==18 && y==20 && pal==sRes.categoryPal.c); drawn++;
}
''' + actual + '''
int main(void) { for(u16 move=0;move<3;move++) DrawCategoryIcon(move,10,20); assert(drawn==3); }
''')


if __name__ == '__main__':
    unittest.main()
