"""A room's pieces judged by what the 45-degree proof cannot see
(voxel_defect_scan.scan_piece, gen_voxel_buildings.room_facts): every fault
found in Littleroot's rooms, made on purpose on synthetic pieces, is reported
by its own kind - and the same piece made right is reported by none. And the
verdict that fails the build on them (lint_verdict, F5): a fault in any room
but one the allowlist accepts with its reason, and an entry in it that finds
no fault any more."""

import os
import pathlib
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_voxel_buildings as g  # noqa: E402
import voxel_building as vb  # noqa: E402
import voxel_defect_scan as vds  # noqa: E402
from voxel_building_specs import piece  # noqa: E402
from PIL import Image  # noqa: E402

OPT = types.SimpleNamespace(stretch=0.4, tiny=3.0, steps=6.0, thin=8.0)
OUTLINE, WHITE = (65, 65, 82, 255), (255, 255, 255, 255)
CASING, FLOOR = (150, 150, 170, 255), (200, 200, 180, 255)
FAILED = []


def check(name, kinds, want):
    got = sorted({k for k, _ in kinds})
    ok = got == sorted(want)
    print("%s %-34s %s" % ("PASS" if ok else "FAIL", name, got))
    if not ok:
        FAILED.append(name)


# ── scan_piece, on a box built as interior_specs builds it ──────────────

def box(side_rows, height=8, rows=20, lint=None, side_w=None, keep=False):
    """A 16-wide box drawn `rows` rows tall, `height` of them its front,
    its sides dressed with `side_rows` (one list of RGBA per row) laid
    below the drawing, as interior_specs lays a piece's `side` sample."""
    w16, h16 = 16, 32
    obj = Image.new("RGBA", (w16, h16), (0, 0, 0, 0))
    opx = obj.load()
    for v in range(rows):
        for u in range(w16):
            opx[u, v] = OUTLINE if u in (0, 15) or v in (0, rows - 1) else CASING
    sw = side_w or len(side_rows[0])
    art = Image.new("RGBA", (w16, 2 * h16 + len(side_rows)), (0, 0, 0, 0))
    art.paste(obj, (0, 0))
    apx = art.load()
    for j, row in enumerate(side_rows):
        for i, c in enumerate(row):
            apx[i, 2 * h16 + j] = c
    rect = (0, 2 * h16, sw, 2 * h16 + len(side_rows))
    relief = vb.Relief("box", obj, height, vb.Tile(*rect), foot=rows, solid=True)
    model = vb.Model("room_box", art, [relief], (1, 2), 0)
    own = frozenset((u, v) for v in range(rows) for u in range(w16))
    facts = {"kind": "furniture", "cut": {}, "own": own, "fill": frozenset(),
             "height": height, "base": 0, "side": rect, "top": None,
             "side_foreign": frozenset()}
    facts.update(lint or {})
    m = types.SimpleNamespace(name="room_box", cells=(1, 2), art=art, mesh=model.mesh,
                              lint=facts)
    flags, _, notes = vds.scan_model(m, set(), OPT)
    if keep:
        m.flags = flags
        return m, rect
    return notes, rect


def plain(n=8, c=CASING):
    return [[c, c] for _ in range(n)]


notes, _ = box(plain(4, CASING) + [[OUTLINE, WHITE] for _ in range(4)])
check("television side: casing over a stand", notes, ["stripes"])
notes, _ = box(plain(4, CASING) + plain(4, OUTLINE[:3] + (255,)), lint=None)
check("television side, one colour a row", notes, [])

notes, rect = box(plain(6) + [[FLOOR, FLOOR] for _ in range(2)])
floor_rows = frozenset((u, v) for v in range(rect[3] - 2, rect[3]) for u in range(2))
notes, _ = box(plain(6) + [[FLOOR, FLOOR] for _ in range(2)],
               lint={"side_foreign": floor_rows})
check("side reading the floor round it", notes, ["foreign"])
notes, _ = box(plain(6) + [[(0, 0, 0, 0), CASING] for _ in range(2)])
check("side with empty texels (fins)", notes, ["foreign", "stripes"])

notes, _ = box(plain(8, OUTLINE))
check("side all outline: a black slab", notes, ["dark"])

