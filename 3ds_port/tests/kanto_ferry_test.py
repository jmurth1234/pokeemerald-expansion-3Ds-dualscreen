#!/usr/bin/env python3
"""Host checks for the bounded Kanto ferry visit, using production scripts/maps."""
import argparse
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile


def check_fly_guard(tree, group):
    source = (tree / 'src/party_menu.c').read_text()
    body = source[source.index('bool32 SetUpFieldMove_Fly(void)\n{'):
                  source.index('\nvoid CB2_ReturnToPartyMenuFromFlyMap(void)')]
    harness = f'''
#include <assert.h>
typedef int bool32;
#define TRUE 1
#define FALSE 0
#define FOLLOWER_NPC_FLAG_CAN_LEAVE_ROUTE 1
#define MAP_GROUP(x) ((x) >> 8)
#define MAP_KANTO_VISIT_VERMILION ({group} << 8)
static struct {{ struct {{ int mapGroup; }} location; }} save, *gSaveBlock1Ptr = &save;
static struct {{ int mapType; }} gMapHeader;
static int followerCanLeave = 1;
static int CheckFollowerNPCFlag(int flag) {{ return followerCanLeave; }}
static int Overworld_MapTypeAllowsTeleportAndFly(int type) {{ return type == 1; }}
''' + body + f'''
int main(void)
{{
    gMapHeader.mapType = 1;
    save.location.mapGroup = {group};
    assert(!SetUpFieldMove_Fly());
    save.location.mapGroup = 0;
    assert(SetUpFieldMove_Fly());
    followerCanLeave = 0;
    assert(!SetUpFieldMove_Fly());
    followerCanLeave = 1;
    gMapHeader.mapType = 0;
    assert(!SetUpFieldMove_Fly());
    return 0;
}}
'''
    with tempfile.TemporaryDirectory(prefix='kanto-fly-') as directory:
        p = Path(directory)
        (p / 'test.c').write_text(harness)
        subprocess.run(['cc', '-std=c99', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
        subprocess.run([str(p / 'test')], check=True)


def check_linked_maps(image, group_index, names):
    data = image.read_bytes()
    assert data[:6] == b'\x7fELF\x01\x01', 'Expected little-endian ELF32'
    offset = struct.unpack_from('<I', data, 32)[0]
    stride, count = struct.unpack_from('<HH', data, 46)
    sections = [struct.unpack_from('<10I', data, offset + i * stride) for i in range(count)]

    def contents(s):
        return data[s[4]:s[4] + s[5]]

    symbols = {}
    for s in sections:
        if s[1] != 2:  # SHT_SYMTAB
            continue
        strings = contents(sections[s[6]])
        for pos in range(s[4], s[4] + s[5], s[9]):
            name, value = struct.unpack_from('<II', data, pos)
            symbols[strings[name:strings.index(b'\0', name)].decode()] = value

    def read(address, size):
        for s in sections:
            if s[1] != 8 and s[3] <= address and address + size <= s[3] + s[5]:
                return contents(s)[address - s[3]:address - s[3] + size]
        raise AssertionError(f'No file-backed data at {address:#x}')

    def ptr(address):
        return struct.unpack('<I', read(address, 4))[0]

    group = ptr(symbols['gMapGroups'] + group_index * 4)
    assert group, 'Visitor map group was excluded by mapjson'
    for i, name in enumerate(names):
        header = ptr(group + i * 4)
        assert header == symbols[name]
        layout, events, scripts, connections = struct.unpack('<IIII', read(header, 16))
        assert events == symbols[name + '_MapEvents']
        assert scripts == symbols[name + '_MapScripts'] and not connections
        layout_id = struct.unpack('<H', read(header + 18, 2))[0]
        assert ptr(symbols['gMapLayouts'] + (layout_id - 1) * 4) == layout
        assert read(layout + 24, 1) == b'\x01', 'FRLG tile format must be retained'
        for tileset_offset in (16, 20):
            tileset = ptr(layout + tileset_offset)
            assert tileset
            # Graphics, palettes, metatiles and attributes all need real pointers.
            assert all(ptr(tileset + field) for field in (4, 8, 12, 16))
    print('PASS: linked visitor map group, headers, scripts, FRLG layouts and tilesets')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tree', type=Path, required=True)
    p.add_argument('--image', type=Path)
    args = p.parse_args()
    r = args.tree
    names = ['KantoVisit_Vermilion', 'KantoVisit_PokemonCenter', 'KantoVisit_Mart']
    maps = {n: json.loads((r / f'data/maps/{n}/map.json').read_text()) for n in names}
    by_id = {m['id']: m for m in maps.values()}
    source = (r / 'data/scripts/kanto_ferry.inc').read_text()
    source += '\n' + '\n'.join((r / f'data/maps/{n}/scripts.inc').read_text() for n in names)
    instructions, labels = [], {}
    for raw in source.splitlines():
        line = raw.split('@')[0].strip()
        if not line or line.startswith('.'):
            continue
        if line.endswith(':'):
            label = line.rstrip(':')
            assert label not in labels, label
            labels[label] = len(instructions)
        else:
            op, _, arguments = line.partition(' ')
            instructions.append((op, [x.strip() for x in arguments.split(',')]))

    class Game:
        def __init__(self, champion=False):
            self.flags = {'FLAG_SYS_GAME_CLEAR'} if champion else set()
            self.vars = {}
            self.warp = None
            self.original_menu = False
            self.intros = 0
            self.healed = False

        def run(self, entry, yes=True):
            pc, stack = labels[entry], []
            for _ in range(500):
                op, a = instructions[pc]
                pc += 1
                if op == 'end':
                    break
                if op == 'return':
                    if not stack:
                        break
                    pc = stack.pop()
                elif op in ('goto_if_set', 'goto_if_unset', 'call_if_unset'):
                    cond = (a[0] in self.flags) == (op == 'goto_if_set')
                    if cond:
                        if op.startswith('call'):
                            stack.append(pc)
                        pc = labels[a[1]]
                elif op in ('goto', 'call', 'goto_if_eq'):
                    if op == 'goto_if_eq' and self.vars.get(a[0]) != {'NO': 0, 'YES': 1}.get(a[1], a[1]):
                        continue
                    target = a[-1]
                    if target == 'SlateportCity_Harbor_EventScript_AskForTicket':
                        self.original_menu = True
                        break
                    if target == 'SlateportCity_Harbor_EventScript_BoardFerry':
                        continue  # Existing movement/cutscene, no visitor progression state.
                    if op == 'call':
                        stack.append(pc)
                    pc = labels[target]
                elif op == 'setvar':
                    self.vars[a[0]] = int(a[1])
                elif op == 'setflag':
                    self.flags.add(a[0])
                elif op == 'msgbox':
                    self.intros += a[0] == 'KantoVisit_Text_Welcome'
                    if a[-1] == 'MSGBOX_YESNO':
                        self.vars['VAR_RESULT'] = int(yes)
                elif op == 'warp':
                    self.warp = (a[0], int(a[1]), int(a[2]))
                elif op == 'special':
                    assert a[0] == 'HealPlayerParty'
                    self.healed = True
                elif op in ('lock', 'lockall', 'faceplayer', 'releaseall', 'waitstate',
                            'closemessage', 'playse', 'fadescreen', 'applymovement',
                            'waitmovement', 'playfanfare', 'waitfanfare'):
                    pass
                else:
                    raise AssertionError(('Unsupported operation', op))
            else:
                raise AssertionError('Non-terminating script')

    g = Game()
    g.run('KantoFerry_Offer')
    assert g.warp is None
    g = Game(True)
    g.run('KantoFerry_Offer', yes=False)
    assert g.warp is None and g.original_menu
    g.run('KantoFerry_Offer')
    assert g.warp == ('MAP_KANTO_VISIT_VERMILION', 23, 32)
    g.run('KantoVisit_OnTransition')
    assert g.vars['VAR_TEMP_1'] == 1
    g.run('KantoVisit_Introduction')
    g.run('KantoVisit_OnTransition')
    assert g.vars['VAR_TEMP_1'] == 0 and g.intros == 1
    # Intro persists through save/reload; returning to the map cannot replay it.
    restored = Game(True)
    restored.flags = set(g.flags)
    restored.run('KantoVisit_OnTransition')
    assert restored.vars['VAR_TEMP_1'] == 0
    for entry in ('KantoFerry_Return', 'KantoFerry_DockReturn'):
        g = Game()  # No ticket, cash, badge, or intro prerequisite for getting home.
        g.run(entry, yes=False)
        assert g.warp is None
        g.run(entry)
        assert g.warp == ('MAP_SLATEPORT_CITY_HARBOR', 8, 11)
    g = Game()
    g.run('KantoVisit_Nurse', yes=False)
    assert not g.healed
    g.run('KantoVisit_Nurse')
    assert g.healed

    # Visitor area contains no original Kanto scripts, flags, battles or exit routes.
    for m in maps.values():
        assert m['include_in_emerald'] is True
        assert not m['connections'] and not m['allow_escaping']
        for o in m['object_events']:
            assert o['script'] in labels and o['flag'] == '0'
            assert o['trainer_type'] == 'TRAINER_TYPE_NONE'
        for e in m['coord_events'] + m['bg_events']:
            assert e['script'] in labels
        for w in m['warp_events']:
            target = by_id[w['dest_map']]
            arrival = target['warp_events'][int(w['dest_warp_id'])]
            assert arrival['dest_map'] == m['id'], (m['name'], w)
    assert set(re.findall(r'\bFLAG_\w+', source)) == {'FLAG_SYS_GAME_CLEAR', 'FLAG_KANTO_FERRY_INTRO_SEEN'}
    assert not re.search(r'\b(setrespawn|trainerbattle|setworldmapflag)\b', source)
    groups = json.loads((r / 'data/maps/map_groups.json').read_text())
    assert groups['group_order'][-1] == 'gMapGroup_KantoVisit'
    assert groups['gMapGroup_KantoVisit'] == names
    # Both ferry landing spots are passable and unoccupied.
    layouts = {x['id']: x for x in json.loads((r / 'data/layouts/layouts.json').read_text())['layouts']}
    for name, x, y in [('KantoVisit_Vermilion', 23, 32), ('SlateportCity_Harbor', 8, 11)]:
        m = json.loads((r / f'data/maps/{name}/map.json').read_text())
        layout = layouts[m['layout']]
        block = struct.unpack_from('<H', (r / layout['blockdata_filepath']).read_bytes(), 2*(y*layout['width']+x))[0]
        assert (block >> 10) & 3 == 0
        assert all((o['x'], o['y']) != (x, y) for o in m['object_events'])
    harbour = (r / 'data/maps/SlateportCity_Harbor/scripts.inc').read_text()
    assert 'goto_if_set FLAG_SYS_GAME_CLEAR, KantoFerry_Offer' in harbour
    check_fly_guard(r, groups['group_order'].index('gMapGroup_KantoVisit'))
    print('PASS: Champion gate, Hoenn menu, ferry round trip/cancel, intro persistence, healing, map isolation and landing tiles')
    if args.image:
        check_linked_maps(args.image, groups['group_order'].index('gMapGroup_KantoVisit'), names)


if __name__ == '__main__':
    main()
