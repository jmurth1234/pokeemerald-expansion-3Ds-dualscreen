#!/usr/bin/env python3
"""What every cell of a layout is, for the voxel generators.

The cartridge answers most of it itself: the behaviour byte says water,
ledges and doors; the blockdata says what is blocked and at which elevation;
the maps' own events say where the doors (warps) and the signs are, and the
map type says whether it is outdoors. The drawing is only asked one thing the
data does not state: whether a blocked cell is drawn as foliage.

Roles, in the order they are decided:

    water     a water behaviour
    ledge     a jump behaviour
    stair     walkable, between walkable cells of two different elevations
    floor     any other walkable cell
    signpost  an outdoor sign event on a blocked cell open on three sides
    wall      part of a building: the blocked mass above a house door
    tree      blocked and drawn mostly in foliage
    fence     blocked, one cell thin, walkable on both sides
    cliff     blocked terrain touching walkable ground
    shelf     blocked terrain away from walkable ground
    prop      nothing of the above

    python voxel_cells.py LAYOUT_ROUTE104      prints one layout's roles
"""

import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voxel_art  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NUM_PRIMARY = 512
OUTDOOR_MAP_TYPES = {"MAP_TYPE_ROUTE", "MAP_TYPE_TOWN", "MAP_TYPE_UNDERWATER",
                     "MAP_TYPE_CITY", "MAP_TYPE_OCEAN_ROUTE"}
NEIGHBOURS = [(0, -1), (0, 1), (-1, 0), (1, 0)]
# A house is at most this many cells either side of its door and this tall.
HOUSE_HALF_WIDTH = 5
HOUSE_HEIGHT = 7
FOLIAGE = 0.5


def behaviours():
    """Metatile behaviour values by name, counted off the enum in
    include/constants/metatile_behaviors.h."""
    text = open(os.path.join(ROOT, "include", "constants", "metatile_behaviors.h"),
                encoding="utf-8").read()
    body = text[text.index("{") + 1:text.index("}")]
    values, value = {}, 0
    for item in body.split(","):
        item = re.sub(r"//.*", "", item).strip()
        if not item:
            continue
        m = re.match(r"(\w+)\s*(?:=\s*(\w+))?", item)
        if m.group(2):
            value = int(m.group(2), 0)
        values[m.group(1)] = value
        value += 1
    return values


MB = behaviours()
WATER = {v for k, v in MB.items() if any(s in k for s in (
    "WATER", "POND", "OCEAN", "DEEP", "WATERFALL", "SOOTOPOLIS_DEEP", "SEAWEED", "CURRENT",
    "PUDDLE", "SHALLOW"))} - {MB.get(k) for k in ("MB_WATER_DOOR",) if k in MB}
JUMPS = {v for k, v in MB.items() if k.startswith("MB_JUMP_")}
HOUSE_DOORS = {v for k, v in MB.items() if k in ("MB_ANIMATED_DOOR", "MB_PETALBURG_GYM_DOOR",
                                                   "MB_CLOSED_SOOTOPOLIS_DOOR")}


def _incbin(pattern):
    text = open(os.path.join(ROOT, "src", "data", "tilesets", "metatiles.h"),
                encoding="utf-8").read()
    return {"gTileset_" + n: os.path.join(ROOT, p) for n, p in re.findall(pattern, text)}


ATTRIBUTES = _incbin(r"gMetatileAttributes_(\w+)\[\]\s*=\s*INCBIN_U16\(\"([^\"]+)\"\)")


def read_u16(path):
    raw = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(raw) // 2), raw[:len(raw) // 2 * 2]))


class MapEvents:
    """Per layout: outdoors or not, warp cells and sign cells, from every map
    that uses the layout."""

    def __init__(self):
        self.outdoor, self.warps, self.signs = set(), {}, {}
        maps_dir = os.path.join(ROOT, "data", "maps")
        for name in sorted(os.listdir(maps_dir)):
            path = os.path.join(maps_dir, name, "map.json")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                m = json.load(f)
            layout = m.get("layout")
            if not layout:
                continue
            if m.get("map_type") in OUTDOOR_MAP_TYPES:
                self.outdoor.add(layout)
            for w in m.get("warp_events") or []:
                self.warps.setdefault(layout, set()).add((w["x"], w["y"]))
            for b in m.get("bg_events") or []:
                if b.get("type") == "sign":
                    self.signs.setdefault(layout, set()).add((b["x"], b["y"]))


_PAIRS = {}


def pair_for(primary, secondary, num_primary=NUM_PRIMARY):
    key = (primary, secondary, num_primary)
    if key not in _PAIRS:
        _PAIRS[key] = voxel_art.Pair(primary, secondary, num_primary)
    return _PAIRS[key]


