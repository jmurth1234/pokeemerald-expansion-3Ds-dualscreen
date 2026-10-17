#!/usr/bin/env python3
"""Generate the independent ROMFS signpost mask sidecar.

This intentionally does not alter regions.bin. Signposts are thin objects and
are indexed by layout-local cell, not stored as structure regions.
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voxel_cells  # noqa: E402
from voxel_sign_mask import event_board_masks, head_mask, metatile_mask  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PORT = os.path.join(ROOT, "3ds_port")
OUT = os.path.join(PORT, "romfs", "voxel", "signposts.bin")


def head_ground(layout, pair, x, y):
    """The ground under a sign's head: the head cell's drawing without it.

    A walkable neighbour that draws nothing on its upper layer and the same
    lower layer is the head cell's ground exactly; failing that the commonest
    such neighbour. The map holds it, so its atlas does too.
    """
    own = pair.layer_pixels(layout.metatile(x, y), 0)
    counts = {}
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            nx, ny = x + dx, y + dy
            if (dx, dy) in ((0, 0), (0, 1)) or layout.off_map(nx, ny) or layout.blocked(nx, ny):
                continue
            m = layout.metatile(nx, ny)
            if pair.layer_pixels(m, 1):
                continue
            if pair.layer_pixels(m, 0) == own:
                return m
            counts[m] = counts.get(m, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return layout.metatile(x, y + 2) if not layout.off_map(x, y + 2) else 0


def cell_ground(layout, pair, x, y, rows):
    """The metatile the sign's own cell is laid with under it: of the clean
    walkable cells round it (nothing on their upper layer), the one whose
    lower layer matches most of the cell's drawing outside the sign; on a
    tie the one that runs on best into its four neighbours along the
    cell's edges, then the commonest round it, then the cell south. A sign
    drawn over its whole cell (Petalburg's) leaves nothing to match: the
    one by the Pokemon Center stands on the lawn, the path south and west of
    it, and the cell south of it alone laid it on a slab of sand."""
    own = pair.layer_pixels(layout.metatile(x, y), 0)
    seen = {p: c for p, c in own.items() if not rows[p[1]] >> p[0] & 1}
    south = None if layout.off_map(x, y + 1) else layout.metatile(x, y + 1)
    found = {}
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            nx, ny = x + dx, y + dy
            if (dx, dy) == (0, 0) or layout.off_map(nx, ny) or layout.blocked(nx, ny):
                continue
            m = layout.metatile(nx, ny)
            if pair.layer_pixels(m, 1) or layout.role_at(nx, ny) == "signpost":
                continue
            found[m] = found.get(m, 0) + 1
    if south is not None:
        found.setdefault(south, 0)

    # its four neighbours' lower layers, for the seams a ground makes with
    # them: (neighbour's pixels, its edge row/column, the cell's own edge)
    seams = []
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        nx, ny = x + dx, y + dy
        if not layout.off_map(nx, ny):
            seams.append((pair.layer_pixels(layout.metatile(nx, ny), 0), dx, dy))

    def seam(lower):
        """Pixels along the cell's edges that run on into the neighbour."""
        n = 0
        for other, dx, dy in seams:
            for k in range(16):
                if dx:
                    a, b = ((15, k), (0, k)) if dx > 0 else ((0, k), (15, k))
                else:
                    a, b = ((k, 15), (k, 0)) if dy > 0 else ((k, 0), (k, 15))
                n += lower.get(a) is not None and lower.get(a) == other.get(b)
        return n

    def score(m):
        lower = pair.layer_pixels(m, 0)
        return (sum(1 for p, c in seen.items() if lower.get(p) == c), seam(lower),
                found[m], m == south)

    if not found:
        return 0
    return max(found, key=score)


def blocked_head(pair, head, north):
    """A sign's head in a blocked cell - the wall it stands against:
    Lavaridge's gym sign tops the corner of the gym - when that cell's upper
    layer is the head and nothing else. Asked of sign events only: a rock in
    the sea is a post, and the ripples round its top are no head.
    Its ground is its own drawing: its lower layer alone is a metatile the map
    may not hold, and the atlas has only the map's."""
    return all(head[py] >> px & 1 for px, py in pair.layer_pixels(north, 1))


def sign_record(layout, pair, x, y):
    """A signpost's mask rows, its head's (0 for none), the metatile the
    head's cell is drawn with under it and the one its own cell is."""
    rows = metatile_mask(pair, layout, x, y)
    ground = cell_ground(layout, pair, x, y, rows)
    if y == 0:
        return rows, [0] * 16, 0, ground
    north = layout.metatile(x, y - 1)
    head = head_mask(pair, rows, north)
    if any(head) and not layout.blocked(x, y - 1):
        return rows, head, head_ground(layout, pair, x, y - 1), ground
    if any(head) and (x, y) in layout.signs and blocked_head(pair, head, north):
        return rows, head, north, ground
    return rows, [0] * 16, 0, ground


def layout_records(layout_id, layout, pair):
    records = []
    for y in range(layout.h):
        for x in range(layout.w):
            if layout.role_at(x, y) != "signpost":
                board = event_board_masks(pair, layout, x, y)
                if board is not None:
                    rows, head, ground = board
                    # its ground is proven: the cell south's
                    records.append((layout_id, x, y, rows, head, ground, ground))
                continue
            records.append((layout_id, x, y) + sign_record(layout, pair, x, y))
    return records


def write_records(records, path):
    """VXS3: a record is the sign's cell, its 16 mask rows, the 16 rows of
    its head in the cell north of it (a lamp's lantern; 0 for none), the
    metatile that cell's ground is drawn with instead and the one the sign's
    own cell is laid with."""
    records = sorted(records, key=lambda item: (item[0], item[2], item[1]))
    with open(path, "wb") as stream:
        stream.write(b"VXS3")
        stream.write(struct.pack("<I", len(records)))
        for layout_id, x, y, rows, head, ground, cell in records:
            stream.write(struct.pack("<HHH16H16HHH", int(layout_id), x, y, *rows, *head, ground,
                                     cell))


def main():
    with open(os.path.join(ROOT, "data", "layouts", "layouts.json"),
              encoding="utf-8") as stream:
        layouts = json.load(stream)["layouts"]
    events = voxel_cells.MapEvents()
    pairs = {}
    records = []
    for layout_id, entry in enumerate(layouts, start=1):
        layout = voxel_cells.Layout(entry, events)
        key = (layout.primary, layout.secondary)
        if not layout.outdoor:
            continue
        if key not in pairs:
            pairs[key] = voxel_cells.pair_for(*key)
        pair = pairs[key]
        records.extend(layout_records(layout_id, layout, pair))
    # The runtime uses the numeric layout id, not the string map name.
    # layouts.json ids are numeric in the generated map table.
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    write_records(records, OUT)
    print("voxel sign masks: %d records, %d with a head -> %s"
          % (len(records), sum(1 for r in records if any(r[4])), OUT))


if __name__ == "__main__":
    main()
