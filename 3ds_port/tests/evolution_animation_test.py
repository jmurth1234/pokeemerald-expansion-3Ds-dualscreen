"""Run the expansion's animation task after battle resources are freed."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--cc', default='gcc')
    args = parser.parse_args()
    text = (args.tree / 'src/pokemon_animation.c').read_text()
    start = text.index('static void Task_HandleMonAnimation(u8 taskId)\n{')
    end = text.index('\nvoid LaunchAnimationTaskForFrontSprite', start)
    source = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#define PLATFORM_3DS 1
#define TRUE 1
#define FALSE 0
#define ARRAY_COUNT(a) (sizeof(a) / sizeof((a)[0]))
typedef uint8_t u8;
typedef uint32_t u32;
struct Sprite { struct { int paletteNum; } oam; int data[8];
    void (*callback)(struct Sprite *); };
struct Task { int tState, tBattlerId, tSpeciesId, tAnimId; };
static struct Task gTasks[1];
static struct Sprite testSprite;
#define ANIM_SPRITE(id) (&testSprite)
#define sDontFlip data[1]
static struct { int battlerKOAnimsRunning; } battle, *gBattleStruct;
static struct { int forceMoveAnim; } runner, *gBattleTestRunnerState = &runner;
static int gTestRunnerHeadless, sIsSummaryAnim, destroyed;
static void SpriteCallbackDummy(struct Sprite *s) { (void)s; }
static void WaitAnimEnd(struct Sprite *s) { (void)s; }
static void (*sMonAnimFunctions[])(struct Sprite *) = { WaitAnimEnd };
static void DestroyTask(u8 id) { assert(id == 0); ++destroyed; }
''' + text[start:end] + r'''
static void RunFinished(void)
{
    gTasks[0] = (struct Task){.tState=1, .tBattlerId=3, .tSpeciesId=25};
    testSprite.callback = SpriteCallbackDummy;
    testSprite.data[1] = 1;
    destroyed = 0;
    Task_HandleMonAnimation(0);
    assert(destroyed == 1);
    assert(testSprite.data[0] == 3 && testSprite.data[1] == 0 && testSprite.data[2] == 25);
}
int main(void)
{
    /* Evolution: the animation starts and finishes with no battle allocation. */
    gBattleStruct = NULL;
    testSprite.oam.paletteNum = 3;
    testSprite.data[2] = 25;
    Task_HandleMonAnimation(0);
    assert(gTasks[0].tState == 1 && destroyed == 0);
    assert(testSprite.callback == WaitAnimEnd);
    RunFinished();
    /* Ordinary battle animations and KO accounting retain their behavior. */
    gBattleStruct = &battle;
    battle.battlerKOAnimsRunning = 0;
    RunFinished();
    assert(battle.battlerKOAnimsRunning == 0);
    battle.battlerKOAnimsRunning = 2;
    RunFinished();
    assert(battle.battlerKOAnimsRunning == 1);
    return 0;
}
'''
    pokemon = (args.tree / 'src/pokemon.c').read_text()
    start = pokemon.index('static struct PartyState *GetBattlerPartyStateByPokemon(')
    end = pokemon.index('\nbool32 TryFormChange(', start)
    form_source = r'''
#include <assert.h>
#include <stddef.h>
#define PLATFORM_3DS 1
#define PARTY_SIZE 6
enum BattleTrainer { PLAYER, OPPONENT };
struct Pokemon { int species; };
struct PartyState { int changedSpecies; };
static struct Pokemon parties[2][PARTY_SIZE];
static struct { struct PartyState partyState[2][PARTY_SIZE]; } battle, *gBattleStruct;
static struct Pokemon *GetTrainerParty(enum BattleTrainer trainer)
{ return parties[trainer]; }
''' + pokemon[start:end] + r'''
int main(void)
{
    struct Pokemon unrelated;
    for (int trainer = PLAYER; trainer <= OPPONENT; ++trainer)
        for (int i = 0; i < PARTY_SIZE; ++i)
        {
            gBattleStruct = NULL;
            assert(GetBattlerPartyStateByPokemon(&parties[trainer][i], trainer) == NULL);
            gBattleStruct = &battle;
            assert(GetBattlerPartyStateByPokemon(&parties[trainer][i], trainer)
                   == &battle.partyState[trainer][i]);
        }
    assert(GetBattlerPartyStateByPokemon(&unrelated, PLAYER) == NULL);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        for name, code in [('animation', source), ('form_change', form_source)]:
            (path / (name + '.c')).write_text(code)
            subprocess.run([args.cc, '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-g', str(path / (name + '.c')),
                            '-o', str(path / name)], check=True)
            subprocess.run([str(path / name)], check=True)
    print('PASS: evolution, battle KO accounting, overworld and battle form state')


if __name__ == '__main__':
    main()
