#!/usr/bin/env python3
"""Reject null slots in the sprite animation tables of the linked game data.

Read gamedata_image.elf, whose PROGBITS section contains the pointers. The
shipped ELF reserves that region as NOBITS and cannot be used for this audit.
Includes enum-designated holes, which a source search for NULL would miss.
"""
import argparse
from pathlib import Path
import re
import struct


def audit(tree, image):
    arrays = set()
    templates = set()
    for path in (tree / 'src').rglob('*'):
        if path.suffix not in ('.c', '.h'):
            continue
        source = path.read_text(errors='replace')
        arrays.update(re.findall(
            r'union\s+(?:AffineAnimCmd|AnimCmd)\s*\*\s*(?:const\s+)?'
            r'(\w+)\s*\[[^\]\n]*\]\s*=\s*\{', source))
        templates.update(re.findall(
            r'const\s+struct\s+SpriteTemplate\s+(\w+)\s*'
            r'(?:\[[^\]\n]*\])?\s*=\s*\{', source))

    data = image.read_bytes()
    if data[:6] != b'\x7fELF\x01\x01':
        raise SystemExit('Expected a little-endian ELF32 image')
    offset = struct.unpack_from('<I', data, 32)[0]
    stride, count, names_index = struct.unpack_from('<HHH', data, 46)
    sections = [struct.unpack_from('<10I', data, offset + i * stride) for i in range(count)]

    def contents(section):
        return data[section[4]:section[4] + section[5]]

    def string(strings, index):
        return strings[index:strings.index(b'\0', index)].decode()

    section_names = contents(sections[names_index])
    payload = next(s for s in sections if string(section_names, s[0]) == '.gamedata')
    if payload[1] != 1:
        raise SystemExit('Use gamedata_image.elf: .gamedata must contain PROGBITS')
    ranges = [(s[3], s[3] + s[5]) for s in sections if s[2] & 2 and s[1] == 1]

    def readable(address):
        return any(low <= address and address + 4 <= high for low, high in ranges)

    tables = slots = 0
    problems = []
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        strings = contents(sections[section[6]])
        for off in range(section[4], section[4] + section[5], section[9]):
            name_id, address, size, info, _, index = struct.unpack_from('<IIIBBH', data, off)
            name = string(strings, name_id)
            if info & 15 != 1 or name not in arrays or not size:  # STT_OBJECT
                continue
            target = sections[index]
            if target[1] != 1:
                problems.append(f'{name}: animation table has no linked contents')
                continue
            values = struct.unpack_from('<' + 'I' * (size // 4), data,
                                        target[4] + address - target[3])
            # A local SpriteTemplate in another translation unit shares the
            # name of party_menu.h's animation table. Its packed tags are not
            # pointers; do not interpret that distinct object as an array.
            if name in templates and any(v and not readable(v) for v in values):
                continue
            tables += 1
            slots += len(values)
            for i, value in enumerate(values):
                if not value or not readable(value):
                    problems.append(f'{name}[{i}]: invalid animation pointer {value:#x}')
    if not tables:
        raise SystemExit('No sprite animation tables found')
    if problems:
        raise SystemExit('\n'.join(problems))
    print(f'PASS: {tables} linked sprite animation tables, {slots} valid animation pointers')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    args = parser.parse_args()
    audit(args.tree, args.image)


if __name__ == '__main__':
    main()
