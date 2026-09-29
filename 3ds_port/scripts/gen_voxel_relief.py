#!/usr/bin/env python3
"""Terrain relief read off the drawing: cliffs, their stairs, rocks at sea.

The premise is the one voxel_building.py builds on: the GBA draws the world
in a 45-degree oblique projection, a point (X, Y, Z) of it landing on the art
at u = X, v = Z - Y. Turned round, a height h(u, v) for every point of the
art places that point at

    (u, h, v + h)

and for ANY choice of h the GBA view of the result is the drawing itself,
pixel for pixel. So terrain is not built of boxes: the art is laid as a sheet
and lifted, and the only question is how high each point of it is.

How high
--------
The cartridge does not say - a plateau and the beach below it share an
elevation - but the drawing does, because in an oblique view a cliff is only
ever seen from the front or from the side:

  * land splits into regions at the rock bands (roles `cliff` and `shelf`)
    and at the stairs through them;
  * a band with land to its north and land to its south, of different
    regions, is a south-facing face: the north region stands one level above
    the south one. Contradictions (a fence line between one wood) are simply
    not constraints;
  * water stands at the lowest land it touches, and a level below any land
    it only reaches across a rock band (a sea cliff facing west or east).

One level is one tile - the rock band is drawn one cell tall. Heights live on
a lattice every 4 pixels. Land and water fix it; the rock and stair cells in
between are solved harmonically, which makes a south face exactly vertical (h
falls one pixel per row, so v + h is constant down it), a west or east face a
45-degree slope (the diagonal striations the artist drew) and every corner of
the staircase a smooth turn - never a square block. A rock mass with no higher
land beside it (a rock at sea) is raised as a mound by its distance from the
water.

Ledges
------
Every outdoor map's ledges are lifted the same way, on maps whose cliffs
are not solved as well: the brown lip, told from the ground round it by its
colours, rises
from its foot on the jump side to LIP pixels at its top edge, and the ground
behind rises to meet it, a low berm facing where the jump goes. Corner cells
jump two ways and take the nearer foot; the junction pieces where a ledge
steps are ledge too; a lip comes down to the ground where the ledge ends.

    python gen_voxel_relief.py [--preview DIR] [--proof DIR] [--layouts A,B] [--output FILE]
"""

import argparse
import collections
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voxel_building as vb  # noqa: E402
import voxel_cells as vc  # noqa: E402

PORT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

LEVEL = 16          # pixels per level: the rock band is drawn one cell tall
STEP = 4            # lattice spacing in pixels
PER_CELL = 16 // STEP
MOUND = 8           # mound rise per lattice step inland from the water
MOUND_MAX = 24

# Maps the relief is solved for. Others keep the region extrusion until each
# has been looked at: a height model is a claim about a whole map.
ENABLED = ["LAYOUT_ROUTE104", "LAYOUT_RUSTBORO_CITY"]

RELIEF_ROLES = {"cliff", "shelf", "stair"}

# Ledges. The cartridge names each ledge cell's jump by its behaviour; the
# drawing gives its lip, the half of the cell on the jump side painted in the
# colours no ground round it has.
JUMPS = {0x38: ((1, 0),), 0x39: ((-1, 0),), 0x3A: ((0, -1),), 0x3B: ((0, 1),),
         0x3C: ((1, 0), (0, -1)), 0x3D: ((-1, 0), (0, -1)),
         0x3E: ((1, 0), (0, 1)), 0x3F: ((-1, 0), (0, 1))}
LIP = 6             # height of the lip's top edge, pixels
LIP_WIDTH = 8       # the lip is drawn half a cell deep: its foot to its top
LIP_BACK = 8        # the ground behind the lip rises to it over this many pixels