WOOD = (148, 115, 90, 255)
drawers = [[WOOD] * 8 for _ in range(8)]
drawers[3] = [OUTLINE] * 8                      # the line between two drawers
notes, _ = box(drawers)
check("desk's end with its drawers on it", notes, ["copied"])
notes, _ = box([[WOOD] * 8 for _ in range(8)])
check("desk's end in plain wood", notes, [])

notes, _ = box(plain(8), lint={"under": (10, 20)})
check("table: shadow stood up under it", notes, ["under"])
m, _ = box(plain(8), lint={"under": (10, 20)}, keep=True)
tinted = {m.mesh.tris[k][2].split(".")[1] for k, ks in m.flags.items() if "under" in ks}
check("table: its sides tinted too, not its front only",
      [("under", "")] if {"front", "flank", "back"} <= tinted else [], ["under"])
notes, _ = box(plain(8), lint={"under": (2, 20)})
check("table with a little shadow", notes, [])

own = frozenset((u, v) for v in range(20) for u in range(16))
notes, _ = box(plain(8), lint={"bleed": frozenset(list(own)[:40]), "support": "room_desk"})
check("book carrying the desk's top", notes, ["bleed"])
notes, _ = box(plain(8), lint={"bleed": frozenset(list(own)[:6]), "support": "room_desk"})
check("book touching the desk's colours", notes, [])

notes, _ = box(plain(8), lint={"cut": {"room_book": 40}})
check("desk with a book's hole in it", notes, ["cut"])

notes, _ = box(plain(8), lint={"backdrop": frozenset(list(own)[:24])})
check("computer carrying the wall behind it", notes, ["backdrop"])
notes, _ = box(plain(8), lint={"backdrop": frozenset(list(own)[:4])})
check("computer touching the wall's colour", notes, [])

notes, _ = box(plain(8), lint={"floating": True, "base": 4})
check("lifted onto nothing", notes, ["floating"])

notes, _ = box(plain(8), height=8, rows=8)
check("chair's back with no depth", notes, ["flat"])
notes, _ = box(plain(8), height=8, rows=12)
check("chair's back four pixels deep", notes, [])


def card(bg):
    art = Image.new("RGBA", (16, 64), (0, 0, 0, 0))
    apx = art.load()
    for v in range(4, 20):
        for u in range(4, 12):
            if (u + v) % 3:
                apx[u, v] = (80, 180, 60, 255)      # leaves, with gaps
    obj = art.crop((0, 0, 16, 32))
    model = vb.Model("room_plant", art, [vb.Card("plant", obj, 20)], (1, 2), 0)
    facts = {"kind": "card", "cut": {}, "own": frozenset(), "fill": frozenset(), "height": 18,
             "base": 0, "side": None, "top": None, "card_bg": bg}
    m = types.SimpleNamespace(name="room_plant", cells=(1, 2), art=art, mesh=model.mesh,
                              lint=facts)
    return vds.scan_model(m, set(), OPT)


flags, _, notes = card((40, 100))
check("plant carrying the wall", notes, ["card_bg"])
flags, _, notes = card((0, 100))
check("plant on its own", notes, [])
check("plant's gaps are not holes", [(k, "") for ks in flags.values() for k in ks], [])


# ── room_facts: what a room tells of its pieces ─────────────────────────

