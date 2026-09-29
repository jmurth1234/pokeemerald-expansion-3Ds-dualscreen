#!/usr/bin/env python3
"""Build the voxel building models, prove them, and preview them.

    python gen_voxel_buildings.py [--preview DIR] [--only NAME]

For every spec in voxel_building_specs.py:
  * extracts the art with the ground made transparent;
  * builds the mesh;
  * renders it in the GBA projection and compares every pixel with the art
    (the build fails unless the model reproduces the drawing exactly);
  * with --preview, renders the reference layout from several cameras.
"""

import argparse
from PIL import Image
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voxel_building as vb  # noqa: E402
import voxel_building_specs as specs  # noqa: E402

PORT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def component_specs(spec, layouts):
    """Expand a `components` spec - hedges, walls: objects with no fixed
    shape - into one ordinary spec per connected run of its metatiles, in
    every layout whose secondary tileset is the one named."""
    comp = spec["components"]
    tiles = comp["tiles"]
    layouts_json = json.load(open(os.path.join(vb.ROOT, "data", "layouts", "layouts.json"),
                                  encoding="utf-8"))["layouts"]
    out = []
    for entry in layouts_json:
        if entry.get("secondary_tileset") != comp["secondary"] or "blockdata_filepath" not in entry:
            continue
        blocks = vb.read_u16(os.path.join(vb.ROOT, entry["blockdata_filepath"]))
        w, h = entry["width"], entry["height"]
        seen = set()
        for y in range(h):
            for x in range(w):
                if (x, y) in seen or (blocks[y * w + x] & 0x3FF) not in tiles:
                    continue
                todo, cells = [(x, y)], []
                seen.add((x, y))
                while todo:
                    cx, cy = todo.pop()
                    cells.append((cx, cy))
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if (0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen
                                and (blocks[ny * w + nx] & 0x3FF) in tiles):
                            seen.add((nx, ny))
                            todo.append((nx, ny))
                pieces = [cells]
                size = comp.get("block")
                if size:
                    # A thin object of long runs (a railing) is cut into
                    # blocks aligned on the map, so a stretch that repeats is
                    # one model placed many times.
                    by = {}
                    for (cx, cy) in cells:
                        by.setdefault((cx // size, cy // size), []).append((cx, cy))
                    pieces = list(by.values())
                for piece in pieces:
                    out.append(piece_spec(spec, comp, entry, blocks, w, h, piece, tiles))
    # identical blocks: one model, placed at each
    if comp.get("block"):
        merged = {}
        for sp in out:
            key = (sp["layout"], sp["pattern"])
            if key in merged:
                merged[key]["repeat_at"].append(sp["rect"][:2])
            else:
                sp["repeat_at"] = [sp["rect"][:2]]
                merged[key] = sp
        out = list(merged.values())
    return out


def piece_spec(spec, comp, entry, blocks, w, h, cells, tiles):
    x0, y0 = min(c[0] for c in cells), min(c[1] for c in cells)
    x1, y1 = max(c[0] for c in cells) + 1, max(c[1] for c in cells) + 1
    owned = {(cx - x0, cy - y0) for cx, cy in cells}
    pattern = tuple((i, j, blocks[(y0 + j) * w + x0 + i] & 0x3FF)
                    for j in range(y1 - y0) for i in range(x1 - x0) if (i, j) in owned)
    name = "%s_%s_%d_%d" % (spec["name"], entry["id"][len("LAYOUT_"):].lower(), x0, y0)
    return {"name": name, "layout": entry["id"], "rect": (x0, y0, x1 - x0, y1 - y0),
            "ground": spec["ground"], "owned": owned, "relief": comp, "pattern": pattern}


def pick_side(art, height):
    """An 8-column tile of the object's own drawn front, for the faces the
    drawing never shows: the longest stretch of columns whose runs end on the
    same row with a full front."""
    W, Hh = art.size
    px = art.load()
    ends = []
    for u in range(W):
        end = None
        for v in range(Hh - 1, -1, -1):
            if px[u, v][3] >= 128:
                end = v + 1
                break
        full = end is not None and end >= height and all(
            px[u, v][3] >= 128 for v in range(end - height, end))
        ends.append(end if full else None)
    for width in (8, 6, 4):
        best = None
        for u in range(W - width + 1):
            e = ends[u]
            if e is not None and all(ends[u + k] == e for k in range(width)):
                if best is None or e > best[1]:
                    best = (u, e)
        if best is not None:
            u, e = best
            return vb.Tile(u, e - height, u + width, e)
    # No straight front at all (a railing is mostly gaps): the block of the
    # object's own drawing with the most drawn pixels; its gaps stay gaps.
    best, score = None, -1
    for v in range(0, max(1, Hh - height + 1)):
        for u in range(0, max(1, W - 4 + 1)):
            n = sum(px[u + i, v + j][3] >= 128 for i in range(min(4, W))
                    for j in range(min(height, Hh)))
            if n > score:
                best, score = (u, v), n
    u, v = best
    return vb.Tile(u, v, u + min(4, W), v + min(height, Hh))


def kit_specs(spec):
    """Expand a `kit` spec: every building of one kit in its layout, found by
    its top-left corner metatile, its top row and its bottom-left foot, each
    modelled from its own art. Identical drawings are one model."""
    kit = spec["kit"]
    layout = vb.LayoutArt(kit["layout"])
    out, grids = [], set()
    for y in range(layout.h):
        for x in range(layout.w):
            if layout.metatile(x, y) != kit["corner"]:
                continue
            x2 = x + 1
            while x2 < layout.w and layout.metatile(x2, y) in kit["top"]:
                x2 += 1
            if x2 >= layout.w or layout.metatile(x2, y) not in kit["end"]:
                continue
            y2 = y + 1
            while y2 < layout.h and layout.metatile(x, y2) != kit["foot"]:
                y2 += 1
            if y2 >= layout.h:
                continue
            w, h = x2 - x + 1, y2 - y + 1
            grid = tuple(layout.metatile(x + i, y + j) for j in range(h) for i in range(w))
            if grid in grids:
                continue
            grids.add(grid)
            top_row = [layout.metatile(x + i, y) for i in range(w)]
            meta = {"unit": 0x222 in top_row and top_row[-1] == 0x223}
            out.append({"name": "%s_%d_%d" % (spec["name"], x, y), "layout": kit["layout"],
                        "rect": (x, y, w, h), "ground": spec["ground"],
                        "parts": (lambda f, W, H, M: (lambda: f(W, H, M)))(
                            spec["parts"], w * 16, h * 16, meta),
                        "exact": spec["exact"](w * 16, h * 16, meta)})
    return out


def _inside(shape, x, y):
    """Is pixel (x, y) in a piece's shape: rectangles (x0, y0, x1, y1),
    ellipses ("ellipse", cx, cy, rx, ry) and polygons (a list of points), any
    number of each."""
    cx, cy = x + 0.5, y + 0.5
    for part in shape:
        if part[0] == "ellipse":
            _, ex, ey, rx, ry = part
            if ((cx - ex) / rx) ** 2 + ((cy - ey) / ry) ** 2 <= 1.0:
                return True
            continue
        if len(part) == 4 and not isinstance(part[0], (tuple, list)):
            x0, y0, x1, y1 = part
            if x0 <= cx < x1 and y0 <= cy < y1:
                return True
            continue
        hit = False
        n = len(part)
        for k in range(n):
            (ax, ay), (bx, by) = part[k], part[(k + 1) % n]
            if (ay > cy) != (by > cy) and cx < ax + (bx - ax) * (cy - ay) / (by - ay):
                hit = not hit
        if hit:
            return True
    return False


class ArtMismatch(Exception):
    """The drawing no longer matches a spec (another tileset's art)."""


def interior_specs(spec):
    """Expand an `interior` spec: a room cut into pieces, one model each.

    A room's drawing is walls, furniture and floor. Every pixel equal to the
    floor metatile's own at that place in its cell is floor, left to the
    terrain; the rest is claimed by the first piece (front first) whose shape
    holds it. A piece is read column by column off the pixels it claimed
    (vb.Relief: its top, and its front `height` rows at its foot), standing on
    `base` - a machine on a counter. A piece does not claim the colours it
    `leave`s where they run on out of its shape: the wall seen between a
    plant's leaves or round a machine's corners. With `fill` = P, what the pieces in front
    of it hide of it is drawn from its own pixels P columns away, so the wall
    behind a bookcase is a wall and not a hole in the shape of one. Pixels no
    piece claims are the floor's own marks (an emblem, a mat, shadows), laid
    flat by the pieces whose cells hold them.

    Each piece is its own model, of the cells it covers: a room would not fit
    one chunk's vertices whole, and apart the pieces spread over the chunks
    they stand in.
    """
    room = spec["interior"]
    layout = vb.LayoutArt(room["layout"])
    W, H = layout.w * 16, layout.h * 16
    full = Image.new("RGBA", (W, H))
    for y in range(layout.h):
        for x in range(layout.w):
            full.paste(layout.cell_image(layout.metatile(x, y)), (x * 16, y * 16))
    fpx = full.load()
    floors = [layout.cell_image(m).load() for m in room["ground"]]
    pieces = room["pieces"]
    # Floor is a pixel the floor metatile has there too - and, inside a
    # piece's shape, one that the open floor reaches through such pixels: a
    # counter top can be painted in the floor's own cream, but its outline
    # closes it off.
    match = [[any(f[x % 16, y % 16] == fpx[x, y] for f in floors) for x in range(W)]
             for y in range(H)]
    shaped = [[any(_inside(pc["shape"], x, y) for pc in pieces) for x in range(W)]
              for y in range(H)]
    ground = [[match[y][x] and not shaped[y][x] for x in range(W)] for y in range(H)]
    # The room's front corners: black in the drawing, the outside of a room
    # whose front wall the GBA never draws. Seen from the console's camera, a
    # black triangle on the floor is a hole; they are floor.
    opened = [[_inside(room.get("open", ()), x, y) for x in range(W)] for y in range(H)]
    todo = [(x, y) for y in range(H) for x in range(W) if ground[y][x]]
    while todo:
        x, y = todo.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < W and 0 <= ny < H and match[ny][nx] and not ground[ny][nx]:
                ground[ny][nx] = True
                todo.append((nx, ny))
    # the floor's shaded variants: their colours are floor wherever they run
    # on from the open floor, never a piece's
    shade = set()
    for m in room.get("shade", ()):
        shade.update("%02x%02x%02x" % c[:3] for c in layout.cell_image(m).getdata())
    owner = [[None] * W for _ in range(H)]
    for k, pc in enumerate(pieces):
        inside = [[_inside(pc["shape"], x, y) for x in range(W)] for y in range(H)]
        # what the piece leaves: its `leave` colours where they run on out of
        # its shape - the wall round a machine's corners, not the machine's
        # own orange lamps inside its outline - and, but for a wall, the
        # floor's shadows
        leave = set(pc.get("leave", ())) | (set() if pc.get("fill") or pc.get("facet") else shade)
        left = set()
        if leave:
            def colour(x, y):
                return "%02x%02x%02x" % fpx[x, y][:3]
            todo = [(x, y) for y in range(H) for x in range(W)
                    if not inside[y][x] and colour(x, y) in leave]
            seen = set(todo)
            while todo:
                x, y = todo.pop()
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if (0 <= nx < W and 0 <= ny < H and (nx, ny) not in seen
                            and colour(nx, ny) in leave):
                        seen.add((nx, ny))
                        todo.append((nx, ny))
                        if inside[ny][nx]:
                            left.add((nx, ny))
        for y in range(H):
            for x in range(W):
                # a wall (a filled piece, a corner) is solid in its whole shape: its
                # baseboard may be drawn in the floor's own colours
                # `claim`: where an outline happens to be drawn in the floor's
                # own colour at the floor's own place - a bed's corner on a
                # nail head - and so was taken for floor
                if (owner[y][x] is None and (not ground[y][x] or pc.get("fill") or pc.get("facet")
                                             or _inside(pc.get("claim", ()), x, y))
                        and inside[y][x]
                        and (x, y) not in left):
                    owner[y][x] = k
    # the claims, for the eye: each piece a colour, floor dark, marks white
    claims = Image.new("RGB", (W, H))
    cpx = claims.load()
    for y in range(H):
        for x in range(W):
            o = owner[y][x]
            if o is not None:
                cpx[x, y] = ((o * 97) % 200 + 55, (o * 57) % 200 + 55, (o * 151) % 200 + 55)
            else:
                cpx[x, y] = (30, 30, 36) if ground[y][x] else (255, 255, 255)
    out_dir = os.path.join(PORT, "build", "buildings")
    os.makedirs(out_dir, exist_ok=True)
    claims.resize((W * 3, H * 3), Image.NEAREST).save(
        os.path.join(out_dir, spec["name"] + "_claims.png"))
    # each piece's pixels, and what of it is hidden behind earlier pieces
    walls = {k for k, pc in enumerate(pieces) if pc.get("fill")}
    drawn = []
    for k, pc in enumerate(pieces):
        mine = {(x, y) for y in range(H) for x in range(W) if owner[y][x] == k}
        fill = {}
        period = pc.get("fill")
        if period:
            for y in range(H):
                for x in range(W):
                    o = owner[y][x]
                    if o is None or o >= k or not _inside(pc["shape"], x, y):
                        continue
                    for d in range(period, W, period):
                        # from any wall's own pixel, this piece's or the
                        # other half's: a row's every panel may be hidden
                        src = next(((xx, y) for xx in (x - d, x + d)
                                    if 0 <= xx < W and owner[y][xx] in walls), None)
                        if src:
                            fill[(x, y)] = fpx[src]
                            break
        if not mine and not pc.get("walls"):
            raise ArtMismatch("%s: piece %s claims no pixel" % (spec["name"], pc["name"]))
        drawn.append((mine, fill))
    # the room with what the pieces hide filled in: where hidden faces are
    # dressed from
    filled = full.copy()
    lpx = filled.load()
    for mine, fill in drawn:
        for (x, y), c in fill.items():
            lpx[x, y] = c
    rects = []
    for k, (mine, fill) in enumerate(drawn):
        pts = list(mine) + list(fill)
        if pieces[k].get("facet"):
            (xa, _, fa), (xb, _, fb) = pieces[k]["facet"]
            pts += [(int(xa), int(fa) - 1), (int(xb) - 1, int(fb) - 1)]
        # cells a piece answers for beyond its pixels: where its side walls
        # run, the black corners it closes the room at
        for wall in pieces[k].get("walls", ()):
            wa, wb = wall[0], wall[1]
            pts += [(min(int(wa[0]), W - 1), min(int(wa[1]), H - 1)),
                    (min(int(wb[0]), W - 1), min(int(wb[1]), H - 1))]
        pts += [(cx * 16, cy * 16) for (cx, cy) in pieces[k].get("cells", ())]
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        rects.append((min(xs) // 16, min(ys) // 16, max(xs) // 16 + 1, max(ys) // 16 + 1))
    decal_of = {}
    for k, (x0, y0, x1, y1) in enumerate(rects):
        for cy in range(y0, y1):
            for cx in range(x0, x1):
                decal_of.setdefault((cx, cy), k)
    for y in range(layout.h):
        for x in range(layout.w):
            if (x, y) not in decal_of and (layout.blocks[y * layout.w + x] >> 10) & 3:
                print("  %s: blocked cell %d,%d is in no piece" % (spec["name"], x, y))
    out = []
    for k, pc in enumerate(pieces):
        mine, fill = drawn[k]
        x0, y0, x1, y1 = rects[k]
        w, h = x1 - x0, y1 - y0
        obj = Image.new("RGBA", (w * 16, h * 16), (0, 0, 0, 0))
        # the art: the piece and its floor marks; the marks alone below it;
        # below those, the stretch of the room its hidden faces are dressed
        # from, which need not be its own (a side wall's inside is the back
        # wall's orange, not the line its top is drawn with)
        sample = pc.get("side")
        sh = (sample[3] - sample[1]) if sample else 0
        sw = (sample[2] - sample[0]) if sample else 0
        lid = pc.get("top")
        th = (lid[3] - lid[1]) if lid else 0
        tw = (lid[2] - lid[0]) if lid else 0
        art = Image.new("RGBA", (max(w * 16, sw, tw), 2 * h * 16 + sh + th), (0, 0, 0, 0))
        if sample:
            art.paste(filled.crop(sample), (0, 2 * h * 16))
        if lid:
            # and the stretch its hidden top is laid with
            art.paste(filled.crop(lid), (0, 2 * h * 16 + sh))
        opx, apx = obj.load(), art.load()
        for (x, y) in mine:
            opx[x - x0 * 16, y - y0 * 16] = fpx[x, y]
        for (x, y), c in fill.items():
            opx[x - x0 * 16, y - y0 * 16] = c
        art.paste(obj, (0, 0))
        cells = sorted(c for c, o in decal_of.items() if o == k)
        for (cx, cy) in cells:
            for j in range(16):
                for i in range(16):
                    x, y = cx * 16 + i, cy * 16 + j
                    if not ground[y][x] and owner[y][x] is None and not opened[y][x]:
                        lx, ly = x - x0 * 16, y - y0 * 16
                        apx[lx, ly] = fpx[x, y]
                        apx[lx, ly + h * 16] = fpx[x, y]
        if sample:
            side = vb.Tile(0, 2 * h * 16, sw, 2 * h * 16 + sh)
        else:
            side = pick_side(obj, int(pc["height"]))
        if pc.get("facet"):
            # a chamfered corner: one wall along its foot, not a staircase of
            # columns whose steps show from any other angle. It stands a
            # pixel south of its drawn foot, a pixel taller - the same pixels
            # at 45 degrees - so its foot row is not level with the floor.
            (xa, ta, fa), (xb, tb, fb) = pc["facet"]
            ox, oz = x0 * 16, y0 * 16
            parts = [vb.Facet(pc["name"], (xa - ox, fa + 1 - oz), (xb - ox, fb + 1 - oz),
                              fa + 1 - ta, fb + 1 - tb)]
        elif not mine:
            parts = []  # a stairwell's sides: walls only, drawn nowhere
        else:
            # furniture stands on its foot, the lowest row it is drawn to; a
            # filled piece (a wall) is whole in every column already - but a
            # wall with a doorway in it names its foot, or the lintel would
            # stand at the doorway's top
            if pc.get("foot") is not None:
                foot = pc["foot"] - y0 * 16
            else:
                foot = None if pc.get("fill") else max(y for _, y in mine) + 1 - y0 * 16
            back = pc.get("back") if pc.get("against") is None else pc["against"]
            if back is not None:
                back = back - y0 * 16 - pc.get("base", 0)
            if pc.get("card"):
                parts = [vb.Card(pc["name"], obj, foot)]
            else:
                top_tile = (vb.Tile(0, 2 * h * 16 + sh, tw, 2 * h * 16 + sh + th)
                            if lid else None)
                relief = vb.Relief(pc["name"], obj, pc["height"], side, foot=foot,
                                   solid=pc.get("solid", False), back=back, top_tile=top_tile,
                                   against=pc.get("against") is not None)
                parts = [vb.Lifted(relief, pc.get("base", 0)) if pc.get("base") else relief]
        for n, wall in enumerate(pc.get("walls", ())):
            # the room's side walls: edge on to the GBA camera, so drawn
            # nowhere, and dressed with the stretch of wall `side` names;
            # a third member is a wall's own height (a stairwell's sides)
            wa, wb = wall[0], wall[1]
            tall = wall[2] if len(wall) > 2 else pc["height"]
            ox, oz = x0 * 16, y0 * 16
            # a stairwell's back shows at 45 degrees only where the wall in
            # front of it has its doorway: the room's check judges it, not
            # its own model's
            behind = "" if mine else "~behind"
            parts.append(vb.PlainWall("%s_side%d%s" % (pc["name"], n, behind),
                                      (wa[0] - ox, wa[1] - oz),
                                      (wb[0] - ox, wb[1] - oz), -1, tall, side))
        # only the cells with a mark: a quad over nothing but floor would sample
        # outside the drawing once the export crops it to what is opaque
        local = [(cx - x0, cy - y0) for (cx, cy) in cells
                 if art.crop(((cx - x0) * 16, (cy - y0) * 16 + h * 16,
                              (cx - x0) * 16 + 16, (cy - y0) * 16 + h * 16 + 16)).getbbox()]
        if local:
            parts.append(vb.Decal(pc["name"] + "_floor", local, h * 16))
        out.append({"name": "%s_%s" % (spec["name"], pc["name"]), "layout": room["layout"],
                    "rect": (x0, y0, w, h), "ground": room["ground"], "art": art,
                    "parts": (lambda ps: (lambda: ps))(parts),
                    "exact": [(0, 0, w * 16, h * 16)], "interior": spec["name"]})
    return out


def build_models(only=None, allow_mismatch=False):
    models = []
    layouts = {}
    expanded = []
    for spec in specs.SPECS:
        try:
            if "components" in spec:
                expanded += component_specs(spec, layouts)
            elif "kit" in spec:
                expanded += kit_specs(spec)
            elif "interior" in spec:
                expanded += interior_specs(spec)
            else:
                expanded.append(spec)
        except ArtMismatch as e:
            # A tree whose art differs from the reference (the expansion's
            # FRLG tilesets) cannot reproduce this model; leave it flat.
            if not allow_mismatch:
                raise
            print("voxel buildings: skipping %s (%s)" % (spec.get("name", "?"), e))
            continue
    for spec in expanded:
        if only and not spec["name"].startswith(only):
            continue
        lid = spec["layout"]
        if lid not in layouts:
            layouts[lid] = vb.LayoutArt(lid)
        layout = layouts[lid]
        x, y, w, h = spec["rect"]
        owned = spec.get("owned")
        if "art" in spec:
            art = spec["art"]  # composed by its expander (a room's piece)
        else:
            art = layout.building_art(x, y, w, h, layout.ground_tiles(spec["ground"]), cells=owned,
                                      ground_px=layout.ground_pixels(spec["ground"]) if owned else None)
        if "relief" in spec:
            height = spec["relief"]["height"]
            relief = spec["relief"]
            parts = [vb.Relief("relief", art, height, pick_side(art, height),
                               hull=relief.get("hull", 0), bridge=relief.get("bridge", 0))]
            spec["exact"] = [(0, 0, w * 16, h * 16)]
        else:
            parts = spec["parts"]()
        model = vb.Model(spec["name"], art, parts, (w, h), spec["ground"][0])
        model.owned = owned if owned is not None else {(i, j) for i in range(w) for j in range(h)}
        model.spec = spec
        model.layout = layout
        models.append(model)
    return models


CAMERAS = [
    ("game", dict(pitch=40.0, yaw=0.0, distance=12.0)),
    ("left", dict(pitch=40.0, yaw=0.0, distance=12.0, shift=(4.5, 0))),
    ("right", dict(pitch=40.0, yaw=0.0, distance=12.0, shift=(-4.5, 0))),
    ("yaw35", dict(pitch=35.0, yaw=35.0, distance=9.0)),
    ("yaw-50", dict(pitch=30.0, yaw=-50.0, distance=8.0)),
    ("high", dict(pitch=62.0, yaw=15.0, distance=8.0)),
]


def preview(model, out_dir, cams=None):
    layout = model.layout
    x, y, w, h = model.spec["rect"]
    override = {(x + i, y + j): model.ground_metatile for i in range(w) for j in range(h)}
    ground, gtex = vb.ground_tris(layout, x - 8, y - 6, x + w + 8, y + h + 8, override, None)
    house = vb.model_world_tris(model, x, y)
    art = model.art
    paths = []
    for name, c in CAMERAS:
        if cams and name not in cams:
            continue
        sx, sz = c.get("shift", (0, 0))
        target = (x + w / 2.0 + sx, 0.0, y + h - 1.0 + sz)
        cam = vb.Camera(target, pitch=c["pitch"], yaw=c["yaw"], distance=c["distance"])
        img = vb.render_scene(cam, [(ground, gtex), (house, art)], scale=2)
        path = os.path.join(out_dir, "%s_%s.png" % (model.name, name))
        img.save(path)
        paths.append(path)
    return paths


# ── Export ───────────────────────────────────────────────────────────────

MAGIC = b"VXB5"


def cell_heights(model):
    """Tallest point of the model over each cell, in pixels.

    The lighting pass casts a building's shadow from one height per cell;
    this gives it the model's own instead of a guessed box.
    """
    w, h = model.cells
    tops = [0.0] * (w * h)
    for (tri, shade, tag) in model.mesh.tris:
        pts = [p[:3] for p in tri]
        for cy in range(h):
            for cx in range(w):
                piece = vb.clip(pts, 0, cx * 16.0, True)
                for axis, val, keep in ((0, cx * 16.0 + 16, False), (2, cy * 16.0, True),
                                        (2, cy * 16.0 + 16, False)):
                    if len(piece) >= 3:
                        piece = vb.clip(piece, axis, val, keep)
                if len(piece) >= 3 and _area_xz(piece) > 1e-6:
                    i = cy * w + cx
                    tops[i] = max(tops[i], max(p[1] for p in piece))
    return [min(255, int(round(t))) for t in tops]


def _area_xz(poly):
    """Plan area of a clipped piece; a face lying on the cell's edge has none."""
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][2] -
                   poly[(i + 1) % len(poly)][0] * poly[i][2] for i in range(len(poly)))) / 2


def find_placements(model, layouts_json):
    """Every place in Hoenn the building stands, with the ground around it.

    A spec may name `match_rows` (first, end): only those rows of the
    reference must repeat cell for cell. The rest belong to the building's
    surroundings as much as to it - Oldale paints its path and a tree's crown
    into the Pokemon Center's and the Mart's top row - and are drawn by the
    model regardless. When every matched metatile is in the primary tileset
    the building is the same in every town that shares it, so any secondary
    tileset is accepted.

    A core cell the map paints with another metatile still matches when it
    draws the building's own pixels: Rustboro paints the Center's and the
    Mart's corners with its paving round them, not Petalburg's grass.

    Each placement carries its own ground metatile: the commonest walkable
    one round it, which that map's atlas is sure to hold.
    """
    ref = model.layout
    x, y, w, h = model.spec["rect"]
    r0, r1 = model.spec.get("match_rows", (0, h))
    template = [ref.metatile(x + i, y + j) for j in range(h) for i in range(w)]
    core = [(i, j) for j in range(r0, r1) for i in range(w) if (i, j) in model.owned]
    core.sort(key=lambda c: (c[1], c[0]))
    primary_only = all(template[j * w + i] < ref.num_primary for i, j in core)
    found = []
    for index, entry in enumerate(layouts_json):
        if entry.get("primary_tileset") != ref.primary:
            continue
        # An object of free shape (a hedge run) stands only where it was
        # found: a short run also repeats inside every longer one. A block of
        # one stands wherever the generator found the same block.
        if "owned" in model.spec and entry["id"] != ref.id:
            continue
        if not primary_only and entry.get("secondary_tileset") != ref.secondary:
            continue
        path = os.path.join(vb.ROOT, entry["blockdata_filepath"])
        if not os.path.exists(path):
            continue
        blocks = vb.read_u16(path)
        lw, lh = entry["width"], entry["height"]
        i0, j0 = core[0]
        for py in range(lh - h + 1):
            for px in range(lw - w + 1):
                if (blocks[(py + j0) * lw + px + i0] & 0x3FF) != template[j0 * w + i0]:
                    continue
                if not all((blocks[(py + j) * lw + px + i] & 0x3FF) == template[j * w + i]
                           or same_building_pixels(model, entry["id"],
                                                   blocks[(py + j) * lw + px + i] & 0x3FF, i, j)
                           for i, j in core):
                    continue
                if "owned" in model.spec and (px, py) not in model.spec.get("repeat_at", [(x, y)]):
                    continue
                if "interior" in model.spec:
                    # a room's piece belongs to its place in the room: a stool
                    # drawn like another stands only where it was drawn, and
                    # on the room's floor, whatever the cells round it are;
                    # and in another layout only if the room is the same room,
                    # or it would lend half a room to a different one
                    if (px, py) != (x, y):
                        continue
                    if entry["id"] != ref.id and (
                            (lw, lh) != (ref.w, ref.h)
                            or any((blocks[k] & 0x3FF) != (ref.blocks[k] & 0x3FF)
                                   for k in range(lw * lh))):
                        continue
                    odd = [(i, j) for j in range(h) for i in range(w)
                           if (blocks[(py + j) * lw + px + i] & 0x3FF) != template[j * w + i]]
                    found.append((index + 1, px, py, model.ground_metatile, entry["id"], odd))
                    continue
                ring = {}
                for yy in range(py - 1, py + h + 1):
                    for xx in range(px - 1, px + w + 1):
                        inside = px <= xx < px + w and py <= yy < py + h
                        if inside or not (0 <= xx < lw and 0 <= yy < lh):
                            continue
                        cell = blocks[yy * lw + xx]
                        if cell & 0xC00:
                            continue
                        ring[cell & 0x3FF] = ring.get(cell & 0x3FF, 0) + 1
                ground = max(ring, key=ring.get) if ring else model.ground_metatile
                odd = [(i, j) for j in range(h) for i in range(w) if (i, j) in model.owned
                       and (blocks[(py + j) * lw + px + i] & 0x3FF) != template[j * w + i]]
                found.append((index + 1, px, py, ground, entry["id"], odd))
    return found


_LAYOUT_ART = {}


def same_building_pixels(model, layout_id, metatile, i, j):
    """Does `metatile` draw every pixel the model owns in its cell (i, j)?"""
    if layout_id not in _LAYOUT_ART:
        _LAYOUT_ART[layout_id] = vb.LayoutArt(layout_id)
    ipx = _LAYOUT_ART[layout_id].cell_image(metatile).load()
    apx = model.art.load()
    owned = 0
    for y in range(16):
        for x in range(16):
            a = apx[i * 16 + x, j * 16 + y]
            if a[3] < 128:
                continue
            owned += 1
            if ipx[x, y][:3] != a[:3]:
                return False
    return owned > 0


def pack_atlas(images):
    """Shelf-pack (key, image) pairs, tallest first, into the smallest
    power-of-two texture. Returns ((width, height), {key: (ox, oy)})."""
    order = sorted(images, key=lambda kv: (-kv[1].size[1], -kv[1].size[0]))
    sizes = sorted(((w, h) for w in (64, 128, 256, 512, 1024)
                    for h in (64, 128, 256, 512, 1024) if w <= 8 * h and h <= 8 * w),
                   key=lambda s: (s[0] * s[1], s[1]))
    for tw, th in sizes:
        x = y = shelf = 0
        spots = {}
        ok = True
        for key, img in order:
            aw, ah = img.size
            if x + aw > tw:
                x, y, shelf = 0, y + shelf, 0
            if aw > tw or y + ah > th:
                ok = False
                break
            spots[key] = (x, y)
            x += aw
            shelf = max(shelf, ah)
        if ok:
            return (tw, th), spots
    raise SystemExit("building art does not fit a 1024x1024 texture")


MAX_TEXTURE = (512, 512)   # one page in VRAM; ctr_voxel.c caches four


def texel_offset(x, y, width):
    """8x8 Morton tiles, the PICA200 order (as gen_voxel_trees.py)."""
    morton = 0
    for bit in range(3):
        morton |= ((x >> bit) & 1) << (2 * bit) | ((y >> bit) & 1) << (2 * bit + 1)
    return ((y // 8) * (width // 8) + x // 8) * 64 + morton


def ground_patch(model, layout, px, py, i, j):
    """What the map paints in cell (i, j) of a placement besides the building.

    The map's own metatile with every pixel the model's drawing owns made
    transparent: Oldale's path beside the Center's roof, a tree's crown
    behind the Mart, a cliff's edge. Laid flat over the placement's ground.
    """
    img = layout.cell_image(layout.metatile(px + i, py + j)).convert("RGBA")
    ipx, apx = img.load(), model.art.load()
    for y in range(16):
        for x in range(16):
            if apx[i * 16 + x, j * 16 + y][3] >= 128:
                ipx[x, y] = (0, 0, 0, 0)
    return img


def placement_patches(model, layout_json_entry_name, layouts, px, py, odd):
    """The ground patches of one placement: cells the model leaves empty
    where the map paints something the reference does not."""
    w, h = model.cells
    tops = cell_heights(model)
    cells = []
    for (i, j) in odd:
        if tops[j * w + i] != 0:
            continue  # the model stands there; the map's own paint is lost
        if layout_json_entry_name not in layouts:
            layouts[layout_json_entry_name] = vb.LayoutArt(layout_json_entry_name)
        img = ground_patch(model, layouts[layout_json_entry_name], px, py, i, j)
        if img.getbbox() is not None:
            cells.append((i, j, img))
    return cells


def export(models, path):
    """Write buildings.bin (VXB5).

    Geometry is stored once per model, texture coordinates in pixels of the
    model's own drawing. Textures are paged by map: each layout that places
    anything gets one page holding just the drawings it uses (and its ground
    patches), and a page-model record tells where on that page a model's
    drawing went. The console loads the pages of the maps on screen only.

      "VXB5", u16 pages, models, pageModels, placements, heightBytes, 0,
      u32 vertices
      pages       x 8:  u16 w, h; u32 file offset of its RGBA5551 texels
      models      x 16: u8 w, h; u16 ground; u32 firstVertex, vertexCount, heights
      pageModels  x 8:  u16 model, page; i16 ox, oy (pixels)
      placements  x 16: u16 layout, pageModel, x, y, ground, extraCount;
                        u32 extraFirst (ground patches, uv in page pixels)
      heightBytes, padding to 4, vertices x 24 (x, y, z, u, v, shade),
      then the pages' texels.
    """
    layouts_json = json.load(open(os.path.join(vb.ROOT, "data", "layouts", "layouts.json"),
                                  encoding="utf-8"))["layouts"]
    layouts = {}
    found = []
    for index, m in enumerate(models):
        for lid, px, py, ground, name, odd in find_placements(m, layouts_json):
            cells = placement_patches(m, name, layouts, px, py, odd)
            found.append((lid, index, px, py, ground, cells, name))
            if odd:
                print("  %s: %s at %d,%d differs in %d cell(s), %d patched from the map"
                      % (m.name, name, px, py, len(odd), len(cells)))
    crops = {m.name: (m.art.getbbox() or (0, 0, 1, 1)) for m in models}

    # models: geometry once, uv in the model's own art pixels
    records, verts, heights = [], [], bytearray()
    for index, m in enumerate(models):
        first = len(verts) // 6
        for (tri, shade, tag) in m.mesh.tris:
            for (x, y, z, u, v) in tri:
                verts += [x / 16.0, y / 16.0, z / 16.0, u, v, shade]
        count = len(verts) // 6 - first
        w, h = m.cells
        records.append(struct.pack("<BBHIII", w, h, m.ground_metatile, first, count, len(heights)))
        # 255: a cell of the rectangle the object does not own (a hedge's
        # rectangle holds the house it runs round)
        heights += bytes(t if (i % w, i // w) in m.owned else 255
                         for i, t in enumerate(cell_heights(m)))

    # pages: one per layout
    by_layout = {}
    for rec in found:
        by_layout.setdefault(rec[0], []).append(rec)
    pages, page_models, placements, texels = [], [], [], []
    for lid in sorted(by_layout):
        recs = by_layout[lid]
        used = sorted({r[1] for r in recs})
        patches = {}
        for r in recs:
            for (i, j, img) in r[5]:
                patches.setdefault(img.tobytes(), img)
        images = [(("m", k), models[k].art.crop(crops[models[k].name])) for k in used]
        images += [(("p", key), img) for key, img in patches.items()]
        (tw, th), spots = pack_atlas(images)
        if tw * th > MAX_TEXTURE[0] * MAX_TEXTURE[1]:
            raise SystemExit("%s: building page %dx%d exceeds the console's %dx%d"
                             % (recs[0][6], tw, th, MAX_TEXTURE[0], MAX_TEXTURE[1]))
        page = len(pages)
        tex = bytearray(tw * th * 2)
        for key, img in images:
            ox, oy = spots[key]
            apx = img.load()
            for y in range(img.size[1]):
                for x in range(img.size[0]):
                    r_, g_, b_, a_ = apx[x, y]
                    value = (r_ >> 3) << 11 | (g_ >> 3) << 6 | (b_ >> 3) << 1 | int(a_ >= 128)
                    struct.pack_into("<H", tex, 2 * texel_offset(ox + x, oy + y, tw), value)
        pages.append((tw, th))
        texels.append(tex)
        pm_of = {}
        for k in used:
            ox, oy = spots[("m", k)]
            ox -= crops[models[k].name][0]
            oy -= crops[models[k].name][1]
            pm_of[k] = len(page_models)
            page_models.append((k, page, ox, oy))
        for (lid_, index, px, py, ground, cells, name) in recs:
            first = len(verts) // 6
            for (i, j, img) in cells:
                ox, oy = spots[("p", img.tobytes())]
                a = [i, 0.01, j, ox, oy, 1.0]
                b = [i + 1, 0.01, j, ox + 16, oy, 1.0]
                c = [i + 1, 0.01, j + 1, ox + 16, oy + 16, 1.0]
                d = [i, 0.01, j + 1, ox, oy + 16, 1.0]
                verts += a + b + c + a + c + d
            placements.append((lid, pm_of[index], px, py, ground, len(verts) // 6 - first, first))
    placements.sort()
    nverts = len(verts) // 6
    head = MAGIC + struct.pack("<HHHHHHI", len(pages), len(models), len(page_models),
                               len(placements), len(heights), 0, nverts)
    body = bytearray()
    for r in records:
        body += r
    for pm in page_models:
        body += struct.pack("<HHhh", *pm)
    for p in placements:
        body += struct.pack("<HHHHHHI", *p)
    body += heights
    table_size = 8 * len(pages)
    fixed = len(head) + table_size + len(body)
    pad = (-fixed) % 4
    body += bytes(pad)
    body += struct.pack("<%df" % len(verts), *verts)
    offset = len(head) + table_size + len(body)
    table = bytearray()
    for (tw, th), tex in zip(pages, texels):
        table += struct.pack("<HHI", tw, th, offset)
        offset += len(tex)
    blob = head + table + body + b"".join(texels)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(blob)
    print("voxel buildings: %d models, %d pages, %d placements, %d vertices, %.1f KiB -> %s"
          % (len(models), len(pages), len(placements), nverts, len(blob) / 1024.0, path))
    for (tw, th), lid in zip(pages, sorted(by_layout)):
        print("  page for layout %3d: %dx%d" % (lid, tw, th))


def town_preview(models, layout_id, out_dir):
    """A whole layout with every model placed in it, by the console's rules."""
    layouts_json = json.load(open(os.path.join(vb.ROOT, "data", "layouts", "layouts.json"),
                                  encoding="utf-8"))["layouts"]
    layout = vb.LayoutArt(layout_id)
    override, items = {}, []
    for m in models:
        w, h = m.cells
        tops = cell_heights(m)
        for lid, px, py, ground, name, odd in find_placements(m, layouts_json):
            if name != layout_id:
                continue
            for j in range(h):
                for i in range(w):
                    override[(px + i, py + j)] = ground
            for (i, j, patch) in placement_patches(m, layout_id, {layout_id: layout}, px, py, odd):
                q = [(px + i, 0.01, py + j, 0, 0), (px + i + 1, 0.01, py + j, 16, 0),
                     (px + i + 1, 0.01, py + j + 1, 16, 16), (px + i, 0.01, py + j + 1, 0, 16)]
                items.append(([((q[0], q[1], q[2]), 1.0), ((q[0], q[2], q[3]), 1.0)], patch))
            items.append((vb.model_world_tris(m, px, py), m.art))
    ground, gtex = vb.ground_tris(layout, 0, 0, layout.w, layout.h, override, None)
    shots = []
    for name, (tx, tz, pitch, yaw, dist) in (
            ("overview", (layout.w / 2.0, layout.h / 2.0 + 2, 45.0, 0.0, 18.0)),
            ("yaw30", (layout.w / 2.0, layout.h / 2.0, 35.0, 30.0, 16.0))):
        cam = vb.Camera((tx, 0.0, tz), pitch=pitch, yaw=yaw, distance=dist)
        img = vb.render_scene(cam, [(ground, gtex)] + items, scale=2)
        path = os.path.join(out_dir, "%s_%s.png" % (layout_id.lower(), name))
        img.save(path)
        shots.append(path)
    return shots


def room_check(models, layout_id, out_dir):
    """A whole room as the console composes it, against its drawing.

    The terrain draws each cell a model covers with the placement's floor
    and every other cell with its own metatile; the pieces stand over them.
    Rendered in the GBA projection, the room must be its drawing again,
    pixel for pixel. Returns the pixels that differ.
    """
    layouts_json = json.load(open(os.path.join(vb.ROOT, "data", "layouts", "layouts.json"),
                                  encoding="utf-8"))["layouts"]
    layout = vb.LayoutArt(layout_id)
    W, H = layout.w * 16, layout.h * 16
    ras = vb.Raster(W, H, bg=(0, 0, 0))
    covered, items = {}, []
    for m in models:
        w, h = m.cells
        for lid, px, py, ground, name, odd in find_placements(m, layouts_json):
            if name != layout_id:
                continue
            for j in range(h):
                for i in range(w):
                    covered[(px + i, py + j)] = ground
            items.append((m, px, py))
    for y in range(layout.h):
        for x in range(layout.w):
            img = layout.cell_image(covered.get((x, y), layout.metatile(x, y)))
            q = [(x * 16, 0, y * 16, 0, 0), (x * 16 + 16, 0, y * 16, 16, 0),
                 (x * 16 + 16, 0, y * 16 + 16, 16, 16), (x * 16, 0, y * 16 + 16, 0, 16)]
            for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
                ras.draw([(X, Z - Y, Y + Z, 1.0, U, V) for (X, Y, Z, U, V) in tri], img, 1.0)
    for (m, px, py) in items:
        for (tri, shade, tag) in m.mesh.tris:
            if "~depth" in tag:
                continue  # real depth rising behind the drawing
            vs = [(x + px * 16, z + py * 16 - y, y + z + py * 16, 1.0, u, v)
                  for (x, y, z, u, v) in tri]
            ras.draw(vs, m.art, shade, tag)
    full = Image.new("RGB", (W, H))
    for y in range(layout.h):
        for x in range(layout.w):
            full.paste(layout.cell_image(layout.metatile(x, y)).convert("RGB"), (x * 16, y * 16))
    got = ras.image()
    fpx, gpx = full.load(), got.load()
    bad = 0
    diff = got.copy()
    dpx = diff.load()
    culprits = {}
    room = next((s["interior"] for s in specs.SPECS
                 if s.get("interior", {}).get("layout") == layout_id), {})
    for y in range(H):
        for x in range(W):
            if _inside(room.get("open", ()), x, y):
                continue  # floor in the round where the drawing is black
            if fpx[x, y] != gpx[x, y]:
                bad += 1
                dpx[x, y] = (255, 0, 255)
                tag = ras.owner[y * W + x] or "terrain"
                culprits.setdefault(tag, []).append((x, y))
    for tag, pts in sorted(culprits.items(), key=lambda kv: -len(kv[1])):
        print("    %-34s %4d px, e.g. %s" % (tag, len(pts), pts[:3]))
    sheet = Image.new("RGB", (W * 2 + 4, H))
    sheet.paste(full, (0, 0))
    sheet.paste(diff, (W + 4, 0))
    sheet.resize((sheet.width * 3, sheet.height * 3), Image.NEAREST).save(
        os.path.join(out_dir, layout_id.lower() + "_room_check.png"))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--cams", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--town", default=None, help="render a whole layout, e.g. LAYOUT_OLDALE_TOWN")
    ap.add_argument("--allow-art-mismatch", action="store_true",
                    help="skip models whose reference art this tree does not carry")
    args = ap.parse_args()
    out = args.preview or os.path.join(PORT, "build", "buildings")
    os.makedirs(out, exist_ok=True)
    failed = False
    models = build_models(args.only, allow_mismatch=args.allow_art_mismatch)
    for model in models:
        wrong, missing, extra = vb.ortho_check(model, os.path.join(out, model.name + "_ortho.png"),
                                               exact=model.spec.get("exact"))
        bad = vb.density_check(model)
        print("%-22s %4d tris  exact: wrong=%d missing=%d extra=%d  texel density: %d bad"
              % (model.name, model.triangle_count(), wrong, missing, extra, len(bad)))
        for tag, along, down, shear in bad[:8]:
            print("    %-28s along=%.3f down=%.3f shear=%.3f" % (tag, along, down, shear))
        if wrong or missing or extra or bad:
            failed = True
        if args.preview:
            preview(model, out, args.cams.split(",") if args.cams else None)
    rooms = sorted({m.spec["layout"] for m in models if "interior" in m.spec})
    for lid in rooms:
        bad = room_check(models, lid, out)
        print("room %-28s composed with the terrain: %d pixel(s) differ from the drawing"
              % (lid, bad))
        failed = failed or bad != 0
    if failed:
        raise SystemExit("a model does not reproduce its drawing")
    if args.output:
        export(models, args.output)
    if args.town:
        for path in town_preview(models, args.town, out):
            print("town preview:", path)


if __name__ == "__main__":
    main()