def solve(layout):
    """Per-cell level (None for relief cells) and the lattice heights."""
    W, H = layout.w, layout.h
    role = [[layout.role_at(x, y) for x in range(W)] for y in range(H)]
    kind = [["relief" if role[y][x] in RELIEF_ROLES else
             "water" if role[y][x] == "water" else "land" for x in range(W)] for y in range(H)]

    # land regions
    region = [[-1] * W for _ in range(H)]
    count = 0
    for y in range(H):
        for x in range(W):
            if kind[y][x] != "land" or region[y][x] >= 0:
                continue
            stack = [(x, y)]
            region[y][x] = count
            while stack:
                cx, cy = stack.pop()
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < W and 0 <= ny < H and kind[ny][nx] == "land" and region[ny][nx] < 0:
                        region[ny][nx] = count
                        stack.append((nx, ny))
            count += 1

    # south faces: land above a rock run and land below it
    edges = {}
    for x in range(W):
        y = 0
        while y < H:
            if kind[y][x] != "relief" or role[y][x] == "stair":
                y += 1
                continue
            y0 = y
            while y < H and kind[y][x] == "relief" and role[y][x] != "stair":
                y += 1
            if y0 > 0 and y < H and kind[y0 - 1][x] == "land" and kind[y][x] == "land":
                up, down = region[y0 - 1][x], region[y][x]
                if up != down:
                    edges[(up, down)] = edges.get((up, down), 0) + 1

    # levels: breadth first over "up is down + 1", first answer kept
    level = [None] * count
    adj = [[] for _ in range(count)]
    for (up, down), n in edges.items():
        adj[up].append((down, -1))
        adj[down].append((up, +1))
    for start in range(count):
        if level[start] is not None:
            continue
        level[start] = 0
        comp, queue = [start], [start]
        while queue:
            r = queue.pop()
            for (o, d) in adj[r]:
                if level[o] is None:
                    level[o] = level[r] + d
                    comp.append(o)
                    queue.append(o)
        low = min(level[r] for r in comp)
        for r in comp:
            level[r] -= low

    cell = [[None] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if kind[y][x] == "land":
                cell[y][x] = level[region[y][x]]

    # Level 0 is the land the map meets its neighbours with: a town beside
    # this route is drawn flat, so the seam must be too. Whatever lies lower
    # (a beach under its cliff) goes below the ground plane.
    edge = []
    for side in getattr(layout, "connected_sides", ()):
        cells = {"up": [(x, 0) for x in range(W)], "down": [(x, H - 1) for x in range(W)],
                 "left": [(0, y) for y in range(H)], "right": [(W - 1, y) for y in range(H)]}[side]
        edge += [cell[y][x] for (x, y) in cells if kind[y][x] == "land"]
    if edge:
        ref = max(set(edge), key=edge.count)
        for y in range(H):
            for x in range(W):
                if cell[y][x] is not None:
                    cell[y][x] -= ref

    # water: the lowest land it touches, body by body
    seen = set()
    for y in range(H):
        for x in range(W):
            if kind[y][x] != "water" or (x, y) in seen:
                continue
            body, stack, touch = [], [(x, y)], []
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                body.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if not (0 <= nx < W and 0 <= ny < H):
                        continue
                    if kind[ny][nx] == "water" and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
                    elif kind[ny][nx] == "land":
                        touch.append(cell[ny][nx])
                    elif kind[ny][nx] == "relief" and role[ny][nx] != "stair":
                        # across a rock band the land stands above the water
                        dx, dy = nx - cx, ny - cy
                        ax, ay = nx, ny
                        for _ in range(3):
                            ax, ay = ax + dx, ay + dy
                            if not (0 <= ax < W and 0 <= ay < H) or kind[ay][ax] == "water":
                                break
                            if kind[ay][ax] == "land":
                                touch.append(cell[ay][ax] - 1)
                                break
            lv = min(touch) if touch else 0
            for (cx, cy) in body:
                cell[cy][cx] = lv

    # Rock masses with one level all round are not terrain steps. Touching
    # water they are rocks at sea, raised as mounds below; on land they are a
    # fence line or a patch of soil the role table calls rock, and they stay
    # level with the ground round them for the structure pass to stand up.
    mounds = []
    mass_seen = set()
    for y in range(H):
        for x in range(W):
            if kind[y][x] != "relief" or role[y][x] == "stair" or (x, y) in mass_seen:
                continue
            mass, stack, around, wet = [], [(x, y)], set(), False
            mass_seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                mass.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if not (0 <= nx < W and 0 <= ny < H):
                        continue
                    if kind[ny][nx] == "relief" and role[ny][nx] != "stair":
                        if (nx, ny) not in mass_seen:
                            mass_seen.add((nx, ny))
                            stack.append((nx, ny))
                    elif cell[ny][nx] is not None:
                        around.add(cell[ny][nx])
                        wet = wet or kind[ny][nx] == "water"
            if len(around) > 1:
                continue
            if wet and not awash(layout, mass, kind):
                mounds.append(mass)
            else:
                lv = around.pop() if around else 0
                for (cx, cy) in mass:
                    cell[cy][cx] = lv

    # lattice: fixed where any non-relief cell touches, the highest of them
    LW, LH = W * PER_CELL + 1, H * PER_CELL + 1
    h = [[0.0] * LW for _ in range(LH)]
    fixed = [[False] * LW for _ in range(LH)]
    for j in range(LH):
        for i in range(LW):
            touching = []
            xs = (i // PER_CELL - 1, i // PER_CELL) if i % PER_CELL == 0 else (i // PER_CELL,)
            ys = (j // PER_CELL - 1, j // PER_CELL) if j % PER_CELL == 0 else (j // PER_CELL,)
            for cx in xs:
                for cy in ys:
                    if 0 <= cx < W and 0 <= cy < H and cell[cy][cx] is not None:
                        touching.append(cell[cy][cx])
            if touching:
                h[j][i] = max(touching) * LEVEL
                fixed[j][i] = True
    # harmonic fill of the relief (Gauss-Seidel, warm-started at the mean)
    free = [(i, j) for j in range(LH) for i in range(LW) if not fixed[j][i]]
    for _ in range(400):
        delta = 0.0
        for (i, j) in free:
            s, n = 0.0, 0
            for (a, b) in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if 0 <= a < LW and 0 <= b < LH:
                    s += h[b][a]
                    n += 1
            v = s / n
            delta = max(delta, abs(v - h[j][i]))
            h[j][i] = v
        if delta < 0.01:
            break

    # mounds: rocks at sea, raised by their distance from the water
    for mass in mounds:
        inside = set()
        for (cx, cy) in mass:
            for j in range(cy * PER_CELL, cy * PER_CELL + PER_CELL + 1):
                for i in range(cx * PER_CELL, cx * PER_CELL + PER_CELL + 1):
                    if not fixed[j][i]:
                        inside.add((i, j))
        # distance (in lattice steps) to the nearest fixed point
        dist = {}
        frontier = [p for p in inside if any(
            0 <= a < LW and 0 <= b < LH and fixed[b][a]
            for (a, b) in ((p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1)))]
        for p in frontier:
            dist[p] = 1
        k = 0
        while k < len(frontier):
            p = frontier[k]
            k += 1
            for q in ((p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1)):
                if q in inside and q not in dist:
                    dist[q] = dist[p] + 1
                    frontier.append(q)
        for p, d in dist.items():
            h[p[1]][p[0]] += min(MOUND_MAX, d * MOUND)
    return role, cell, h


_ART = {}


# ── mountains read off the drawing ──────────────────────────────────────────
#
# The mountains of the General tileset (Route 116, Verdanturf, ...) are drawn
# terrace upon terrace, and their tops, faces and rims are told apart by their
# colours, not by the cell they are in: most of their metatiles are part top,
# part face. So these maps are read pixel by pixel.
#
#   * a pixel is top, face, rim (the pale edge a terrace is drawn with where it
#     rises from the one north of it: its own face, turned north, is hidden),
#     ground (anything else: grass, trees, houses), or free (stairs);
#   * tops and grounds are regions, 4-connected, each of its own kind;
#   * down a column, a face between two regions is a south face: seen straight
#     on at 45 degrees it is drawn as tall as it is, so the region above stands
#     exactly as many pixels higher as the face is long. A rim is a step of
#     RIM_RISE up to the south;
#   * the levels are the least-squares answer to all of that, weighed by how
#     many columns say it, the ground at the maps' open edges held at 0. Runs
#     that disagree with the answer (a west or east face read down its length)
#     are dropped and it is solved again;
#   * levels are whole levels (every face is drawn one level tall);
#   * the shape is laid cell by cell, since the art is drawn in whole cells:
#     a cell mostly top or mostly ground is a footprint at its region's level
#     (a ledge a cell wide between bands counts its level across them); the
#     rest - faces, bands, corners, stairs - is rock, one level per cell,
#     hung from the cell it is drawn below (see the tile pass). A tile
#     repeated is the same plane all along, so every edge is a straight line
#     where the drawing's is, parallel to the next; the rim's drawn wobble
#     stays in the texture, and grass is never bent. `--proof` draws it.
#
# Maps drawn across a seam are solved on one canvas, placed as they connect,
# so the mountain is one mountain on both sides of it.

ROCK_TOP = {(0xde, 0xb4, 0xa4), (0xbd, 0x94, 0x8b)}
ROCK_RIM = {(0xee, 0xd5, 0xcd)}
ROCK_FACE = {(0x83, 0x5a, 0x5a), (0x62, 0x41, 0x52), (0x41, 0x31, 0x41)}
ROCK_FLECK = {(0x9c, 0x73, 0x73)}   # in both: the tops' speckle, the faces' shading
ROCK_ALL = ROCK_TOP | ROCK_RIM | ROCK_FACE | ROCK_FLECK
RIM_RISE = 16
# Patches of bare soil on the grass: drawn in the rock faces' colours, flat.
DIRT = {0x113, 0x114, 0x115, 0x14b, 0x14c, 0x14d}
# A boulder drawn two cells tall sits on its terrace: all of it is top.
BOULDER = {0x93, 0x94, 0x9b, 0x9c}
# West and east faces are drawn as bands down the side of the higher terrace,
# one band a level: the face on the left of the cell and the top on its right
# faces west, and the other way round east. Nothing in a 45-degree drawing
# says how tall they are, so each is a level, as its south faces are.
SIDE_WEST = {0x070, 0x073}
SIDE_EAST = {0x072, 0x075, 0x0a2}
SIDE_RISE = 16
# South faces as drawn down a column, one level each: the faces, the cave
# mouths and doors in them, their feet over grass or trees. A cell of rock
# that is neither one of these nor a band is a corner.
FACE_SOUTH = {0x07c, 0x0a9, 0x09f, 0x0a7, 0x091, 0x079, 0x1b0, 0x0af, 0x0cf,
              0x1f0, 0x1f1, 0x33b, 0x33c}
FOOTPRINT = 0.6    # share of a cell drawn as top (or as ground) that makes it a footprint
REGION_MIN = 200    # smaller patches of top (a boulder, a speck) are not terraces
THIN = 7            # nor is a strip of top thinner than this down its columns, on average
MAJORITY = 2        # half-width of the window a rock pixel's kind is voted in

GROUND, TOP, FACE, FLECK, RIM, VOID, FREE = range(7)
# Rock is never walked on, and never a sign or water: a cell the player can
# walk (a bridge, a pier, a path of soil), a signpost or water is ground,
# flat at the level of the ground it joins, whatever colours it is drawn in -
# the wood of a bridge is the rock's brown. Not the stairs, nor a cave's
# mouth; but a bridge's planks read as treads, so a "stair" the cartridge
# calls a bridge, a log or a door is flat too. Not a cell the rock is drawn
# over, in the upper layer (a sea stack's crown over the water behind it).
FLAT_ROLES = {"floor", "signpost", "water"}
WATERFALL = vc.MB["MB_WATERFALL"]
# Water drawn as rock, the shoulder of a stack or a hill in the sea with the
# water surfed behind it, is the rock: this share of its pixels in the rock's
# colours.
ROCKY_WATER = 0.5
_ROCKY_WATER = {}
SHORE_STRIP = 8     # cells: a patch of shore this small, reached from nowhere, is the water's
FLAT_BEHAVIOURS = {vc.MB[k] for k in vc.MB if "BRIDGE" in k or "_LOG_" in k or k.endswith("_DOOR")
                   or k == "MB_NO_RUNNING"} - {vc.MB.get("MB_REFLECTION_UNDER_BRIDGE")}

# Every map whose mountains are drawn with the General tileset's rock is read
# off its drawing: a layout on it with at least DRAWN_MIN cells of face or
# band, outdoors. Maps whose rock runs across a seam are solved together
# (a neighbour joins with any rock at all at the seam), on one
# canvas placed as the maps connect, so a mountain is one mountain on both
# sides of it; a seam with no rock at it keeps the maps apart.
DRAWN_MIN = 5
DRAWN_SEAM = 2          # cells either side of a seam that count as "at" it
ROCK_TILES = ({0x070, 0x072, 0x073, 0x074, 0x075, 0x07b, 0x07c, 0x07d, 0x089, 0x0a9}
              | FACE_SOUTH | SIDE_WEST | SIDE_EAST)


def find_drawn():
    root = vb.ROOT
    layouts = {e["id"]: e for e in json.load(open(os.path.join(root, "data", "layouts", "layouts.json"),
                                                  encoding="utf-8"))["layouts"] if e.get("id")}
    blocks, seeds = {}, set()
    outdoor = vc.MapEvents().outdoor
    alternates = vc.alternate_layouts()
    for lid, e in layouts.items():
        if lid in alternates:
            continue
        if e.get("primary_tileset") != "gTileset_General":
            continue
        path = os.path.join(root, e["blockdata_filepath"])
        if not os.path.exists(path):
            continue
        raw = open(path, "rb").read()
        ms = [v & 0x3FF for v in struct.unpack("<%dH" % (len(raw) // 2), raw)]
        n = sum(1 for m in ms if m in ROCK_TILES)
        if n and lid in outdoor:
            blocks[lid] = ms
            if n >= DRAWN_MIN:
                seeds.add(lid)
    def rock_near(lid, x0, y0, x1, y1):
        e, ms = layouts[lid], blocks[lid]
        w, h = e["width"], e["height"]
        return any(ms[y * w + x] in ROCK_TILES for y in range(max(0, y0), min(h, y1))
                   for x in range(max(0, x0), min(w, x1)))
    # map connections, as layouts: (a, b, dx, dy) places b at a's origin + (dx, dy)
    links = {}
    maps_dir = os.path.join(root, "data", "maps")
    for name in sorted(os.listdir(maps_dir)):
        path = os.path.join(maps_dir, name, "map.json")
        if not os.path.exists(path):
            continue
        m = json.load(open(path, encoding="utf-8"))
        a = m.get("layout")
        if a not in blocks:
            continue
        for c in m.get("connections") or []:
            other = os.path.join(maps_dir, "".join(p.capitalize() for p in c["map"][4:].split("_")), "map.json")
            b = None
            for cand in (other,):
                if os.path.exists(cand):
                    b = json.load(open(cand, encoding="utf-8")).get("layout")
            if b is None:
                for n2 in os.listdir(maps_dir):
                    p2 = os.path.join(maps_dir, n2, "map.json")
                    if os.path.exists(p2):
                        j = json.load(open(p2, encoding="utf-8"))
                        if j.get("id") == c["map"]:
                            b = j.get("layout")
                            break
            if b not in blocks or b == a:
                continue
            A, B, off, d = layouts[a], layouts[b], c.get("offset", 0), c.get("direction")
            if d == "down":
                dx, dy = off, A["height"]
                seam = (rock_near(a, off, A["height"] - DRAWN_SEAM, off + B["width"], A["height"])
                        and rock_near(b, -off, 0, -off + A["width"], DRAWN_SEAM))
            elif d == "up":
                dx, dy = off, -B["height"]
                seam = (rock_near(a, off, 0, off + B["width"], DRAWN_SEAM)
                        and rock_near(b, -off, B["height"] - DRAWN_SEAM, -off + A["width"], B["height"]))
            elif d == "right":
                dx, dy = A["width"], off
                seam = (rock_near(a, A["width"] - DRAWN_SEAM, off, A["width"], off + B["height"])
                        and rock_near(b, 0, -off, DRAWN_SEAM, -off + A["height"]))
            elif d == "left":
                dx, dy = -B["width"], off
                seam = (rock_near(a, 0, off, DRAWN_SEAM, off + B["height"])
                        and rock_near(b, B["width"] - DRAWN_SEAM, -off, B["width"], -off + A["height"]))
            else:
                continue
            if seam:
                links.setdefault(a, []).append((b, dx, dy))
                links.setdefault(b, []).append((a, -dx, -dy))
    groups, seen = {}, set()
    for lid in sorted(seeds):
        if lid in seen:
            continue
        pos, queue = {lid: (0, 0)}, [lid]
        while queue:
            a = queue.pop(0)
            for (b, dx, dy) in links.get(a, []):
                if b not in pos:
                    pos[b] = (pos[a][0] + dx, pos[a][1] + dy)
                    queue.append(b)
        seen.update(pos)
        mx, my = min(p[0] for p in pos.values()), min(p[1] for p in pos.values())
        groups[lid[7:].lower()] = {l: (x - mx, y - my) for l, (x, y) in pos.items()}
    # a layout a script swaps in is solved with its map's neighbours, in its
    # map's place, as a group of its own
    originals = list(groups.items())
    for alt, base in sorted(alternates.items()):
        for name, members in originals:
            if base in members and alt in outdoor:
                groups[alt[7:].lower()] = {(alt if l == base else l): at for l, at in members.items()}
    return groups


DRAWN = find_drawn()


_DRAWN = {}
_SHIFT = {}     # layout -> the depth lattice of a drawn map (see solve_drawn)
_DRAWN_SIDE = {}  # group -> per-cell band side, for camera proofs
_ROCK = {}      # group -> (rock tops, rock tile kinds, soil, metatile lookup), for proofs
_CELLS = {}     # group -> footprint level per cell (None: rock between), for proofs


def drawn_group(layout_id, checked=True):
    """The group a layout is drawn in, or None. With `checked`, only a group
    whose solve passed drawn_ok: the rest keep level ground and their
    ledges until they are looked at."""
    for name, members in DRAWN.items():
        if layout_id in members:
            return name if not checked or drawn_ok(name) else None
    return None


# A group's solve is trusted when its ground stays within reach of itself:
# at most this share of its grass, sand and soil cells stands more than
# MASSIF off the level most of it stands at. Terraces a few levels apart are
# what a mountain is; where the rock is drawn as a fill (a massif of one band
# tile repeated, as round Ever Grande) counting a level a band lifts whole
# towns hundreds of pixels up, and the map is left level instead.
GROUND_SPREAD = 0.05
MASSIF = 160    # pixels: ground this far from most of the group's is a massif counted a level a band
_QUALITY = {}
_PREP = {}      # group -> drawn_prepare's reading of it
_BASE = {}      # layout -> the level its ground stands at, pixels (world_levels, solve_drawn)
_WORLD = {}


# Groups that pass but were seen wrong in their camera proofs, and why.
DRAWN_EXCLUDED = {
    "route122": "Mt Pyre's island is a massif of one band tile: counted a level a band it spikes",
}


def drawn_ok(name):
    if name in DRAWN_EXCLUDED:
        return False
    return name in world_levels()["regions"]


# ── the world's levels ───────────────────────────────────────────────────
#
# A map is lifted as a whole where its ground has to be: Fortree stands on
# the plateau Route 119's cliffs climb to, not at the level of the sea Route
# 118 runs down to. Every drawn group's terraces are solved against each
# other first, in blocks the drawing holds together (terraces joined by a
# face, a band, a walk); then the blocks and the maps that are not drawn -
# a town, a route of grass - are placed so that the ground either side of
# every seam meets. Where the world does not close up (the seams round a
# loop of routes asking for more than its cliffs give), the seams that
# disagree most are the ones given up: a step at one seam, not a cliff
# laid flat.

SEAM_WEIGHT = 16     # per cell of seam, as a column of face pixels counts
HARD_WEIGHT = 1e6    # ground walked from one to the other
LOOSE_WEIGHT = 0.01  # a block nothing places: kept near its group's ground
WORLD_ROOT = "LAYOUT_LITTLEROOT_TOWN"


def map_links():
    """[(a, b, direction, offset)]: every connection between two outdoor
    layouts, as map.json states it (b is `direction` of a, shifted by
    `offset`). A layout a script swaps in has its map's."""
    root = vb.ROOT
    maps_dir = os.path.join(root, "data", "maps")
    layout_of, found = {}, []
    for name in sorted(os.listdir(maps_dir)):
        path = os.path.join(maps_dir, name, "map.json")
        if os.path.exists(path):
            m = json.load(open(path, encoding="utf-8"))
            layout_of[m.get("id")] = m.get("layout")
            found.append(m)
    outdoor = vc.MapEvents().outdoor
    links = set()
    for m in found:
        a = m.get("layout")
        if a not in outdoor:
            continue
        for c in m.get("connections") or []:
            b = layout_of.get(c.get("map"))
            if b in outdoor and b != a and c.get("direction") in ("up", "down", "left", "right"):
                links.add((a, b, c["direction"], c.get("offset", 0)))
    for alt, base in vc.alternate_layouts().items():
        if alt in outdoor:
            for (a, b, d, off) in list(links):
                if a == base:
                    links.add((alt, b, d, off))
                if b == base:
                    links.add((a, alt, d, off))
    return sorted(links)


def _seam_cells(a, b, direction, offset, size):
    """[(edge of a, index along it, edge of b, index along it)] for the cells
    of a seam, `size` a layout's (w, h)."""
    (aw, ah), (bw, bh) = size[a], size[b]
    out = []
    if direction in ("up", "down"):
        for x in range(max(0, offset), min(aw, offset + bw)):
            out.append(("down" if direction == "down" else "up", x,
                        "up" if direction == "down" else "down", x - offset))
    else:
        for y in range(max(0, offset), min(ah, offset + bh)):
            out.append(("right" if direction == "right" else "left", y,
                        "left" if direction == "right" else "right", y - offset))
    return out


def _gauss_seidel(n, pairs, fixed, iterations=4000):
    """Least squares over "h[a] - h[b] = d" (pairs: {(a, b): (d, w)}), the
    nodes in `fixed` held at their values."""
    h = [0.0] * n
    for k, v in fixed.items():
        h[k] = v
    adj = [[] for _ in range(n)]
    for (a, b), (d, w) in pairs.items():
        adj[a].append((b, d, w))
        adj[b].append((a, -d, w))
    for _ in range(iterations):
        delta = 0.0
        for r in range(n):
            if r in fixed or not adj[r]:
                continue
            v = sum(w * (h[o] + d) for (o, d, w) in adj[r]) / sum(w for (_, _, w) in adj[r])
            delta = max(delta, abs(v - h[r]))
            h[r] = v
        if delta < 0.001:
            break
    return h


def _robust(n, samples, fixed, hard=()):
    """Solve samples {(a, b): [d...]} with each pair's median, drop the
    samples more than a half level off the answer and solve again. The
    pairs in `hard` are one level, whatever the rest says."""
    def median(v):
        v = sorted(v)
        return v[len(v) // 2]
    tie = {k: (0.0, HARD_WEIGHT) for k in hard}
    first = dict(tie)
    first.update({k: (median([d for d, _ in v]), sum(w for _, w in v))
                  for k, v in samples.items() if k not in tie})
    first = _gauss_seidel(n, first, fixed)
    kept = dict(tie)
    for (a, b), v in samples.items():
        if (a, b) in tie:
            continue
        good = [(d, w) for d, w in v if abs(first[a] - first[b] - d) <= 8]
        if good:
            kept[(a, b)] = (median([d for d, _ in good]), sum(w for _, w in good))
    return _gauss_seidel(n, kept, fixed), kept


def _give_up_seams(n, samples, fixed):
    """The world's offsets: solved, and while some pair of nodes is joined
    more than a half level off what it asks, the worst of them is given up
    - one seam at a time, so that a map pulled two ways follows one of them
    and the step is at the other, not halfway at both."""
    def median(v):
        v = sorted(v)
        return v[len(v) // 2]
    pairs = {k: (median([d for d, _ in v]), sum(w for _, w in v)) for k, v in samples.items()}
    while True:
        off = _gauss_seidel(n, pairs, fixed)
        worst, err = None, 8.0
        for (a, b), (d, w) in pairs.items():
            e = abs(off[a] - off[b] - d)
            if w >= SEAM_WEIGHT and e > err:
                worst, err = (a, b), e
        if worst is None:
            return off
        del pairs[worst]


def _blocks(prep):
    """A group's terraces solved against each other: (levels relative to
    their block's first terrace, block of each terrace). A terrace nothing
    joins to another is a block of its own."""
    sizes, runs = prep["sizes"], prep["runs"]
    n = len(sizes)
    adj = [set() for _ in range(n)]
    for (a, b) in runs:
        adj[a].add(b)
        adj[b].add(a)
    block, anchors = [-1] * n, {}
    for r in sorted(range(n), key=lambda r: -sizes[r]):
        if block[r] >= 0 or not prep["big"][r]:
            continue
        k = len(anchors)
        anchors[r] = 0.0
        block[r], stack = k, [r]
        while stack:
            c = stack.pop()
            for o in adj[c]:
                if block[o] < 0:
                    block[o] = k
                    stack.append(o)
    level, _ = _robust(n, {k: [(d, 1) for d in v] for k, v in runs.items()}, anchors,
                       prep.get("ties", ()))
    return level, block


def world_levels():
    """{"regions": {group: absolute level of each terrace}, "base": {layout:
    level of a map not drawn}} - see above. Groups whose ground comes out
    spread over many levels (a massif of one band tile, counted a level a
    band) are left out and their maps laid level, as maps not drawn."""
    if _WORLD:
        return _WORLD
    size = {e["id"]: (e["width"], e["height"])
            for e in json.load(open(os.path.join(vb.ROOT, "data", "layouts", "layouts.json"),
                                    encoding="utf-8"))["layouts"] if e.get("id")}
    links = map_links()
    candidates = [g for g in DRAWN if g not in DRAWN_EXCLUDED]
    local = {}
    for g in candidates:
        local[g] = _blocks(drawn_prepare(g))

    def solve(groups):
        owner = {}
        for g in groups:
            for lid in DRAWN[g]:
                owner.setdefault(lid, g)
        nodes, index = [], {}

        def node(key):
            if key not in index:
                index[key] = len(nodes)
                nodes.append(key)
            return index[key]
        samples = collections.defaultdict(list)
        # every block is kept loosely at its group's biggest block, so that
        # one nothing places does not drift off
        for g in groups:
            level, block = local[g]
            main = node(("b", g, 0))
            for k in set(block) - {-1, 0}:
                samples[(node(("b", g, k)), main)].append((0.0, LOOSE_WEIGHT))
        for (a, b, direction, offset) in links:
            ga, gb = owner.get(a), owner.get(b)
            if ga is not None and ga == gb and b in DRAWN[ga] and a in DRAWN[ga]:
                continue    # a seam inside a group: its canvas has it
            for (ea, ia, eb, ib) in _seam_cells(a, b, direction, offset, size):
                def side(g, lid, edge, i):
                    if g is None:
                        return node(("f", lid)), 0.0
                    r = _PREP[g]["edges"][(lid, edge)][i]
                    if r is None:
                        return None
                    level, block = local[g]
                    return node(("b", g, block[r])), level[r]
                sa, sb = side(ga, a, ea, ia), side(gb, b, eb, ib)
                if sa is None or sb is None:
                    continue
                # off[a] + h[a] = off[b] + h[b]
                samples[(sa[0], sb[0])].append((sb[1] - sa[1], SEAM_WEIGHT))
        fixed = {}
        root = index.get(("f", WORLD_ROOT))
        # one node held in every piece of the world: Littleroot where it is
        comp = [-1] * len(nodes)
        adj = collections.defaultdict(set)
        for (a, b) in samples:
            adj[a].add(b)
            adj[b].add(a)
        order = ([root] if root is not None else []) + list(range(len(nodes)))
        for start in order:
            if comp[start] >= 0:
                continue
            comp[start] = start
            fixed[start] = 0.0
            stack = [start]
            while stack:
                c = stack.pop()
                for o in adj[c]:
                    if comp[o] < 0:
                        comp[o] = start
                        stack.append(o)
        off = _give_up_seams(len(nodes), samples, fixed)
        regions = {}
        for g in groups:
            level, block = local[g]
            regions[g] = [LEVEL * round((off[index[("b", g, block[r])]] + level[r]) / LEVEL)
                          if block[r] >= 0 and ("b", g, block[r]) in index else
                          LEVEL * round(off[index[("b", g, 0)]] / LEVEL)
                          for r in range(len(level))]
        base = {key[1]: LEVEL * round(off[i] / LEVEL) for key, i in index.items() if key[0] == "f"}
        broken = collections.Counter()
        for (a, b), v in samples.items():
            for d, w in v:
                if w == SEAM_WEIGHT and abs(off[a] - off[b] - d) > 8:
                    broken[(nodes[a][1], nodes[b][1])] += 1
        return regions, base, broken

    def spread(g, regions):
        prep = _PREP[g]
        got = collections.Counter()
        for row in prep["stats"]:
            for st in row:
                if st is None:
                    continue
                n, top, ground, counts = st
                if ground >= FOOTPRINT * n and counts:
                    got[regions[g][max(counts, key=counts.get)]] += 1
        if not got:
            return 0.0
        mode = got.most_common(1)[0][0]
        return sum(v for k, v in got.items() if abs(k - mode) > MASSIF) / float(sum(got.values()))

    groups = list(candidates)
    while True:
        regions, base, broken = solve(groups)
        for g in groups:
            _QUALITY[g] = spread(g, regions)
        bad = [g for g in groups if _QUALITY[g] > GROUND_SPREAD]
        if not bad:
            break
        groups = [g for g in groups if g not in bad]
    for g in candidates:
        if g not in groups:
            print("level  %-28s ground spread %.1f%%: laid level" % (g, 100 * _QUALITY[g]))
    print("world: %d groups, %d maps placed, %d seam cells given up"
          % (len(groups), len(base), sum(broken.values())))
    for (a, b), n in sorted(broken.items(), key=lambda kv: -kv[1]):
        print("  step at the seam %s / %s: %d cells" % (a, b, n))
    for lid, b in sorted(base.items()):
        if b:
            print("  %-36s stands at %+d px" % (lid, b))
    _WORLD["broken"] = broken
    _WORLD["regions"], _WORLD["base"] = regions, base
    return _WORLD


def drawn_canvas(name):
    """The group's canvas: (layouts, width, height, per-pixel kind, per-cell
    hiding - a roof or a tree a face can go down behind -, per-cell side: +1
    a face turned west, -1 east, per-cell flat: ground that is never rock
    whatever its colours - see FLAT_ROLES)."""
    members = DRAWN[name]
    layouts = {lid: open_roles(lid) for lid in members}
    for lid in members:
        if lid not in _ART:
            _ART[lid] = vb.LayoutArt(lid)
    CW = max(ox + layouts[l].w for l, (ox, oy) in members.items())
    CH = max(oy + layouts[l].h for l, (ox, oy) in members.items())
    W, H = CW * 16, CH * 16
    kind = [[VOID] * W for _ in range(H)]
    blocked = [[False] * CW for _ in range(CH)]
    side = [[0] * CW for _ in range(CH)]
    flat = [[False] * CW for _ in range(CH)]
    for lid, (ox, oy) in members.items():
        L, A = layouts[lid], _ART[lid]
        for cy in range(L.h):
            for cx in range(L.w):
                # a face goes down behind a roof or a tree, not a signpost
                blocked[oy + cy][ox + cx] = L.blocked(cx, cy) and                     L.role_at(cx, cy) in ("tree", "wall", "prop")
                m = A.metatile(cx, cy)
                side[oy + cy][ox + cx] = 1 if m in SIDE_WEST else -1 if m in SIDE_EAST else 0
                role = L.role_at(cx, cy)
                if L.behaviour(cx, cy) == WATERFALL:
                    # a waterfall is a face of water, falling a level
                    flat[oy + cy][ox + cx] = "fall"
                    for j in range(16):
                        kind[(oy + cy) * 16 + j][(ox + cx) * 16:(ox + cx + 1) * 16] = [FACE] * 16
                    continue
                tile_key = (L.secondary, m)     # a secondary tileset's ids are its own
                if role == "water" and tile_key not in _ROCKY_WATER:
                    px = list(A.cell_image(m).convert("RGB").getdata())
                    _ROCKY_WATER[tile_key] = sum(1 for c in px if c in ROCK_ALL) >= ROCKY_WATER * len(px)
                if m not in FACE_SOUTH and ((role in FLAT_ROLES and (cx, cy) not in L.warps
                                             and not L.covers(m)
                                             and not (role == "water" and _ROCKY_WATER[tile_key]))
                                            or L.behaviour(cx, cy) in FLAT_BEHAVIOURS):
                    flat[oy + cy][ox + cx] = ("bridge" if L.behaviour(cx, cy) in FLAT_BEHAVIOURS else
                                              role if role in ("water", "signpost") else "floor")
                img = A.cell_image(A.metatile(cx, cy)).load()
                stair = role == "stair" and not flat[oy + cy][ox + cx]
                if m in DIRT:
                    for j in range(16):
                        kind[(oy + cy) * 16 + j][(ox + cx) * 16:(ox + cx + 1) * 16] = [GROUND] * 16
                    continue
                for j in range(16):
                    row = kind[(oy + cy) * 16 + j]
                    for i in range(16):
                        c = img[i, j][:3]
                        row[(ox + cx) * 16 + i] = (
                            FREE if stair else TOP if c in ROCK_TOP else
                            FACE if c in ROCK_FACE else FLECK if c in ROCK_FLECK else
                            RIM if c in ROCK_RIM else GROUND)
    # a bridge's ends, drawn over the player where they cross a rock's lip,
    # are the bridge
    grow = True
    while grow:
        grow = False
        for lid, (ox, oy) in members.items():
            L = layouts[lid]
            for cy in range(L.h):
                for cx in range(L.w):
                    if flat[oy + cy][ox + cx] or L.role_at(cx, cy) not in ("floor", "stair"):
                        continue
                    if any(0 <= cx + dx < L.w and 0 <= cy + dy < L.h
                           and flat[oy + cy + dy][ox + cx + dx] == "bridge"
                           for (dx, dy) in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                        flat[oy + cy][ox + cx] = "bridge"
                        grow = True
    # a rock pixel is what most rock round it is: faces are speckled with the
    # tops' colours and the tops with the faces'
    voted = [row[:] for row in kind]
    R = MAJORITY
    for y in range(H):
        for x in range(W):
            if kind[y][x] not in (TOP, FACE, FLECK):
                continue
            t = f = 0
            for j in range(max(0, y - R), min(H, y + R + 1)):
                row = kind[j]
                for i in range(max(0, x - R), min(W, x + R + 1)):
                    k = row[i]
                    t += k == TOP or k == RIM
                    f += k == FACE
            voted[y][x] = TOP if t > f else FACE
    return layouts, CW, CH, voted, blocked, side, flat


def drawn_prepare(name):
    """What a group's drawing says, the pixels read and let go: its regions
    (terraces and ground), the drops between them, and per cell what it is
    drawn as. The levels come from world_levels, which weighs every group
    and every map's seams together."""
    if name in _PREP:
        return _PREP[name]
    cache = os.environ.get("VOXEL_RELIEF_CACHE")    # a developer's, between runs
    if cache:
        import hashlib, inspect, pickle
        here = os.path.dirname(os.path.abspath(__file__))
        # what the reading depends on: the constants, this function, the canvas, the cells
        text = open(os.path.join(here, "gen_voxel_relief.py"), encoding="utf-8").read()
        key = hashlib.sha1((text[:text.index("def find_drawn")]     # the constants
                            + inspect.getsource(drawn_prepare) + inspect.getsource(drawn_canvas)
                            + open(os.path.join(here, "voxel_cells.py"), encoding="utf-8").read()
                            ).encode("utf-8")).hexdigest()[:12]
        path = os.path.join(cache, "prep_%s_%s.pickle" % (name, key))
        if os.path.exists(path):
            _PREP[name] = pickle.load(open(path, "rb"))
            for lid in DRAWN[name]:
                if lid not in _ART:
                    _ART[lid] = vb.LayoutArt(lid)
            return _PREP[name]
    members = DRAWN[name]
    layouts, CW, CH, kind, blocked, side, flat = drawn_canvas(name)

    def metatile_at(cx, cy):
        for lid, (ox, oy) in members.items():
            L = layouts[lid]
            if ox <= cx < ox + L.w and oy <= cy < oy + L.h:
                return _ART[lid].metatile(cx - ox, cy - oy)
        return None
    W, H = CW * 16, CH * 16

    # regions
    region = [[-1] * W for _ in range(H)]
    sizes = []
    for y in range(H):
        for x in range(W):
            k = kind[y][x]
            if k not in (GROUND, TOP) or region[y][x] >= 0:
                continue
            n = len(sizes)
            stack, count = [(x, y)], 0
            region[y][x] = n
            while stack:
                a, b = stack.pop()
                count += 1
                for (i, j) in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
                    if 0 <= i < W and 0 <= j < H and region[j][i] < 0 and kind[j][i] == k:
                        region[j][i] = n
                        stack.append((i, j))
            sizes.append(count)
    # a strip of top a few pixels thick - the light lip between two faces
    # stacked one on the other - is part of the face, not a terrace
    columns = [set() for _ in sizes]
    for y in range(H):
        row = region[y]
        for x in range(W):
            if row[x] >= 0:
                columns[row[x]].add(x)
    big = [s >= REGION_MIN and s >= THIN * len(columns[n]) for n, s in enumerate(sizes)]

    # what the columns say: (upper, lower) -> drops
    runs = {}
    for x in range(W):
        y = 0
        while y < H:
            k = kind[y][x]
            if k not in (FACE, RIM):
                y += 1
                continue
            y0 = y
            while y < H and kind[y][x] == k:
                y += 1
            if y0 == 0 or y == H:
                continue
            a, b = region[y0 - 1][x], region[y][x]
            if a < 0 or b < 0 or a == b or not (big[a] and big[b]):
                continue
            if kind[y][x] == GROUND and blocked[y // 16][x // 16]:
                continue  # its foot is behind a roof or a tree: not all of it shows
            if k == FACE and sum(side[j // 16][x // 16] != 0 for j in range(y0, y)) * 2 > y - y0:
                continue  # down a west or east face, not across a south one
            drop = y - y0 if k == FACE else -RIM_RISE
            runs.setdefault((a, b), []).append(drop)

    # across the rows: the west and east faces
    for y in range(H):
        x = 0
        row = kind[y]
        while x < W:
            if row[x] != FACE:
                x += 1
                continue
            x0 = x
            while x < W and row[x] == FACE:
                x += 1
            if x0 == 0 or x == W:
                continue
            turn = sum(side[y // 16][i // 16] for i in range(x0, x))
            if abs(turn) * 2 <= x - x0:
                continue
            left, right = region[y][x0 - 1], region[y][x]
            if left < 0 or right < 0 or left == right or not (big[left] and big[right]):
                continue
            if turn > 0:
                runs.setdefault((right, left), []).append(SIDE_RISE)
            else:
                runs.setdefault((left, right), []).append(SIDE_RISE)

    # a flight of two bands or more down a row, all turned the same way, is
    # as many levels between the terraces at its two ends - a gorge's walls
    # count the river down, where no single face joins the two
    for y in range(H):
        cy = y // 16
        cx = 0
        while cx < CW:
            s_ = side[cy][cx]
            if not s_:
                cx += 1
                continue
            x0 = cx
            while cx < CW and side[cy][cx] == s_:
                cx += 1
            if cx - x0 < 2 or x0 == 0 or cx == CW:
                continue
            west, east = region[y][x0 * 16 - 8], region[y][cx * 16 + 8]
            if west < 0 or east < 0 or west == east or not (big[west] and big[east]):
                continue
            rise = SIDE_RISE * (cx - x0)
            if s_ > 0:
                runs.setdefault((east, west), []).append(rise)
            else:
                runs.setdefault((west, east), []).append(rise)

    # and so is a column of two south faces or more stacked one on the other
    # (a waterfall among them): the lip between two faces is a strip of top
    # too thin to be a terrace, so no single face joins the two ends
    def south_face(cx, cy):
        return (not side[cy][cx] and flat[cy][cx] in (None, "fall")
                and (metatile_at(cx, cy) in FACE_SOUTH or flat[cy][cx] == "fall"))
    for x in range(8, W, 16):
        cx = x // 16
        cy = 0
        while cy < CH:
            if not south_face(cx, cy):
                cy += 1
                continue
            y0 = cy
            while cy < CH and south_face(cx, cy):
                cy += 1
            if cy - y0 < 2 or y0 == 0 or cy == CH:
                continue
            for i in range(x - 6, x + 7, 3):
                up, down = region[y0 * 16 - 4][i], region[cy * 16 + 4][i]
                if up >= 0 and down >= 0 and up != down and big[up] and big[down]:
                    runs.setdefault((up, down), []).append(LEVEL * (cy - y0))

    # the ground the player walks on without a flight of stairs or a ledge
    # to jump is one level: a bridge's two ends, the ground either side of a
    # gap in a fence. The cartridge says so; the drawing only draws it.
    # So is a body of water, a waterfall apart - its shaded edge along a
    # shore is not a pool of its own -, and the land it laps: a shore is
    # never a step. A bridge goes over the water, not down to it.
    WALKED = 1000
    walk_region = {}
    ties = set()        # never given up: the cartridge says they are one level
    for cy in range(CH):
        for cx in range(CW):
            if flat[cy][cx] not in ("floor", "bridge", "water"):
                continue
            got = collections.Counter(region[cy * 16 + j][cx * 16 + i] for j in range(0, 16, 2)
                                      for i in range(0, 16, 2))
            got = [(n, r) for r, n in got.items() if r >= 0 and big[r]]
            walk_region[(cx, cy)] = max(got)[1] if got else None
    seen_walk = set()
    for start in walk_region:
        if start in seen_walk:
            continue
        comp, stack = [], [start]
        seen_walk.add(start)
        while stack:
            c = stack.pop()
            comp.append(c)
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                q = (c[0] + d[0], c[1] + d[1])
                if (q in walk_region and q not in seen_walk
                        and {flat[q[1]][q[0]], flat[c[1]][c[0]]} != {"bridge", "water"}):
                    seen_walk.add(q)
                    stack.append(q)
        found = sorted({walk_region[c] for c in comp if walk_region[c] is not None})
        for r in found[1:]:
            runs.setdefault((found[0], r), []).extend([0] * WALKED)
            ties.add((found[0], r))

    # per cell: how much of it is drawn, as top, as ground, and which
    # terraces it is drawn in
    stats = [[None] * CW for _ in range(CH)]
    free_mid = [[False] * CW for _ in range(CH)]
    for cy in range(CH):
        for cx in range(CW):
            counts = {}
            n = top = ground = 0
            for j in range(16):
                krow, rrow = kind[cy * 16 + j], region[cy * 16 + j]
                for i in range(cx * 16, cx * 16 + 16):
                    k = krow[i]
                    if k == VOID:
                        continue
                    n += 1
                    top += k in (TOP, RIM)
                    ground += k == GROUND
                    r = rrow[i]
                    if r >= 0 and big[r]:
                        counts[r] = counts.get(r, 0) + 1
            if n:
                stats[cy][cx] = (n, top, ground, counts)
            free_mid[cy][cx] = kind[cy * 16 + 8][cx * 16 + 8] == FREE
    # what stands at each map's edges, cell by cell: the terrace or ground
    # drawn there, where a seam joins it to the next map
    edges = {}
    for lid, (ox, oy) in members.items():
        L = layouts[lid]
        for edge in ("up", "down", "left", "right"):
            if edge in ("up", "down"):
                y = oy * 16 + 1 if edge == "up" else (oy + L.h) * 16 - 2
                px = [((ox + i) * 16 + 8, y) for i in range(L.w)]
            else:
                x = ox * 16 + 1 if edge == "left" else (ox + L.w) * 16 - 2
                px = [(x, (oy + j) * 16 + 8) for j in range(L.h)]
            edges[(lid, edge)] = [region[y][x] if region[y][x] >= 0 and big[region[y][x]]
                                  and kind[y][x] in (GROUND, TOP) else None for (x, y) in px]
    prep = dict(members=members, layouts=layouts, CW=CW, CH=CH, side=side, flat=flat,
                sizes=sizes, big=big, runs=runs, ties=ties, stats=stats, free_mid=free_mid,
                edges=edges)
    _PREP[name] = prep
    if cache:
        os.makedirs(cache, exist_ok=True)
        pickle.dump(prep, open(path, "wb"))
    return prep


def solve_drawn(name):
    """{layout id: lattice} of a group of maps drawn as one."""
    if name in _DRAWN:
        return _DRAWN[name]
    prep = drawn_prepare(name)
    members, layouts, CW, CH = prep["members"], prep["layouts"], prep["CW"], prep["CH"]
    side, flat, sizes, big = prep["side"], prep["flat"], prep["sizes"], prep["big"]
    stats, free_mid = prep["stats"], prep["free_mid"]
    level = world_levels()["regions"][name]

    def metatile_at(cx, cy):
        for lid, (ox, oy) in members.items():
            L = layouts[lid]
            if ox <= cx < ox + L.w and oy <= cy < oy + L.h:
                return _ART[lid].metatile(cx - ox, cy - oy)
        return None

    # The shape, cell by cell. The mountain is drawn in whole cells: a cell
    # is the top of a terrace, ground, or the rock between them (a south
    # face, a west or east band, a corner, a flight of stairs). Tops and
    # ground are footprints, each at its region's level; the rock between
    # is a ramp from the footprint above it, planes that start at the
    # footprint's cell edge - so every edge is a straight line, parallel to
    # the next - and turn an outer corner on an ellipse. The drawn rim's
    # wobble stays in the texture, never in the shape.
    cell = [[None] * CW for _ in range(CH)]     # level of a footprint cell

    void = [[True] * CW for _ in range(CH)]
    thin = set()
    level_from_neighbours = set()   # flat cells with no terrace in them
    soil = [[False] * CW for _ in range(CH)]    # a ground footprint: never lifted
    for cy in range(CH):
        for cx in range(CW):
            if stats[cy][cx] is None:
                continue
            n, top, ground, counts = stats[cy][cx]
            void[cy][cx] = False
            boulder = metatile_at(cx, cy) in BOULDER
            if flat[cy][cx] == "bridge" or (flat[cy][cx] == "floor"
                                            and not (top >= FOOTPRINT * n or ground >= FOOTPRINT * n)):
                level_from_neighbours.add((cx, cy))
                soil[cy][cx] = True
            elif flat[cy][cx] in ("water", "signpost") and not (top >= FOOTPRINT * n or ground >= FOOTPRINT * n):
                if counts:
                    cell[cy][cx] = level[max(counts, key=counts.get)]
                else:
                    level_from_neighbours.add((cx, cy))
                soil[cy][cx] = True
            elif boulder or top >= FOOTPRINT * n or ground >= FOOTPRINT * n:
                if counts:
                    cell[cy][cx] = level[max(counts, key=counts.get)]
                    soil[cy][cx] = not boulder and top < FOOTPRINT * n
                elif top >= FOOTPRINT * n:
                    thin.add((cx, cy))
    # The ledges between bands are top too, a cell wide: too thin for the
    # regions, their level is counted across the bands from a known one -
    # a band turned west is a level down going west, one turned east a level
    # down going east - or taken from a ledge above or below them.
    while thin:
        found = {}
        for (cx, cy) in thin:
            for dx in (-1, 1):
                x, n = cx + dx, 0
                while 0 <= x < CW and side[cy][x] and cell[cy][x] is None:
                    n += side[cy][x] * dx
                    x += dx
                if x != cx + dx and 0 <= x < CW and cell[cy][x] is not None:
                    found[(cx, cy)] = cell[cy][x] - SIDE_RISE * n
                    break
            else:
                for dy in (-1, 1):
                    y = cy + dy
                    if 0 <= y < CH and cell[y][cx] is not None and (cx, y) not in found:
                        found[(cx, cy)] = cell[y][cx]
                        break
        if not found:
            break
        for (cx, cy), v in found.items():
            cell[cy][cx] = v
            thin.discard((cx, cy))
    # a bridge, and a flat cell drawn all in rock colours, is at the level of
    # the ground it joins - its neighbours' most common level, the water
    # under a bridge left out -, taken along the bridge from its ends
    while level_from_neighbours:
        found = {}
        for (cx, cy) in level_from_neighbours:
            got = collections.Counter(cell[y][x] for (x, y) in ((cx, cy - 1), (cx, cy + 1), (cx - 1, cy), (cx + 1, cy))
                                      if 0 <= x < CW and 0 <= y < CH and cell[y][x] is not None
                                      and flat[y][x] != "water" and (x, y) not in level_from_neighbours)
            if got:
                found[(cx, cy)] = got.most_common(1)[0][0]
        if not found:
            break
        for (cx, cy), v in found.items():
            cell[cy][cx] = v
            level_from_neighbours.discard((cx, cy))

    # every body of water lies at one level: the level most of it is at. It
    # runs on under a bridge.
    seen_water = set()
    for cy0 in range(CH):
        for cx0 in range(CW):
            if flat[cy0][cx0] != "water" or (cx0, cy0) in seen_water:
                continue
            body, stack = [], [(cx0, cy0)]
            seen_water.add((cx0, cy0))
            while stack:
                c = stack.pop()
                body.append(c)
                for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    q = (c[0] + d[0], c[1] + d[1])
                    if (0 <= q[0] < CW and 0 <= q[1] < CH and q not in seen_water
                            and flat[q[1]][q[0]] in ("water", "bridge")):
                        seen_water.add(q)
                        stack.append(q)
            body = [(x, y) for (x, y) in body if flat[y][x] == "water"]
            got = collections.Counter(cell[y][x] for (x, y) in body if cell[y][x] is not None)
            if got:
                lv = got.most_common(1)[0][0]
                for (x, y) in body:
                    cell[y][x] = lv
                    soil[y][x] = True
                # a strip of shore walked to from nowhere else, down at the
                # water between two walls, is at the water's level
                for (x, y) in body:
                    for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        q = (x + d[0], y + d[1])
                        if not (0 <= q[0] < CW and 0 <= q[1] < CH) or flat[q[1]][q[0]] != "floor":
                            continue
                        strip, stack = {q}, [q]
                        while stack and len(strip) <= SHORE_STRIP:
                            c = stack.pop()
                            for e in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                                r = (c[0] + e[0], c[1] + e[1])
                                if (0 <= r[0] < CW and 0 <= r[1] < CH and r not in strip
                                        and flat[r[1]][r[0]] == "floor"):
                                    strip.add(r)
                                    stack.append(r)
                        if len(strip) <= SHORE_STRIP:
                            for (sx, sy) in strip:
                                cell[sy][sx] = lv
                                soil[sy][sx] = True

    # The rock, tile by tile. Every cell of rock is one level of the drawing,
    # and it hangs from the cell it is drawn below: a south face (or a
    # flight of stairs) from the cell north of it, a band from the cell on
    # its high side, a corner from whichever of its eight neighbours stands
    # highest. Its top is that neighbour's level - the terrace's own, or one
    # level under the rock it hangs from - so rings of rock round a terrace
    # step down a level each, as drawn. Inside the cell the rock falls one
    # level with the distance from what it hangs from: a face straight down
    # its column, a band straight across its row, a corner round a quarter
    # circle of its own. A tile repeated is the same slope all along, and
    # the grass is never bent.
    _CELLS[name] = cell

    def tile(cx, cy):
        m = metatile_at(cx, cy)
        if side[cy][cx]:
            return "band"
        if m in FACE_SOUTH or free_mid[cy][cx] or flat[cy][cx] == "fall":
            return "face"
        return "corner"

    def hangs_from(cx, cy):
        t = tile(cx, cy)
        if t == "face":
            return [(cx, cy - 1)]
        if t == "band":
            return [(cx + side[cy][cx], cy)]
        return [(cx + dx, cy + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy]

    rock = [(cx, cy) for cy in range(CH) for cx in range(CW)
            if cell[cy][cx] is None and not void[cy][cx]]
    top = {}

    def height_of(x, y):
        """What a neighbour offers to hang from: a terrace's level, or one
        level under a cell of rock."""
        if not (0 <= x < CW and 0 <= y < CH) or void[y][x]:
            return None
        if cell[y][x] is not None:
            return cell[y][x]
        return top[(x, y)] - LEVEL if (x, y) in top else None

    # Nor does rock go down past the terrace it falls to: where the drawing
    # has more rings than the terraces' levels allow (both ends held at a
    # seam's level), the rings left over lie flat on it, not dug into a pit.
    def landing(cx, cy):
        t_ = tile(cx, cy)
        if t_ == "corner":
            got = [cell[y][x] for (x, y) in hangs_from(cx, cy)
                   if 0 <= x < CW and 0 <= y < CH and cell[y][x] is not None]
            return min(got) if got else None
        dx, dy = (0, 1) if t_ == "face" else (-side[cy][cx], 0)
        x, y = cx + dx, cy + dy
        while 0 <= x < CW and 0 <= y < CH and not void[y][x]:
            if cell[y][x] is not None:
                return cell[y][x]
            x, y = x + dx, y + dy
        return None
    floor_of = {c: landing(*c) for c in rock}

    changed = True
    while changed:
        changed = False
        for (cx, cy) in rock:
            got = [v for v in (height_of(x, y) for (x, y) in hangs_from(cx, cy)) if v is not None]
            if got:
                v = max(got)
                if floor_of[(cx, cy)] is not None:
                    v = max(v, floor_of[(cx, cy)])
                if top.get((cx, cy)) is None or v > top[(cx, cy)]:
                    top[(cx, cy)] = v
                    changed = True

    _ROCK[name] = (dict(top), {c: tile(*c) for c in rock}, soil, metatile_at)
    _DRAWN_SIDE[name] = side

    LW, LH = CW * PER_CELL + 1, CH * PER_CELL + 1
    h = [[None] * LW for _ in range(LH)]
    fixed = [[False] * LW for _ in range(LH)]
    for cy in range(CH):
        for cx in range(CW):
            lv = cell[cy][cx]
            if lv is None:
                continue
            for j in range(cy * PER_CELL, (cy + 1) * PER_CELL + 1):
                for i in range(cx * PER_CELL, (cx + 1) * PER_CELL + 1):
                    if soil[cy][cx]:
                        if not fixed[j][i] or lv > h[j][i]:
                            h[j][i] = lv
                        fixed[j][i] = True
                    elif not fixed[j][i] and (h[j][i] is None or lv > h[j][i]):
                        h[j][i] = lv
    for (cx, cy) in rock:
        hi = top.get((cx, cy))
        if hi is None:
            continue
        rects = [(x, y) for (x, y) in hangs_from(cx, cy) if height_of(x, y) == hi]
        if not rects:
            # laid flat on the terrace it falls to
            rects = [(x, y) for (x, y) in hangs_from(cx, cy) if height_of(x, y) is not None]
        # never below the land it falls to: the last ring onto the grass is
        # flat. Only the downhill side counts - a face's south, a band's low
        # side, a corner's neighbours other than what holds it up - never the
        # terrace it hangs from or one beside it.
        t_ = tile(cx, cy)
        if t_ == "face":
            below = [(cx, cy + 1)]
        elif t_ == "band":
            below = [(cx - side[cy][cx], cy)]
        else:
            below = [(x, y) for (x, y) in hangs_from(cx, cy) if (x, y) not in rects]
        land = [cell[y][x] for (x, y) in below
                if 0 <= x < CW and 0 <= y < CH and cell[y][x] is not None and cell[y][x] <= hi]
        # the last ring lands on the terrace below it at that terrace's level,
        # a level or not: where the drawing's count of rings and the
        # terraces' levels disagree, the ring takes up the difference
        floor = min(land) if land else hi - LEVEL
        for j in range(cy * PER_CELL, (cy + 1) * PER_CELL + 1):
            py = j * STEP
            for i in range(cx * PER_CELL, (cx + 1) * PER_CELL + 1):
                if fixed[j][i]:
                    continue
                px = i * STEP
                d = min(math.hypot(max(x * 16 - px, 0, px - x * 16 - 16),
                                   max(y * 16 - py, 0, py - y * 16 - 16)) for (x, y) in rects)
                v = hi - (hi - floor) * min(1.0, d / 16.0)
                if h[j][i] is None or v > h[j][i]:
                    h[j][i] = v
    # each map's own level, the one most of its ground stands at: the map is
    # lifted to it as a whole, its relief written from there
    base = {}
    for lid, (ox, oy) in members.items():
        L = layouts[lid]
        got = collections.Counter(cell[oy + y][ox + x] for y in range(L.h) for x in range(L.w)
                                  if cell[oy + y][ox + x] is not None and soil[oy + y][ox + x])
        base[lid] = got.most_common(1)[0][0] if got else 0
    # a point nothing stands on (off every map) is at its map's level
    for lid, (ox, oy) in members.items():
        L = layouts[lid]
        for j in range(oy * PER_CELL, (oy + L.h) * PER_CELL + 1):
            for i in range(ox * PER_CELL, (ox + L.w) * PER_CELL + 1):
                if h[j][i] is None:
                    h[j][i] = float(base[lid])
    for j in range(LH):
        for i in range(LW):
            if h[j][i] is None:
                h[j][i] = 0.0

    # every point of the drawing as far south as it is high: (u, h, v + h)
    out = {}
    for lid, (ox, oy) in members.items():
        L = layouts[lid]
        b = base[lid]
        out[lid] = [[v - b for v in row[ox * PER_CELL:(ox + L.w) * PER_CELL + 1]]
                    for row in h[oy * PER_CELL:(oy + L.h) * PER_CELL + 1]]
        if drawn_group(lid, checked=False) == name:
            _BASE[lid] = b
            _SHIFT[lid] = [row[:] for row in out[lid]]
    print("drawn %-10s %d regions (%d terraces), %.0f..%.0f px"
          % (name, len(sizes), sum(big), min(min(r) for r in h), max(max(r) for r in h)))
    _DRAWN[name] = out
    return out





def ledge_cells(layout, junctions=True):
    """{(x, y): jump directions} of every ledge cell of a layout.

    Where a ledge turns a corner by a step, the artist joins the two runs
    with a piece the cartridge does not let anyone jump from: a blocked cell
    with ledges on two sides at right angles. It belongs to the ledge, and
    takes the directions of the ledge cells beside it. Not in a map whose cliffs are solved: there the rock
    round it is relief already.
    """
    out = {}
    for y in range(layout.h):
        for x in range(layout.w):
            d = JUMPS.get(layout.behaviour(x, y))
            if d:
                out[(x, y)] = d
    if not junctions or getattr(layout, "layout_id", None) in ENABLED:
        return out
    joins = {}
    for (x, y) in out:
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in out or layout.off_map(nx, ny) or not layout.blocked(nx, ny):
                continue
            if not layout.is_ledge_junction(nx, ny):
                continue
            dirs = joins.setdefault((nx, ny), [])
            dirs.extend(d for d in out[(x, y)] if d not in dirs)
    out.update((c, tuple(d)) for c, d in joins.items())
    return out


def ledge_berms(layout, art, h):
    """Raise every ledge's lip off the drawing, added to the lattice `h`.

    The lip's top edge - where the ground behind it meets the brown - stands
    LIP pixels up, its foot on the jump side at the ground: lifted as all
    relief is, (u, h, v + h), the brown is a face turned to where the jump
    goes and the GBA view is still the drawing. The ground behind rises to the
    top edge over LIP_BACK pixels, so the ledge is a low berm, not a step in
    the land.

    A point on the lip is as high as it is far from the lip's foot, walking
    the way the jump goes: LIP at LIP_WIDTH pixels and more. Where the lip
    turns (a corner cell jumps two ways) the nearer foot counts, so an outer
    corner rounds off like the drawing and neither arm is read along its
    length. Where it ends - a point shared with a cell that is no ledge - it
    comes down to the ground, and the drawn end taper does the rest.
    """
    cells = ledge_cells(layout)
    if not cells:
        return 0
    lip = {}
    for (x, y), dirs in cells.items():
        # ground colours: the cells the jump leaves and lands on, else round it
        ground = set()
        for ring in ([(-dx, -dy) for dx, dy in dirs] + list(dirs),
                     [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]):
            for dx, dy in ring:
                nx, ny = x + dx, y + dy
                if (nx, ny) in cells or layout.off_map(nx, ny) or layout.blocked(nx, ny):
                    continue
                img = art.cell_image(art.metatile(nx, ny))
                ground.update(img.getpixel((i, j))[:3] for j in range(16) for i in range(16))
            if ground:
                break
        if not ground:
            continue
        img = art.cell_image(art.metatile(x, y)).load()
        for j in range(16):
            for i in range(16):
                if img[i, j][:3] not in ground:
                    lip[(x * 16 + i, y * 16 + j)] = True

    def run(px, py, dx, dy, limit=64):
        n = 0
        while n < limit and lip.get((px + n * dx, py + n * dy)):
            n += 1
        return n

    def first(px, py, dx, dy, limit):
        for k in range(limit):
            if lip.get((px + k * dx, py + k * dy)):
                return k
        return None

    def lip_height(f):
        return LIP * min(f, LIP_WIDTH) / float(LIP_WIDTH)

    raised = {}
    for (x, y), dirs in cells.items():
        for j in range(PER_CELL + 1):
            for i in range(PER_CELL + 1):
                px, py = x * 16 + i * STEP, y * 16 + j * STEP
                reads = []
                for dx, dy in dirs:
                    # the pixel line through the point, inside the cell across
                    # the jump, entered on the jump side of the point along it
                    if dx == 0:
                        sx = min(max(px, x * 16), x * 16 + 15)
                        sy = py - (dy < 0)
                    else:
                        sx = px - (dx < 0)
                        sy = min(max(py, y * 16), y * 16 + 15)
                    reads.append((sx, sy, dx, dy, run(sx, sy, dx, dy),
                                  run(sx - dx, sy - dy, -dx, -dy)))
                if all(f for (_, _, _, _, f, _) in reads):
                    best = min(lip_height(f) for (_, _, _, _, f, _) in reads)
                elif any(b and not f for (_, _, _, _, f, b) in reads):
                    best = 0.0  # in front of the lip: the ground jumped down to
                else:
                    # behind the lip: rising to where the line meets it
                    best = 0.0
                    for (sx, sy, dx, dy, f, b) in reads:
                        k = first(sx, sy, dx, dy, LIP_BACK)
                        if k is not None:
                            top = lip_height(run(sx + k * dx, sy + k * dy, dx, dy))
                            best = max(best, top * (1.0 - k / float(LIP_BACK)))
                key = (x * PER_CELL + i, y * PER_CELL + j)
                raised[key] = max(raised.get(key, 0.0), best)
    for (gx, gy), v in raised.items():
        # a point shared with a cell that is no ledge stays on the ground
        touching = [(cx, cy) for cx in {(gx - 1) // PER_CELL, gx // PER_CELL}
                    for cy in {(gy - 1) // PER_CELL, gy // PER_CELL}
                    if 0 <= cx < layout.w and 0 <= cy < layout.h]
        if all(c in cells for c in touching):
            h[gy][gx] += v
    return len(cells)


def flat_lattice(layout):
    return [[0.0] * (layout.w * PER_CELL + 1) for _ in range(layout.h * PER_CELL + 1)]


def layout_heights(layout_id):
    """The lattice of a layout: its relief where it is solved, its ledges."""
    roles_layout = open_roles(layout_id)
    group = drawn_group(layout_id)
    if group:
        h = [row[:] for row in solve_drawn(group)[layout_id]]
    elif layout_id in ENABLED:
        role, cell, h = solve(roles_layout)
    else:
        h = flat_lattice(roles_layout)
    art = _ART.get(layout_id)
    if art is None:
        art = _ART[layout_id] = vb.LayoutArt(layout_id)
    ledges = ledge_berms(roles_layout, art, h)
    return roles_layout, h, ledges


def ledge_layouts():
    """Every outdoor layout that has a ledge."""
    layouts = json.load(open(os.path.join(vb.ROOT, "data", "layouts", "layouts.json"),
                             encoding="utf-8"))["layouts"]
    out = []
    for e in layouts:
        path = os.path.join(vb.ROOT, e["blockdata_filepath"])
        if not os.path.exists(path):
            continue
        layout = open_roles(e["id"], connections=False)
        if layout.outdoor and ledge_cells(layout, junctions=False):
            out.append(e["id"])
    return out


def awash(layout, mass, kind):
    """A rock drawn mostly in the colours of the water round it - foam over a
    submerged reef - lies at the water line; only a rock drawn as rock is
    raised out of the sea."""
    art = _ART.get(layout.layout_id)
    if art is None:
        art = _ART[layout.layout_id] = vb.LayoutArt(layout.layout_id)
    water = set()
    for (x, y) in mass:
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < layout.w and 0 <= ny < layout.h and kind[ny][nx] == "water":
                water.update(art.cell_image(art.metatile(nx, ny)).getdata())
    same = total = 0
    for (x, y) in mass:
        for p in art.cell_image(art.metatile(x, y)).getdata():
            total += 1
            same += p in water
    return total and same > total // 2


def cell_grid(h, x, y):
    return [[h[y * PER_CELL + j][x * PER_CELL + i] for i in range(PER_CELL + 1)]
            for j in range(PER_CELL + 1)]


def relief_cells(layout, h):
    """Cells whose lattice is not all zero: (x, y, grid)."""
    out = []
    for y in range(layout.h):
        for x in range(layout.w):
            g = cell_grid(h, x, y)
            if any(abs(v) > 0.25 for row in g for v in row):
                out.append((x, y, g))
    return out


# ── preview ─────────────────────────────────────────────────────────────────

def preview(layout_id, out_dir, cams):
    art = vb.LayoutArt(layout_id)
    roles_layout, h, _ = layout_heights(layout_id)
    grids = {(x, y): g for (x, y, g) in relief_cells(art, h)}
    depth = _SHIFT.get(layout_id)
    ids = {}
    for y in range(art.h):
        for x in range(art.w):
            ids.setdefault(art.metatile(x, y), len(ids))
    cols = 32
    from PIL import Image
    tex = Image.new("RGBA", (cols * 16, ((len(ids) + cols - 1) // cols) * 16), (0, 0, 0, 255))
    for m, i in ids.items():
        tex.paste(art.cell_image(m), ((i % cols) * 16, (i // cols) * 16))
    tris = []
    for y in range(art.h):
        for x in range(art.w):
            i = ids[art.metatile(x, y)]
            u0, v0 = (i % cols) * 16, (i // cols) * 16
            g = grids.get((x, y))
            if g is None:
                g = [[0.0] * (PER_CELL + 1) for _ in range(PER_CELL + 1)]
            for j in range(PER_CELL):
                for k in range(PER_CELL):
                    def P(a, b):
                        hh = g[b][a] / 16.0
                        zz = depth[y * PER_CELL + b][x * PER_CELL + a] / 16.0 if depth else hh
                        return (x + a * STEP / 16.0, hh, y + b * STEP / 16.0 + zz,
                                u0 + a * STEP, v0 + b * STEP)
                    A, B, C, D = P(k, j), P(k + 1, j), P(k + 1, j + 1), P(k, j + 1)
                    tris.append(((A, B, C), 1.0))
                    tris.append(((A, C, D), 1.0))
    paths = []
    for name, (tx, tz, pitch, yaw, dist) in cams:
        cam = vb.Camera((tx, 0.0, tz), pitch=pitch, yaw=yaw, distance=dist)
        img = vb.render_scene(cam, [(tris, tex)], scale=2)
        path = os.path.join(out_dir, "relief_%s_%s.png" % (layout_id.lower(), name))
        img.save(path)
        paths.append(path)
        if depth is not None:
            path = os.path.join(out_dir, "relief_%s_%s_lines.png" % (layout_id.lower(), name))
            check_lines(layout_id, cam, img.copy(), h, depth).save(path)
            paths.append(path)
    return paths


def check_lines(layout_id, cam, img, h, depth):
    """A camera proof: the geometry each tile of rock should have, over the
    render. Red is what the drawing asks for - a face's top edge at its
    level and its foot a level lower, a band's high and low edges, straight
    along the tile - and yellow is where the mesh actually is along the
    same edges. Where yellow leaves red, the shape is wrong."""
    from PIL import ImageDraw
    group = drawn_group(layout_id)
    ox, oy = DRAWN[group][layout_id]
    top, kinds, _, _ = _ROCK[group]
    cam2 = vb.Camera(cam.target, cam.pitch, cam.yaw, cam.distance, cam.fov,
                     img.width, img.height)
    d = ImageDraw.Draw(img)

    def line(pts, colour):
        xy = [cam2.project(p) for p in pts]
        xy = [(q[0], q[1]) for q in xy if q is not None]
        if len(xy) > 1:
            d.line(xy, fill=colour, width=2)

    def world(i, j, hh):
        return (i * STEP / 16.0, hh / 16.0, j * STEP / 16.0 + hh / 16.0)

    def actual(i, j):
        return (i * STEP / 16.0, h[j][i] / 16.0, j * STEP / 16.0 + depth[j][i] / 16.0)

    L = len(h) - 1, len(h[0]) - 1
    for (gx, gy), kind_ in kinds.items():
        cx, cy = gx - ox, gy - oy
        hi = top.get((gx, gy))
        if hi is None or not (0 <= cx < L[1] // PER_CELL and 0 <= cy < L[0] // PER_CELL):
            continue
        i0, j0 = cx * PER_CELL, cy * PER_CELL
        if kind_ == "face":
            edges = [([(i0 + k, j0) for k in range(PER_CELL + 1)], hi),
                     ([(i0 + k, j0 + PER_CELL) for k in range(PER_CELL + 1)], hi - LEVEL)]
        elif kind_ == "band":
            s_ = _DRAWN_SIDE[group][gy][gx]
            high = i0 + (PER_CELL if s_ > 0 else 0)
            low = i0 + (0 if s_ > 0 else PER_CELL)
            edges = [([(high, j0 + k) for k in range(PER_CELL + 1)], hi),
                     ([(low, j0 + k) for k in range(PER_CELL + 1)], hi - LEVEL)]
        else:
            continue
        for pts, want in edges:
            pts = list(pts)
            line([actual(i, j) for (i, j) in pts], (255, 230, 0))
            line([world(i, j, want) for (i, j) in pts], (255, 0, 0))
    return img


_ROLES = []


def open_roles(layout_id, connections=True):
    layouts = json.load(open(os.path.join(vb.ROOT, "data", "layouts", "layouts.json"),
                             encoding="utf-8"))["layouts"]
    entry = next(e for e in layouts if e["id"] == layout_id)
    if not _ROLES:
        _ROLES.append(vc.MapEvents())
    layout = vc.Layout(entry, _ROLES[0])
    layout.layout_id = layout_id
    if not connections:
        return layout
    # the sides some map using this layout is connected to another on
    sides = set()
    maps = os.path.join(vb.ROOT, "data", "maps")
    for name in os.listdir(maps):
        path = os.path.join(maps, name, "map.json")
        if not os.path.exists(path):
            continue
        m = json.load(open(path, encoding="utf-8"))
        if m.get("layout") == layout_id:
            for c in m.get("connections") or []:
                if c.get("direction") in ("up", "down", "left", "right"):
                    sides.add(c["direction"])
    layout.connected_sides = sorted(sides)
    layout.layout_id = layout_id
    return layout

def proof(name, path):
    """The model's terraces over the drawing of a drawn group, whole canvas:
    a thick line where each terrace's flat top ends, in its level's colour,
    its level written in each of its cells, and a dot on every point the
    slope above lifts off the land below (a face's foot). What the model
    claims, checked against the art without the game."""
    from PIL import Image, ImageDraw
    out = solve_drawn(name)
    layouts, CW, CH, _, _, _, _ = drawn_canvas(name)
    cell = _CELLS[name]
    H = [[None] * (CW * PER_CELL + 1) for _ in range(CH * PER_CELL + 1)]
    for lid, (ox, oy) in DRAWN[name].items():
        for j, row in enumerate(out[lid]):
            for i, v in enumerate(row):
                H[oy * PER_CELL + j][ox * PER_CELL + i] = v
    Z = 4
    im = Image.new("RGB", (CW * 16 * Z, CH * 16 * Z))
    for lid, (ox, oy) in DRAWN[name].items():
        A = _ART[lid]
        for cy in range(A.h):
            for cx in range(A.w):
                im.paste(A.cell_image(A.metatile(cx, cy)).convert("RGB").resize(
                    (16 * Z, 16 * Z), Image.NEAREST), ((ox + cx) * 16 * Z, (oy + cy) * 16 * Z))
    d = ImageDraw.Draw(im)
    palette = [(0, 170, 0), (0, 120, 255), (255, 200, 0), (255, 90, 0), (230, 0, 0), (200, 0, 200)]
    colour = lambda lv: palette[int(lv) // LEVEL % len(palette)] if lv >= 0 else (255, 255, 255)
    S = 16 * Z
    for Y in range(CH):
        for X in range(CW):
            lv = cell[Y][X]
            if lv is None:
                continue
            px, py = X * S, Y * S
            d.text((px + S // 2 - 6, py + S // 2 - 6), str(int(lv)), fill=colour(lv))
            for dx, dy, seg in ((0, -1, (px, py, px + S - 1, py)),
                                (0, 1, (px, py + S - 1, px + S - 1, py + S - 1)),
                                (-1, 0, (px, py, px, py + S - 1)),
                                (1, 0, (px + S - 1, py, px + S - 1, py + S - 1))):
                nx, ny = X + dx, Y + dy
                nb = cell[ny][nx] if 0 <= nx < CW and 0 <= ny < CH else lv
                if nb is None or nb < lv:
                    d.line(seg, fill=colour(lv), width=6)
    for j in range(CH * PER_CELL + 1):
        for i in range(CW * PER_CELL + 1):
            v = H[j][i]
            base = cell[min(j // PER_CELL, CH - 1)][min(i // PER_CELL, CW - 1)]
            if v is not None and base is not None and v > base + 0.5:
                x, y = i * STEP * Z, j * STEP * Z
                d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255))
    im.save(path)
    return path



# ── export ──────────────────────────────────────────────────────────────────

MAGIC = b"VXL3"
HEIGHT_UNIT = 2      # pixels per stored step of a drawn map's height


def export(layout_ids, path):
    """"VXL3", u16 layouts, u16 per-cell lattice side (5), then per layout
    u16 layout id, u16 cells, u16 width, u16 height, u32 offset, s16 base; cells
    are u8 x, u8 y and 25 signed bytes of height, row major, over the base: the
    level the whole map is lifted to (world_levels), pixels. A map with a base
    and no relief is written with no cells. Bit 15 of the height marks
    a map read off its drawing (DRAWN): its relief is the whole of the terrain,
    and every blocked cell of rock is written, level or not. Bit 14 says its
    heights are in units of HEIGHT_UNIT pixels (a mountain stands taller than
    a byte of pixels). A point's depth is its height, so it is not written."""
    layouts = json.load(open(os.path.join(vb.ROOT, "data", "layouts", "layouts.json"),
                             encoding="utf-8"))["layouts"]
    index = {e["id"]: i + 1 for i, e in enumerate(layouts)}
    tables = []
    world = world_levels()["base"]
    layout_ids = list(layout_ids) + sorted(
        l for l, b in world.items() if b and l not in layout_ids and drawn_group(l) is None)
    for lid in layout_ids:
        roles_layout, h, ledges = layout_heights(lid)
        cells = relief_cells(roles_layout, h)
        drawn = drawn_group(lid) is not None
        if drawn:
            have = {(x, y) for (x, y, _) in cells}
            cells += [(x, y, cell_grid(h, x, y)) for y in range(roles_layout.h)
                      for x in range(roles_layout.w)
                      if (x, y) not in have and (
                          (roles_layout.blocked(x, y)
                           and roles_layout.role_at(x, y) in RELIEF_ROLES)
                          or any(abs(v) > 0.25 for row in cell_grid(_SHIFT[lid], x, y)
                                 for v in row))]
            cells.sort(key=lambda c: (c[1], c[0]))
        lift = _BASE.get(lid, 0) if drawn else world.get(lid, 0)
        if not cells and not lift:
            continue
        unit = HEIGHT_UNIT if drawn else 1
        cells = [(x, y, [[v / unit for v in row] for row in g]) for (x, y, g) in cells]
        tables.append((index[lid], cells, roles_layout.w,
                       roles_layout.h | (0xC000 if drawn else 0), lift))
        print("relief %-34s %4d cells lifted, %3d ledge cells, map at %+d px"
              % (lid, len(cells), ledges, lift))
    side = PER_CELL + 1
    head = MAGIC + struct.pack("<HH", len(tables), side)
    offset = len(head) + 14 * len(tables)
    body = bytearray()
    idx = bytearray()
    for lid, cells, w, hh, lift in sorted(tables):
        idx += struct.pack("<HHHHIh", lid, len(cells), w, hh, offset + len(body), int(lift))
        for (x, y, g) in cells:
            body += struct.pack("<BB", x, y)
            body += struct.pack("<%db" % (len(g) * len(g[0])),
                                *(max(-128, min(127, int(round(v)))) for row in g for v in row))
    blob = head + idx + body
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(blob)
    print("voxel relief: %d layouts, %.1f KiB -> %s" % (len(tables), len(blob) / 1024.0, path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", default=None)
    ap.add_argument("--layouts", default=None,
                    help="default: the solved maps and every map with a ledge")
    ap.add_argument("--output", default=None)
    ap.add_argument("--at", default=None, help="x,z of the preview target")
    ap.add_argument("--proof", default=None,
                    help="directory: each drawn group's terraces over its drawing")
    args = ap.parse_args()
    if args.layouts:
        lids = args.layouts.split(",")
    else:
        drawn = [l for members in DRAWN.values() for l in members]
        lids = list(ENABLED) + [l for l in drawn if l not in ENABLED]
        lids += [l for l in ledge_layouts() if l not in lids]
        lids = list(dict.fromkeys(lids))   # a neighbour is in its alternate's group too
    if args.preview:
        os.makedirs(args.preview, exist_ok=True)
        tx, tz = (float(v) for v in args.at.split(",")) if args.at else (20.0, 60.0)
        cams = [("game", (tx, tz, 40.0, 0.0, 12.0)), ("yaw40", (tx, tz, 35.0, 40.0, 11.0)),
                ("yaw-45", (tx, tz, 30.0, -45.0, 10.0)), ("high", (tx, tz, 60.0, 15.0, 12.0)),
                ("near", (tx, tz, 40.0, 0.0, 7.0))]
        for lid in lids:
            for p in preview(lid, args.preview, cams):
                print("preview:", p)
    if args.proof:
        os.makedirs(args.proof, exist_ok=True)
        for name in DRAWN:
            print("proof:", proof(name, os.path.join(args.proof, "terraces_%s.png" % name)))
    if args.output:
        export(lids, args.output)


if __name__ == "__main__":
    main()