class Room:
    """A drawing of 64 x 48: a wall along the top, a desk with a book on
    it, a table with its shadow under it, a plant against the wall (and,
    `poster`, a picture on the wall in the leaves' green, the plant inked
    round with the picture's frame's ink)."""

    YELLOW, BROWN, RED = (255, 246, 123, 255), (148, 115, 90, 255), (197, 74, 65, 255)
    PINK, SHADOW, GREEN = (246, 197, 230, 255), (131, 131, 148, 255), (80, 180, 60, 255)
    WALL = (213, 213, 180, 255)

    GREY = (170, 170, 180, 255)

    def __init__(self, book_on_desk_colours, table_shadow, plant_wall, desk_fill=None,
                 pc=(24, 4, 36, 16), poster=False):
        W, H = 64, 48
        self.img = Image.new("RGBA", (W, H), FLOOR)
        px = self.img.load()
        for y in range(0, 8):
            for x in range(W):
                px[x, y] = self.WALL
        if poster:                                   # a picture on it
            for y in range(1, 7):
                for x in range(2, 14):
                    px[x, y] = OUTLINE if y in (1, 6) or x in (2, 13) else self.GREEN
        for y in range(16, 32):                      # the desk: top, then front
            for x in range(0, 32):
                px[x, y] = self.YELLOW if y < 24 else self.BROWN
        for y in range(10, 22):                      # the book on it
            for x in range(8, 20):
                corner = book_on_desk_colours and y >= 19 and x in (8, 9, 18, 19)
                px[x, y] = self.YELLOW if corner else self.RED
        for y in range(24, 40):                      # the table, its shadow under it
            for x in range(40, 64):
                under = table_shadow and y >= 36 and 42 <= x < 62
                px[x, y] = self.SHADOW if under else self.PINK
        for y in range(0, 20):                       # the plant, against the wall
            for x in range(40, 56):
                if 44 <= x < 52 and y >= 2:
                    px[x, y] = self.GREEN
                elif poster and 43 <= x <= 52 and y >= 1:
                    px[x, y] = OUTLINE
                else:
                    px[x, y] = self.WALL if plant_wall else FLOOR
        for y in range(6, 16):                       # a computer against the wall
            for x in range(25, 35):
                px[x, y] = self.GREY
        for y in range(36, 46):                      # a cupboard as white as the wall
            for x in range(20, 36):
                px[x, y] = self.WALL
        self.pieces = [
            piece("book", [(8, 10, 20, 22)], 6, base=8, solid=True),
            # its rectangle a pixel wider and two rows taller than it: the
            # wall round its corners
            piece("pc", [pc], 10, solid=True),
            piece("cupboard", [(20, 36, 36, 46)], 6, solid=True),
            piece("plant", [(40, 0, 56, 20)], 18, card=True),
            piece("desk", [(0, 16, 32, 32)], 8, solid=True, fill=desk_fill),
            piece("table", [(40, 24, 64, 40)], 8, solid=True,
                  leave=("838394",)),
            piece("wall", [(0, 0, 64, 8)], 8, fill=16),
        ]
        self.owner = [[None] * W for _ in range(H)]
        for k, pc in enumerate(self.pieces):
            for y in range(H):
                for x in range(W):
                    if (self.owner[y][x] is None and g._inside(pc["shape"], x, y)
                            and px[x, y] != FLOOR):
                        self.owner[y][x] = k
        self.drawn = [({(x, y) for y in range(H) for x in range(W) if self.owner[y][x] == k}, {})
                      for k in range(len(self.pieces))]
        floor = Image.new("RGBA", (16, 16), FLOOR)
        self.layout = types.SimpleNamespace(cell_image=lambda m: floor)
        spec = {"name": "room", "interior": {"ground": [0]}}
        self.facts = g.room_facts(spec, self.layout, self.pieces, self.drawn, self.owner,
                                  set(), px)

    def of(self, name):
        return self.facts[[pc["name"] for pc in self.pieces].index(name)]


bad = Room(True, True, True)
good = Room(False, False, False, desk_fill=1)
book, book_ok = bad.of("book"), good.of("book")
check("room: the book stands on the desk",
      [("support", "")] if book.get("support") == "room_desk" else [], ["support"])
check("room: the book carries the desk's top",
      [("bleed", "")] if len(book["bleed"]) >= 8 else [], ["bleed"])
check("room: a book without the desk's top",
      [("bleed", "")] if book_ok["bleed"] else [], [])
check("room: the desk has the book's hole",
      [("cut", "")] if bad.of("desk")["cut"].get("room_book") else [], ["cut"])
check("room: a desk filling what it hides", [("cut", "")] if good.of("desk")["cut"] else [], [])
n, of = bad.of("table")["under"]
check("room: shadow under the table", [("under", "")] if n * 4 > of else [], ["under"])
check("room: a table without", [("under", "")] if good.of("table")["under"][0] else [], [])
n, of = bad.of("plant")["card_bg"]
check("room: the plant carries the wall", [("card_bg", "")] if n * 20 > of else [], ["card_bg"])
check("room: a plant on its own",
      [("card_bg", "")] if good.of("plant")["card_bg"][0] else [], [])
check("room: a plant as green as a picture",
      [("card_bg", "")] if Room(False, False, False, poster=True).of("plant")["card_bg"][0]
      else [], [])
check("room: the wall is the shell", [("shell", "")] if bad.of("wall")["kind"] == "shell" else [],
      ["shell"])
check("room: the computer carries the wall",
      [("backdrop", "")] if len(bad.of("pc")["backdrop"]) >= 12 else [], ["backdrop"])
