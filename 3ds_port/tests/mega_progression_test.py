#!/usr/bin/env python3
"""Execute the production reward script's control flow with a bounded bag model.

This checks progression/retry semantics, not the game's rendering or script VM.
Run against a bootstrapped expansion tree, including a freshly patched checkout.
"""
import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess
import tempfile


def check_battle_eligibility(tree):
    """Run the actual game's eligibility function with controlled battle state."""
    util = (tree / 'src/battle_util.c').read_text()
    function = util[util.index('bool32 CanMegaEvolve('):util.index('\nbool32 CanUltraBurst(')]
    harness = r'''
#include <assert.h>
typedef int bool32;
#define TRUE 1
#define FALSE 0
#define TESTING 0
#define ITEM_MEGA_RING 1
#define BATTLE_TYPE_MULTI 1
#define GIMMICK_MEGA 1
#define GIMMICK_NONE 0
#define STATE_SKY_DROP_TARGET 1
#define FORM_CHANGE_BATTLE_MEGA_EVOLUTION_ITEM 1
#define FORM_CHANGE_BATTLE_MEGA_EVOLUTION_MOVE 2
enum BattlerId { PLAYER, FOE };
enum BattlerPosition { B_POSITION_PLAYER_LEFT, B_POSITION_OPPONENT_LEFT, B_POSITION_PLAYER_RIGHT };
enum HoldEffect { HOLD_EFFECT_NONE, HOLD_EFFECT_Z_CRYSTAL };
enum Ability { ABILITY_NONE };
static int ring, used, active, matchingStone, gBattleTypeFlags;
static enum HoldEffect held;
static struct { int species; struct { int semiInvulnerable; } volatiles; } gBattleMons[2];
static enum HoldEffect GetBattlerHoldEffectIgnoreNegation(enum BattlerId b) { return held; }
static enum BattlerPosition GetBattlerPosition(enum BattlerId b) { return b == PLAYER ? B_POSITION_PLAYER_LEFT : B_POSITION_OPPONENT_LEFT; }
static int CheckBagHasItem(int item, int n) { return ring; }
static int HasTrainerUsedGimmick(enum BattlerId b, int g) { return used; }
static int GetActiveGimmick(enum BattlerId b) { return active; }
static enum Ability GetBattlerAbility(enum BattlerId b) { return ABILITY_NONE; }
static int GetBattleFormChangeTargetSpecies(enum BattlerId b, int method, enum Ability a)
{ return gBattleMons[b].species + (matchingStone && method == FORM_CHANGE_BATTLE_MEGA_EVOLUTION_ITEM); }
''' + function + r'''
int main(void)
{
    matchingStone = 1;
    assert(!CanMegaEvolve(PLAYER)); /* Stone alone cannot bypass ring reward. */
    assert(CanMegaEvolve(FOE));     /* Boss needs no player bag item. */
    ring = 1;
    assert(CanMegaEvolve(PLAYER));
    matchingStone = 0;
    assert(!CanMegaEvolve(PLAYER) && !CanMegaEvolve(FOE));
    matchingStone = 1;
    used = 1;
    assert(!CanMegaEvolve(PLAYER) && !CanMegaEvolve(FOE));
    used = 0;
    active = 1;
    assert(!CanMegaEvolve(PLAYER));
    active = 0;
    gBattleMons[PLAYER].volatiles.semiInvulnerable = STATE_SKY_DROP_TARGET;
    assert(!CanMegaEvolve(PLAYER));
    gBattleMons[PLAYER].volatiles.semiInvulnerable = 0;
    held = HOLD_EFFECT_Z_CRYSTAL;
    assert(!CanMegaEvolve(PLAYER));
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='mega-eligibility-') as directory:
        path = Path(directory)
        (path / 'check.c').write_text(harness)
        subprocess.run(['cc', '-std=c99', str(path / 'check.c'), '-o', str(path / 'check')], check=True)
        subprocess.run([str(path / 'check')], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tree', type=Path, required=True)
    tree = parser.parse_args().tree
    source = (tree / 'data/scripts/mega_progression.inc').read_text()
    code, labels = [], {}
    for raw in source.splitlines():
        line = raw.split('@')[0].strip()
        if not line or line.startswith('.'):
            continue
        if line.endswith(':'):
            labels[line.rstrip(':')] = len(code)
        else:
            op, _, args = line.partition(' ')
            code.append((op, [x.strip() for x in args.split(',')]))

    class Game:
        def __init__(self, badges=0, starter=0, blocked=()):
            self.flags = {f'FLAG_BADGE0{n}_GET' for n in range(1, badges + 1)}
            self.bag = Counter()
            self.vars = {'VAR_STARTER_MON': starter, 'VAR_0x8008': 2}
            self.blocked = set(blocked)
            self.shop = False

        def value(self, x):
            return self.vars.get(x, {'TRUE': 1, 'FALSE': 0}.get(x, int(x) if x.isdigit() else x))

        def run(self, entry):
            pc, stack = labels['MegaProgression_EventScript_' + entry], []
            for _ in range(2000):
                op, a = code[pc]
                pc += 1
                if op in ('end', 'release'):
                    if op == 'end':
                        break
                elif op == 'return':
                    if not stack:
                        break
                    pc = stack.pop()
                elif op == 'msgbox':
                    pass
                elif op == 'pokemart':
                    self.shop = True
                elif op == 'setvar':
                    self.vars[a[0]] = self.value(a[1])
                elif op == 'setflag':
                    self.flags.add(a[0])
                elif op == 'checkitem':
                    self.vars['VAR_RESULT'] = int(self.bag[a[0]] > 0)
                elif op == 'giveitem':
                    item = self.value(a[0])
                    success = item not in self.blocked
                    self.vars['VAR_RESULT'] = int(success)
                    self.bag[item] += int(success)
                elif op == 'call':
                    stack.append(pc)
                    pc = labels[a[0]]
                elif op in ('goto_if_set', 'goto_if_unset'):
                    if (a[0] in self.flags) == (op == 'goto_if_set'):
                        pc = labels[a[1]]
                elif op in ('goto_if_eq', 'call_if_eq'):
                    if self.value(a[0]) == self.value(a[1]):
                        if op == 'call_if_eq':
                            stack.append(pc)
                        pc = labels[a[2]]
                else:
                    raise AssertionError(('Unsupported script operation', op))
            else:
                raise AssertionError('Script did not terminate')

    for starter, stone in enumerate(('SCEPTILITE', 'BLAZIKENITE', 'SWAMPERTITE')):
        g = Game(4, starter)
        g.run('Starter')
        assert not g.bag
        g.flags.add('FLAG_BADGE05_GET')
        g.blocked = {'ITEM_MEGA_RING'}
        g.run('Starter')
        assert not +g.bag and 'FLAG_RECEIVED_MEGA_STARTER' not in g.flags
        g.blocked = {'ITEM_' + stone}
        g.run('Starter')
        assert g.bag['ITEM_MEGA_RING'] == 1
        assert 'FLAG_RECEIVED_MEGA_STARTER' not in g.flags
        g.blocked.clear()
        g.run('Starter')
        g.run('Starter')
        assert +g.bag == Counter({'ITEM_MEGA_RING': 1, 'ITEM_' + stone: 1})
        assert g.vars['VAR_0x8008'] == 2

    expected = {5: 2, 6: 5, 7: 8, 8: 12}
    for badges, count in expected.items():
        g = Game(badges)
        g.run('Devon')  # Existing saves need no Route 118 event history.
        assert sum(g.bag.values()) == count, (badges, g.bag)
        assert not g.shop
        g.run('Devon')
        assert sum(g.bag.values()) == count
        g.flags.add('FLAG_SYS_GAME_CLEAR')
        g.run('Devon')
        assert g.shop

    # Each optional source and Devon share the same successful-receipt flag.
    gifts = re.findall(r'MegaProgression_EventScript_(\w+)::\n\tgoto_if_unset FLAG_BADGE0[678]_GET', source)
    for gift in gifts:
        g = Game(8, blocked={'ITEM_' + gift})
        g.run(gift)
        assert 'FLAG_RECEIVED_MEGA_' + gift not in g.flags
        g.blocked.clear()
        g.run(gift)
        g.run('Devon')
        assert g.bag['ITEM_' + gift] == 1

    party = (tree / 'src/data/trainers.party').read_text()
    bosses = {'ARCHIE': 'Sharpedonite', 'MAXIE_MAGMA_HIDEOUT': 'Cameruptite',
              'MAXIE_MOSSDEEP': 'Cameruptite', 'SIDNEY': 'Absolite',
              'PHOEBE': 'Sablenite', 'GLACIA': 'Glalitite', 'DRAKE': 'Salamencite',
              'WALLACE': 'Gyaradosite', 'STEVEN': 'Metagrossite',
              **{f'WINONA_{n}': 'Altarianite' for n in range(1, 6)}}
    for boss, stone in bosses.items():
        body = re.search(rf'=== TRAINER_{boss} ===\n(.*?)(?=\n===|\Z)', party, re.S)[1]
        assert body.count('@ ' + stone) == 1
    early = re.search(r'=== TRAINER_MAXIE_MT_CHIMNEY ===\n(.*?)(?=\n===)', party, re.S)[1]
    assert 'Cameruptite' not in early
    items = (tree / 'src/data/items.h').read_text()
    stock = re.findall(r'\.2byte (ITEM_\w+)', source)
    assert len(stock) == len(set(stock))
    for item in stock:
        if item == 'ITEM_NONE':
            continue
        block = re.search(rf'\[{item}\] =\n    \{{(.*?)\n    \}},', items, re.S)[1]
        assert '.price = 20000,' in block and 'HOLD_EFFECT_MEGA_STONE' in block
    check_battle_eligibility(tree)
    print('Mega progression: starter choices, badge gates, full bags, retries, old saves, shared gifts, shop, bosses and battle eligibility passed')


if __name__ == '__main__':
    main()