class Layout:
    """One layout and the questions the voxel generators ask of it."""

    def __init__(self, entry, events):
        self.layout_id = entry["id"]
        self.w, self.h = entry["width"], entry["height"]
        self.blocks = read_u16(os.path.join(ROOT, entry["blockdata_filepath"]))
        self.primary = entry["primary_tileset"]
        self.secondary = entry["secondary_tileset"]
        self.attr = (read_u16(ATTRIBUTES[self.primary]) if self.primary in ATTRIBUTES else [],
                     read_u16(ATTRIBUTES[self.secondary]) if self.secondary in ATTRIBUTES else [])
        # The expansion's FRLG layouts split primary/secondary metatiles at
        # 640 instead of Emerald's 512; the layout version says which.
        self.num_primary = 640 if entry.get("layout_version") == "frlg" else NUM_PRIMARY
        self.outdoor = self.layout_id in events.outdoor
        self.warps = sorted(events.warps.get(self.layout_id, ()))
        self.signs = events.signs.get(self.layout_id, set())
        self._memo = {}
        self._houses = None
        self._foliage = {}

    # ── what the cartridge states ─────────────────────────────────────────

    def off_map(self, x, y):
        return x < 0 or y < 0 or x >= self.w or y >= self.h

    def metatile(self, x, y):
        return None if self.off_map(x, y) else self.blocks[y * self.w + x] & 0x3FF

    def blocked(self, x, y):
        return False if self.off_map(x, y) else ((self.blocks[y * self.w + x] >> 10) & 3) != 0

    def elevation(self, x, y):
        return 0 if self.off_map(x, y) else (self.blocks[y * self.w + x] >> 12) & 0xF

    def attributes(self, metatile):
        if metatile is None:
            return 0
        which = 0 if metatile < self.num_primary else 1
        table = self.attr[which]
        index = metatile - which * self.num_primary
        return table[index] if index < len(table) else 0

    def behaviour(self, x, y):
        return self.attributes(self.metatile(x, y)) & 0xFF

    def touches_walkable(self, x, y):
        return any(not self.off_map(x + dx, y + dy) and not self.blocked(x + dx, y + dy)
                   for dx, dy in NEIGHBOURS)

    # ── what the drawing is asked ─────────────────────────────────────────

    def foliage(self, metatile):
        """Share of a metatile's drawn pixels that are leaves."""
        if metatile not in self._foliage:
            pair = pair_for(self.primary, self.secondary, self.num_primary)
            drawn = dict(pair.layer_pixels(metatile, 0))
            drawn.update(pair.layer_pixels(metatile, 1))
            green = sum(1 for r, g, b in drawn.values() if g > 64 and g > r + 16 and g > b + 16)
            self._foliage[metatile] = green / len(drawn) if drawn else 0.0
        return self._foliage[metatile]

    # ── buildings: the mass above each house door ──────────────────────────

    def houses(self):
        if self._houses is None:
            cells = set()
            for dx, dy in self.warps:
                if self.behaviour(dx, dy) not in HOUSE_DOORS:
                    continue
                seed = (dx, dy - 1)
                if self.off_map(*seed) or not self.blocked(*seed):
                    continue
                stack, seen = [seed], {seed}
                while stack:
                    x, y = stack.pop()
                    cells.add((x, y))
                    for ox, oy in NEIGHBOURS:
                        nx, ny = x + ox, y + oy
                        if ((nx, ny) in seen or self.off_map(nx, ny) or not self.blocked(nx, ny)
                                or abs(nx - dx) > HOUSE_HALF_WIDTH or ny > dy or ny < dy - HOUSE_HEIGHT
                                or self.foliage(self.metatile(nx, ny)) >= FOLIAGE):
                            continue
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            self._houses = cells
        return self._houses

    # ── the roles ─────────────────────────────────────────────────────────

    def is_signpost(self, x, y):
        if not self.outdoor or (x, y) not in self.signs or not self.blocked(x, y):
            return False
        return all(not self.blocked(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1)))

    def is_ledge_junction(self, x, y):
        """A blocked cell where a ledge turns a corner: ledges on two sides
        at right angles."""
        if not self.blocked(x, y):
            return False
        horizontal = any(self.behaviour(x + dx, y) in JUMPS for dx in (-1, 1))
        vertical = any(self.behaviour(x, y + dy) in JUMPS for dy in (-1, 1))
        return horizontal and vertical

    def role_at(self, x, y):
        hit = self._memo.get((x, y))
        if hit is not None:
            return hit
        behaviour = self.behaviour(x, y)
        if behaviour in WATER:
            role = "water"
        elif behaviour in JUMPS:
            role = "ledge"
        elif not self.blocked(x, y):
            here = self.elevation(x, y)
            role = "floor"
            for dy in (-1, 1):
                if (not self.off_map(x, y + dy) and not self.blocked(x, y + dy)
                        and self.elevation(x, y + dy) not in (here, 0, 15) and here not in (0, 15)):
                    role = "stair"
        elif self.is_signpost(x, y):
            role = "signpost"
        elif (x, y) in self.houses():
            role = "wall"
        elif self.foliage(self.metatile(x, y)) >= FOLIAGE:
            role = "tree"
        elif ((not self.blocked(x, y - 1) and not self.blocked(x, y + 1)
               and not self.off_map(x, y - 1) and not self.off_map(x, y + 1))
              or (not self.blocked(x - 1, y) and not self.blocked(x + 1, y)
                  and not self.off_map(x - 1, y) and not self.off_map(x + 1, y))):
            role = "fence"
        elif self.touches_walkable(x, y):
            role = "cliff"
        else:
            role = "shelf"
        self._memo[(x, y)] = role
        return role


def load_layouts():
    with open(os.path.join(ROOT, "data", "layouts", "layouts.json"), encoding="utf-8") as f:
        return json.load(f)["layouts"]


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else "LAYOUT_LITTLEROOT_TOWN"
    entry = next(e for e in load_layouts() if e.get("id") == want)
    layout = Layout(entry, MapEvents())
    letters = {"water": "~", "ledge": "_", "stair": "=", "floor": ".", "signpost": "S", "wall": "W",
               "tree": "T", "fence": "|", "cliff": "#", "shelf": "%", "prop": "o"}
    for y in range(layout.h):
        print("".join(letters[layout.role_at(x, y)] for x in range(layout.w)))


if __name__ == "__main__":
    main()