check("room: a computer drawn without it",
      [("backdrop", "")] if Room(False, False, False, pc=(25, 6, 35, 16)).of("pc")["backdrop"] else [], [])
check("room: a cupboard as white as the wall is its own",
      [("backdrop", "")] if bad.of("cupboard")["backdrop"] else [], [])



def stand_of_pots():
    """room_facts for two potted plants (cards) in a row on a green stand,
    the one behind with its foot drawn under the crown of the one before
    it, as on the flower shop's tables."""
    W, H = 16, 40
    img = Image.new("RGBA", (W, H), FLOOR)
    px = img.load()
    pieces = [piece("pot_s", [(4, 24, 12, 36)], 12, base=8, card=True),
              piece("pot_n", [(4, 12, 12, 24)], 12, base=8, card=True),
              piece("stand", [(0, 10, 16, 40)], 8, solid=True)]
    for y in range(10, H):
        for x in range(W):
            px[x, y] = Room.GREEN
    for y0 in (12, 24):
        for y in range(y0, y0 + 12):
            for x in range(4, 12):
                px[x, y] = Room.RED if y < y0 + 6 else Room.BROWN
    owner = [[next((k for k, pc in enumerate(pieces) if g._inside(pc["shape"], x, y)
                    and px[x, y] != FLOOR), None) for x in range(W)] for y in range(H)]
    drawn = [({(x, y) for y in range(H) for x in range(W) if owner[y][x] == k}, {})
             for k in range(len(pieces))]
    floor = Image.new("RGBA", (16, 16), FLOOR)
    layout = types.SimpleNamespace(cell_image=lambda m: floor)
    spec = {"name": "room", "interior": {"ground": [0]}}
    return g.room_facts(spec, layout, pieces, drawn, owner, set(), px)


pots = stand_of_pots()
check("room: a pot before another stands on the stand",
      [] if pots[0].get("support") == "room_stand" else [("floating", "")], [])
check("room: the pot behind it, its foot hidden, too",
      [("floating", "")] if pots[1].get("floating") or pots[1].get("support") != "room_stand"
      else [], [])

def lone(draw, pc, wall_rows=0):
    """room_facts for one piece standing alone in a 32 x 24 room of floor
    (and `wall_rows` of wall along its top): `draw(x, y)` its colour, or
    None where the room is seen through it (not its own)."""
    W, H = 32, 24
    img = Image.new("RGBA", (W, H), FLOOR)
    px = img.load()
    floor = Image.new("RGBA", (16, 16), FLOOR)
    for y in range(wall_rows):
        for x in range(W):
            px[x, y] = Room.WALL
    for y in range(H):
        for x in range(W):
            if g._inside(pc["shape"], x, y) and draw(x, y) is not None:
                px[x, y] = draw(x, y)
    owner = [[0 if g._inside(pc["shape"], x, y) and draw(x, y) is not None else None
              for x in range(W)] for y in range(H)]
    drawn = [({(x, y) for y in range(H) for x in range(W) if owner[y][x] == 0}, {})]
    layout = types.SimpleNamespace(cell_image=lambda m: floor)
    spec = {"name": "room", "interior": {"ground": [0]}}
    return g.room_facts(spec, layout, [pc], drawn, owner, set(), px)[0]


def wire(x, y):
    """A tap's spokes: outline, a white of the wall's own colour along
    them, the room seen through between (not drawn: floor)."""
    if (x + y) % 4 == 0:
        return OUTLINE
    return Room.WALL if (x + y) % 4 == 1 else None


f = lone(wire, piece("tap", [(8, 10, 16, 16)], 5, solid=True, through=True), wall_rows=8)
check("room: a wire's white beside its holes is its own",
      [("backdrop", "")] if f["backdrop"] else [], [])
f = lone(lambda x, y: Room.WALL if y < 8 else OUTLINE,
         piece("pc", [(8, 6, 16, 16)], 5, solid=True), wall_rows=8)
check("room: the wall running on into it is the room's",
      [("backdrop", "")] if f["backdrop"] else [], ["backdrop"])


def computer_on_desk():
    """A white computer on a desk, a dark line down it from its top edge in
    the brown of its desk's front: room_facts of the computer."""
    W, H = 32, 24
    img = Image.new("RGBA", (W, H), FLOOR)
    px = img.load()
    for y in range(12, 24):
        for x in range(W):
            px[x, y] = Room.GREY if y < 18 else Room.BROWN
    for y in range(2, 12):
        for x in range(8, 20):
            rim = x in (8, 19) or y in (2, 11)
            px[x, y] = OUTLINE if rim else Room.BROWN if x == 14 and y < 11 else WHITE
    px[14, 2] = Room.BROWN
    pieces = [piece("pc", [(8, 2, 20, 12)], 6, base=6, solid=True),
              piece("desk", [(0, 12, 32, 24)], 6, solid=True)]
    owner = [[0 if 8 <= x < 20 and 2 <= y < 12 else 1 if y >= 12 else None for x in range(W)]
             for y in range(H)]
    drawn = [({(x, y) for y in range(H) for x in range(W) if owner[y][x] == k}, {})
             for k in range(2)]
    floor = Image.new("RGBA", (16, 16), FLOOR)
    layout = types.SimpleNamespace(cell_image=lambda m: floor)
    spec = {"name": "room", "interior": {"ground": [0]}}
    return g.room_facts(spec, layout, pieces, drawn, owner, set(), px)[0]


check("room: a computer's dark line in its desk's brown is its own",
      [("bleed", "")] if computer_on_desk()["bleed"] else [], [])


# ── the allowlist: faults accepted, each with its reason ─────────────────

allowed, finished = g.lint_allowed()
check("allowlist: every fault accepted has its reason",
      [] if all(str(r).strip() for kinds in allowed.values() for r in kinds.values())
      else [("reason", "")], [])
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "allow.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write('finished = ["LAYOUT_X"]\n[allow.room_sink]\nunder = "its doors"\n')
    got = g.lint_allowed(path)
    check("allowlist: a piece's fault and its reason, a room finished",
          [] if got == ({"room_sink": {"under": "its doors"}}, ["LAYOUT_X"]) else [("read", "")], [])
    with open(path, "w", encoding="utf-8") as f:
        f.write('[allow.room_sink]\nunder = " "\n')
    try:
        g.lint_allowed(path)
        refused = []
    except SystemExit:
        refused = [("refused", "")]
    check("allowlist: a fault accepted with no reason is refused", refused, ["refused"])


# ── the verdict: the lint fails the build, in every room (F5) ───────────

def verdict(faults, linted, allowed, finished=(), rooms=("LAYOUT_X",), complete=True):
    """lint_verdict's failures, as (failure, text) for check()."""
    lines, failures = g.lint_verdict(faults, set(linted), allowed, list(finished),
                                     list(rooms), complete)
    return [("fail", t) for t in failures], lines


SINK = ("LAYOUT_X", "room_sink", "under", "the floor under it")
# a room no one has approved yet: until F5 a fault there was a warning
got, _ = verdict([SINK], ["room_sink"], {})
check("verdict: a fault in a room not finished fails", got, ["fail"])
got, lines = verdict([SINK], ["room_sink"], {"room_sink": {"under": "its doors"}})
check("verdict: a fault accepted passes, with its reason",
      got + ([] if any("(allowed: its doors)" in t for t in lines) else [("reason", "")]), [])
got, _ = verdict([SINK], ["room_sink"], {"room_sink": {"under": "its doors", "bleed": "its tap"}})
check("verdict: an allowance that finds no fault fails", got, ["fail"])
got, _ = verdict([], ["room_sink"], {"room_sink": {"undr": "its doors"}})
check("verdict: an allowance with its kind misspelt fails", got, ["fail"])
gone = {"room_tap": {"bleed": "its tap"}}
got, _ = verdict([], ["room_sink"], gone)
check("verdict: an allowance for a piece no room has fails", got, ["fail"])
got, _ = verdict([], ["room_sink"], gone, complete=False)
check("verdict: ...but not in an --only build, which leaves it out", got, [])
got, _ = verdict([], ["room_sink"], {}, finished=["LAYOUT_Y"])
check("verdict: a finished room with no pieces fails", got, ["fail"])
got, _ = verdict([], ["room_sink"], {}, finished=["LAYOUT_X"])
check("verdict: a finished room at 0 passes", got, [])

if FAILED:
    raise SystemExit("%d interior lint case(s) failed: %s" % (len(FAILED), ", ".join(FAILED)))
print("PASS interior lint: every fault by its kind, every piece made right by none")
