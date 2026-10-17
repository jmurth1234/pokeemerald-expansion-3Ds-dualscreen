#!/usr/bin/env python3
"""Building models, one reading of the drawing per building type.

Each spec names where a reference copy of the building sits (layout and cell
rectangle), which metatiles are the ground around it, the parts that rebuild
it, and the art rectangles the model must reproduce pixel for pixel when it
is rendered in the GBA's projection. Row numbers are art rows of that
rectangle.

Littleroot's houses
-------------------
The two houses are the same building. The drawing, from the top:

    rows  1- 8  upper ridge cap       rows 17-24 lower ridge cap  (the "ears")
    rows  9-15  upper ridge teeth     rows 25-31 lower ridge teeth
    rows 16-35  upper roof, 5 courses rows 32-51 lower roof, 5 courses
    rows 36-37  upper fascia          rows 52-53 lower fascia
    rows 38-44  upper storey wall     rows 54-79 ground floor facade

The ears are the same ridge and the same courses as the upper roof, sixteen
rows lower: a full-width lower roof, and a narrower upper storey that comes up
through it with its own roof.

The drawing is shallower than the house. Taken literally (every band at the
depth the 45-degree projection gives it) the house would be two tiles deep,
while it stands on four rows of collision. So the model keeps the real
footprint, and the depth the drawing does not have is covered the way a roof
covers it - with more courses of the same tiles (Strip), never with stretched
ones. The facade, the pent roof in front of the upper storey, the upper wall
and the upper roof's first four courses stay exactly the drawing; only the
roofs grow, by the courses the depth needs.

Both roofs are hipped: seen from the side they are tiles too, laid along the
side eave, so no gable wall of plaster ever shows.
"""

import math

from PIL import Image

from voxel_building import (Band, ProjCanopy, ProjDome, ProjHull, ProjLathe, ProjSection, BakedLathe, VoxelLathe, Card, Grown, PlanHip, PlanPrism, PlanPyramid, PlatePrism, SectionPrism, Cylinder, Frustum, HeightSolid, HipRoof, Mound, PlainWall, Prism, Proj,
                            Strip, Tile, Vault, Volume, Walls, clip, own_bands, _unit, strip_face,
                            LayoutArt, anim_frame,
                            SHADE_ART, SHADE_BACK, SHADE_EAST, SHADE_WEST)

GRASS = 0x001

PITCH = 22.0        # degrees, front and back slopes
ROOF_COLUMNS = (12, 68)   # courses sampled away from the drawn verges


def littleroot_house(plaster_x):
    """Both houses. They share every row and every edge; only the middle of
    the facade is rearranged, so each names the 8-px column of plain plaster
    (no window, no door, no post) its walls are dressed from."""
    px0, px1 = plaster_x, plaster_x + 8
    front_z, back_z = 80, 16    # the four collision rows, 5..8
    overhang = 2

    lower = HipRoof(
        "roof_lo", 0, 82, zf=front_z + overhang, zb=back_z - overhang, y0=28,
        fascia=Strip((52, 54), wrap=ROOF_COLUMNS),
        # the seven rows drawn in front of the upper storey, then courses
        # continuing in phase (row 45 sits one above a course's first row)
        slope=Strip((45, 52), repeat=(16, 20), start=16, wrap=ROOF_COLUMNS),
        teeth=(25, 32), cap=(17, 25), pitch=PITCH, run=7,
        ridge_u=(0, 80), end_tile=Tile(0, 25, 8, 32))

    # The upper storey stands on the pent roof where its seventh row ends.
    z_wall, y_base = lower.slope_point(7)
    zf_hi = z_wall + overhang
    y0_hi = zf_hi - 38          # the upper fascia is drawn on rows 36-37
    zb_hi = (lower.zf + lower.zb) - zf_hi
    upper = HipRoof(
        "roof_hi", 8, 72, zf=zf_hi, zb=zb_hi, y0=y0_hi,
        fascia=Strip((36, 38), wrap=ROOF_COLUMNS),
        slope=Strip((32, 36), repeat=(16, 32), wrap=ROOF_COLUMNS),
        teeth=(9, 16), cap=(1, 9), pitch=PITCH, run=6,
        ridge_u=(8, 72), end_tile=Tile(12, 9, 20, 16))

    storey_back = zb_hi + overhang
    post = Tile(10, 38, 14, 45, top=y0_hi)
    storey = Prism(
        "storey", 10, 70,
        [(z_wall, 28), (z_wall, y0_hi), (storey_back, y0_hi), (storey_back, 28)],
        edges={0: Proj(38, 45), 2: Tile(20, 38, 28, 45, top=y0_hi)}, skip=(1, 3),
        caps=[Band(-64, 200, Tile(20, 38, 28, 45, top=y0_hi), z_wall,
                   front=post, back=post, z1=storey_back)])

    wall_top = 28
    corner = Tile(2, 54, 8, 80, top=26)
    base = Prism(
        "ground_floor", 2, 80,
        [(front_z, 0), (front_z, wall_top), (back_z, wall_top), (back_z, 0)],
        edges={0: Proj(54, 80), 2: Tile(px0, 54, px1, 80, top=26)}, skip=(1, 3),
        caps=[Band(0, 26, Tile(px0, 54, px1, 80, top=26), front_z,
                   front=corner, back=corner, z1=back_z),
              Band(26, wall_top + 1, Tile(px0, 54, px1, 55, top=wall_top), front_z)])
    return [base, lower, storey, upper]


# What the model must reproduce exactly: the ground floor, the pent roof in
# front of the upper storey, the upper storey, and the upper roof's fascia and
# first four courses - everything the drawing shows at its own depth.
HOUSE_EXACT = [(2, 54, 80, 80), (12, 45, 68, 54), (10, 38, 70, 45), (12, 16, 68, 38)]

def littleroot_lab():
    """Professor Birch's lab, cells 3..9 x 12..16 of Littleroot.

    The drawing, from the top (112 x 80):

        rows  1- 9  flat top of the roof, light panels, period 8 across
        rows 10-14  its front edge
        rows 15-49  olive tile courses, period 4 down and 8 across
        rows 50-52  fascia
        rows 53-79  facade: posts, two windows, the door

    and on the roof, x 16-47 rows 2-31, a ventilator: a box whose front is
    rows 25-31 and top rows 15-24, carrying a round cowl drawn as a circle
    (rows 2-24). The box stands on the slope exactly where its front row 31
    meets the courses, so its front is the drawing at 45 degrees.
    """
    front_z, back_z = 80, 16    # collision rows 13..16
    overhang = 2
    lab_columns = (8, 104)      # courses away from both verges (12 x 8)
    roof = HipRoof(
        "roof", 0, 114, zf=front_z + overhang, zb=back_z - overhang, y0=29,
        fascia=Strip((50, 53), wrap=lab_columns),
        slope=Strip((46, 50), repeat=(34, 46), wrap=lab_columns),
        teeth=(10, 15), cap=(1, 10), pitch=18.0, run=6,
        ridge_u=(0, 112), end_tile=Tile(48, 10, 57, 15),
        ridge_wrap=(48, 104))    # the drawn ridge is behind the vent at 17-47

    # Ventilator: box front bottom on the slope where art row 32 lies.
    zbox, ybox = roof.slope_point(50 - 32)
    ytop = zbox - 25
    grey = Tile(20, 25, 40, 32, top=ytop)
    box = Prism(
        "vent_box", 16, 48,
        [(zbox, ybox - 6), (zbox, ytop), (zbox - 10, ytop), (zbox - 10, ybox - 6)],
        edges={0: Proj(25, 32), 2: grey}, skip=(3,),
        caps=[Band(-64, 200, grey, zbox)])
    cowl = Cylinder("vent_cowl", 32, zbox - 10, 12, 9, ytop - 4, ytop + 4,
                    back_tile=Tile(24, 16, 40, 25))

    wall_top = 29
    post = Tile(2, 53, 8, 80, top=27)
    plaster = Tile(8, 53, 16, 80, top=27)
    base = Prism(
        "ground_floor", 2, 112,
        [(front_z, 0), (front_z, wall_top), (back_z, wall_top), (back_z, 0)],
        edges={0: Proj(53, 80), 2: plaster}, skip=(1, 3),
        caps=[Band(0, 27, plaster, front_z, front=post, back=post, z1=back_z),
              Band(27, wall_top + 1, Tile(8, 53, 16, 54, top=wall_top), front_z)])
    return [base, roof, box, cowl]


LAB_EXACT = [(2, 53, 112, 80), (8, 32, 104, 53), (48, 16, 104, 32)]

# The Center's crown, read off its drawn front arch (rows 16-23): the height
# of the arch over each column, 1 px at x 16 and 47 and 8 px from 27 to 36.
# Its back arch is drawn exactly 16 rows higher in every column, so the vault
# is 16 px deep - the one depth the drawing states outright.
CENTER_ARCH = [(16, 0), (16.5, 1), (17.5, 2), (18.5, 3), (19.5, 4), (20.5, 4),
               (21.5, 5), (22.5, 6), (23.5, 6), (24.5, 7), (26.5, 7), (27.5, 8),
               (36.5, 8), (37.5, 7), (39.5, 7), (40.5, 6), (41.5, 6), (42.5, 5),
               (43.5, 4), (44.5, 4), (45.5, 3), (46.5, 2), (47.5, 1), (48, 0)]


def center_or_mart(rib_repeat, crown=False):
    """The Pokemon Center and the Poke Mart: one building, two paint jobs.

    The drawing (64 x 64), from the top:

        rows  2- 8  the Center's raised crown (the Mart has none)
        rows  9-23  the flat top, ribbed front to back
        rows 24-37  the roof band with its emblem, drawn round
        rows 38-63  walls: fascia, glass, the door, the sign, the plinth

    The plan is an octagon: the front runs x 8-56, and 8-px chamfers turn the
    corners. The drawing says so itself - the plinth runs diagonally from
    (8, 63) to (0, 56) and the eave from (8, 38) to (0, 30), which is what a
    45-degree chamfer looks like at 45 degrees. The emblem is drawn round, so
    the band faces the camera square on: it pitches at 45 degrees, rising 7.

    Real depth: three collision rows, Z 16-64. The top grows backwards by rows
    of its own ribs, which run front to back and so repeat without a seam.
    """
    front, back = 64, 16
    # the front chamfers a pixel out: the plinth's outline is drawn on the
    # diagonal's outer side, and on it stood on the ground
    plan = [(7, front), (57, front), (64, front - 7), (64, back + 8),
            (56, back), (8, back), (0, back + 8), (0, front - 7)]
    parts = [Frustum(
        "body", plan, wall_top=26,
        wall_side=Strip((38, 64), wrap=(8, 16)),
        band_rise=7,
        band_side=Strip((24, 38), wrap=(8, 16)),
        top=Strip((9, 24), repeat=rib_repeat, wrap=(8, 56)))]
    if crown:
        # on the flat top (y 33), from its front edge (z 57) back 16
        parts.append(Vault("crown", CENTER_ARCH, zf=57, zb=41, y0=33))
    return parts


# The walls, the chamfers, the band and the top as drawn; the corners above
# the chamfers are the band's sides, which the real depth moves.
CENTER_EXACT = [(8, 30, 56, 64), (0, 38, 64, 64), (12, 9, 52, 30)]
# The Center adds its crown, whole: both arches and the ribs between them.
CROWN_EXACT = CENTER_EXACT + [(16, 0, 48, 24, True)]

def oldale_house():
    """Oldale's two houses, cells 4..7 x 4..7 and 14..17 x 13..16.

    The drawing (64 x 64), from the top:

        rows  0- 9  the ridge's top, notched along its back edge
        rows 10-13  its front
        rows 14-33  five courses of tiles, period 4 down and 8 across
        rows 34-35  fascia
        rows 36-63  facade: posts, the window, the door

    One storey under a hipped roof, the same reading as Littleroot's lower
    roof. Real depth: the three collision rows, Z 16-64.
    """
    front_z, back_z = 64, 16
    overhang = 2
    columns = (8, 56)           # courses away from both verges (6 x 8)
    roof = HipRoof(
        "roof", 0, 64, zf=front_z + overhang, zb=back_z - overhang, y0=30,
        fascia=Strip((34, 36), wrap=columns),
        slope=Strip((30, 34), repeat=(14, 30), wrap=columns),
        teeth=(10, 14), cap=(0, 10), pitch=22.0, run=6,
        ridge_u=(0, 64), end_tile=Tile(8, 10, 18, 14))

    wall_top = 30
    post = Tile(2, 36, 9, 64, top=28)
    plaster = Tile(9, 36, 15, 64, top=28)
    base = Prism(
        "ground_floor", 2, 64,
        [(front_z, 0), (front_z, wall_top), (back_z, wall_top), (back_z, 0)],
        edges={0: Proj(36, 64), 2: plaster}, skip=(1, 3),
        caps=[Band(0, 28, plaster, front_z, front=post, back=post, z1=back_z),
              Band(28, wall_top + 1, Tile(9, 36, 15, 37, top=wall_top), front_z)])
    return [base, roof]


OLDALE_HOUSE_EXACT = [(2, 36, 64, 64), (8, 14, 56, 36)]


def briney_house():
    """Mr Briney's cottage on Route 104, cells 15..19 x 47..50 (80 x 64).

    The drawing, from the top:

        rows  0- 8  the ridge's top: thatch bundles, period 8 across
        rows  9-15  its front: a batten and a short course between dark lines
        rows 16-35  the thatch: period 8 down (rows 16-23 = 24-31) and across,
                    its eave course fringed at rows 32-35
        rows 36-38  fascia
        rows 39-47  the lattice under the eave (x 0-80)
        rows 48-63  the ground floor (x 1-80): posts, reed walls, the door

    One storey under a hipped roof, like Oldale's houses. Real depth: the
    three collision rows, Z 16-64. The verges (x 0-7 and 72-79) are drawn
    as edge bundles, so the courses are sampled from x 8-72.
    """
    front_z, back_z = 64, 16
    overhang = 2
    columns = (8, 72)
    roof = HipRoof(
        "roof", 0, 80, zf=front_z + overhang, zb=back_z - overhang, y0=27,
        fascia=Strip((36, 39), wrap=columns),
        slope=Strip((16, 36), repeat=(16, 24), wrap=columns),
        teeth=(9, 16), cap=(0, 9), pitch=22.0, run=6,
        ridge_u=(0, 80), end_tile=Tile(8, 9, 18, 16))

    floor_top, wall_top = 16, 27
    post = Tile(1, 48, 8, 64, top=floor_top)
    reed = Tile(8, 48, 18, 64, top=floor_top)
    lattice = Tile(8, 39, 16, 48, top=25)
    lattice_end = Tile(0, 39, 3, 48, top=25)
    ground_floor = Prism(
        "ground_floor", 1, 80,
        [(front_z, 0), (front_z, floor_top), (back_z, floor_top), (back_z, 0)],
        edges={0: Proj(48, 64), 2: reed}, skip=(1, 3),
        caps=[Band(0, floor_top, reed, front_z, front=post, back=post, z1=back_z)])
    upper = Prism(
        "lattice", 0, 80,
        [(front_z, floor_top), (front_z, wall_top), (back_z, wall_top), (back_z, floor_top)],
        edges={0: Proj(39, 48), 2: lattice}, skip=(1, 3),
        caps=[Band(floor_top, wall_top + 1, lattice, front_z, front=lattice_end,
                   back=lattice_end, z1=back_z)])
    return [ground_floor, upper, roof]


BRINEY_HOUSE_EXACT = [(1, 39, 80, 64), (0, 39, 1, 48), (8, 16, 72, 39)]


def flower_shop():
    """The Pretty Petal flower shop on Route 104, cells 3..8 x 15..18 (96 x 64).

    A flat roof of corrugated sheet, seen from above as the GBA sees every
    flat top. The drawing, from the top:

        rows  0- 3  the roof's back edge: an outline and three light rows
        rows  4-27  the sheet, every row the same (ribs period 4 across)
        rows 28-37  the roof slab's red front and the dark line under it
        rows 38-63  the facade (x 1-95): posts, the awning, windows, the door

    Real depth: the three collision rows, Z 16-64. The sheet is laid back
    to them with more of its own rows, the back edge closing it.
    """
    front, back = 64, 16
    wall = 64 - 38
    top = wall + (38 - 28)
    post = Tile(1, 38, 6, 64, top=wall)
    siding = Tile(72, 40, 80, 48, top=wall - 2)
    rim = Tile(9, 28, 17, 38, top=top)
    body = Prism(
        "ground_floor", 1, 95,
        [(front, 0), (front, wall), (back, wall), (back, 0)],
        edges={0: Proj(38, 64), 2: siding}, skip=(1, 3),
        caps=[Band(0, wall, siding, front, front=post, back=post, z1=back)])
    roof = Prism(
        "roof", 0, 96,
        [(front, wall), (front, top), (back, top), (back, wall)],
        edges={0: Proj(28, 38), 1: Strip((4, 28), repeat=(20, 28), tail=(0, 4)), 2: rim},
        skip=(3,), caps=[Band(wall, top + 1, rim, front)])
    return [body, roof]


FLOWER_SHOP_EXACT = [(0, 4, 96, 38), (1, 38, 95, 64)]

def kit_house(width):
    """The General tileset's red-roofed house, any width (in pixels).

    Built from a kit - roof caps, repeated middles, a facade of posts, windows
    and a door - so the same drawing comes 4 and 5 cells wide. The rows are
    the kit's, whatever the width (64 high):

        rows  0- 9  the ridge's top, period 8 across
        rows 10-15  its front
        rows 16-34  scale tiles: two 8-row courses and the eave's three rows
        rows 35-37  fascia
        rows 38-63  facade

    Real depth: three collision rows, Z 16-64. Courses are sampled between
    the drawn verges (x 0-2 and the last three columns), keeping the phase.
    """
    front_z, back_z = 64, 16
    overhang = 2
    columns = (8, width - 8)
    roof = HipRoof(
        "roof", 0, width, zf=front_z + overhang, zb=back_z - overhang, y0=28,
        fascia=Strip((35, 38), wrap=columns),
        slope=Strip((32, 35), repeat=(16, 32), wrap=columns),
        teeth=(10, 16), cap=(0, 10), pitch=22.0, run=6,
        ridge_u=(0, width), end_tile=Tile(8, 10, 18, 16))
    wall_top = 28
    post = Tile(2, 38, 8, 64, top=26)
    boards = Tile(9, 38, 15, 64, top=26)
    base = Prism(
        "ground_floor", 2, width - 2,
        [(front_z, 0), (front_z, wall_top), (back_z, wall_top), (back_z, 0)],
        edges={0: Proj(38, 64), 2: boards}, skip=(1, 3),
        caps=[Band(0, 26, boards, front_z, front=post, back=post, z1=back_z),
              Band(26, wall_top + 1, Tile(9, 38, 15, 39, top=wall_top), front_z)])
    return [base, roof]


def kit_house_exact(width):
    return [(2, 38, width - 2, 64), (8, 16, width - 8, 38)]


def kit_house_keep(width):
    """The kit house's own pixels: its roof, and of its facade's rows what its
    ground floor stands on (x 2 to width - 2). The lawn either side and its
    shadow on the ground are the map's, laid under it (patch_own): as its
    own, nothing drew them and the ground round it showed there (the strip
    of paving between Slateport's two houses)."""
    return [(0, 0, width, 38), (2, 38, width - 2, 64)]


def dewford_house(width):
    """Dewford's blue tiled houses and Hall, with their own facade art.

    Three collision rows give Z 16..64. The ridge cap occupies rows 0..8,
    its front 9..15, the roof courses 16..34, fascia 35..37 and facade
    38..63. Clean ridge columns exclude the trees painted into the Hall's
    northern edge; the map's trees remain separate objects.
    """
    front, back, wall = 64, 16, 28
    columns = (8, width - 8)
    roof = HipRoof(
        "roof", 0, width, zf=front + 2, zb=back - 2, y0=wall,
        fascia=Strip((35, 38), wrap=columns),
        slope=Strip((32, 35), repeat=(16, 32), wrap=columns),
        teeth=(9, 16), cap=(0, 9), pitch=PITCH, run=6,
        ridge_u=(0, width), end_tile=Tile(16, 9, 25, 16),
        ridge_wrap=(16, width - 8))
    plaster = Tile(9, 38, 15, 64, top=26)
    post = Tile(2, 38, 8, 64, top=26)
    body = Prism(
        "ground_floor", 2, width - 2,
        [(front, 0), (front, wall), (back, wall), (back, 0)],
        edges={0: Proj(38, 64), 2: plaster}, skip=(1, 3),
        caps=[Band(0, 26, plaster, front, front=post, back=post, z1=back),
              Band(26, wall + 1, Tile(9, 38, 15, 39, top=wall), front)])
    return [body, roof]


def gym():
    """The gym of Petalburg, Mauville, Mossdeep and Lavaridge (96 x 80).

        rows  1-40  a flat gravel roof, period 2 down and across
        rows 41-44  its edge: a light lip and the fascia
        rows 45-71  the facade either side of the porch
        rows 41-48  (x 40-71) the roof running on over the porch
        rows 49-53  the porch's own fascia, eight rows lower: it stands out 8
        rows 54-79  the porch: doors square on, and 8-px chamfers either side,
                    whose plinth runs diagonally from (40, 71) to (48, 79)

    Real depth: the four collision rows reach the top of the drawing, so the
    back wall stands at Z 2 and the gravel repeats back to it.
    """
    front, porch, back = 72, 80, 2
    wall_top, roof_top = 27, 31
    panel = Tile(72, 45, 88, 72, top=wall_top)      # a window bay, for sides
    corner = Tile(88, 45, 94, 72, top=wall_top)
    lip = Tile(8, 41, 16, 45, top=roof_top)
    body = Prism(
        "body", 2, 94,
        [(front, -1), (front, wall_top), (back, wall_top), (back, -1)],
        edges={0: Proj(45, 72), 2: panel}, skip=(1, 3),
        caps=[Band(-1, wall_top, panel, front, front=corner, back=corner, z1=back)])
    roof = Prism(
        "roof", 0, 96,
        [(front, wall_top), (front, roof_top), (back, roof_top), (back, wall_top)],
        edges={0: Proj(41, 45), 1: Strip((5, 41), repeat=(5, 7)), 2: lip},
        skip=(3,), caps=[Band(wall_top, roof_top + 1, lip, front)])
    porch_roof = Prism(
        "porch_roof", 40, 72,
        [(porch, wall_top), (porch, roof_top), (front, roof_top), (front, wall_top)],
        edges={0: Proj(49, 54)}, skip=(2, 3),
        caps=[Band(wall_top, roof_top + 1, lip, porch)])
    porch_walls = Walls("porch", [(40, front), (48, porch), (64, porch), (72, front)],
                        -1, wall_top)
    return [body, roof, porch_roof, porch_walls]


GYM_EXACT = [(2, 45, 40, 72), (72, 45, 94, 72), (3, 5, 93, 45), (40, 41, 72, 80)]

def flat_block(width, height, roof, cornice, facade_top, unit=None, brick=None, rim=None,
               x0=0, x1=None):
    """A flat-roofed block: parapeted roof, cornice, facade (any size).

    `roof` = (fixed, repeat, tail): the roof's rows as the drawing lays them
    from its front rim back, the course that repeats to the real depth, and
    the back parapet that closes it. `cornice` = (top, bottom) rows of the
    parapet's front; the facade runs from `facade_top` to the bottom. `unit`
    = (x0, x1, top, face, foot): a box on the roof whose top is drawn from
    row `top`, whose front from `face` to its foot on the roof at `foot`.

    Real depth: the collision starts one row below the drawing's top, so the
    back wall stands at Z 16 and the front at the drawing's foot.

    The sides and the back are dressed with `brick` (u0, v0, u1, v1: a clean
    stretch of the facade, by default the 8 x 16 next to its west corner)
    between the corner posts, and the parapet's sides with `rim` (by default
    the cornice next to the corner): a facade whose first rows there are
    something else (an awning drawn slanting) names its own.

    `x0`, `x1`: the columns the block is drawn over, when its drawing does
    not fill the whole rectangle (the gatehouses start a column in, the
    ground's, and end a column early, their shadow's): a block over the
    clear columns had them as slits up its corners and along its roof.
    """
    x1 = width if x1 is None else x1
    front, back = height, 16
    wall = height - facade_top
    c0, c1 = cornice
    top = wall + (c1 - c0)
    fixed, repeat, tail = roof
    brick = Tile(*(brick or (8, facade_top, 16, facade_top + 16)), top=wall)
    pilaster = Tile(x0, facade_top, x0 + 8, facade_top + 16, top=wall)
    rim = Tile(*(rim or (8, c0, 16, c1)), top=top)
    body = Prism(
        "body", x0, x1,
        [(front, -1), (front, wall), (back, wall), (back, -1)],
        edges={0: Proj(facade_top, height), 2: brick}, skip=(1, 3),
        caps=[Band(-1, wall, brick, front, front=pilaster, back=pilaster, z1=back)])
    parts = [body]

    def slab(name, a, b, offset=0.0):
        return Prism(
            name, a, b,
            [(front, wall), (front, top), (back, top), (back, wall)],
            edges={0: Proj(c0, c1),
                   1: Strip(fixed, repeat=repeat, tail=tail,
                            repeat_offset=offset if offset else None), 2: rim},
            skip=(3,), caps=[Band(wall, top + 1, rim, front)],
            west=(a == x0), east=(b == x1))

    if unit is None:
        parts.append(slab("roof", x0, x1))
    else:
        ux0, ux1, t0, t1, t2 = unit
        # under and behind the unit the roof is laid from plain roof columns
        parts += [slab("roof_w", x0, ux0), slab("roof_u", ux0, ux1, offset=8 - ux0),
                  slab("roof_e", ux1, x1)]
        zu = t2 + top
        side = Tile(ux0 + 8, t1, ux0 + 16, t2, top=top + (t2 - t1))
        parts.append(Prism(
            "unit", ux0, ux1,
            [(zu, top - 1), (zu, top + (t2 - t1)), (zu - (t1 - t0), top + (t2 - t1)),
             (zu - (t1 - t0), top - 1)],
            edges={0: Proj(t1, t2), 1: Proj(t0, t1), 2: side}, skip=(3,),
            caps=[Band(top - 1, top + (t2 - t1) + 1, side, zu)]))
    return parts


def stone_block(width, height, meta):
    """Rustboro's stone blocks (224..21f): the flat roof drawn rows 0-38, a
    course of 4 rows repeating, the back parapet rows 0-6; the parapet's front
    rows 39-47; the facade below, one row of windows per storey."""
    return flat_block(width, height, ((7, 39), (7, 11), (0, 7)), (39, 48), 48)


def olive_block(width, height, meta):
    """Rustboro's olive-roofed blocks (220..243): the roof drawn rows 0-39 in
    8-row courses, the cornice rows 40-47, the facade below. Most carry a
    ventilation unit on the right end of the roof (metatiles 222 223)."""
    unit = (width - 24, width - 4, 1, 16, 31) if meta.get("unit") else None
    return flat_block(width, height, ((8, 40), (8, 16), (0, 8)), (40, 48), 48, unit)


def flat_block_exact(width, height, first_roof_row):
    return [(0, first_roof_row, width, height)]

def flat_part(name, x0, x1, front, back, roof, cornice, facade_top,
              brick, rim, west=True, east=True):
    """One flat-roofed volume over [x0, x1): facade from `facade_top` to its
    foot at `front`, the parapet's front `cornice` above it, and the roof laid
    back to `back` by `roof` = (fixed, repeat, tail). `brick` and `rim` are
    the art rectangles its sides are dressed with."""
    wall = front - facade_top
    c0, c1 = cornice
    top = wall + (c1 - c0)
    fixed, repeat, tail = roof
    brick_t = Tile(*brick, top=wall)
    rim_t = Tile(*rim, top=top)
    body = Prism(
        name, x0, x1,
        [(front, 0), (front, wall), (back, wall), (back, 0)],
        edges={0: Proj(facade_top, front), 2: brick_t}, skip=(1, 3),
        caps=[Band(0, wall, brick_t, front)], west=west, east=east)
    slab = Prism(
        name + "_roof", x0, x1,
        [(front, wall), (front, top), (back, top), (back, wall)],
        edges={0: Proj(c0, c1), 1: Strip(fixed, repeat=repeat, tail=tail), 2: rim_t},
        skip=(3,), caps=[Band(wall, top + 1, rim_t, front)], west=west, east=east)
    return [body, slab]


def devon():
    """Devon Corporation (160 x 144): two wings and a tower between them.

        wings  x 0-47, 112-159: roof rows 2-31 (a 2-row lattice), parapet
               front rows 32-39, three storeys rows 40-127
        tower  x 48-111: roof rows 0-39 (8-row courses) with the company's
               emblem across its front rim, a double cornice rows 40-55,
               the facade and the arched doors rows 56-143 - it stands one
               row further south than the wings

    Real depth: the collision covers the drawing's top row, so the back wall
    stands at Z 0.
    """
    wing_roof = ((8, 32), (8, 10), (2, 8))
    parts = []
    parts += flat_part("wing_w", 0, 48, 128, 0, wing_roof, (32, 40), 40,
                       brick=(8, 40, 48, 72), rim=(8, 32, 48, 40), east=False)
    parts += flat_part("wing_e", 112, 160, 128, 0, wing_roof, (32, 40), 40,
                       brick=(112, 40, 152, 72), rim=(112, 32, 152, 40), west=False)
    parts += flat_part("tower", 48, 112, 144, 0, ((9, 40), (9, 17), (0, 9)), (40, 56), 56,
                       brick=(48, 56, 56, 88), rim=(56, 40, 64, 56))
    return parts


DEVON_EXACT = [(0, 8, 160, 128), (48, 128, 112, 144)]


# Rustboro's fountain (48 x 48), as its drawing tells it:
#
#   the basin's outer wall, 5 px: on the front rows 41-45 (its top edge,
#       three rows of stone, the outline), the chamfers a band as wide;
#       the plan below is its foot, the outline drawn round it
#   the rim's top at that height, in to the pool's edge: 6-7 px wide at the
#       sides and the front, 3 at the back (rows 2-4)
#   the pool's water 2 px lower: the back's inner wall is rows 5-6, the
#       inner chamfers' the grey line beside the water
#   the dish in the middle, its top 5 px over the water (the dotted rim's
#       ellipse, rows 11-23, x 15-33), its edge 2 px (rows 23-24 in front),
#       on a stem the dish hides; the grey under it, down to row 27, is its
#       shadow on the water
#   the jet standing on the dish, rows 8-16 - the only thing that moves
FOUNTAIN_RECT = (27, 38)
FOUNTAIN_FOOT = [(11, 7), (37, 7), (47, 17), (47, 34), (35, 46), (13, 46), (1, 34), (1, 17)]
FOUNTAIN_POOL = [(15, 10), (33, 10), (40, 17), (40, 32), (32, 40), (16, 40), (8, 32), (8, 17)]
FOUNTAIN_RIM, FOUNTAIN_WATER, FOUNTAIN_DISH = 5, 3, 8
DISH_AT, DISH_R, DISH_EDGE, STEM_R = (24.0, 25.0), (9.0, 6.0), 2, (3.0, 2.2)
# The jet: columns 19-28 of rows 8-16, drawn with Rustboro's secondary tiles
# 448-451, which the game redraws every 8 frames from two frames
# (tileset_anims.c QueueAnimTiles_Rustboro_Fountain; BG tiles 960-963).
JET_COLUMNS, JET_ROWS = (19, 29), (8, 17)
# (full.mk builds them, tools/vtree_manifest.py hands them to the builder)
FOUNTAIN_ANIM = ["data/tilesets/secondary/rustboro/anim/fountain/%d.4bpp" % f for f in (0, 1)]
FOUNTAIN_ANIM_TILE = 512 + 448
JET_BAND = 48       # the texture's row the jet's first row is laid on


def fountain_jet():
    """The jet's pixels: {(x, y): (tile, pixel, palette)} where each comes
    from on the upper layer, and their colours in each animation frame."""
    lay = LayoutArt("LAYOUT_RUSTBORO_CITY")
    ox, oy = FOUNTAIN_RECT[0] * 16, FOUNTAIN_RECT[1] * 16
    src = {(x, y): lay.upper_source(ox + x, oy + y)
           for y in range(*JET_ROWS) for x in range(*JET_COLUMNS)}
    frames = []
    for path in FOUNTAIN_ANIM:
        tiles = anim_frame(path, FOUNTAIN_ANIM_TILE)
        colours = {}
        for at, (tile, pixel, pal) in src.items():
            index = lay.tile_pixel(tile, pixel, tiles)
            if index == 0:
                raise SystemExit("rustboro_fountain: the jet's pixel %d,%d is clear in %s" % (at + (path,)))
            colours[at] = lay.palette(pal)[index]
        frames.append(colours)
    return src, frames


def fountain(art):
    """The fountain in 3D (see FOUNTAIN_FOOT), and its texture: the drawing
    with the jet taken out of it - the pool and the dish as they lie behind
    it - and the jet alone on a band below, read by a card standing on the
    dish. The card's texels follow the game's animation (`live`): the
    console redraws them from the jet's tiles whenever the game does, so
    the jet plays as it does on the GBA, and only the jet."""
    _, frames = fountain_jet()
    apx = art.load()
    for (x, y), rgb in frames[0].items():
        if apx[x, y][:3] != rgb:
            raise SystemExit("rustboro_fountain: the jet's first frame is not the drawing at %d,%d" % (x, y))
    # what lies behind the jet: above the dish the pool's own lines (the
    # ripple under the back rim, open water, the dish's far edge); on the
    # dish its water, wherever the two frames differ or draw the jet's
    # white and blue (down its middle)
    jet = {f[(x, y)] for f in frames for x in (23, 24) for y in range(9, JET_ROWS[1])}
    dish_water = frames[0][(21, 11)]
    behind = {}
    for (x, y) in frames[0]:
        a, b = frames[0][(x, y)], frames[1][(x, y)]
        if y < 11:
            behind[(x, y)] = frames[0][(JET_COLUMNS[0], y)]
        else:
            behind[(x, y)] = a if a == b and a not in jet else dish_water
    rows = JET_ROWS[1] - JET_ROWS[0]
    tex = Image.new("RGBA", (art.size[0], JET_BAND + rows), (0, 0, 0, 0))
    tex.paste(art, (0, 0))
    tpx = tex.load()
    live = {}
    for (x, y), bg in behind.items():
        tpx[x, y] = bg + (255,)
        rgb = frames[0][(x, y)]
        at = (x, JET_BAND + y - JET_ROWS[0])
        tpx[at] = rgb + (255,) if rgb != bg else (0, 0, 0, 0)
        live[at] = (x, y, bg)
    parts = [
        PlanPrism("basin", FOUNTAIN_FOOT, 0, FOUNTAIN_RIM, (14, 34, 46), open_top=True),
        PlanRing("rim", FOUNTAIN_FOOT, FOUNTAIN_POOL, FOUNTAIN_RIM),
        PlanPrism("pool", FOUNTAIN_POOL, FOUNTAIN_WATER, FOUNTAIN_RIM, (16, 32, 7),
                  open_top=True, inward=True),
        PlanRing("water", FOUNTAIN_POOL, None, FOUNTAIN_WATER),
        Cylinder("dish", DISH_AT[0], DISH_AT[1], DISH_R[0], DISH_R[1], FOUNTAIN_DISH - DISH_EDGE,
                 FOUNTAIN_DISH, back_tile=Tile(21, 23, 27, 25), sides=24),
        Cylinder("stem", DISH_AT[0], DISH_AT[1], STEM_R[0], STEM_R[1], FOUNTAIN_WATER,
                 FOUNTAIN_DISH - DISH_EDGE, back_tile=Tile(18, 25, 30, 28), sides=12, proj=False),
        # under the dish, a little over the water: inside what the dish hides
        # at 45 degrees (its top's ellipse swept down its edge, moved to this
        # height), its shadow's grey
        Shade("dish_shadow", DISH_AT[0], DISH_AT[1] - (FOUNTAIN_DISH - FOUNTAIN_WATER) + 1.25,
              DISH_R[0] - 0.5, DISH_R[1] + 0.5, FOUNTAIN_WATER + 0.25, (24, 25)),
        Jet("jet", JET_COLUMNS, DISH_AT[1], FOUNTAIN_DISH, JET_BAND - JET_ROWS[0]),
    ]
    return parts, tex, live


def fountain_drawing(model):
    """What the fountain is judged against: its drawing, the jet in it as
    the first frame draws it (the band laid back over the pool)."""
    art = model.art.crop((0, 0, 48, 48))
    band = model.art.crop((0, JET_BAND, 48, JET_BAND + JET_ROWS[1] - JET_ROWS[0]))
    art.paste(band, (0, JET_ROWS[0]), band)
    return art


class Awning:
    """A cloth roof on posts that people walk under: a flat top, a valance
    hanging round it and a post at each front corner.

    Drawn: the top over rows [top_v, valance_v), the valance's front over
    [valance_v, post_v), the posts below it to their foot at `foot_v`, in the
    columns `posts`. As drawn it would stand lower than a person, so the
    whole roof is lifted by `lift` along the GBA's line of sight, (0, l, l),
    which leaves it where it is drawn; the posts reach the ground with more
    of their own shaft, their foot `lift` rows south of where it is drawn.
    The top goes back to `back` (its real depth) with rows of its pattern
    (`repeat`); the valance runs round all four sides.
    """

    def __init__(self, name, x0, x1, top_v, valance_v, post_v, foot_v, back, lift,
                 repeat, posts, shaft, foot):
        self.name, self.x0, self.x1 = name, x0, x1
        self.top_v, self.valance_v, self.post_v, self.foot_v = top_v, valance_v, post_v, foot_v
        self.back, self.lift, self.repeat = back, lift, repeat
        self.posts, self.shaft, self.foot = posts, shaft, foot

    def roof(self, mesh, zf, yt, zb):
        # its back edge: the outline and the light row under it
        top = Strip((self.top_v + 2, self.valance_v), repeat=self.repeat,
                    tail=(self.top_v, self.top_v + 2))
        x0, x1 = self.x0, self.x1
        strip_face(mesh, [(x0, yt, zf), (x1, yt, zf), (x1, yt, zb), (x0, yt, zb)],
                   (0.0, yt, zf), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), top, SHADE_ART,
                   self.name + ".top")

    def emit(self, mesh):
        n, x0, x1, l = self.name, self.x0, self.x1, self.lift
        zf = self.foot_v + l                     # the posts' foot, on the ground
        yt = zf - self.valance_v                 # the top's height
        yb = zf - self.post_v                    # the valance's lower edge
        zb = self.back
        up = (0.0, 1.0, 0.0)
        self.roof(mesh, zf, yt, zb)
        val = Strip((self.valance_v, self.post_v))
        # the flanks take the front's columns from its west end on
        flank = Strip((self.valance_v, self.post_v), offset=x0 + 1 - zb)
        sides = [
            ([(x0, yb, zf), (x1, yb, zf), (x1, yt, zf), (x0, yt, zf)],
             (0.0, yb, zf), (1.0, 0.0, 0.0), SHADE_ART, val),
            ([(x1, yb, zb), (x0, yb, zb), (x0, yt, zb), (x1, yt, zb)],
             (0.0, yb, zb), (1.0, 0.0, 0.0), SHADE_BACK, val),
            ([(x0, yb, zb), (x0, yb, zf), (x0, yt, zf), (x0, yt, zb)],
             (x0, yb, 0.0), (0.0, 0.0, 1.0), SHADE_WEST, flank),
            ([(x1, yb, zf), (x1, yb, zb), (x1, yt, zb), (x1, yt, zf)],
             (x1, yb, 0.0), (0.0, 0.0, 1.0), SHADE_EAST, flank),
        ]
        cx, cz = (x0 + x1) / 2.0, (zf + zb) / 2.0
        for quad, origin, sdir, shade, val in sides:
            strip_face(mesh, quad, origin, sdir, up, val, shade, n + ".valance")
            # its inside, seen between the posts: a quarter of a pixel in,
            # or the two faces fight for the same depth
            inner = [(x + 0.25 * ((cx > x) - (cx < x)) * (sdir[2] != 0), y,
                      z + 0.25 * ((cz > z) - (cz < z)) * (sdir[0] != 0))
                     for (x, y, z) in reversed(quad)]
            o = inner[-1]
            strip_face(mesh, inner, (origin[0] if sdir[0] else o[0], origin[1],
                                     origin[2] if sdir[2] else o[2]),
                       sdir, up, val, SHADE_BACK, n + ".valance_in")
        # the posts: thin legs, so each is one flat card facing south with
        # the drawn front post on it - the foot as drawn, then the shaft up
        # to the valance. As boxes, every side repeated the front and from
        # the game's camera each leg read as two. The back ones (the roof
        # hides them at 45 degrees) stand at the back edge.
        for (pa, pb) in self.posts:
            for z in (zf, zb + (pb - pa)):
                strip_face(mesh, [(pa, 0, z), (pb, 0, z), (pb, yb, z), (pa, yb, z)],
                           (0.0, 0.0, z), (1.0, 0.0, 0.0), up,
                           Strip(self.foot, repeat=self.shaft), SHADE_ART, n + ".post")


class Tent(Awning):
    """A market stall: an awning whose roof is hipped, its ridge along X.

    Every face of it is a flat colour in the drawing - the front slope, the
    ridge, the back slope (seen over the ridge: the roof is low) and the two
    hips, lit from the west - so the faces take the GBA projection where the
    drawing shows them and their own colour where the real depth carries
    them beyond it. `ridge` = (xa, xb, v0, v1): the ridge's ends and the rows
    of its flat top; `pitch` the front slope's. `fills` = (back, west, east):
    an art rectangle of each face's colour.
    """

    def __init__(self, name, x0, x1, top_v, valance_v, post_v, foot_v, back, lift,
                 posts, shaft, foot, ridge, pitch, fills):
        Awning.__init__(self, name, x0, x1, top_v, valance_v, post_v, foot_v, back, lift,
                        None, posts, shaft, foot)
        self.ridge, self.pitch, self.fills = ridge, pitch, fills

    def roof(self, mesh, zf, ye, zb):
        n, x0, x1 = self.name, self.x0, self.x1
        xa, xb, r0, r1 = self.ridge
        # the front slope rises r1 - valance_v rows of drawing: run + rise
        rows = self.valance_v - r1
        t = math.tan(math.radians(self.pitch))
        run = rows / (1.0 + t)
        zr, yr = zf - run, ye + run * t
        zq = zr - (r1 - r0)

        def proj(pts, lo, hi):
            return [(x, y, z, x, min(max(z - y, lo), hi)) for (x, y, z) in pts]

        def fill(pts, rect):
            u0, v0, u1, v1 = rect
            return [(x, y, z, min(max(x, u0), u1), min(max(z - y, v0), v1))
                    for (x, y, z) in pts]

        front = [(x0, ye, zf), (x1, ye, zf), (xb, yr, zr), (xa, yr, zr)]
        mesh.poly(proj(front, r1, self.valance_v), SHADE_ART, n + ".slope_s~proj")
        top = [(xa, yr, zr), (xb, yr, zr), (xb, yr, zq), (xa, yr, zq)]
        mesh.poly(proj(top, r0, r1), SHADE_ART, n + ".ridge~proj")
        back, west, east = self.fills
        mesh.poly(fill([(xa, yr, zq), (xb, yr, zq), (x1, ye, zb), (x0, ye, zb)], back),
                  SHADE_ART, n + ".slope_n~proj")
        for hip, rect in (([(x0, ye, zb), (x0, ye, zf), (xa, yr, zr), (xa, yr, zq)], west),
                          ([(x1, ye, zf), (x1, ye, zb), (xb, yr, zq), (xb, yr, zr)], east)):
            pts = [(x, y, z, x, z - y) for (x, y, z) in hip]
            # as drawn below the ridge's front, its own colour behind it
            near = clip(pts, 4, float(r1), True)
            far = clip(pts, 4, float(r1), False)
            if len(near) >= 3:
                mesh.poly([(x, y, z, u, min(v, self.valance_v)) for (x, y, z, u, v) in near],
                          SHADE_ART, n + ".hip~proj")
            if len(far) >= 3:
                mesh.poly(fill([q[:3] for q in far], rect), SHADE_ART, n + ".hip~proj")


def proj_rows(mesh, pts, rows, tag, shade=SHADE_ART):
    """A face in the GBA projection, as drawn between `rows`; where the
    real depth carries it beyond them, its edge row held (courses that run
    straight across those rows go on as they are)."""
    lo, hi = rows
    poly = [(x, y, z, x, z - y) for (x, y, z) in pts]
    for piece, hold in ((clip(poly, 4, lo, False), lo), (clip(poly, 4, hi, True), hi)):
        if len(piece) >= 3:
            mesh.poly([q[:4] + (hold,) for q in piece], shade, tag)
    mid = clip(clip(poly, 4, lo, True), 4, hi, False) if len(poly) >= 3 else []
    if len(mid) >= 3:
        mesh.poly(mid, shade, tag)


class Mansard:
    """A hipped roof with a flat deck: four slopes from the eave rectangle
    (x0..x1, zb..zf at `ye`) up to the deck (inset `side` across, `run` front
    and back, at `ye + rise`).

    The front slope and the hips take the GBA projection - the front as
    drawn, the hips' courses run front to back, so beyond the drawing they
    go on as their own columns (rows clamped to `hip_rows`). The deck is laid
    from its front edge back by `deck` (a Strip), the back slope by `back`.
    """

    def __init__(self, name, x0, x1, zf, zb, ye, side, run, rise, front_rows, hip_rows,
                 deck, back):
        self.name, self.x0, self.x1, self.zf, self.zb, self.ye = name, x0, x1, zf, zb, ye
        self.side, self.run, self.rise = side, run, rise
        self.front_rows, self.hip_rows, self.deck, self.back = front_rows, hip_rows, deck, back

    def emit(self, mesh):
        n, x0, x1, zf, zb, ye = self.name, self.x0, self.x1, self.zf, self.zb, self.ye
        xa, xb = x0 + self.side, x1 - self.side
        za, zq, yt = zf - self.run, zb + self.run, ye + self.rise
        proj_rows(mesh, [(x0, ye, zf), (x1, ye, zf), (xb, yt, za), (xa, yt, za)],
                  self.front_rows, n + ".slope_s~proj")
        proj_rows(mesh, [(x0, ye, zb), (x0, ye, zf), (xa, yt, za), (xa, yt, zq)],
                  self.hip_rows, n + ".hip_w~proj")
        proj_rows(mesh, [(x1, ye, zf), (x1, ye, zb), (xb, yt, zq), (xb, yt, za)],
                  self.hip_rows, n + ".hip_e~proj")
        strip_face(mesh, [(x1, ye, zb), (x0, ye, zb), (xa, yt, zq), (xb, yt, zq)],
                   (0.0, ye, zb), (1.0, 0.0, 0.0), _unit((0.0, self.rise, self.run)),
                   self.back, SHADE_BACK, n + ".slope_n")
        strip_face(mesh, [(xa, yt, za), (xb, yt, za), (xb, yt, zq), (xa, yt, zq)],
                   (0.0, yt, za), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), self.deck, SHADE_ART,
                   n + ".deck")


class Posts:
    """Square posts standing in front of a wall: every side shows the drawn
    front of the post (its columns, its rows), the top its first row."""

    def __init__(self, name, posts, z0, z1, y1, last=None):
        self.name, self.posts, self.z0, self.z1, self.y1 = name, posts, z0, z1, y1
        # the drawing's last row: a post standing out past the model's cells
        # has its foot drawn in the cell south of them, which is not the
        # model's - its last drawn row goes on down to the ground (a post is
        # the same all the way down)
        self.last = last

    def emit(self, mesh):
        z0, z1, y1, n = self.z0, self.z1, self.y1, self.name
        top = z1 - y1
        if self.last is not None:
            emit_poly = mesh.poly
            last = self.last - 0.5

            class _Held:
                def poly(_, pts, shade, tag):
                    emit_poly([p[:4] + (min(p[4], last),) for p in pts], shade, tag)
            mesh = _Held()
        for (a, b) in self.posts:
            mesh.poly([(a, -1, z1, a, z1 + 1), (b, -1, z1, b, z1 + 1), (b, y1, z1, b, top),
                       (a, y1, z1, a, top)], SHADE_ART, n + "~proj")
            mesh.poly([(b, -1, z0, b, z1 + 1), (a, -1, z0, a, z1 + 1), (a, y1, z0, a, top),
                       (b, y1, z0, b, top)], SHADE_BACK, n + "~proj")
            # the sides: the front's columns laid across the depth, the
            # corner's own column at the front (one column stretched back
            # along the depth only made stripes)
            mesh.poly([(a, -1, z0, b, z1 + 1), (a, -1, z1, a, z1 + 1), (a, y1, z1, a, top),
                       (a, y1, z0, b, top)], SHADE_WEST, n + "~proj")
            mesh.poly([(b, -1, z1, b, z1 + 1), (b, -1, z0, a, z1 + 1), (b, y1, z0, a, top),
                       (b, y1, z1, b, top)], SHADE_EAST, n + "~proj")
            mesh.poly([(a, y1, z1, a, top), (b, y1, z1, b, top), (b, y1, z0, b, top + 1),
                       (a, y1, z0, a, top + 1)], SHADE_ART, n + "~proj")


class Canopy:
    """A porch roof over a door: a flat slab standing straight out of the
    facade (at `zw`) to its front at `zf`, on two posts.

    Drawn: its top, `y1` high, from the wall out (its flutes run away from
    the wall, so they are drawn as columns), then its front down to the
    posts' tops at `y0`, the front's lower edge arched over the door
    (`arch`: (x, y) from post to post). The top's back corners are cut
    (`chamfer` = (dx, dz)) as the drawing rounds them. Every face the
    drawing shows is projected; the vault under the arch takes the
    doorway's dark `inside` texel, the two ends the front's edge columns
    laid across the depth. The posts are one flat card each, facing south,
    as drawn down to their foot (`last`: their last drawn row goes on to
    the ground). Under it the wall paints what the drawing has there (the
    flutes, the posts again), seen past the posts as a second porch: a
    skin of the wall's own column beside the porch (`skin`) covers it
    beside the door, and the doorway's dark over it down to the row of its
    sill (`sill`).
    """

    def __init__(self, name, x0, x1, zw, zf, y0, y1, arch, chamfer, posts, inside, last,
                 skin, sill):
        self.name, self.x0, self.x1, self.zw, self.zf = name, x0, x1, zw, zf
        self.sill = sill
        self.y0, self.y1, self.arch, self.chamfer = y0, y1, arch, chamfer
        self.posts, self.inside, self.last, self.skin = posts, inside, last, skin

    def emit(self, mesh):
        n, x0, x1, zw, zf, y0, y1 = (self.name, self.x0, self.x1, self.zw, self.zf,
                                     self.y0, self.y1)
        cx, cz = self.chamfer
        iu, iv = self.inside

        def proj(pts):
            return [(x, y, z, x, z - y) for (x, y, z) in pts]
        mesh.poly(proj([(x0 + cx, y1, zw), (x1 - cx, y1, zw), (x1, y1, zw + cz), (x1, y1, zf),
                        (x0, y1, zf), (x0, y1, zw + cz)][::-1]), SHADE_ART, n + ".top~proj")
        edge = [(x0, y0)] + list(self.arch) + [(x1, y0)]
        for (xa, ya), (xb, yb) in zip(edge, edge[1:]):
            if xb - xa > 1e-6:
                mesh.poly(proj([(xa, ya, zf), (xb, yb, zf), (xb, y1, zf), (xa, y1, zf)]),
                          SHADE_ART, n + ".front~proj")
            # under it: flat over the posts, the vault under the arch
            mesh.poly([(xa, ya, zw, iu, iv), (xb, yb, zw, iu, iv), (xb, yb, zf, iu, iv),
                       (xa, ya, zf, iu, iv)], SHADE_BACK, n + ".under~clamp")
        # the ends: the front's edge columns laid across the depth; their cut
        # corners as drawn
        zc = zw + cz
        for x, xc, inward, shade in ((x0, x0 + cx, 1, SHADE_WEST), (x1, x1 - cx, -1, SHADE_EAST)):
            mesh.poly([(x, y, z, x + inward * (zf - z) * 0.5, zf - y) for (x, y, z) in
                       ((x, y0, zf), (x, y0, zc), (x, y1, zc), (x, y1, zf))],
                      shade, n + ".end~proj")
            mesh.poly(proj([(x, y0, zc), (xc, y0, zw), (xc, y1, zw), (x, y1, zc)]),
                      shade, n + ".end~proj")
        (pa, pb), (qa, qb) = self.posts
        (wa, wb) = self.skin
        for xa, xb, col in ((pa, pb, wa), (qa, qb, wb)):
            mesh.poly([(x, y, zw + 0.25, col, zw - max(y, 0)) for (x, y) in
                       ((xa, -1), (xb, -1), (xb, y0), (xa, y0))], SHADE_ART, n + ".skin~proj")
        sill = zw + 0.25 - self.sill            # the doorway's dark down to its sill
        for (xa, ya), (xb, yb) in zip(self.arch, self.arch[1:]):
            if xb - xa > 1e-6:
                mesh.poly([(xa, sill, zw + 0.25, iu, iv), (xb, sill, zw + 0.25, iu, iv),
                           (xb, yb, zw + 0.25, iu, iv), (xa, ya, zw + 0.25, iu, iv)],
                          SHADE_ART, n + ".door~clamp")
        last = self.last - 0.5
        for (a, b) in self.posts:
            mesh.poly([(x, y, zf, x, min(zf - y, last)) for (x, y) in
                       ((a, -1), (b, -1), (b, y0), (a, y0))], SHADE_ART, n + ".post~proj")


def fan_club():
    """Slateport's Pokemon Fan Club (80 x 80, cells 2..6 x 22..26).

        rows  8-39  the roof's deck, courses every 4 rows (x 14-65), the hips
                    either side, their courses running front to back
        rows 40-53  the front slope, courses every 3 rows
        rows 54-79  the facade: chamfered corners 7 px, pilasters, windows,
                    the door between two posts that stand 7 px out (to 86)

    Real depth: four collision rows, Z 16-80. The deck grows backwards by
    its own courses, the hips by their own columns.
    """
    zf, zb, ye = 80, 16, 26
    roof = Mansard("roof", 0, 80, zf, zb, ye, side=14, run=7, rise=7,
                   front_rows=(40, 54), hip_rows=(16, 54),
                   deck=Strip((12, 40), repeat=(12, 16), tail=(8, 12)),
                   back=Strip((12, 16), repeat=(12, 16)))
    # its sides on the drawing's outermost columns (their outline): on 1
    # and 79 the outline columns 0 and 79 stood on the ground beside it
    walls = Walls("facade", [(0, 73), (7, 80), (73, 80), (80, 73)], -1, ye)
    pilaster = Tile(8, 55, 16, 79)
    sides = [PlainWall("side_w", (0, 73), (0, zb), -1, ye, pilaster),
             PlainWall("side_e", (80, zb), (80, 73), -1, ye, pilaster),
             PlainWall("back", (0, zb), (80, zb), -1, ye, pilaster)]
    # the door's porch: a flat roof at Y 24, just under the eave, from the
    # wall out to Z 87 (its fluted top rows 56-63, its front 63-73), the
    # front arched over the door up to row 69; the posts under its front
    # (in steps, as drawn: a slope across a step cut its outline's pixels)
    arch = [(33, 14), (33, 15), (35, 15), (35, 16), (37, 16), (37, 17), (39, 17), (39, 18),
            (41, 18), (41, 17), (43, 17), (43, 16), (45, 16), (45, 15), (47, 15), (47, 14)]
    porch = Canopy("porch", 28, 52, 80, 87, 14, 24, arch, chamfer=(3, 2),
                   posts=[(29, 33), (47, 51)], inside=(40.5, 74.5), last=87, skin=(27.5, 53.5),
                   sill=77)
    return [roof, walls, porch] + sides


# the door posts' tops, which the drawing does not show, stand over 73-77; the
# grass beside the chamfers is drawn in the facade's own blocks
FAN_CLUB_EXACT = [(14, 12, 66, 54), (0, 16, 14, 54), (66, 16, 80, 54), (0, 55, 80, 73),
                  (3, 73, 29, 77), (33, 73, 47, 77), (51, 73, 77, 77)]


def museum():
    """Slateport's Oceanic Museum (112 x 96, cells 25..31 x 7..12).

        rows  2- 3  the roof's back edge (the trees' feet above it are theirs)
        rows  4-47  the roof's deck: red and white stripes running front to
                    back, the east flank darker
        rows 48-57  its steeper red skirt, chamfered at either end
        rows 58-61  fascia
        rows 62-95  the facade: portholes, the waves, the door

    The same reading as the fan club's roof. Real depth: four collision
    rows, Z 32-96; the stripes run on back as they are.
    """
    zf, zb, ye = 96, 32, 38
    roof = Mansard("roof", 0, 112, zf, zb, ye, side=8, run=5, rise=5,
                   front_rows=(48, 58), hip_rows=(4, 58),
                   deck=Strip((4, 48), repeat=(20, 28), tail=(2, 4)),
                   back=Strip((20, 28), repeat=(20, 28)))
    walls = Walls("facade", [(0, zf), (112, zf)], -1, ye)
    side = Tile(8, 62, 24, 96)
    sides = [PlainWall("side_w", (0, zf), (0, zb), -1, ye, side),
             PlainWall("side_e", (112, zb), (112, zf), -1, ye, side),
             PlainWall("back", (0, zb), (112, zb), -1, ye, side)]
    return [roof, walls] + sides


MUSEUM_EXACT = [(9, 4, 103, 58), (0, 58, 112, 96)]


class FlatSlab:
    """A flat roof slab on columns: its top at `y1`, its front edge (y0..y1)
    as drawn and its other three edges dressed with `edge`.

    The top is the drawing's `front` rows from the front edge back; then, to
    the real depth, `fill` = (rows, wrap): rows of it repeated, sampled
    between the columns `wrap` (clear of the lamps), with the rim's own
    columns (`rim` pixels each side) going on beside them; then the back
    rim, `tail` rows."""

    def __init__(self, name, x0, x1, zf, zb, y0, y1, front, fill, rim, tail, edge):
        self.name, self.x0, self.x1, self.zf, self.zb = name, x0, x1, zf, zb
        self.y0, self.y1, self.edge = y0, y1, edge
        self.front, self.fill, self.rim, self.tail = front, fill, rim, tail

    def emit(self, mesh):
        n, x0, x1, zf, zb, y0, y1 = self.name, self.x0, self.x1, self.zf, self.zb, self.y0, self.y1
        a, b = self.front
        za = zf - (b - a)                       # the drawn top ends here
        zt = zb + (self.tail[1] - self.tail[0])  # the back rim starts here
        north = (0.0, 0.0, -1.0)

        def lay(xa, xb, z0, z1, strip, tag):
            strip_face(mesh, [(xa, y1, z0), (xb, y1, z0), (xb, y1, z1), (xa, y1, z1)],
                       (0.0, y1, z0), (1.0, 0.0, 0.0), north, strip, SHADE_ART, n + tag)

        lay(x0, x1, zf, za, Strip(self.front), ".top")
        rows, wrap = self.fill
        r = self.rim
        lay(x0, x0 + r, za, zt, Strip(rows, repeat=rows), ".top")
        lay(x0 + r, x1 - r, za, zt, Strip(rows, repeat=rows, wrap=wrap), ".top")
        lay(x1 - r, x1, za, zt, Strip(rows, repeat=rows), ".top")
        lay(x0, x1, zt, zb, Strip(self.tail), ".top")
        mesh.poly([(x, y, z, x, z - y) for (x, y, z) in
                   [(x0, y0, zf), (x1, y0, zf), (x1, y1, zf), (x0, y1, zf)]], SHADE_ART,
                  n + ".front")
        for (a, b) in (((x0, zf), (x0, zb)), ((x1, zb), (x1, zf)), ((x0, zb), (x1, zb))):
            PlainWall(n + ".edge", a, b, y0, y1, self.edge).emit(mesh)


def contest_hall():
    """Slateport's Contest Hall (96 x 96, cells 28..33 x 22..27).

        rows  9-47  the flat roof: its rim and the deck with its lamps
        rows 48-55  the slab's front edge, the columns' caps in it
        rows 56-79  the facade, set back: chamfered 8 px at either end,
                    windows, the door
        rows 56-87  four columns standing out to Z 88 (x 8, 24, 64, 80)
        rows 76-87  the steps down from the door

    Real depth: four collision rows, Z 16-80, the columns in front. The deck
    grows backwards by its dashed rows, the seawall drawn behind is not its.
    """
    zf, zb = 88, 16
    roof = FlatSlab("roof", 0, 96, zf, zb, 32, 40, front=(16, 48), fill=((24, 32), (8, 40)),
                    rim=8, tail=(8, 16), edge=Tile(40, 48, 56, 56))
    # the chamfers a pixel out: their outline, drawn on the diagonal's outer
    # side, stood on the lawn
    walls = Walls("facade", [(0, 73), (7, 80), (89, 80), (96, 73)], -1, 32)
    side = Tile(16, 56, 24, 80)
    sides = [PlainWall("side_w", (0, 73), (0, zb), -1, 32, side),
             PlainWall("side_e", (96, zb), (96, 73), -1, 32, side),
             PlainWall("back", (0, zb), (96, zb), -1, 32, side)]
    columns = Posts("columns", [(8, 16), (24, 32), (64, 72), (80, 88)], 80, zf, 32)
    steps = Relief_top([(32, 80), (64, 80), (64, 88), (32, 88)], 0.5)
    return [roof, walls, columns, steps] + sides


# the deck's rounded corners are the rim's, which the real depth moves
CONTEST_EXACT = [(8, 16, 88, 48), (0, 48, 96, 88)]


class Porch:
    """A box standing out of a building's front: its facade drawn upright
    from `y1` down to the ground at z = `zf`, its top a strip back to `zb`,
    its two sides dressed with the facade's own edge columns (the pillars)."""

    def __init__(self, name, x0, x1, zf, zb, y1, side_w):
        self.name, self.x0, self.x1, self.zf, self.zb, self.y1 = name, x0, x1, zf, zb, y1
        self.side_w = side_w

    def emit(self, mesh):
        x0, x1, zf, zb, y1 = self.x0, self.x1, self.zf, self.zb, self.y1
        n = self.name
        mesh.poly([(x0, 0, zf, x0, zf), (x1, 0, zf, x1, zf), (x1, y1, zf, x1, zf - y1),
                   (x0, y1, zf, x0, zf - y1)], SHADE_ART, n + ".front")
        mesh.poly([(x0, y1, zf, x0, zf - y1), (x1, y1, zf, x1, zf - y1),
                   (x1, y1, zb, x1, zb - y1), (x0, y1, zb, x0, zb - y1)], SHADE_ART, n + ".top")
        # the sides: the pillar's own columns, from the facade back
        d = zf - zb
        k = min(1.0, float(self.side_w) / d)
        mesh.poly([(x0, 0, zb, x0 + d * k, zf), (x0, 0, zf, x0, zf), (x0, y1, zf, x0, zf - y1),
                   (x0, y1, zb, x0 + d * k, zf - y1)], SHADE_WEST, n + ".west~proj")
        mesh.poly([(x1, 0, zf, x1, zf), (x1, 0, zb, x1 - d * k, zf), (x1, y1, zb, x1 - d * k, zf - y1),
                   (x1, y1, zf, x1, zf - y1)], SHADE_EAST, n + ".east~proj")


def on_ray(vol, u, v, sink, tmax=96.0):
    """The point of the drawing's (u, v) where its 45-degree ray enters
    `vol`, `sink` pixels further down the ray: a tube's axis laid on it.
    A ray that passes over the volume (a cloth arm drawn past a dome's
    outline) gives the point of it nearest the volume's top: the arm
    lies on the dome there, standing out of its outline."""
    ins, t = vol.inside, tmax
    while t >= 0 and not ins(u, t, v + t):
        t -= 0.25
    if t >= 0:
        f = t - sink
        return (u, f, v + f)

    def top(z):
        y = tmax
        while y >= 0 and not ins(u, y, z):
            y -= 0.25
        return y
    best = min((t - y, t) for t, y in ((k * 0.5, top(v + k * 0.5)) for k in range(int(tmax * 2)))
               if y >= 0)
    f = best[1]
    return (u, f, v + f)


# The battle tent, read off its drawing (art pixels, the 5x5 cells' own):
#   a dome of blue and white panels, its grey skirt on the ground (v 64-76);
#   on its top, the highest thing, a low crown: a frustum, blue side (v 20-27)
#   and white lid in a dark rim (v 12-21);
#   four orange cloth arms in relief on the dome, from the crown down: two
#   down its front to the door's lintel (u 26-38 and 42-54, v 27-48); two
#   back over its top (where the 45-degree view sees past the crown: the
#   "M" of its outline, v 2-12) and down its flanks to the ground at its
#   sides (u 0-12 and 68-80);
#   the door, a box standing out of it (Porch).
TENT_BODY = dict(cx=40, cz=48, rx=33, rz=31, h=30, p=3.5)
TENT_CROWN = (40, 48, 17, 8, 14, 4.5, 24, 32)
# the arms' axes: (u, v, radius, y) - drawn at (u, v); laid on the dome
# (y None) or standing y high where they leave it, at its sides
TENT_OUTER = [(40, 9, 3.5, None), (32, 5, 4, None), (24, 7, 4.5, None), (16, 14, 5.5, None),
              (9, 24, 6.5, 20), (6, 36, 6.5, 14), (6, 50, 6.5, 9), (6, 66, 6, 3)]
TENT_INNER = [(32, 28, 5, None), (32, 36, 6, None), (31, 46, 5.5, None)]


_TENT_SHAPE = []


def battle_tent_elsewhere(art):
    """A battle tent drawn in a town's own shades (Verdanturf's outline),
    the town's drawing round it (its cliff in the top rows) cleared by the
    Slateport tent's outline: the same model, its own pixels."""
    if not _TENT_SHAPE:
        import voxel_building as vb
        la = vb.LayoutArt("LAYOUT_SLATEPORT_CITY")
        ref = la.building_art(8, 8, 5, 5, la.ground_tiles([GRASS]),
                              ground_px=la.ground_pixels([GRASS]))
        rpx = ref.load()
        _TENT_SHAPE.append({(u, v) for v in range(80) for u in range(80) if rpx[u, v][3] >= 128})
    art = art.copy()
    px = art.load()
    for v in range(80):
        for u in range(80):
            if (u, v) not in _TENT_SHAPE[0]:
                px[u, v] = (0, 0, 0, 0)
    return battle_tent(art)


def battle_tent(art):
    """The battle tent: its volume cut to its drawing (HeightSolid), and its door."""
    tex = Mound.with_ring(art, ())
    body = Volume().dome(**TENT_BODY)
    vol = Volume().dome(**TENT_BODY)
    vol.drum(*TENT_CROWN)
    for mirror in (False, True):
        f = (lambda u: 80 - u) if mirror else (lambda u: u)
        for arm in (TENT_OUTER, TENT_INNER):
            vol.tube([(on_ray(body, f(u), v, 0.4 * r) if y is None else (f(u), y, v + y)) + (r,)
                      for (u, v, r, y) in arm])
    door = lambda u, v: 19 <= u < 60 and 48 <= v < 80
    return [HeightSolid("tent", tex, art.size[1], vol, step=2, mask=door, mirror_z=48, lean=2.0),
            Porch("door", 19, 60, 80, 73, 25, 6)], tex


# Slateport's sailboat (art of the whole one at (34, 44), 3x3 cells): a
# hull on a long oval plan, its deck 8 high (the deck's oval v 22-32, the
# planked side v 32-40), a mast at u 28-31 up to v 3, and two sails in the
# plane of the boat's keel: the striped jib before the mast, the white
# mainsail behind it.
# the harbour bollard's profile, (y, radius) from its foot
BOLLARD = [(0, 6.5), (2, 6), (5, 6), (6, 5.5), (7, 7), (8.5, 7), (9, 4), (11, 0)]
BOAT_Z = 35
# the sails (x, y) in the keel's plane, and the mast (x0, x1, y0, y1)
BOAT_SAILS = [[(27, 33), (9, 12), (27, 12)], [(32, 32), (44, 11), (32, 11)]]
BOAT_MAST = (27.5, 31.5, 8, 33)
# the hull's plan and sheer (ProjHull)
BOAT_HULL = dict(cx=24.5, rx=24.5, rz=5.5, p=3.0, sheer=6, keel=0)


def _grown(poly, d):
    """A polygon's corners pushed `d` out from its middle."""
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    out = []
    for (x, y) in poly:
        dx, dy = x - cx, y - cy
        k = math.hypot(dx, dy) or 1.0
        out.append((x + dx / k * d, y + dy / k * d))
    return out


def _inside(poly, x, y):
    inside = False
    for i in range(len(poly)):
        (xa, ya), (xb, yb) = poly[i], poly[i - 1]
        if (ya > y) != (yb > y) and x < xa + (y - ya) * (xb - xa) / (yb - ya):
            inside = not inside
    return inside


class SailCards:
    """A boat's sails and mast as flat cards in its keel's plane, the
    drawing projected on them: at 45 degrees exactly the drawing, its
    transparency cutting their outline. In the hull's height field they
    were plates two pixels thick, cut column by column, and from the game's
    camera their slanted edges stood in spikes. Each card is its outline
    grown a pixel and a half, so that the outline's own pixels are in it."""

    def __init__(self, name, zs, dx=0.0):
        self.name, self.zs, self.dx = name, zs, dx
        x0, x1, y0, y1 = BOAT_MAST
        self.polys = [_grown([(x + dx, y) for (x, y) in poly], 1.5) for poly in BOAT_SAILS]
        self.polys.append([(x0 + dx, y0), (x1 + dx, y0), (x1 + dx, y1), (x0 + dx, y1)])

    def owns(self, u, v):
        """Whether the 45-degree ray of pixel (u, v) meets a card."""
        return any(_inside(poly, u + 0.5, z - v - 0.5) for z in self.zs for poly in self.polys)

    def emit(self, mesh):
        for z in self.zs:
            for poly in self.polys:
                mesh.poly([(x, y, z, x, z - y) for (x, y) in poly], 1.0, self.name + "~proj")


def sailboat(art, boats=(0,), keel=0.0):
    """One sailboat, or several drawn one behind another (`boats`: how many
    rows lower each is drawn than the whole one's own art). `keel` moves
    the boat north: where a pier's edge hides its hull, the drawing ends
    at that edge and the boat lies behind it. Each hull a smooth decked
    ProjHull on a long, blunt-ended oval plan (BOAT_HULL: the drawing takes
    its ends down to the water square), its deck 8 high rising 6 to the
    ends; the sails and mast flat cards in its keel's plane (a height field
    had stood the hull in steps, the sails in spikes)."""
    zs = [BOAT_Z + dv + keel for dv in boats]
    h = BOAT_HULL
    # its foot a pixel under the water: on the console the vertices are
    # rounded, and a hull ending on the drawing's last row lost it in places
    return [ProjHull("boat", h["cx"], z, h["rx"], h["rz"], -2, 8, p=h["p"], sheer=h["sheer"],
                     keel=h["keel"], seg=24) for z in zs] + [SailCards("sail", zs)]


def rowboat(art):
    """The rowboat by the shipyard, up on stands: an open hull, its gunwale
    15 high (the rim's oval, v 0-19), its floor 8, a thwart across it at
    u 36-40. Smooth surfaces under its drawing (ProjHull)."""
    return [ProjHull("rowboat", 24, 22.5, 22.8, 8.5, 0, 15, p=2.3, sheer=2,
                     inner=(23.5, 22.5, 19.5, 6.5, 8),
                     # the thwart; the stand under the stern, drawn past the
                     # hull's curve down to row 30
                     boxes=[(36, 40, 8, 14, 15, 30), (33, 45, 0, 4, 25, 31)])]


# The stone lantern in Slateport's square (art 32x64, its foot at v 63): a
# turned thing on a plan 0.7 as deep as wide (the GBA draws its rings as flat
# ellipses; round, the drawing would leave it 30 high, its keyhole lying on
# a pedestal's top; at 0.5 it read too thin) - a bell for a base with the
# keyhole, a neck, a drum with its windows, a capped top. The profile is
# fitted to the drawing's outline row by row; the cubes are carved to it.
LANTERN = [(0, 12.5), (2, 14), (5, 16), (7, 16), (8, 14), (10, 14), (11, 12), (13, 10), (21, 10),
           (22, 12.5), (24, 14), (26, 16), (30, 16), (32, 14), (33, 12), (41, 12), (42, 9.5),
           (44, 8), (45, 6.5), (45.5, 0)]
LANTERN_Z = 55.3
# its plan 0.7 as deep as wide (the user's choice over 0.5, 2026-10-10)
LANTERN_SQUASH = 0.7
# stood half a cell north of where it is drawn, in the middle of its lawn
# (the drawing's cap then starts on the drawing's first row); the user's
# choice, 2026-10-10
LANTERN_BACK = 8


def lantern_drawing(model):
    """The lantern's drawing as it stands: moved LANTERN_BACK rows up."""
    from PIL import Image
    art = model.art
    out = Image.new("RGBA", art.size, (0, 0, 0, 0))
    out.paste(art.crop((0, LANTERN_BACK, art.size[0], 64)), (0, 0))
    return out


def lantern(art):
    """A low-poly lathe (BakedLathe, 32 sides, ~1200 triangles) with its
    texture baked off the true-cube model (VoxelLathe): the cubes' look -
    turned all round, its cap squarer (drawn with a straight back), no face
    stretched - at the cost of a prop. Its faces turned to the GBA's view
    read the drawing itself; only its outline is a polygon, not a stair."""
    px = art.load()
    W, H = art.size

    def drawn(u, v):
        return 0 <= u < W and 0 <= v < H and px[u, v][3] >= 128
    cubes = VoxelLathe("lantern", art, drawn, 16.0, LANTERN_Z, LANTERN, LANTERN_SQUASH,
                       power=lambda y: 4.0 if y >= 33.0 else 2.0)
    baked = BakedLathe("lantern", cubes, sides=32, shift_z=-LANTERN_BACK)
    return [baked], baked.texture()


def small_round(name, volume, depth=16.0, step=1, cell=2, tol=1, hang=(), side=None,
                open_top=False):
    """A thing off its volume alone (a bush, a bollard, a bin; `step` 4 and
    a wide `cell` for one of flat faces, a station, a tall block)."""
    def parts(art):
        tex = Mound.with_ring(art, ())
        return [HeightSolid(name, tex, art.size[1], volume, depth=(0.0, depth), step=step,
                            mirror_z=depth / 2, cell=cell, tol=tol, hang=hang, side=side,
                            open_top=open_top)], tex
    return parts


def silhouette(vol, u, v, depth):
    """Whether the 45-degree ray through pixel (u, v) meets the volume."""
    return any(vol.inside(u + 0.5, z - v - 0.5, z) for z in range(int(v), int(v) + depth))


def space_center(art):
    """Mossdeep's Space Center (art 144x224), split at the roof's back edge
    (v 96). The building: a block 83 high on z 179-224, its back corners
    cut 8 (flat faces, the drawing projected); on its roof the observatory's
    dome, the dish and the small dish (low domes, 16 deep and high for 19
    wide), a solid lifted onto the roof claiming the pixels it covers.
    Behind it, on the ground at z 96: the two lattice towers (flat boxes, 96
    high, their caps cut by the map's top edge), the rocket (round, r 8,
    fins r 12, the nose at 73) and the small tower; the towers' arms, beams
    in the air, lifted along the ray on their own."""
    tex = Mound.with_ring(art, ())
    H = art.size[1]
    apx = art.load()

    def px_on(u, v):
        return apx[u, v][3] >= 128
    L = 83.0
    domes = (Volume().dome(36, 202.5 - L, 20, 16.5, 16.5)
             .dome(104, 201 - L, 19, 14.5, 14.5, p=3.0)
             .dome(134, 218 - L, 7, 5.5, 5.5))
    roofish = {}

    def on_roof(u, v):
        if (u, v) not in roofish:
            roofish[(u, v)] = v >= 96 and silhouette(domes, u, v, 160)
        return roofish[(u, v)]
    TOWERS = ((15, 33), (95, 113))

    def own(u, v):
        if v >= 96:
            return "domes" if on_roof(u, v) else "block"
        if (32 <= u < 48 or 80 <= u < 96) and v < 50:
            return "arm_hi" if v < 30 else "arm_lo"
        if any(a <= u < b for a, b in TOWERS):
            return "tower"
        return "pad"
    names = ("domes", "pad", "arm_hi", "arm_lo")
    tex, band = own_bands(tex, H, {n: (lambda n: lambda u, v: own(u, v) == n)(n) for n in names})
    parts = [Grown(PlanPrism("space_block", [(8, 179), (136, 179), (144, 187), (144, 224), (0, 224),
                                             (0, 187)], 0, 83, (2, 8, 224)), 2 * H)]
    for (a, b) in TOWERS:
        parts.append(Grown(PlanPrism("space_tower", [(a, 82), (b, 82), (b, 96), (a, 96)], 0, 96,
                                     (a + 2, a + 6, 96), top=Tile(a + 2, 2, a + 6, 6)), 2 * H))
    pad = (Volume().lathe(64, 84, [(0, 13), (12, 10), (16, 8.5), (58, 8.5), (66, 5), (74, 1), (75, 0)])
           .box(50, 78, 0, 12, 80, 97).box(32, 49, 0, 33, 84, 96))
    arm = Volume().box(31, 49, 0, 5, 0, 60).box(79, 97, 0, 5, 0, 60)
    for name, vol, mz, st, cell, lift in (("domes", domes, 201.0 - L, 1, 2, L),
                                          ("pad", pad, 89.0, 1, 2, 0.0),
                                          ("arm_hi", arm, 11.0, 1, 4, 77.0),
                                          ("arm_lo", arm, 43.0, 1, 4, 46.0)):
        other = (lambda n: lambda u, v: px_on(u, v) and own(u, v) != n)(name)
        parts.append(HeightSolid("space_" + name, tex, H, vol, depth=(0.0, float(H)), step=st,
                                 mirror_z=mz, cell=cell, tol=1, band=band[name], lift=lift,
                                 mask=None if name == "domes" else other,
                                 behind=other if name == "domes" else None))
    return parts, tex


HUT_THATCH = {(180, 189, 123), (222, 230, 164), (90, 98, 41), (156, 164, 98), (131, 139, 74)}


def pacifidlog_hut(art):
    """Pacifidlog's round hut (art 48x64): a wall 22 high (rows 41-63) on a
    rounded plan (a superellipse, the GBA drawing its sides straight down),
    and its thatch, a cone with its finial whose eave overhangs the wall: a
    solid of its own lifted 20 along the ray, with an underside. Round
    things drawn as the GBA draws them: the plan half as deep as wide."""
    tex = Mound.with_ring(art, ())
    px = art.load()
    H = art.size[1]

    def own(u, v):
        if px[u, v][3] < 128:
            return None
        return "thatch" if v < 37 or px[u, v][:3] in HUT_THATCH else "wall"
    tex, band = own_bands(tex, H, {n: (lambda n: lambda u, v: own(u, v) == n)(n)
                                   for n in ("thatch", "wall")})
    L = 21.0

    def other(n):
        return lambda u, v: own(u, v) not in (None, n)
    wall = Volume().slab(24, 52.5, 21, 11, 0, 20.5, p=8.0)
    thatch = (Volume().lathe(24, 51 - L, [(0, 25.5), (3, 25.5), (4, 24), (16, 2.5), (17.5, 0)],
                             squash=0.54)
              .lathe(24, 51 - L, [(13, 3.5), (19, 3.5), (21, 7), (28, 7), (31, 4), (33, 0)]))
    parts = []
    for name, vol, lift in (("wall", wall, 0.0), ("thatch", thatch, L)):
        parts.append(HeightSolid("hut_" + name, tex, H, vol, depth=(0.0, float(H)), step=1,
                                 mirror_z=51 - lift, cell=2, band=band[name], lift=lift,
                                 side=(4, 10) if name == "wall" else None,
                                 # the eave stands in front of the wall
                                 mask=other(name) if name == "wall" else None,
                                 behind=other(name) if name == "thatch" else None))
    return parts, tex


SOOTOPOLIS_ROCK = {(205, 205, 189), (222, 222, 213), (246, 246, 238), (148, 148, 123),
                   (172, 172, 156), (131, 131, 139)}


def sootopolis_parts(volume, depth, step=1, cell=2, side=(2, 8)):
    """A Sootopolis house off `volume`: the white rock drawn round it (the
    terrace behind, between its tiles) is the map's, laid back as patches."""
    def parts(art):
        px = art.load()
        for v in range(art.size[1]):
            for u in range(art.size[0]):
                if px[u, v][3] >= 128 and px[u, v][:3] in SOOTOPOLIS_ROCK:
                    px[u, v] = (0, 0, 0, 0)
        return small_round("house", volume, depth=depth, step=step, cell=cell, side=side)(art)
    return parts


LEAGUE_FACADE = {(238, 156, 90), (246, 197, 123), (164, 90, 49), (205, 115, 41)}


def pokemon_league(art):
    """Ever Grande's Pokemon League (art 176x128), its facade cut by the
    map's top edge: a block on a plan with its front corners cut (the
    drawing's slanting wings, x 0-16 and 160-176, their feet rising 16 to
    z 80), its front z 96 and its roof above the map (open_top); four
    columns against the doors; a half-round dais 3.5 high before them
    reaching z 124; the curved canopy over them, 13 thick at y 35-48, lifted along
    the ray on its own; and the four round bushes either side."""
    tex = Mound.with_ring(art, ())
    px = art.load()
    H = art.size[1]
    # the grass flecks below the wings' slanting feet are the ground's
    for v in range(H):
        for u in range(art.size[0]):
            if (u < 16 and v > 80 + u) or (u >= 160 and v > 80 + (175 - u)):
                if v < 96:
                    px[u, v] = (0, 0, 0, 0)
    tex = Mound.with_ring(art, ())

    def own(u, v):
        if px[u, v][3] < 128:
            return None
        if v >= 96 and (u < 48 or u >= 128):
            return "bush"
        if 48 <= u < 128 and 47 <= v < 77 and px[u, v][:3] not in LEAGUE_FACADE:
            return "canopy"
        if v >= 94 and 40 <= u < 136:
            return "dais"
        return "block"
    names = ("block", "canopy", "dais", "bush")
    tex, band = own_bands(tex, H, {n: (lambda n: lambda u, v: own(u, v) == n)(n) for n in names})

    def other(n):
        return lambda u, v: own(u, v) not in (None, n)
    L = 35.0
    COLS = ((51, 61), (63, 74), (102, 113), (115, 125))
    columns = Volume()
    for cx in (56, 68, 108, 120):
        columns.lathe(cx, 96, [(0, 5.5), (L + 1, 5.5)], squash=0.55)
    canopy = Volume().slab(88, 96 - L, 41, 16, 0, 13.5, p=2.0)
    dais = Volume().slab(88, 96, 46, 28.5, 0, 3.5, p=2.0)
    dais.hollow(dais.box, 0, 176, -1, 10, 0, 97.5)     # its top drawn from row 94
    bush = Volume()
    for cx in (24, 40, 136, 152):
        for vb_ in (111, 127):
            bush.dome(cx, vb_ - 6, 8, 6.5, 8.5)
    # the block: flat faces, the drawing projected; its roof, above the
    # map's top edge, laid with the brickwork between the windows
    parts = [Grown(PlanPrism("league_block", [(16, 24), (160, 24), (176, 40), (176, 80), (160, 96),
                                              (16, 96), (0, 80), (0, 40)], 0, 160, (24, 40, 96),
                             top=Tile(100, 20, 116, 30)), 2 * H)]

    def column(u, v):
        return 76 <= v < 94 and any(a <= u < b for a, b in COLS) and own(u, v) == "block"
    # the columns: their own pixels; the facade round them is behind them,
    # the canopy and the dais hide their ends
    parts.append(HeightSolid("league_columns", tex, H, columns, depth=(0.0, float(H)), step=1,
                             mirror_z=96.0, cell=2, band=band["block"],
                             mask=lambda u, v: own(u, v) in ("canopy", "dais"),
                             behind=lambda u, v: own(u, v) in ("block", "bush") and not column(u, v)))
    for name, vol, lift, mz, st, cell in (
            ("canopy", canopy, L, 61.0, 1, 2),
            ("dais", dais, 0.0, 96.0, 2, 4),
            ("bush", bush, 0.0, 110.0, 1, 2)):
        parts.append(HeightSolid("league_" + name, tex, H, vol, depth=(0.0, float(H)), step=st,
                                 mirror_z=mz, cell=cell, band=band[name], lift=lift,
                                 mask=other(name) if name != "canopy" else None,
                                 behind=other(name) if name == "canopy" else None))
    return parts, tex


DOME_BOXES = ((24, 184, 24, 170, 216), (40, 168, 48, 160, 200), (80, 128, 30, 196, 224),
              (0, 16, 14, 76, 224), (192, 208, 14, 76, 224))      # x0, x1, height, z0, z1


def battle_dome(art):
    """The Battle Dome (art 208x224, from map row 4): the white shell, a
    dome 92 high on an ellipse 98 by 70 (its drawn top row 30 is where the
    45-degree ray grazes it) with five ribs (r 8.5) lying over it from front
    to back, their tops row 12 - a solid claiming the pixels the boxes do
    not; and of flat faces, the drawing projected: the two-tier glass base
    (24 then 48 high), the porch, a fence line down each side."""
    tex = Mound.with_ring(art, ())
    H = art.size[1]
    apx = art.load()
    cx, cz, rx, rz, h = 104.0, 146.0, 98.0, 70.0, 92.0
    vol = Volume().dome(cx, cz, rx, rz, h, p=2.6)
    for x in (72.0, 88.0, 104.0, 120.0, 136.0):
        pts = []
        for z in range(int(cz - 60), int(cz + 61), 15):
            q = 1.0 - ((z - cz) / rz) ** 2 - ((x - cx) / rx) ** 2
            pts.append((x, h * math.sqrt(max(0.0, q)) + 2.0, float(z)))
        vol.tube(pts, 8.5, n=16)
    # inside the base: the shell's foot stands within it, not before it
    vol.hollow(vol.box, 20, 188, -1, 24, 168, 230)
    vol.hollow(vol.box, 36, 172, -1, 48, 158, 230)

    def boxed(u, v):
        return any(x0 <= u < x1 and z0 - hh <= v < z1 for (x0, x1, hh, z0, z1) in DOME_BOXES)
    parts = [Grown(PlanPrism("dome_box", [(x0, z0), (x1, z0), (x1, z1), (x0, z1)], 0, hh,
                             (x0 + 4, x0 + 12, z1)), 2 * H)
             for (x0, x1, hh, z0, z1) in DOME_BOXES]
    parts.append(HeightSolid("dome_shell", tex, H, vol, depth=(0.0, float(H)), step=2, mirror_z=cz, side=(40, 48, 150),
                             cell=4, mask=lambda u, v: apx[u, v][3] >= 128 and boxed(u, v)))
    return parts, tex


def octagon(x0, x1, z0, z1, cut):
    """A plan polygon (x, z): the rectangle with its corners cut `cut`."""
    return [(x0 + cut, z0), (x1 - cut, z0), (x1, z0 + cut), (x1, z1 - cut), (x1 - cut, z1),
            (x0 + cut, z1), (x0, z1 - cut), (x0, z0 + cut)]


def flat(make):
    """A spec's flat-faced parts (PlanPrism and kin) on the drawing with its
    grown band: their hidden faces read it, so no clear texel is a hole."""
    def parts(art):
        tex = Mound.with_ring(art, ())
        return [Grown(p, 2 * art.size[1]) for p in make()], tex
    return parts


def rounded_plan(cx, cz, rx, rz, p=4.0, n=32):
    """A superellipse plan polygon (x, z), its corners rounded."""
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        c, s_ = math.cos(t), math.sin(t)
        pts.append((cx + rx * math.copysign(abs(c) ** (2 / p), c),
                    cz + rz * math.copysign(abs(s_) ** (2 / p), s_)))
    return pts


def sand_specks(make):
    """Drops the Battle Frontier sand's loose specks from a drawing (the
    ground's own flecks, painted on its tiles beside the building)."""
    def parts(art):
        px = art.load()
        for v in range(art.size[1]):
            for u in range(art.size[0]):
                if px[u, v][3] >= 128 and px[u, v][:3] in ((238, 230, 164), (213, 180, 106)):
                    px[u, v] = (0, 0, 0, 0)
        return make(art)
    return parts


def battle_pyramid():
    """The Battle Pyramid (art 240x176, from map 51,5): thirteen tiers 8
    high, each 8 in on the sides and 4 in front and back (the base x 8-232,
    z 57-160), the portico before the entrance and its tower, the posts of
    the chain fences either side."""
    parts = [PlanPrism("tier", [(8 + 8 * i, 56 + 4 * i), (232 - 8 * i, 56 + 4 * i), (232 - 8 * i, 160 - 4 * i),
                                (8 + 8 * i, 160 - 4 * i)], 8 * i, 8 * i + 8, (60, 76, 160 - 4 * i))
             for i in range(13)]
    parts.append(PlanPrism("steps", [(100, 160), (140, 160), (140, 176), (100, 176)], 0, 4, (104, 112, 176)))
    parts.append(PlanPrism("portico", [(40, 140), (200, 140), (200, 160), (40, 160)], 0, 20, (60, 76, 160)))
    parts.append(PlanPrism("gate", [(104, 120), (136, 120), (136, 160), (104, 160)], 0, 40, (60, 76, 160)))
    for (a, b) in ((0, 16), (224, 240)):
        parts.append(PlanPrism("fence", [(a, 30), (b, 30), (b, 176), (a, 176)], 0, 10, (a + 2, a + 6, 176)))
    return parts


def battle_factory():
    """The Battle Factory (art 240x144): octagonal towers side by side, flat
    topped, their corners cut - the middle one (x 88-152) 101 high, its top
    rows 2-42; the outer pairs 80 high, their tops rows 16-56 - and a fence
    line down the east end."""
    side = (100, 108, 144)
    parts = [PlanPrism("mid", octagon(87, 153, 101, 144, 8), 0, 101, side),
             PlanPrism("fence", [(0, 60), (8, 60), (8, 144), (0, 144)], 0, 14, (0, 8, 144))]
    for (a, b, cut) in ((8, 64, 8), (176, 232, 8), (60, 92, 6), (148, 180, 6)):
        parts.append(PlanPrism("tower", octagon(a, b, 96, 136, cut), 0, 80, side))
    parts.append(PlanPrism("fence", [(232, 60), (240, 60), (240, 144), (232, 144)], 0, 14, (232, 240, 144)))
    return parts


def battle_pike_volume():
    """The Battle Pike's Seviper (art 80x208), lying on the ground: its body
    a tube (r 27) along the middle, the tail lying on the ground behind it
    and curling to the west (its tip row 3), the head a broad dome whose open mouth is
    the door, the yellow fins along its sides, the tongue a carpeted ramp."""
    vol = Volume()
    vol.tube([(40, 20, 96, 26), (40, 22, 120, 28), (40, 24, 150, 30)])
    vol.tube([(44, 12, 90, 14), (54, 12, 62, 13.5), (50, 12, 38, 12.5), (44, 12, 20, 9), (30, 10, 10, 5),
              (24, 8, 6, 3)])
    vol.dome(40, 174, 38, 30, 44)
    for (x, z) in ((8, 96), (72, 96), (6, 116), (74, 116), (8, 128), (72, 128), (10, 150), (70, 150)):
        vol.dome(x, z, 9, 9, 16)
    vol.box(24, 56, 0, 8, 176, 208)
    return vol


def sootopolis_spire(art):
    """Sootopolis's spire house (art 48x80): the octagonal white base, 22
    high (front rows 56-78, flat top rows 18-56: z 40-79, corners cut 8),
    and on it the eight-sided pyramid of dark stone - its base the octagon
    x 4-44, z 39-73 (drawn rows 17-51), its apex row 8 over the middle,
    48 high. Both of flat faces, the drawing projected onto them."""
    px = art.load()
    for v in range(art.size[1]):
        for u in range(art.size[0]):
            if px[u, v][3] >= 128 and px[u, v][:3] in SOOTOPOLIS_ROCK:
                px[u, v] = (0, 0, 0, 0)
    base = PlanPrism("base", octagon(0, 48, 40, 79, 8), 0, 22, (9, 16, 79))
    roof = PlanPyramid("spire", [(13, 39), (35, 39), (44, 50), (44, 65), (37, 73), (11, 73), (4, 65),
                                 (4, 50)], 22, (24, 48, 56))
    return [Grown(base, 2 * art.size[1]), Grown(roof, 2 * art.size[1])], Mound.with_ring(art, ())


def sootopolis_flat_house(art):
    """Sootopolis's flat-roofed white house (art 48x64): a block 30 high on
    z 30-64, its flat roof (with the skylight) drawn rows 0-34."""
    px = art.load()
    for v in range(art.size[1]):
        for u in range(art.size[0]):
            if px[u, v][3] >= 128 and px[u, v][:3] in SOOTOPOLIS_ROCK:
                px[u, v] = (0, 0, 0, 0)
    return [Grown(PlanPrism("house", [(0, 30), (48, 30), (48, 64), (0, 64)], 0, 30, (8, 15, 64)),
                  2 * art.size[1])], Mound.with_ring(art, ())


# Route 109's beach parasol (art 32x48): a round canopy (v 3-31) on a pole
# (v 32-39). The pole's foot is its last row: it stands at z 38.5. The
# canopy's rim, drawn as a ring 32 wide round v 19, is 19.25 high over it,
# its hub (v 13, where the panels meet) 6 higher. Its shadow drawn on the
# sand (v 27-47, these colours) is the ground's (`ground_rgb`): the sand
# lies plain under it and the console casts the canopy's own shadow.
PARASOL_SHADOW = {(205, 156, 82), (213, 180, 106), (222, 205, 131)}


def parasol(art):
    """Two parts of smooth faces, each with the drawing projected from a
    band of its own pixels (own_bands): the canopy a shallow cone, round
    (ProjCanopy: its rim lifted 3.5 at the front, where the drawing ends
    higher than a round rim would - exact at 45 degrees) and closed
    underneath; the pole a thin turned post up into it. (Solids of height
    fields had stood the canopy in fins and its drawn shadow in a mound.)"""
    tex = Mound.with_ring(art, ())
    px = art.load()
    W, H = art.size

    def own(u, v):
        if px[u, v][3] < 128:
            return None
        return "canopy" if v <= 31 else "pole"
    tex, band = own_bands(tex, H, {n: (lambda n: lambda u, v: own(u, v) == n)(n)
                                   for n in ("canopy", "pole")})
    # each band cut to the drawing where the part's edge is drawn: the
    # canopy's to its own pixels (its polygon stands a pixel past the drawn
    # stair here and there), the pole's round foot (its last row) to its own
    tpx = tex.load()
    for v in range(H):
        for u in range(W):
            o = own(u, v)
            if o != "canopy":
                tpx[u, v + band["canopy"] * H] = (0, 0, 0, 0)
            if v == 39 and o != "pole":
                tpx[u, v + band["pole"] * H] = (0, 0, 0, 0)
    return [ProjCanopy("canopy", 16, 38.5, 16.2, 19.25, 25.25, front=3.5, y_under=20.5,
                       offset=band["canopy"] * H, seg=24),
            ProjLathe("pole", 16, 38.5, [(0, 2), (21, 2)], seg=8, offset=band["pole"] * H)], tex


def sun_lounger(art):
    """Route 109's sun lounger (art 16x48, drawn v 9-37, x 1-14): a frame
    on four legs, open underneath. The seat (the footrest and the middle
    cushion, v 20-33) flat 5 high, 2 thick, its front (v 34-35, the rail
    between the legs) at z 39; the back (v 9-19) a cushion raised 40
    degrees from the hinge (z 24.5), 7.5 long, 1.2 thick; the front legs
    (v 35-37: x 1-5 and 10-14) stand at z 38, the back ones are struts
    under the back, inside its drawn outline, in the frame's colour (the
    drawing never shows them). Every face takes the drawing projected
    (ProjSection): exact at 45 degrees, and from the side a lounger, not a
    block (a height field had stood it on the sand whole)."""
    H = art.size[1]
    tex = Mound.with_ring(art, ())
    a = math.radians(40)
    t, L = 1.2, 7.5
    hz, top = 24.5, 5.0
    ez, ey = hz - L * math.cos(a), top + L * math.sin(a)
    nz, ny = math.sin(a), math.cos(a)
    seat = top - 2
    # where the back's underside meets the seat's
    k = (top - t * ny - seat) / math.sin(a)
    frame = [(39, seat), (39, top), (hz, top), (ez, ey), (ez - t * nz, ey - t * ny),
             (hz - t * nz + k * math.cos(a), seat)]

    def under_back(z):
        """The height of the back's underside over z."""
        return top - t * ny + (hz - t * nz - z) * math.tan(a)
    parts = [ProjSection("frame", frame, 1, 14, grown=2 * H)]
    for (x0, x1) in ((1, 5), (10, 14)):
        parts.append(ProjSection("leg", [(36.5, 0), (38, 0), (38, seat), (36.5, seat)], x0, x1,
                                 grown=2 * H))
    for (x0, x1) in ((2, 3.5), (11.5, 13)):
        parts.append(ProjSection("strut", [(20.5, 0), (21.5, 0), (21.5, under_back(21.5)),
                                           (20.5, under_back(20.5))], x0, x1, uv=(1.5, 20.5)))
    return parts, tex


def beach_mat(art):
    """Route 109's air mattress (art 16x48, drawn v 9-36, x 0-16), flat on
    the sand: its tubes 2 thick (the front, v 35-36, at z 37), the pillow
    across its head a tube 2.5 round on the same floor, 4.5 high (v 9-15).
    Every face takes the drawing projected (ProjSection); the pillow's back,
    which the view grazes, reads the grown band (`graze`): the drawing
    rounds the pillow's top corners, and they were holes from the side."""
    H = art.size[1]
    r, y0 = 2.5, 2.0
    zc = 9 + y0 + r * math.sqrt(2)      # the pillow's top at 45 degrees is row 9
    arc = [(zc + r * math.cos(math.pi * k / 8), y0 + r * math.sin(math.pi * k / 8)) for k in range(9)]
    sec = [(37, 0), (37, y0)] + arc + [(zc - r, 0)]
    return [ProjSection("mat", sec, 0, 16, grown=2 * H, graze=0.6)], Mound.with_ring(art, ())


def abandoned_ship(art):
    """Route 108's wreck, stranded on its shoal (art 96x64): a hull 8 high
    on a long plan (its deck v 16-36 = z 24-44; the side, then the green
    of its bottom, v 36-44), a cabin 15 high (roof v 10-29), a slatted
    house forward, a landing stage floating before it (v 47-56) and the
    hull's dark water at its foot (v 44-47)."""
    tex = Mound.with_ring(art, ())
    vol = Volume().slab(44, 34, 43, 10, -1, 8, p=4.0)
    vol.box(22, 61, 0, 15, 25, 44)
    vol.box(61, 75, 0, 11, 23, 44)
    vol.box(29, 51, 0, 2, 47, 56)
    vol.slab(45, 45.2, 43, 3.2, 0, 0.3, p=8.0)
    return [HeightSolid("wreck", tex, art.size[1], vol, depth=(0.0, 64.0), step=1,
                        mirror_z=34)], tex


def two_storey_house(width, front_z, lower, upper, upper_x, storey, pent, facade, side,
                     band):
    """Littleroot's build in other towns' tiles: a hipped lower roof over
    the whole width and a narrower upper storey coming up through it with
    its own hipped roof. `lower`/`upper` = dict(cap, teeth, slope, course,
    fascia, wrap, fascia_wrap, ridge_wrap) of art rows; `upper_x` the upper
    roof's span, `storey` the upper wall's rows, `pent` the lower roof's
    rows drawn before the upper wall, `facade` the ground floor's rows,
    `side`/`band` the columns the ground floor's and the upper wall's sides
    are dressed with. Real depth: z 16 to `front_z`."""
    back_z, overhang = 16, 2
    lo, hi = lower, upper
    roof_lo = HipRoof(
        "roof_lo", 0, width, zf=front_z + overhang, zb=back_z - overhang,
        y0=front_z + overhang - lo["fascia"][1],
        fascia=Strip(lo["fascia"], wrap=lo["fascia_wrap"]),
        slope=Strip(lo["slope"], repeat=lo["course"], wrap=lo["wrap"]),
        teeth=lo["teeth"], cap=lo["cap"], pitch=PITCH, run=8,
        ridge_u=(0, 16), end_tile=Tile(lo["wrap"][0], lo["teeth"][0], lo["wrap"][0] + 8,
                                        lo["teeth"][1]),
        ridge_wrap=lo["ridge_wrap"])
    z_wall, y_base = roof_lo.slope_point(pent)
    zf_hi = z_wall + overhang
    y0_hi = zf_hi - hi["fascia"][1]
    zb_hi = (roof_lo.zf + roof_lo.zb) - zf_hi
    roof_hi = HipRoof(
        "roof_hi", upper_x[0], upper_x[1], zf=zf_hi, zb=zb_hi, y0=y0_hi,
        fascia=Strip(hi["fascia"], wrap=hi["fascia_wrap"]),
        slope=Strip(hi["slope"], repeat=hi["course"], wrap=hi["wrap"]),
        teeth=hi["teeth"], cap=hi["cap"], pitch=PITCH, run=6,
        ridge_u=upper_x, end_tile=Tile(hi["wrap"][0], hi["teeth"][0], hi["wrap"][0] + 8,
                                        hi["teeth"][1]),
        ridge_wrap=hi["ridge_wrap"])
    storey_back = zb_hi + overhang
    wall_hi = Tile(band[0], storey[0], band[1], storey[1], top=y0_hi)
    upper_storey = Prism(
        "storey", upper_x[0] + 1, upper_x[1] - 1,
        [(z_wall, y_base), (z_wall, y0_hi), (storey_back, y0_hi), (storey_back, y_base)],
        edges={0: Proj(*storey), 2: wall_hi}, skip=(1, 3),
        caps=[Band(-64, 200, wall_hi, z_wall, front=wall_hi, back=wall_hi, z1=storey_back)])
    wall_top = roof_lo.y0
    wall = Tile(side[0], facade[0], side[1], facade[1], top=wall_top - 2)
    base = Prism(
        "ground_floor", 2, width - 2,
        [(front_z, 0), (front_z, wall_top), (back_z, wall_top), (back_z, 0)],
        edges={0: Proj(*facade), 2: wall}, skip=(1, 3),
        caps=[Band(0, wall_top - 2, wall, front_z, front=wall, back=wall, z1=back_z),
              Band(wall_top - 2, wall_top + 1,
                   Tile(side[0], facade[0], side[1], facade[0] + 1, top=wall_top), front_z)])
    return [base, roof_lo, upper_storey, roof_hi]


class GableRoof(HipRoof):
    """A HipRoof with no hips: its front and back slopes run to its ends,
    and the ends are gables - the fascia along their foot, the wall `gable`
    (a Tile) over it up to the slopes, the ridge's end tiles over that. The
    ridge's back (its teeth's north face) is laid too: a gable shows it."""

    def __init__(self, *args, gable=None, front=None, pieces=None, **kw):
        kw["run"] = 0
        HipRoof.__init__(self, *args, **kw)
        self.gable = gable
        # the front fascia's own strip, its columns as drawn (the fascia
        # round the other sides wraps its clean columns)
        self.front = front
        # [(x0, x1, strip)]: the slopes laid in pieces across, each with its
        # own strip (the drawn columns where they show, a clean course
        # wrapped where the drawing has something else)
        self.pieces = pieces

    def emit(self, mesh):
        from voxel_building import Mesh, PlanPrism, tile_pieces
        tmp = Mesh()
        HipRoof.emit(self, tmp)
        drop = (".hip_w", ".hip_e") + ((".fascia_s",) if self.front is not None else ()) \
            + ((".slope_s", ".slope_n") if self.pieces is not None else ())
        for (tri, shade, tag) in tmp.tris:
            if not tag.endswith(drop):
                mesh.tri(tri[0], tri[1], tri[2], shade, tag)
        x0, x1, zf, zb, y0, ye = self.x0, self.x1, self.zf, self.zb, self.y0, self.ye
        zr, zq, yr = self.z_rf, self.z_rb, self.yr
        if self.front is not None:
            strip_face(mesh, [(x0, y0, zf), (x1, y0, zf), (x1, ye, zf), (x0, ye, zf)],
                       (0, y0, zf), (1, 0, 0), (0.0, 1.0, 0.0), self.front, SHADE_ART,
                       self.name + ".fascia_s")
        for (xa, xb, sl) in self.pieces or ():
            t_s = _unit((0, self.rise, -(zf - zr)))
            t_n = _unit((0, self.rise, zf - zr))
            strip_face(mesh, [(xa, ye, zf), (xb, ye, zf), (xb, yr, zr), (xa, yr, zr)],
                       (0, ye, zf), (1, 0, 0), t_s, sl, SHADE_ART, self.name + ".slope_s")
            strip_face(mesh, [(xb, ye, zb), (xa, ye, zb), (xa, yr, zq), (xb, yr, zq)],
                       (0, ye, zb), (1, 0, 0), t_n, sl, SHADE_BACK, self.name + ".slope_n")
        # the ridge's back, its teeth turned north
        for (tri, shade, tag) in tmp.tris:
            if tag.endswith(".teeth"):
                back = [(x, y, zq, u, v) for (x, y, z, u, v) in tri]
                PlanPrism._tri(mesh, back, (0.0, 0.0, -1.0), SHADE_BACK, self.name + ".teeth_n")
        # the gables: a pentagon from the eave's top up to the ridge's foot
        sec = [(zb, ye), (zf, ye), (zr, yr), (zq, yr)]
        for x, nx, shade in ((x0, -1.0, SHADE_WEST), (x1, 1.0, SHADE_EAST)):
            for k in range(1, len(sec) - 1):
                tri = [sec[0], sec[k], sec[k + 1]]
                for piece in tile_pieces([(zf - z, yr - y) for (z, y) in tri], self.gable, None, None):
                    pts = [(x, yr - t, zf - s_, u, v) for s_, t, u, v in piece]
                    for j in range(1, len(pts) - 1):
                        PlanPrism._tri(mesh, (pts[0], pts[j], pts[j + 1]), (nx, 0.0, 0.0), shade,
                                       self.name + ".gable")


def trick_house():
    """Route 110's Trick House (80 x 96), a two-storey house in pink metal:

        rows  0- 8  upper cap             rows 16-24  lower cap (the wings)
        rows  8-14  upper ridge's front   rows 24-30  lower ridge's front
        rows 14-48  upper slope           rows 30-64  lower slope
        rows 48-53  upper eave            rows 64-72  lower eave, the full width
        rows 53-64  upper storey          rows 72-96  stone ground floor

    Real depth: five collision rows, z 16-96, the walls standing on all of
    them. A full-width lower roof, gabled (its wings' slopes run to their
    ends as drawn, no hips), and the upper storey coming up through it a
    tile in from its eaves, front and back, with its own gabled roof. The
    slopes lay their drawn rows from the eave and then more courses of
    them; the lower roof is flatter (LOW_PITCH), so its ridge stays inside
    the upper storey. The upper storey's front is its drawn band of windows
    on a wall standing where the lower roof's slope meets it, the lower
    slope in front of it a clean course of the wings' flutes. Exact at 45
    degrees: the facade, the lower eave and the wings' drawn slope. (It was
    built exact to its top: roofs at 45 degrees to the drawn caps, and
    behind them a fall at 45 degrees down to the back wall - seen from the
    side, a pyramid; then the upper storey stood on the lower eaves, front
    and back.) The gables and the walls are the facade's stone."""
    front_z, back_z, overhang, inset = 96, 16, 2, 16
    stone = Tile(3, 72, 8, 96)
    upper_stone = Tile(32, 56, 40, 63)
    zf_lo, zb_lo = front_z + overhang, back_z - overhang
    slope_lo = Strip((30, 64), repeat=(32, 40))
    flutes = Strip((30, 64), repeat=(32, 40), wrap=(2, 10))
    roof_lo = GableRoof(
        "roof_lo", 0, 80, zf=zf_lo, zb=zb_lo, y0=zf_lo - 72,
        fascia=Strip((64, 72), wrap=(2, 10)), front=Strip((64, 72)), slope=slope_lo,
        pieces=[(0, 16, slope_lo), (16, 64, flutes), (64, 80, slope_lo)],
        teeth=(24, 30), cap=(16, 24), pitch=TRICK_LOW_PITCH, ridge_u=(0, 16),
        end_tile=Tile(2, 24, 10, 30), ridge_wrap=(2, 10), gable=stone)
    # the upper storey: a tile in from the lower eaves, standing where the
    # lower slope reaches it, its drawn wall (11 rows) and two more under them
    z_front, z_back = zf_lo - inset, zb_lo + inset
    y_base = roof_lo.ye + inset * math.tan(math.radians(TRICK_LOW_PITCH))
    y_top = y_base + 13
    assert y_top > roof_lo.yt, "the lower ridge must stay inside the upper storey"
    roof_hi = GableRoof(
        "roof_hi", 16, 64, zf=z_front + overhang, zb=z_back - overhang, y0=y_top,
        fascia=Strip((48, 53), wrap=(18, 58)), front=Strip((48, 53)),
        slope=Strip((14, 48), repeat=(16, 24)),
        teeth=(8, 14), cap=(0, 8), pitch=PITCH, ridge_u=(16, 64),
        end_tile=Tile(18, 8, 26, 14), ridge_wrap=(18, 58), gable=upper_stone)
    wall_hi = Tile(*upper_stone.rect, top=y_top)
    storey = Prism(
        "storey", 18, 62,
        [(z_front, y_base), (z_front, y_top), (z_back, y_top), (z_back, y_base)],
        edges={0: Tile(18, 53, 62, 64, s0=18), 2: wall_hi}, skip=(1, 3),
        caps=[Band(-64, 200, wall_hi, z_front, front=wall_hi, back=wall_hi, z1=z_back)])
    wall_top = roof_lo.y0
    wall = Tile(*stone.rect, top=wall_top)
    base = Prism(
        "ground_floor", 2, 79.9,
        [(front_z, 0), (front_z, wall_top), (back_z, wall_top), (back_z, 0)],
        edges={0: Proj(70, 96), 2: wall}, skip=(1, 3),
        caps=[Band(0, wall_top, wall, front_z, front=wall, back=wall, z1=back_z)])
    return [base, roof_lo, storey, roof_hi]


# the Trick House's lower roof: flatter than PITCH, so that its ridge, over
# the middle of the house, stays under the top of the upper storey
TRICK_LOW_PITCH = 15.0
TRICK_EXACT = [(2, 72, 78, 96), (0, 64, 80, 72), (0, 30, 16, 64), (64, 30, 80, 64)]


def lilycove_motel():
    """Lilycove's Cove Lily Motel (96 x 80): the same build thatched, its
    upper storey (x 17-80) standing behind four rows of the lower roof:

        rows  0- 9  upper ridge cap           rows 16-25 lower ridge cap (wings)
        rows  9-16  upper ridge front         rows 25-31 lower ridge front
        rows 16-35  upper thatch              rows 31-50 lower thatch
        rows 35-40  upper eave                rows 50-56 lower eave
        rows 40-46  upper storey's windows    rows 56-80 ground floor
    """
    return two_storey_house(
        96, 80,
        dict(cap=(16, 25), teeth=(25, 31), slope=(31, 50), course=(32, 40), fascia=(50, 56),
             wrap=(18, 58), fascia_wrap=(18, 58), ridge_wrap=(2, 10)),
        dict(cap=(0, 9), teeth=(9, 16), slope=(16, 35), course=(16, 24), fascia=(35, 40),
             wrap=(18, 58), fascia_wrap=(18, 58), ridge_wrap=(22, 62)),
        (17, 80), (40, 46), 4, (56, 80), (3, 6), (30, 38))


def pink_house(width, x0=1):
    """Mauville's pink-roofed house, any width: the kit house's build in
    the Trick House's corrugated metal (its flutes at x 2-5 + 8k). Rows:

        rows  0- 8  ridge cap (period 8 from x 9)
        rows  8-14  ridge front
        rows 14-32  corrugated slope
        rows 32-38  eave
        rows 38-64  facade, yellow posts at its ends

    Real depth: three collision rows, z 16-64, like the kit house.

    `x0`: the facade's first column (Rydel's shop is drawn a column further
    in: its walls from column 1 left that column clear up the corner)."""
    front_z, back_z = 64, 16
    overhang = 2
    roof = HipRoof(
        "roof", 0, width, zf=front_z + overhang, zb=back_z - overhang, y0=28,
        fascia=Strip((32, 38), wrap=(10, 18)),
        slope=Strip((14, 32), repeat=(16, 24), wrap=(2, 10)),
        teeth=(8, 14), cap=(0, 8), pitch=22.0, run=6,
        ridge_u=(0, width), end_tile=Tile(10, 8, 18, 14), ridge_wrap=(9, 17))
    wall_top = 28
    post = Tile(x0 + 1, 38, x0 + 6, 64, top=26)
    base = Prism(
        "ground_floor", x0, width - 1,
        [(front_z, 0), (front_z, wall_top), (back_z, wall_top), (back_z, 0)],
        edges={0: Proj(38, 64), 2: post}, skip=(1, 3),
        caps=[Band(0, 26, post, front_z, front=post, back=post, z1=back_z),
              Band(26, wall_top + 1, Tile(x0 + 1, 38, x0 + 6, 39, top=wall_top), front_z)])
    return [base, roof]


def pink_house_exact(width):
    return [(2, 38, width - 2, 62), (10, 16, width - 10, 38)]


def game_corner(art):
    """Mauville's Game Corner (112 x 80): a flat-roofed block, its roof
    glazed in 8-row panels (rows 3-26, the back parapet rows 0-3), the
    cornice rows 26-32, the facade 32-64; and its portal (x 40-72) standing
    8 forward, drawn down to row 71 - its top a 45-degree slope back to the
    cornice, edge-on to the view, since the drawing never shows it."""
    tex = Mound.with_ring(art, ())
    # The roof's back corners are drawn cut off (a diagonal outline from
    # (8, 0) to (0, 8) and its mirror), but the roof is deeper than drawn:
    # those rows land at its back corners and halfway along its sides, and
    # their clear pixels were holes in it. The verge's hatching (period 4)
    # runs on to the back instead, under the back parapet's outline.
    px = tex.load()
    W = art.size[0]
    for x in list(range(0, 8)) + list(range(W - 8, W)):
        for v in range(8):
            px[x, v] = px[x, v + 8]
    for x in range(W):
        if px[x, 0][3] < 128:
            px[x, 0] = px[8, 0]
    # the sides and the back: the pilaster beside the west window (brown, a
    # pink stripe), uniform down its height - the 16 rows under the cornice
    # hold the awning's slanting end, and repeated along a side they made a
    # saw of blue teeth; the cornice's bulbs away from the corner
    parts = flat_block(112, 64, ((3, 26), (8, 16), (0, 3)), (26, 32), 32,
                       brick=(8, 40, 16, 56), rim=(16, 26, 24, 32))
    portal = lambda u, v: 40 <= u < 72 and 32 <= v < 72
    # its flanks, 8 deep: the west post's own 8 columns (outline, brown,
    # the column of bulbs), not its outline folded back dark
    parts.append(HeightSolid("portal", tex, art.size[1],
                             Volume().profile([(64, 0), (72, 0), (72, 40), (64, 32)], 40, 72),
                             depth=(0.0, 80.0), step=2, mirror_z=68,
                             mask=lambda u, v: not portal(u, v), side=(40, 48, 72)))
    return parts, tex


def pitched_house(width, cap, teeth, slope, course, fascia, foot, wrap, ridge_wrap, wall, inset=2,
                  overhang=2, back_z=16, x0=0, run=6):
    """A town's hip-roofed house drawn like the kit house, any width: the
    roof's rows - `cap` and `teeth` of the ridge, the `slope` from its top
    row to the eave with `course` repeating, the `fascia` - then the facade
    from the fascia's foot down to `foot`, the ground's row. `wrap` and
    `ridge_wrap` are the clean columns (a whole number of tiles) the slope
    and the ridge are sampled from; `wall` the facade's columns its sides
    are dressed with. Real depth: three collision rows, z 16 to `foot`.
    `inset`: the walls' ends in from the roof's (a hall whose eaves reach
    over its deck). `x0`: where it starts (a porch before a hall).
    """
    front_z = foot
    y0 = front_z + overhang - fascia[1]
    roof = HipRoof(
        "roof", x0, x0 + width, zf=front_z + overhang, zb=back_z - overhang, y0=y0,
        fascia=Strip(fascia, wrap=wrap),
        slope=Strip(slope, repeat=course, wrap=wrap),
        teeth=teeth, cap=cap, pitch=22.0, run=run,
        ridge_u=(x0, x0 + width), end_tile=Tile(wrap[0], teeth[0], wrap[0] + 8, teeth[1]),
        ridge_wrap=ridge_wrap)
    wall_top = y0
    boards = Tile(wall[0], fascia[1], wall[1], foot, top=wall_top - 4)
    base = Prism(
        "ground_floor", x0 + inset, x0 + (width - inset + 1 if inset == 2 else width - inset),
        [(front_z, 0), (front_z, wall_top), (back_z, wall_top), (back_z, 0)],
        edges={0: Proj(fascia[1], foot), 2: boards}, skip=(1, 3),
        caps=[Band(0, wall_top - 4, boards, front_z, front=boards, back=boards, z1=back_z),
              Band(wall_top - 4, wall_top + 1,
                   Tile(wall[0], fascia[1], wall[1], fascia[1] + 1, top=wall_top), front_z)])
    return [base, roof]


def verdanturf_house(width):
    """Verdanturf's green-tiled house: ridge cap rows 0-7, its beige beam
    7-14, tiled slope 14-37 in courses of 8 (seams at rows 19, 27), eave
    37-40, wooden facade 40-62. Tiles 8 wide from x 2 (the left verge
    highlighted: sampled from x 10)."""
    return pitched_house(width, (0, 7), (7, 14), (14, 37), (19, 27), (37, 40), 62,
                         (10, 18), (9, 17), (3, 7))


def cozmo_house(art):
    """Fallarbor's house with an antenna on its roof (64 x 80): the house a
    row lower, and the antenna (x 32-56, rows 2-31) a card standing on the
    front slope where the slope is drawn at its foot."""
    parts = pitched_house(64, (16, 25), (25, 29), (29, 51), (35, 43), (51, 55), 78,
                          (10, 18), (9, 17), (3, 7))
    roof = parts[1]
    mast = art.copy()
    px = mast.load()
    for v in range(mast.size[1]):
        for u in range(mast.size[0]):
            if not (32 <= u < 57 and 2 <= v < 32):
                px[u, v] = (0, 0, 0, 0)
    # it stands on the front slope where the slope is drawn at its foot's
    # row: z - y = 32 on the slope (its last row, 31, stands just above it)
    t = math.tan(math.radians(roof.pitch))
    z = (32 + roof.ye + roof.zf * t) / (1 + t)
    return parts + [Card("antenna", mast, z)], art


def mossdeep_house_parts(width):
    """Mossdeep's red-roofed house built as Oldale's: a hipped roof (its
    white ridge beam rows 0-5, the two tiers of red slope with their posts
    rows 8-33 in courses of 14, the dark fascia 33-37) over the facade down
    to row 62; the slope sampled from 16 columns (a post and its tiles), the
    sides dressed with the vine-grown stone of the facade's ends."""
    return pitched_house(width, (0, 5), (5, 8), (8, 33), (19, 33), (33, 37), 62,
                         (12, 28), (12, 28), (2, 8))


def lilycove_house(width):
    """Lilycove's blue-tiled house: ridge cap rows 0-8, its front 8-12, tiles
    12-35 in courses of 8, eave 35-40, facade 40-63."""
    return pitched_house(width, (0, 8), (8, 12), (12, 35), (14, 22), (35, 40), 63,
                         (14, 22), (10, 18), (5, 8))


def lilycove_house_exact(width):
    return [(4, 40, width - 4, 62), (14, 12, width - 14, 40)]


def fallarbor_house(width):
    """Fallarbor's orange-roofed house: ridge cap rows 0-9, its front 9-13,
    ribbed slope 13-35 in courses of 8, eave 35-39, green facade 39-62."""
    return pitched_house(width, (0, 9), (9, 13), (13, 35), (19, 27), (35, 39), 62,
                         (10, 18), (9, 17), (3, 7))


def verdanturf_house_exact(width):
    return [(3, 40, width - 2, 62), (10, 14, width - 10, 40)]


def fallarbor_house_exact(width):
    return [(3, 39, width - 2, 62), (10, 13, width - 10, 39)]


# the pitch of the shipyard's back half (shipyard): at 20 degrees, from the
# game's camera at its default 40, the band of slats beyond the ridge stands
# to the corrugated half as in the drawing (about 0.3); at 25 it read as a
# thin line, the camera's view of the roof's back being lower than 40
SHIPYARD_BACK_PITCH = 20.0


def shipyard():
    """Stern's shipyard in Slateport (128 x 112, cells 24..31 x 32..38).

        rows  1-15  the roof's back half beyond the ridge, its white slats
                    (low enough for the view to see it), the hatches' lids
        row  16     the ridge: the short line dividing the two halves
        rows 16-63  the front half, corrugated, period 4 across; two hatches
                    across the ridge (x 16-31, 96-111, rows 8-31); gold verges
                    x 0-4 down both: a gabled roof, not a hipped one
        rows 64-71  fascia: teeth, gold, purple
        rows 72-111 the facade: pink corner pilasters, boards, the door

    Real depth: six collision rows, Z 16-112. The front half at 20 degrees
    spans its 48 drawn rows from the eave (Y 48) to the ridge. The back half
    falls at SHIPYARD_BACK_PITCH over the rest of the depth: drawn in its 15
    rows, it would fall at 37, and the game's camera (34-46 degrees) would
    hardly see it; so its slats run on back (more of the same rows), its
    outline and trim at its real eave. The gable ends and the back wall take the
    facade's boards. (It was a hipped roof with the slats as a tall ridge
    standing up like a sheet.)
    """
    front, back, wall = 112, 16, 40
    ye = 48                                     # the fascia's top at the eave
    run = 48 / (1 + math.tan(math.radians(20.0)))
    zr, yr = front - run, ye + run * math.tan(math.radians(20.0))
    yb = yr - (zr - back) * math.tan(math.radians(SHIPYARD_BACK_PITCH))
    post = Tile(0, 72, 5, 112, top=wall)
    boards = Tile(8, 72, 30, 112, top=wall)
    gable = Tile(8, 72, 30, 112)
    body = Prism(
        "ground_floor", 0, 128,
        [(back, 0), (front, 0), (front, ye), (zr, yr), (back, yb)],
        edges={1: Proj(), 4: boards}, skip=(0, 2, 3),
        caps=[Band(0, wall, boards, front, front=post, back=post, z1=back),
              Band(wall, yr + 1, gable, front, z1=back)])
    return [body, ShipyardRoof(0, 128, front, back, ye, zr, yr, yb)]


class ShipyardRoof:
    """The shipyard's two roof halves, the drawing projected on both. The
    back half is seen foreshortened, less than one texel per pixel down its
    fall line: so is it drawn ("~proj")."""

    def __init__(self, x0, x1, zf, zb, ye, zr, yr, yb):
        self.x0, self.x1, self.zf, self.zb = x0, x1, zf, zb
        self.ye, self.zr, self.yr, self.yb = ye, zr, yr, yb

    def emit(self, mesh):
        x0, x1 = self.x0, self.x1

        def proj(pts):
            return [(x, y, z, x, z - y) for (x, y, z) in pts]
        mesh.poly(proj([(x0, self.ye, self.zf), (x1, self.ye, self.zf), (x1, self.yr, self.zr),
                        (x0, self.yr, self.zr)]), SHADE_ART, "roof.slope_s")
        back = proj([(x0, self.yr, self.zr), (x1, self.yr, self.zr), (x1, self.yb, self.zb),
                     (x0, self.yb, self.zb)])
        # as drawn from the ridge to the slats' first row; then that row
        # held back to the last three rows, which are its trim and outline
        slats, end = 4.0, self.zb - self.yb
        near = clip(back, 4, slats, True)
        rest = clip(back, 4, slats, False)
        mid = clip(rest, 4, end + 3, True) if len(rest) >= 3 else []
        edge = clip(rest, 4, end + 3, False) if len(rest) >= 3 else []
        if len(near) >= 3:
            mesh.poly(near, SHADE_ART, "roof.slope_n~proj")
        if len(mid) >= 3:
            mesh.poly([q[:4] + (slats + 1.5,) for q in mid], SHADE_ART, "roof.slope_n~clamp")
        if len(edge) >= 3:
            mesh.poly([q[:4] + (q[4] - end + 1,) for q in edge], SHADE_ART, "roof.slope_n~proj")


# its back half's top rows lie further back than drawn (ShipyardRoof)
SHIPYARD_EXACT = [(0, 4, 128, 112)]


def stall(x1, ridge_e, fills):
    """Slateport market's stall, its roof from x 8 to `x1`, lifted 12 like
    the awnings so that people walk under it."""
    return Tent("stall", 8, x1, top_v=1, valance_v=25, post_v=29, foot_v=47, back=12, lift=12,
                posts=[(8, 14), (x1 - 6, x1)], shaft=(34, 44), foot=(44, 47),
                ridge=(22.5, ridge_e, 9, 11), pitch=30.0, fills=fills)


def stall_keep(x1):
    """The stall's own pixels: its roof and its two front posts."""
    return [(8, 1, x1, 29), (8, 29, 14, 47), (x1 - 6, 29, x1, 47)]


class Relief_top:
    """A flat polygon at height `h`, projected: the basin's rim and water."""

    def __init__(self, poly, h):
        self.poly, self.h = poly, h

    def emit(self, mesh):
        h = self.h
        mesh.poly([(x, h, z, x, z - h) for (x, z) in reversed(self.poly)], 1.0, "fountain.top")
        # the sides and back the drawing never shows: the front wall's stone
        n = len(self.poly)
        for i in range(2, n - 1):
            (xa, za), (xb, zb) = self.poly[i], self.poly[(i + 1) % n]
            quad = [(xa, -1, za), (xb, -1, zb), (xb, h, zb), (xa, h, za)]
            # projected: from the front it is the drawing, gaps included
            mesh.poly([q + (q[0], q[2] - q[1]) for q in quad], 0.7, "fountain.side~proj")


class PlanRing:
    """A flat ring at height `y` between two plan polygons of as many
    corners, corner i of one facing corner i of the other (a basin's rim),
    or with `inner` None the whole of `outer` (its water). Projected: the
    drawing at 45 degrees."""

    def __init__(self, name, outer, inner, y):
        self.name, self.outer, self.inner, self.y = name, outer, inner, y

    def emit(self, mesh):
        y = self.y

        def pt(p):
            return (p[0], y, p[1], p[0], p[1] - y)
        o, i = self.outer, self.inner
        if i is None:
            mesh.poly([pt(p) for p in o], SHADE_ART, self.name + "~proj")
            return
        for k in range(len(o)):
            k1 = (k + 1) % len(o)
            mesh.poly([pt(o[k]), pt(o[k1]), pt(i[k1]), pt(i[k])], SHADE_ART, self.name + "~proj")


class Shade:
    """A flat ellipse at height `y`, about (cx, cz), of one colour - the
    texel `texel` of the drawing: what a dish's shadow is under the dish.
    The water there, projected, reads the dish's own pixels (the dish hides
    them at 45 degrees); seen from the side, under the dish's edge, they
    showed as its dots twice. Hidden at 45 degrees itself, so it is not the
    drawing's to judge (~behind), and one texel stretched (~clamp)."""

    def __init__(self, name, cx, cz, rx, rz, y, texel, sides=24):
        self.name, self.cx, self.cz, self.rx, self.rz = name, cx, cz, rx, rz
        self.y, self.texel, self.sides = y, texel, sides

    def emit(self, mesh):
        u, v = self.texel[0] + 0.5, self.texel[1] + 0.5
        ring = [(self.cx + self.rx * math.cos(2 * math.pi * k / self.sides), self.y,
                 self.cz + self.rz * math.sin(2 * math.pi * k / self.sides), u, v)
                for k in range(self.sides)]
        mesh.poly(ring, SHADE_ART, self.name + "~behind~clamp")


class Jet:
    """The fountain's jet: a card standing across the dish at depth `z`,
    from the dish's top `y0` up, over the drawing's columns `columns` and
    rows `rows` - each point reading the drawing's pixel it covers at 45
    degrees, moved down `band` rows to where the jet is laid alone. Its
    texels are `live` (gen_voxel_buildings.py): the console redraws them
    from the game's tiles."""

    def __init__(self, name, columns, z, y0, band, rows=JET_ROWS):
        self.name, self.columns, self.z, self.y0, self.band = name, columns, z, y0, band
        self.y1 = z - rows[0]
        if abs((z - rows[1]) - y0) > 1e-6:
            raise SystemExit("%s: the card's foot is not the dish's top" % name)

    def emit(self, mesh):
        (x0, x1), z = self.columns, self.z
        quad = [(x0, self.y0, z), (x1, self.y0, z), (x1, self.y1, z), (x0, self.y1, z)]
        mesh.poly([(x, y, z_, x, z_ - y + self.band) for (x, y, z_) in quad], SHADE_ART,
                  self.name + "~live")


# ── Interiors ─────────────────────────────────────────────────────────────
#
# A room is cut into pieces, front first; see gen_voxel_buildings.interior_specs.
# Shapes are in pixels of the room's drawing; `height` is the rows of a piece's
# front, standing at its drawn foot; `base` lifts it onto another piece.

WALL_COLOURS = ("f6b473", "de7b31", "ffe6b4")   # the Center's orange wall


def piece(name, shape, height, base=0, fill=None, leave=(), side=None, solid=None,
          back=None, card=False, top=None, foot=None, walls=(), cells=(), against=None,
          claim=(), through=False, round=None, legs=None, run=(), plan=None):
    """`back`: the Z its top runs back to, where the cartridge's collision
    says it ends (or the wall it stands against); `against`: that wall's Z,
    where a top drawn deeper than the room in front of it stops, flat, the
    wall hiding the rows drawn behind it; `top`: the stretch of the room's
    drawing that depth is laid with, when its own drawn top is too thin to
    repeat; `card`: leaves, not a box; `foot`: the row a wall with a doorway
    stands on; `walls`: the side walls it carries, ((x, z), (x, z)[, height])
    - facing left of a->b - dressed with `side`; `through`: the room is seen
    through it - its `leave` colours inside its outline too are holes (a
    tap's wire, a bed's open head frame), not the room painted on it;
    `round`: "dome", a round lid as a smooth cap under its drawing, `height`
    its crown over `base` (gen_voxel_buildings.dome_fit); "ball", round all
    over, its circle's size (gen_voxel_buildings.ball_fit); "lathe", turned
    (a jar), `height` tall, its belly and neck fitted to its drawing
    (gen_voxel_buildings.lathe_fit); `plan`: modelled
    whole, an upright prism on that polygon of (x, z) in the room's pixels
    (vb.PlanPrism), its corners cut as the drawing cuts them - a counter;
    or blocks [(part, polygon, y0, y1, (x0, x1, foot)), ...], each its own
    prism with that stretch of its front for its sides - a terminal's head
    on its pedestal, a table's top on its feet (its legs its own blocks:
    no leg_split);
    `fill`: "column", what the pieces before it hide of its top is its own
    up or down its column, not along its row; `legs`: the rows
    of its front that are its top, the rest its legs, where the drawing's
    own are too short to be found (gen_voxel_buildings.leg_split); `run`:
    built from the side, [(part, [(x, y), ...], z0, z1)] - each section, x
    across and y up in the room's pixels, running back from z0 to z1 with
    the drawing projected on it (vb.ProjRun): an escalator's ramp and its
    balustrades. A `foot` below its drawing: a machine standing behind a
    counter that hides its front's foot, not on it."""
    return {"name": name, "shape": shape, "height": height, "base": base,
            "fill": fill, "leave": leave, "side": side, "solid": solid,
            "back": back, "card": card, "top": top, "foot": foot, "walls": walls,
            "cells": cells, "against": against, "claim": claim, "through": through,
            "round": round, "legs": legs, "run": run, "plan": plan}


def own_shell(pieces):
    """Marks a room's shell - its walls, its doorways' recesses, a maze's
    blocks - as its own (`alone`): the generator stands a known piece
    wherever its cells are drawn again, and a side wall, which is drawn
    nowhere, was found wherever another room had the same floor down a
    column - walls standing loose in the middle of Rustboro's flats."""
    for pc in pieces:
        if pc["name"].split("_")[0] in ("wall", "side", "stairwell", "edge", "block",
                                         "partition", "divider"):
            pc["alone"] = True
    return pieces


# the Center's floor and its shadows, and the counter's cream and trim: what
# a stool, the table or a Poke Ball on the counter leaves round it
CENTER_FLOOR = ("cdc58b", "eedea4", "ffffc5")
CENTER_COUNTER = ("ffffc5", "e6e6b4", "de9c62", "ffe6b4", "de7b31")


def facet(name, a, b, height, walls=(), side=None, cells=()):
    """A chamfered corner: a wall along its foot, from a = (x, top, foot) to
    b in the drawing's pixels, carrying the side walls that run on from it
    (edge on to the GBA camera, drawn nowhere), `height` tall."""
    (xa, ta, fa), (xb, tb, fb) = a, b
    return {"name": name, "shape": [[(xa, ta), (xb, tb), (xb, fb), (xa, fa)]],
            "height": height, "facet": (a, b), "walls": walls, "side": side,
            "cells": cells}


# a stretch of the Center's back wall, full height and one panel wide, as the
# room looks with the furniture taken out: for the faces the drawing never shows
CENTER_WALL_SIDE = (64, 0, 80, 32)


def center_walls(front):
    """The Center's walls, 32 px: the back one, standing at row 32 - in two,
    west and east of x 128, so each half counts against the chunk it stands in
    - and its corners, chamfered at 45 degrees, from which the side walls run
    on to where the floor ends at `front`. The drawing has no side walls: they
    are edge on to its camera; from the console's they close the room."""
    return [
        facet("corner_w", (0, 15, 47), (16, -1, 31), 32, walls=[((0, front), (0, 47))],
              side=CENTER_WALL_SIDE, cells=[(0, front // 16 - 1)]),
        facet("corner_e", (208, -1, 31), (224, 15, 47), 32, walls=[((224, 47), (224, front))],
              side=CENTER_WALL_SIDE, cells=[(13, front // 16 - 1)]),
        piece("wall_e", [(128, 0, 208, 32)], 32, fill=16, side=CENTER_WALL_SIDE),
        piece("wall", [(16, 0, 128, 32)], 32, fill=16, side=CENTER_WALL_SIDE),
    ]


def center_escalator(flat, way):
    """The Center's escalator from the side (piece `run`): a ramp between
    two glass balustrades two pixels thick and 7 tall, to z 96 and 120, running
    west into the side wall. East of x `flat` it is the landing, level with
    the floor; west of it the balustrades and the steps between them climb
    (`way` 1: up to the floor above) half a pixel a pixel, or sink (-1:
    down to the one below) to the floor at x 0. The console's floor has no
    hole for them: sinking, the steps and the glass below the floor are
    the drawing's, laid on it - the GBA's 45 degrees exactly, and from the
    console's camera the stair the drawing shows going down. Rising, the
    balustrades are a band that climbs, the steps a plate: the drawing
    shows the floor under them where they leave it (its glass ends a row
    lower every two columns, from row 113 at x 0), and stood down to the
    floor there they took it on their faces - a pale band under the glass,
    cut in steps."""
    top = 7 + way * flat / 2.0
    rail = [(0, 0), (32, 0), (32, 7), (flat, 7), (0, top)]
    if way > 0:
        # the glass's foot, a straight line through its drawn steps' middles
        # (row 112.5 + x / 2 at the front): every pixel of glass inside it,
        # every one of floor out - its edge the line, not a stair the
        # texture's transparency cuts
        rail = [(0, flat / 2.0 - 0.5), (flat - 1, 0), (32, 0), (32, 7), (flat, 7), (0, top)]
        steps = [(0, flat / 2.0), (flat, 0), (32, 0), (32, 0.5), (flat, 0.5),
                 (0, flat / 2.0 + 0.5)]
        front = 118
    else:
        # down to the floor at x 0; the steps' plate runs on under the south
        # balustrade, where the glass below the floor is drawn
        rail = [(0, 0), (32, 0), (32, 7), (flat, 7)]
        steps, front = [(0, 0), (32, 0), (32, 0.5), (0, 0.5)], 120
    return [("rail_n", rail, 94, 96), ("rail_s", rail, 118, 120), ("steps", steps, 96, front)]


def center_counter_plan(x0, x1, arm, z_back, z_bar, z_front, cut=3, nick=1.2):
    """The Center's counter in plan: a bar across the front from x0 to x1,
    an arm `arm` wide back to `z_back` at each end, the yard between them
    (the nurse's) behind the bar's back at `z_bar`. Its front corners are
    cut on the slant `cut` pixels - the drawing's bands step up a row a
    column round them, its foot a pixel a column - and its arms' back
    corners by a nick, where the drawing leaves the wall."""
    a, b = x0 + arm, x1 - arm
    return [(x0 + cut, z_front), (x1 - cut, z_front), (x1, z_front - cut),
            (x1, z_back + nick), (x1 - nick, z_back), (b + nick, z_back), (b, z_back + nick),
            (b, z_bar), (a, z_bar), (a, z_back + nick), (a - nick, z_back),
            (x0 + nick, z_back), (x0, z_back + nick), (x0, z_front - cut)]


def center_stool(x0, y0):
    """A stool of the Centers, drawn in the 16 x 16 at (x0, y0) (all six
    alike): its cushion, 14 wide, its back corners cut on the slant as the
    drawing rounds them (its top's first two rows two pixels and one in),
    from y 4 to 6; under it the base, a pixel in each side, to y 1; and its
    foot, the dark line at its bottom, four in. Each takes its sides from
    its own rows of the front, the run of middle columns alike in them (a
    strip as wide as that, so that a side is a quad or two): the cushion's
    oranges, the base's band, dark line and grey, the foot's dark."""
    z = y0 + 16
    cushion = [(x0 + 3.25, y0 + 8), (x0 + 12.75, y0 + 8), (x0 + 15, y0 + 10.25), (x0 + 15, z),
               (x0 + 1, z), (x0 + 1, y0 + 10.25)]
    base = [(x0 + 2, y0 + 9), (x0 + 14, y0 + 9), (x0 + 14, z), (x0 + 2, z)]
    foot = [(x0 + 4, y0 + 11), (x0 + 12, y0 + 11), (x0 + 12, z), (x0 + 4, z)]
    return [("cushion", cushion, 4, 6, (x0 + 3, x0 + 13, y0 + 12)),
            ("base", base, 1, 4, (x0 + 5, x0 + 11, y0 + 15)),
            ("foot", foot, 0, 1, (x0 + 4, x0 + 12, y0 + 16))]


def center_table():
    """The Center's glass table: its top a slab from y 1 to 5, its back
    corners round - each row of the drawing's top ending half way between
    the last pixel of glass and the first of floor, so that no pixel's
    centre is on its edge - its front corners cut a pixel; on four feet a
    pixel tall, 2 x 2, the front two where they are drawn under its edge,
    the back two hidden under the glass. The slab's sides are the glass's
    edge (its front's pale and teal rows and the dark line under them, the
    five columns alike in all four), the feet's their dark."""
    left = [(183.5, 101), (183, 101.5), (181, 102.5), (180, 103.5), (179, 104.5),
            (179, 105.5), (178, 106.5), (178, 125.5), (179, 126)]
    # the drawing is symmetric about x 192
    slab = left + [(384 - x, z) for (x, z) in reversed(left)]

    def foot(x, z):
        return [(x, z), (x + 2, z), (x + 2, z + 2), (x, z + 2)]
    return ([("top", slab, 1, 5, (185, 190, 125))]
            + [("foot_%d" % n, foot(x, z), 0, 1, (180, 182, 126))
               for n, (x, z) in enumerate([(180, 124), (202, 124), (180, 104), (202, 104)])])


POKEMON_CENTER_1F = [
    # the Poke Balls on the counter's two arms: round, each as its drawn
    # circle - its shadow under it and the arm round it the counter's
    piece("ball_w", [("ellipse", 73, 46, 6, 6)], 12, base=10, leave=CENTER_COUNTER,
          round="ball"),
    piece("ball_e", [("ellipse", 151, 46, 6, 6)], 12, base=10, leave=CENTER_COUNTER,
          round="ball"),
    # the counter: a bar across the front, an arm back to the wall each end,
    # inside their outlines (the floor beside them is the floor's, the PC's
    # edge the PC's, the row over the bar's back edge the nurse's floor and
    # the machines' feet); the wall round its arms' rounded backs and the
    # floor round its foot's corners left. As deep as its drawn top: run
    # back to its cells' edge it would lay its plate again behind it, where
    # the nurse's floor is seen between the machines. One piece on its
    # plan, its front corners one slanting face each, its top under the
    # balls its arms' own; its sides its front's bands, a stretch with no
    # slot in it (its ends' grey bevel laid along its arms is a slab of
    # shade). The wall in its west arm's back corner is the wall's, though
    # the dark behind the arm closes the outline round it
    piece("counter", [(65, 24, 79, 64), (79, 25, 80, 64), (144, 24, 159, 64), (65, 45, 159, 64)],
          10, fill="column", leave=("de7b31", "cdc58b", "eedea4"), side=(108, 54, 116, 64),
          plan=center_counter_plan(65, 159, 15, 34, 55, 64)),
    # behind it, on the floor where the nurse stands: the healing machine
    # lies flat - its tray is its top, its front the band over the counter,
    # which hides its foot
    piece("healer", [(80, 14, 112, 45)], 14, leave=WALL_COLOURS, foot=54, against=32,
          cells=[(5, 3)]),
    # and the telephone beside it, against the wall: a box, its top the lid
    # with the cross (rows 25 to 32), its front the grey under it; and its
    # dial, a block in front of the box's west end, its keys over its top
    # and front. Inside their outlines: cut wider, the wall and the floor
    # round them stood up as a cream slab beside the box
    piece("phone", [(133, 25, 144, 41), (128, 31, 133, 42)], 8,
          leave=WALL_COLOURS + CENTER_FLOOR,
          plan=[("box", [(133, 33), (141.5, 33), (144, 35.5), (144, 41), (133, 41)], 0, 8,
                 (135, 140, 41)),
                ("dial", [(128, 36), (133, 36), (133, 42), (128, 42)], 0, 5, (129, 130, 42))]),
    # the PC by the map, inside its outline: its top the screen's housing
    # (rows 8 to 15), against the wall; cut two pixels wider, it stood the
    # counter's outline and the baseboard's line beside it up its sides
    piece("pc", [(160, 8, 176, 40)], 24, leave=WALL_COLOURS, back=32),
    piece("bookcase", [(32, 14, 65, 42)], 18, leave=WALL_COLOURS, back=32),
    # a card: the wall, the corner's shade and the floor round its pot left
    piece("plant", [(14, 10, 34, 40)], 26, card=True, through=True,
          leave=WALL_COLOURS + CENTER_FLOOR + ("f6eebd", "bd5220", "949494")),
    piece("stool_1", [(16, 47, 32, 64)], 6, leave=CENTER_FLOOR,
          plan=center_stool(16, 47)),
    piece("stool_2", [(32, 47, 48, 64)], 6, leave=CENTER_FLOOR,
          plan=center_stool(32, 47)),
    piece("stool_3", [(160, 95, 176, 111)], 6, leave=CENTER_FLOOR,
          plan=center_stool(160, 95)),
    piece("stool_4", [(160, 111, 176, 127)], 6, leave=CENTER_FLOOR,
          plan=center_stool(160, 111)),
    piece("stool_5", [(176, 127, 192, 144)], 6, leave=CENTER_FLOOR,
          plan=center_stool(176, 127)),
    piece("stool_6", [(192, 127, 208, 144)], 6, leave=CENTER_FLOOR,
          plan=center_stool(192, 127)),
    # a glass top on four feet a row tall, under its edge (center_table)
    piece("table", [(176, 95, 208, 128)], 5, leave=CENTER_FLOOR, plan=center_table()),
    # up to the floor above, into the west wall
    piece("escalator", [(0, 78, 34, 123)], 8, leave=CENTER_FLOOR + ("949494",),
          run=center_escalator(16, 1)),
    # the walls: the back one, its chamfered corners, the two side walls
    # whose tops are the lines down the room's edges, the front corners
] + center_walls(144)



def center_terminal(x0):
    """A link terminal of the Center's 2F standing at x0 (its drawing 16
    wide, its foot on row 80): its head, a box as deep as its drawn top (rows
    56 to 59: four pixels), from the outline under its screen (row 70, y 9)
    up to y 20, its sides the grey of its casing's frame; under it the
    pedestal, two pixels narrower each side as drawn, its sides its front's
    grey with the dark line under the head and its foot's."""
    head = [(x0, 76), (x0 + 16, 76), (x0 + 16, 80), (x0, 80)]
    stand = [(x0 + 2, 77), (x0 + 14, 77), (x0 + 14, 80), (x0 + 2, 80)]
    return [("head", head, 9, 20, (x0 + 1, x0 + 2, 71)),
            ("stand", stand, 0, 9, (x0 + 7, x0 + 8, 80))]


POKEMON_CENTER_2F = [
    # the three link terminals, in front of the counter, and the glass
    # tubes that stand behind them - each tube's foot its own, not drawn on
    # the terminal's top. The first is the ground floor's PC, pixel for
    # pixel inside its outline, and stands here as it is
    # (gen_voxel_buildings.reuse_pieces; the tube behind it in view over
    # it); the other two are a head on a pedestal, each a block
    # (center_terminal)
    piece("terminal_2", [(128, 56, 144, 81)], 20, plan=center_terminal(128)),
    piece("terminal_3", [(192, 56, 208, 81)], 20, plan=center_terminal(192)),
    # the gates between the counter's stretches: low, set back to its back
    piece("gate_1", [(80, 46, 96, 58)], 6, back=48),
    piece("gate_2", [(144, 46, 160, 58)], 6, back=48),
    piece("counter", [(0, 40, 48, 63), (64, 40, 80, 63), (96, 40, 128, 63),
                      (160, 40, 192, 63), (208, 40, 218, 63)], 9, fill=1, back=48),
    # glass tubes: as deep as they are wide, the rest of their drawing
    # height; their sides the white of their glass, whole - the rounded
    # corners of their feet are not legs
    piece("tube_1", [(48, 6, 64, 42)], 24, leave=WALL_COLOURS, side=(51, 16, 61, 36),
          solid=True),
    piece("tube_2", [(128, 6, 144, 56)], 33, leave=WALL_COLOURS, side=(131, 23, 141, 52),
          solid=True),
    piece("tube_3", [(192, 6, 208, 56)], 33, leave=WALL_COLOURS, side=(195, 23, 205, 52),
          solid=True),
    # cards, the floor round their pots left
    piece("plant_1", [(80, 128, 96, 160)], 24, card=True, leave=CENTER_FLOOR + ("f6eebd",)),
    piece("plant_2", [(96, 128, 112, 160)], 24, card=True, leave=CENTER_FLOOR + ("f6eebd",)),
    piece("plant_3", [(144, 128, 160, 160)], 24, card=True, leave=CENTER_FLOOR + ("f6eebd",)),
    piece("plant_4", [(160, 128, 176, 160)], 24, card=True, leave=CENTER_FLOOR + ("f6eebd",)),
    # the five stools along the front are the ground floor's, tile for tile,
    # and stand here as they are (gen_voxel_buildings.reuse_pieces)
    # down to the floor below: its steps drawn sinking into the floor
    piece("escalator", [(0, 86, 34, 122)], 8, leave=CENTER_FLOOR + ("949494",),
          run=center_escalator(14, -1)),
] + center_walls(160)


MART_WALL = ("ffffff", "8bd5de", "d5ded5", "b4b4a4", "83b4b4")
MART_WALL_SIDE = (32, 0, 48, 32)

MART = [
    # the till on the glass counter's arm; the counter, an L from the wall
    piece("till", [(32, 24, 48, 39)], 8, base=15, leave=MART_WALL),
    # a glass case, whose drawn top is two rows: its depth is laid with the
    # glass of the arm that runs back to the wall, which shows its top whole
    piece("counter", [(32, 24, 48, 80), (0, 58, 48, 80)], 15, fill=1, back=64,
          top=(34, 56, 46, 70)),
    # the shelves along the back wall, the plant, the three standing shelves
    piece("shelves_back", [(96, 14, 160, 42)], 20, leave=MART_WALL, back=32, side="plain"),
    piece("plant", [(160, 30, 172, 64)], 26, leave=MART_WALL, card=True),
    # the display stands: low, their front the band at their foot, their
    # three compartments of goods their top, as long as the three cells
    # their collision blocks
    piece("shelf_1", [(96, 62, 112, 111)], 14, back=64),
    piece("shelf_2", [(112, 62, 128, 111)], 14, back=64),
    piece("shelf_3", [(160, 54, 172, 111)], 14, back=64),
    # the walls as the Center's: 7-px corners, the side walls run on from them
    facet("corner_w", (0, 6, 40), (7, -1, 33), 34, walls=[((0, 128), (0, 40))],
          side=MART_WALL_SIDE, cells=[(0, 7)]),
    facet("corner_e", (169, -1, 33), (176, 6, 40), 34, walls=[((176, 40), (176, 128))],
          side=MART_WALL_SIDE, cells=[(10, 7)]),
    piece("wall", [(7, 0, 169, 32)], 32, fill=16, side=MART_WALL_SIDE),
]


def _replace(pieces, names, new):
    """A room like another but for some pieces: `new` stands where the first
    of `names` did, and the rest of them are gone."""
    out, done = [], False
    for pc in pieces:
        if pc["name"] in names:
            if not done:
                out += new
                done = True
            continue
        out.append(pc)
    return out


# Lavaridge's Center: a door to the hot spring in the back wall, a plant
# either side of it. Its counter, Poke Balls, machines, stools, table and
# escalator are the other Centers', tile for tile, and stand here as they are
# (gen_voxel_buildings.reuse_pieces), and so is the back wall east of the
# door; the plants and the rest of the walls are its own.
LAVARIDGE_CENTER_1F = [
    piece("plant_w", [(14, 10, 34, 40)], 26, leave=WALL_COLOURS),
    piece("plant_e", [(46, 10, 66, 40)], 26, leave=WALL_COLOURS),
] + [pc for pc in center_walls(144) if pc["name"] != "wall_e"]


# The front corners the GBA leaves black: the outside of a room whose front
# wall it never draws. In the round they are floor, not a hole in it.
CENTER_1F_OPEN = [[(0, 126), (18, 144), (0, 144)], [(224, 126), (206, 144), (224, 144)]]
CENTER_2F_OPEN = [[(0, 142), (18, 160), (0, 160)], [(224, 142), (206, 160), (224, 160)]]
MART_OPEN = [[(0, 118), (10, 128), (0, 128)], [(176, 118), (166, 128), (176, 128)]]

# ── Littleroot's two houses ───────────────────────────────────────────────
#
# Brendan's and May's, each a ground floor and a bedroom, the one house the
# other way round. The ground floor's back wall steps forward a cell where the
# stairs go up, and the stairs are a doorway in it: the flight is drawn inside
# the doorway as the GBA sees it from above, so it lies on the floor of a
# recess one cell deep, with the doorway's sides and back round it. Upstairs
# the same doorway leads down, drawn on the floor behind the wall's face.
#
# Furniture against the back wall stands on the wall's own row of collision,
# so what it is drawn with in front of the wall is all the depth it has; a
# desk drawn with more top than that stands its back rows up the wall.

HOUSE_WALL = ("d5d5b4", "b4b4a4", "ffffff")          # plaster, baseboard, trim
HOUSE_FLOOR = ("b4a44a", "947329", "ac8b39", "cdc55a")   # the planks
BLUE_RUG = ("6a94c5", "8bbdf6", "838394")               # and the chairs' shadows
PINK_RUG = ("d57bac", "838394")


def house_chair(name, back, seat, leave):
    """A kitchen chair seen from the side: its back, a narrow slab, and its
    seat, as deep as one another."""
    return [piece(name + "_back", [back], 9, leave=leave, solid=True),
            piece(name + "_seat", [seat], 5, leave=leave, solid=True)]


def stairwell(name, x0, x1, front, back, opening, side, crest):
    """The sides and back of a doorway's recess, x0..x1 from the wall's face
    at `front` back to `back`; `opening` is the doorway's height. `crest` is
    the row the wall's top is drawn on: nothing of the recess may rise above
    it at 45 degrees, so the sides step down where the doorway's height
    would."""
    low = min(opening, back - crest)
    mid = crest + opening
    if mid >= front or mid <= back:
        walls = [((x0, front), (x0, back), low), ((x1, back), (x1, front), low)]
    else:
        walls = [((x0, front), (x0, mid), opening), ((x0, mid), (x0, back), low),
                 ((x1, back), (x1, mid), low), ((x1, mid), (x1, front), opening)]
    walls.append(((x0, back), (x1, back), low))
    return piece(name, [], 32, side=side, walls=walls)


def house_tv():
    """Littleroot's television (piece `plan`): the set, a box as deep as its
    drawn top - the bezel's grey four rows, and behind them the tube's
    housing, narrower row by row (rows 61 to 63) - from y 8 to 20, its sides
    its casing's dark; under it the stand, on the same plan, its sides its
    frame's dark. Laid back over the cell it blocks with one row of that
    top repeated, the set was a black box with a grey band at its front."""
    # its edges between the drawn pixels, through their rows' middles: every
    # centre of the housing's pixels inside, every one of the floor's out
    plan = [(67.5, 81), (76.5, 81), (78, 82.5), (80, 83.5), (80, 88), (64, 88), (64, 83.5),
            (66, 82.5)]
    return [("set", plan, 8, 20, (64, 66, 80)), ("stand", plan, 0, 8, (71, 73, 88))]


def brendan_1f():
    rug = BLUE_RUG
    return (house_chair("chair_nw", (33, 96, 37, 112), (37, 100, 47, 112), rug)
            + house_chair("chair_sw", (33, 112, 37, 128), (37, 116, 47, 128), rug)
            + house_chair("chair_ne", (91, 96, 95, 112), (81, 100, 91, 112), rug)
            + house_chair("chair_se", (91, 112, 95, 128), (81, 116, 91, 128), rug) + [
        piece("table", [(50, 96, 78, 128)], 8, leave=rug, solid=True),
        # the television on its stand (house_tv), and the low white cupboard
        # beside it
        piece("tv", [(64, 61, 80, 88)], 20, leave=HOUSE_FLOOR, plan=house_tv()),
        piece("cabinet", [(32, 65, 63, 88)], 9, back=72),
        # the kitchen along the back wall: fridge, sink, china cabinet. The
        # fridge and the cabinet are as deep as drawn, their tops their
        # drawn rows: laid back to the wall with those rows repeated, a top
        # carried its back outline across its middle (the fridge's dark
        # line, the cabinet's yellow plain twice its size)
        piece("fridge", [(0, 18, 16, 48)], 24, leave=HOUSE_FLOOR, back=42),
        # a wire of a tap: the baseboard between its spokes is seen through it
        piece("tap", [(22, 24, 29, 29)], 5, base=8, leave=HOUSE_WALL[:2], through=True),
        piece("sink", [(16, 29, 48, 41), (17, 41, 47, 48)], 8, back=32, top=(18, 38, 46, 39)),
        # on two legs: the floor under it is the floor
        piece("dresser", [(49, 19, 72, 48)], 23, back=42, leave=HOUSE_FLOOR),
        stairwell("stairwell", 128, 144, 48, 29, 19, (128, 29, 144, 34), 16),
        # the back wall, a cell forward east of the step; the doorway is cut
        # out of it and it stands on its foot. Its west edge, the outline
        # down the step's corner, is its face's full height: drawn only from
        # row 32 down, the face had a slit a pixel wide above, and from
        # below 45 degrees the void behind the wall showed through it
        piece("wall_e", [(112, 16, 113, 48), (113, 16, 176, 29), (113, 29, 128, 48),
                         (144, 29, 176, 48)], 32, fill=16, foot=48,
              side=(156, 16, 172, 48), walls=[((112, 32), (112, 48))]),
        piece("wall", [(0, 0, 113, 32), (113, 0, 117, 16)], 32, fill=16, foot=32,
              side=(72, 0, 80, 32)),
        piece("side_w", [], 32, side=(72, 0, 80, 32), walls=[((0, 144), (0, 32))]),
        piece("side_e", [], 32, side=(156, 16, 172, 48), walls=[((176, 48), (176, 144))]),
    ])


def may_1f():
    rug = PINK_RUG
    # the chairs' seats are Brendan's, pixel for pixel on another rug, and
    # stand here as they are; their backs are drawn over the rug
    return [pc for pc in (house_chair("chair_nw", (81, 96, 85, 112), (85, 100, 95, 112), rug)
                          + house_chair("chair_sw", (81, 112, 85, 128), (85, 116, 95, 128), rug)
                          + house_chair("chair_ne", (139, 96, 143, 112), (129, 100, 139, 112), rug)
                          + house_chair("chair_se", (139, 112, 143, 128), (129, 116, 139, 128), rug))
            if pc["name"].endswith("_back")] + [
        piece("table", [(98, 96, 126, 128)], 8, leave=rug, solid=True),
        # the television and its cabinet, the fridge, the tap and the sink
        # are Brendan's, pixel for pixel, and stand here as they are
        # (gen_voxel_buildings.reuse_pieces)
        piece("dresser", [(105, 19, 128, 48)], 23, back=42, leave=HOUSE_FLOOR),
        stairwell("stairwell", 32, 48, 48, 29, 19, (32, 29, 48, 34), 16),
        piece("wall_w", [(63, 16, 64, 48), (0, 16, 63, 29), (0, 29, 32, 48),
                         (48, 29, 63, 48)], 32, fill=16, foot=48,
              side=(4, 16, 20, 48), walls=[((64, 48), (64, 32))]),
        piece("wall", [(63, 0, 176, 32), (59, 0, 63, 16)], 32, fill=16, foot=32,
              side=(96, 0, 104, 32)),
        piece("side_w", [], 32, side=(4, 16, 20, 48), walls=[((0, 144), (0, 48))]),
        piece("side_e", [], 32, side=(96, 0, 104, 32), walls=[((176, 32), (176, 144))]),
    ]


def brendan_2f():
    return [
        # the desk: the computer and an open book on it, the chair before it
        piece("chair_back", [(1, 33, 5, 48)], 9, leave=HOUSE_FLOOR, solid=True),
        piece("chair_seat", [(5, 36, 14, 48)], 5, leave=HOUSE_FLOOR, solid=True),
        piece("pc", [(1, 9, 16, 30)], 16, base=9, leave=HOUSE_WALL, back=32),
        # lying open: its pages its top, their edge its front, a pixel thick
        piece("book", [(18, 24, 30, 31)], 1, base=9),
        piece("desk", [(0, 20, 32, 40)], 9, leave=HOUSE_FLOOR, against=32),
        # the games console and its pad; the television is the ground
        # floor's, tile for tile (gen_voxel_buildings.reuse_pieces)
        piece("console", [(51, 24, 64, 40)], 9, leave=HOUSE_WALL + HOUSE_FLOOR, back=32),
        # the bed: the pillow lying on the mattress, and behind it the head,
        # an open frame the floor is seen through
        piece("pillow", [(18, 66, 30, 71)], 3, base=7, leave=HOUSE_FLOOR),
        piece("bed_head", [(12, 62, 36, 70)], 7, base=7, leave=HOUSE_FLOOR, through=True),
        piece("bed", [(12, 70, 36, 93)], 7, leave=HOUSE_FLOOR, solid=True, claim=[(12, 70, 36, 72)]),
        stairwell("stairwell", 112, 128, 32, 13, 19, (113, 14, 127, 26), 0),
        piece("wall", [(0, 0, 112, 32), (112, 0, 128, 13), (128, 0, 144, 32)], 32, fill=16,
              foot=32, side=(96, 0, 104, 32)),
        piece("side_w", [], 32, side=(96, 0, 104, 32), walls=[((0, 128), (0, 32))]),
        piece("side_e", [], 32, side=(96, 0, 104, 32), walls=[((144, 32), (144, 128))]),
    ]


def may_2f():
    return [
        piece("chair_back", [(139, 33, 143, 48)], 9, leave=HOUSE_FLOOR, solid=True),
        piece("chair_seat", [(130, 36, 139, 48)], 5, leave=HOUSE_FLOOR, solid=True),
        piece("pc", [(128, 9, 143, 30)], 16, base=9, leave=HOUSE_WALL, back=32),
        piece("book", [(114, 24, 126, 31)], 1, base=9),
        piece("desk", [(112, 20, 144, 40)], 9, leave=HOUSE_FLOOR, against=32),
        piece("console", [(83, 24, 98, 40)], 9, leave=HOUSE_WALL + HOUSE_FLOOR, back=32),
        # the television is Brendan's, tile for tile
        piece("pillow", [(114, 66, 126, 71)], 3, base=7, leave=HOUSE_FLOOR),
        piece("bed_head", [(108, 62, 132, 70)], 7, base=7, leave=HOUSE_FLOOR, through=True),
        piece("bed", [(108, 70, 132, 93)], 7, leave=HOUSE_FLOOR, solid=True,
              claim=[(108, 70, 132, 72)]),
        stairwell("stairwell", 16, 32, 32, 13, 19, (17, 14, 31, 26), 0),
        piece("wall", [(0, 0, 16, 32), (16, 0, 32, 13), (32, 0, 144, 32)], 32, fill=16,
              foot=32, side=(40, 0, 48, 32)),
        piece("side_w", [], 32, side=(40, 0, 48, 32), walls=[((0, 128), (0, 32))]),
        piece("side_e", [], 32, side=(40, 0, 48, 32), walls=[((144, 32), (144, 128))]),
    ]


# ── The two houses most of Hoenn lives in ─────────────────────────────────
#
# LAYOUT_HOUSE1 and LAYOUT_HOUSE2 (Oldale's two houses, and nineteen more
# maps'): a glass case and a chest of drawers or a kitchen along the back
# wall, a table and its chairs in the room, potted plants. Built like
# Littleroot's: furniture against the back wall stands on the wall's row of
# collision; what the second house repeats of the first tile for tile (the
# glass case, the plant in the corner) is the first's model
# (gen_voxel_buildings.reuse_pieces).

GENERIC_WALL = ("d5d5b4", "b4b4a4", "ffffff", "629c8b")   # plaster, trim, baseboard
GENERIC_FLOOR = ("ded552", "bdb431", "8b8b8b", "9c9410")  # the planks, their shade
GENERIC_RUG = ("ffcd8b", "f6f6a4")
GENERIC_TABLE = ("fff683", "bdac52")


def potted_plant(x, y):
    """The potted plant's outline, its crown's top-left at (x, y): the
    lines between the planks are drawn in the pot's own outline colour, so
    the shape follows the crown, the stem and the pot instead of a box."""
    return [(x, y, x + 16, y + 13), (x + 2, y + 13, x + 14, y + 14),
            (x + 3, y + 14, x + 13, y + 15), (x + 5, y + 15, x + 11, y + 16),
            (x + 6, y + 16, x + 10, y + 18), (x + 4, y + 18, x + 12, y + 19),
            (x + 3, y + 19, x + 13, y + 20), (x + 2, y + 20, x + 14, y + 30),
            (x + 4, y + 30, x + 12, y + 31)]


def house1():
    rug = GENERIC_RUG
    return (house_chair("chair_n", (74, 64, 78, 80), (65, 68, 74, 80), rug)
            + house_chair("chair_s", (74, 80, 78, 96), (65, 84, 74, 96), rug) + [
        # the teapot on the table
        piece("teapot", [(49, 72, 59, 80)], 5, base=11, leave=GENERIC_TABLE),
        # what the teapot hides of the table top is the table's own pixels
        piece("table", [(34, 64, 62, 97)], 11, leave=rug, solid=True, fill=1, foot=97),
        piece("plant_se", potted_plant(144, 113), 31, leave=GENERIC_FLOOR, card=True),
        piece("plant_1", potted_plant(128, 17), 31, leave=GENERIC_WALL + GENERIC_FLOOR, card=True),
        piece("plant_2", potted_plant(144, 17), 31, leave=GENERIC_WALL + GENERIC_FLOOR, card=True),
        # the glass case and the chest of drawers against the back wall
        piece("case", [(0, 12, 32, 40)], 20, leave=GENERIC_WALL + GENERIC_FLOOR, back=32),
        # on two feet a pixel in from its ends, the floor's shade between; the
        # line under them is the planks', drawn in its outline's colour
        piece("drawers", [(33, 11, 57, 39)], 21, leave=GENERIC_WALL + GENERIC_FLOOR, back=32,
              side="plain"),
        piece("wall", [(0, 0, 160, 32)], 32, fill=16, foot=32, side=(64, 0, 80, 32)),
        piece("side_w", [], 32, side=(64, 0, 80, 32), walls=[((0, 144), (0, 32))]),
        piece("side_e", [], 32, side=(64, 0, 80, 32), walls=[((160, 32), (160, 144))]),
    ])


def house2():
    fl = GENERIC_FLOOR
    # the east chairs' seats and the plant are the first house's, pixel for
    # pixel on another floor, and stand here as they are
    return (house_chair("chair_nw", (66, 64, 70, 80), (70, 68, 79, 80), fl)
            + house_chair("chair_sw", (66, 80, 70, 96), (70, 84, 79, 96), fl)
            + [piece("chair_ne_back", [(122, 64, 126, 80)], 9, leave=fl, solid=True),
               piece("chair_se_back", [(122, 80, 126, 96)], 9, leave=fl, solid=True)] + [
        # on its legs: the line under them is the planks', darkened
        piece("table", [(82, 64, 110, 95)], 9, leave=fl, solid=True),
        # the television on its glass-doored stand: Littleroot's set, whose
        # tile this is, on a stand a row shorter. Its doors' teal is the
        # baseboard's, and its own; its sides the set's casing over the
        # stand's middle post
        piece("tv", [(32, 13, 48, 39)], 19, leave=GENERIC_WALL[:3] + fl, back=32,
              top=(36, 14, 46, 15), side=[(32, 20, 34, 31), (39, 31, 41, 39)]),
        # the kitchen: a cupboard, the sink and hob, the fridge
        piece("tap", [(133, 16, 142, 25)], 8, base=8, leave=GENERIC_WALL[:2]),
        piece("cupboard", [(112, 17, 128, 40)], 16, leave=GENERIC_WALL + fl, back=32),
        piece("sink", [(128, 21, 160, 40)], 8, leave=GENERIC_WALL + fl, against=32),
        piece("fridge", [(160, 10, 176, 40)], 24, leave=GENERIC_WALL + fl, back=32),
        piece("wall", [(0, 0, 176, 32)], 32, fill=16, foot=32, side=(48, 0, 64, 32)),
        piece("side_w", [], 32, side=(48, 0, 64, 32), walls=[((0, 128), (0, 32))]),
        piece("side_e", [], 32, side=(48, 0, 64, 32), walls=[((176, 32), (176, 128))]),
    ])


def house_with_bed():
    """LAYOUT_HOUSE_WITH_BED (Petalburg's second house, and two more maps'):
    the two houses' room with a bed in it, two chests and a bookcase along
    the back wall, a table and two chairs."""
    fl = GENERIC_FLOOR
    # the chairs and the chest of drawers are the second house's, pixel for
    # pixel, and stand here as they are (gen_voxel_buildings.reuse_pieces)
    return [
        # on its legs; its cloth's stripes run front to back, so its sides
        # are the cloth's edge, not its front's stripes again
        piece("table", [(128, 64, 158, 95)], 9, leave=fl, solid=True, side="plain"),
        # the bed: the pillow lying on the mattress, and behind it the head,
        # an open iron frame the floor is seen through
        piece("pillow", [(10, 53, 22, 58)], 3, base=7, leave=fl),
        piece("bed_head", [(4, 48, 28, 56)], 7, base=7, leave=fl, through=True, side="plain"),
        piece("bed", [(4, 56, 28, 80)], 7, leave=fl, solid=True),
        # along the back wall: the green chest, the bookcase
        piece("chest", [(16, 15, 32, 40)], 17, leave=GENERIC_WALL + fl, back=32),
        # its frame's teal is the baseboard's
        piece("bookcase", [(128, 10, 160, 39)], 21, leave=GENERIC_WALL[:3] + fl, back=32),
        piece("wall", [(0, 0, 160, 32)], 32, fill=16, foot=32, side=(64, 0, 80, 32)),
        piece("side_w", [], 32, side=(64, 0, 80, 32), walls=[((0, 128), (0, 32))]),
        piece("side_e", [], 32, side=(64, 0, 80, 32), walls=[((160, 32), (160, 128))]),
    ]


def petalburg_gym():
    """Petalburg's gym: nine rooms one under another in the black between
    them, each nine cells wide and eight deep under a back wall two cells
    tall, its doors and its plate drawn on the wall's face. Back walls only:
    the drawing has no side walls, and a pair made for every room (as the
    houses have, to close them from the console's camera) puts the layout's
    page past the console's 512x512 - four rooms' fit, and four closed rooms
    of nine would read as a fault."""
    side = (16, 208, 48, 240)       # a stretch of the second room's panelling
    return [piece("wall_%d" % room, [(0, y, 144, y + 32)], 32, fill=16, foot=y + 32, side=side)
            for room, y in ((room, room * 13 * 16) for room in range(9))]


# ── The other houses of the general indoor tileset ────────────────────────
#
# Rooms no one has modelled piece by piece yet: a back wall two cells tall,
# blocked all across, over a rectangle of floor. Their walls stand - the back
# one, and where the floor runs to the room's edges the two sides the drawing
# has no pixel of - and their furniture stays drawn on the floor until the
# room gets pieces of its own. Each: its layout, its size in cells, its floor,
# the wall's cell the sides are dressed with (bare panelling: the pair of
# metatiles the wall repeats most), and whether it has side walls.

PLAIN_ROOMS = [
    ("LAYOUT_HOUSE3", 10, 8, 0x229, 1, True),
    ("LAYOUT_DEWFORD_TOWN_HALL", 17, 9, 0x223, 3, True),
    ("LAYOUT_HOUSE4", 10, 9, 0x229, 3, True),
    ("LAYOUT_LILYCOVE_CITY_HOUSE2", 8, 8, 0x229, 3, True),
    ("LAYOUT_VERDANTURF_TOWN_WANDAS_HOUSE", 17, 8, 0x223, 5, True),
    ("LAYOUT_PACIFIDLOG_TOWN_HOUSE1", 10, 9, 0x3A3, 4, True),
    ("LAYOUT_PACIFIDLOG_TOWN_HOUSE2", 10, 9, 0x3A3, 5, True),
    ("LAYOUT_RUSTBORO_CITY_HOUSE", 12, 9, 0x32C, 3, True),
    ("LAYOUT_RUSTBORO_CITY_HOUSE1", 13, 8, 0x32C, 4, True),
    ("LAYOUT_RUSTBORO_CITY_CUTTERS_HOUSE", 11, 9, 0x32C, 4, True),
    ("LAYOUT_FORTREE_CITY_HOUSE1", 8, 6, 0x3D9, 2, True),
    ("LAYOUT_FORTREE_CITY_HOUSE2", 8, 6, 0x3D9, 1, True),
    ("LAYOUT_ROUTE116_TUNNELERS_REST_HOUSE", 10, 9, 0x229, 3, True),
    ("LAYOUT_ROUTE110_TRICK_HOUSE_ENTRANCE", 12, 8, 0x229, 1, True),
    ("LAYOUT_FORTREE_CITY_DECORATION_SHOP", 8, 6, 0x3D9, 1, False),
    ("LAYOUT_SOOTOPOLIS_CITY_LOTAD_AND_SEEDOT_HOUSE", 8, 7, 0x3FE, 2, True),
    ("LAYOUT_SOOTOPOLIS_CITY_HOUSE1", 8, 7, 0x3FE, 1, True),
    ("LAYOUT_SOOTOPOLIS_CITY_HOUSE2", 8, 7, 0x3FE, 4, True),
    ("LAYOUT_SOOTOPOLIS_CITY_HOUSE3", 8, 7, 0x3FE, 3, True),
    ("LAYOUT_MOSSDEEP_CITY_STEVENS_HOUSE", 11, 8, 0x223, 3, True),
]


def plain_room(width, height, plain_x, sides):
    w, h = width * 16, height * 16
    side = (plain_x * 16, 0, plain_x * 16 + 16, 32)
    pieces = [piece("wall", [(0, 0, w, 32)], 32, fill=16, foot=32, side=side)]
    if sides:
        pieces += [piece("side_w", [], 32, side=side, walls=[((0, h), (0, 32))]),
                   piece("side_e", [], 32, side=side, walls=[((w, 32), (w, h))])]
    return pieces


# ── Mr. Briney's house ────────────────────────────────────────────────────
#
# A room of tatami by the dock on Route 104: a chest of drawers, a small one
# and a glass case along the back wall, three jars in the corner, a low table.

BRINEY_FLOOR = ("c5ffac", "a4cd6a", "83ac4a", "7b7b83", "6a8b31", "414a6a")
BRINEY_WALL = ("d5b483", "ffffff", "d5c54a", "947329", "ac8b39", "629c8b")


def briney_room():
    # the furniture's outline is the tatami's dark border line, and its wood
    # the wall's pillars' and baseboard's: each piece leaves only the colours
    # of the room that are round it, the plaster and the tatami
    fl = BRINEY_FLOOR
    mat = tuple(c for c in fl if c != "414a6a")
    plaster = ("d5b483", "d5c54a")
    side = (48, 0, 64, 32)
    return [
        # low, on four short legs: its top's edge band and the dark under it
        # its front
        piece("table", [(114, 67, 142, 95)], 7, leave=mat, solid=True),
        # turned, their mouths open on top: belly, shoulder and neck as drawn
        piece("jar_s", [(1, 65, 15, 79)], 7, leave=mat, round="lathe"),
        piece("jar_w", [(1, 49, 15, 63)], 7, leave=mat, round="lathe"),
        piece("jar_e", [(17, 49, 31, 63)], 7, leave=mat, round="lathe"),
        piece("drawers", [(17, 10, 40, 40)], 22, leave=plaster + ("947329",) + mat, back=32),
        piece("chest", [(128, 16, 144, 45)], 16, leave=plaster + mat, back=32, side="plain"),
        piece("case", [(144, 12, 176, 45)], 20, leave=plaster + mat, back=32),
        piece("wall", [(0, 0, 192, 32)], 32, fill=16, foot=32, side=side),
        piece("side_w", [], 32, side=side, walls=[((0, 144), (0, 32))]),
        piece("side_e", [], 32, side=side, walls=[((192, 32), (192, 144))]),
    ]


# ── Rooms with a flight of stairs in the back wall ────────────────────────
#
# Rustboro's flats (and whatever else is built like them): the plain room's
# walls, with a doorway a cell wide in the back wall for every flight. Like
# Littleroot's: the flight is drawn inside the doorway as the GBA sees it from
# above, so it lies on the floor of a recess one cell deep, with the doorway's
# sides and back round it - a way out of the room, not a picture of stairs
# flat on the floor. Each: its layout, its size in cells, its floor, the
# wall's cell the sides are dressed with, and the doorways' columns - and, in
# Rustboro's second block of flats, where the wall that parts the room in two
# runs down from the back wall (its left edge), with the low wall the same
# three floors have along the front of the east half.

FLAT_FLOOR = ("d5d5b4", "f6f6a4", "b4b4a4", "8b8b8b", "ded552")

STAIR_ROOMS = [
    ("LAYOUT_RUSTBORO_CITY_FLAT1_1F", 14, 8, 0x32C, 5, (2,)),
    ("LAYOUT_RUSTBORO_CITY_FLAT1_2F", 14, 8, 0x32C, 5, (2,)),
    ("LAYOUT_RUSTBORO_CITY_FLAT2_1F", 14, 9, 0x32C, 1, (3,), 80),
    ("LAYOUT_RUSTBORO_CITY_FLAT2_2F", 14, 9, 0x32C, 9, (1, 3), 80),
    ("LAYOUT_RUSTBORO_CITY_FLAT2_3F", 14, 9, 0x32C, 3, (1,), 64),
    ("LAYOUT_LILYCOVE_CITY_COVE_LILY_MOTEL_1F", 12, 9, 0x229, 4, (2,)),
    ("LAYOUT_LILYCOVE_CITY_COVE_LILY_MOTEL_2F", 12, 9, 0x229, 4, (2,)),
    ("LAYOUT_ROUTE114_FOSSIL_MANIACS_HOUSE", 10, 8, 0x229, 2, (4,)),
    ("LAYOUT_ROUTE110_TRICK_HOUSE_END", 12, 8, 0x229, 6, (2, 10)),
    # the Devon Corporation's upper floors
    ("LAYOUT_RUSTBORO_CITY_DEVON_CORP_2F", 19, 9, 0x380, 4, (2, 14)),
    ("LAYOUT_RUSTBORO_CITY_DEVON_CORP_3F", 19, 9, 0x380, 4, (2,)),
]


def stair_room(width, height, plain_x, doors, partition=None):
    w, h = width * 16, height * 16
    side = (plain_x * 16, 0, plain_x * 16 + 16, 32)
    wall, pieces, x = [], [], 0
    if partition is not None:
        # seen from above: the white of their tops, and a face at the end
        pieces += [
            piece("divider", [(128, 90, 224, 127)], 27, solid=True, leave=FLAT_FLOOR),
            piece("partition", [(partition, 0, partition + 16, 127)], 29, solid=True),
        ]
    for door in sorted(doors):
        d = door * 16
        wall += [(x, 0, d, 32), (d, 0, d + 16, 13)]
        # the doorway: 19 pixels tall under the 13 of wall over it, its
        # sides the dark of the flight's own well
        pieces.append(stairwell("stairwell_%d" % door, d, d + 16, 32, 13, 19,
                                (d + 1, 14, d + 15, 26), 0))
        x = d + 16
    wall.append((x, 0, w, 32))
    return pieces + [
        piece("wall", wall, 32, fill=16, foot=32, side=side),
        piece("side_w", [], 32, side=side, walls=[((0, h), (0, 32))]),
        piece("side_e", [], 32, side=side, walls=[((w, 32), (w, h))]),
    ]


# ── Rustboro's Pokemon school ─────────────────────────────────────────────
#
# One classroom. Its back wall is drawn from 8 rows down the first cell (the
# black over it is nothing) to its foot 36 rows down, the blackboard and the
# windows on its face; the pillars' feet and the teacher's step, drawn a few
# rows further, stay on the floor.

def school_room():
    side = (176, 8, 192, 36)
    return [
        piece("wall", [(0, 8, 192, 36)], 28, fill=16, foot=36, side=side),
        piece("side_w", [], 28, side=side, walls=[((0, 176), (0, 36))]),
        piece("side_e", [], 28, side=side, walls=[((192, 36), (192, 176))]),
    ]


# ── The Pretty Petal flower shop ──────────────────────────────────────────
#
# Route 104's shop: planters of flowers and racks of potted plants about the
# floor, two tables of pots and a shelf along the back wall. The counter, the
# stools and the vases stay drawn on the floor. A potted plant on a stand or
# a table is a card lifted onto it, as the plants on the floor are cards.

SHOP_FLOOR = ("eef6ff", "d5d5e6", "b4acb4")
SHOP_WALL = ("bdbd94", "e6e68b", "ffffbd", "629c62", "9cd59c", "4a7b41")


def flower_shop_room():
    fl = SHOP_FLOOR
    side = (48, 0, 64, 32)

    def planter(name, x, y0, y1):
        return piece(name, [(x, y0, x + 16, y1)], 10, leave=fl, solid=True)

    def rack(name, x):
        # a low stand of green slats on four legs, the floor seen between
        # them, three potted plants in a row along it, each pot's rim
        # twelve rows before the one in front:
        # each plant its crown, the leaf tips either side of the pot behind
        # it, its rim and its pot. Where a crown is drawn over the pot behind
        # it, that pot's; under each pot's foot, the stand's top
        def plant(part, r, crown, foot):
            return piece("%s_%s" % (name, part),
                         crown + [(x + 2, r - 3, x + 14, r), (x + 3, r, x + 13, r + 3),
                                  (x + 4, r + 3, x + 12, r + 8)] + foot,
                         16, base=8, leave=fl, card=True)

        def tips(r):
            return [(x + 3, r - 7, x + 4, r - 3), (x + 12, r - 7, x + 13, r - 3)]
        return [
            plant("plant_s", 111, tips(111), [(x + 6, 119, x + 10, 120)]),
            plant("plant_c", 99, tips(99), []),
            plant("plant_n", 87, [(x + 2, 80, x + 14, 84)], []),
            piece(name, [(x, 87, x + 16, 128)], 8, leave=fl, side="plain", through=True),
        ]

    def back_table(name, x):
        # a low table on four legs against the wall, three cacti in pots
        # along its back and two plants before them, each its own card on
        # it: white flowers in a grey pot, red in a yellow one. What a plant
        # is drawn over of a pot behind it is the plant's
        def card(part, shape, leave=fl):
            return piece("%s_%s" % (name, part), shape, 12, base=7, leave=leave, card=True)

        white = [(x + 6, 27, x + 7, 28), (x + 11, 27, x + 12, 28), (x + 5, 28, x + 13, 29),
                 (x + 3, 29, x + 15, 30), (x + 2, 30, x + 16, 33), (x + 3, 33, x + 15, 35),
                 (x + 4, 35, x + 14, 37), (x + 5, 37, x + 13, 38), (x + 6, 38, x + 12, 39),
                 (x + 7, 39, x + 11, 40)]
        red = [(x + 20, 27, x + 21, 28), (x + 25, 27, x + 26, 28), (x + 19, 28, x + 27, 29),
               (x + 17, 29, x + 29, 30), (x + 16, 30, x + 30, 33), (x + 17, 33, x + 29, 35),
               (x + 18, 35, x + 28, 37), (x + 19, 37, x + 27, 38), (x + 20, 38, x + 26, 39),
               (x + 21, 39, x + 25, 40)]
        return [card("white", white), card("red", red)] + [
            card("cactus_%d" % (k + 1), [(x + 3 + 9 * k, 19, x + 11 + 9 * k, 29)],
                 fl + ("e6e68b",)) for k in range(3)] + [
            piece(name, [(x, 25, x + 32, 48)], 7, leave=fl, back=32, side="plain"),
        ]

    return [
        planter("planter_sw", 0, 112, 144),
        planter("planter_w", 80, 96, 128), planter("planter_c", 128, 96, 128),
        planter("planter_e1", 192, 80, 128), planter("planter_e2", 208, 80, 128),
    ] + rack("rack_1", 96) + rack("rack_2", 112) + rack("rack_e", 224) + [
        planter("planter_n1", 176, 48, 64), planter("planter_n2", 192, 48, 64),
        piece("shelf", [(112, 24, 152, 56)], 18, leave=SHOP_WALL + fl, back=42),
    ] + back_table("table_w", 160) + back_table("table_e", 192) + [
        piece("wall", [(0, 0, 240, 32)], 32, fill=16, foot=32, side=side),
        piece("side_w", [], 32, side=side, walls=[((0, 144), (0, 32))]),
        piece("side_e", [], 32, side=side, walls=[((240, 32), (240, 144))]),
    ]


# ── Dewford's gym ─────────────────────────────────────────────────────────
#
# A maze cut in rock two cells tall: every blocked cell is rock, its top or
# the two rows of its face, so each column's run of blocked cells is a block
# drawn from the run's first row to its foot. Runs side by side with the same
# rows are one rectangle (cells: x0, y0, x1, y1), front first. The leader's
# alcove and the statues, blocked too, are not rock and stay flat.

DEWFORD_GYM_BLOCKS = [
    (0, 0, 1, 28), (1, 18, 2, 28), (2, 23, 3, 28), (3, 24, 4, 28), (8, 23, 12, 28),
    (13, 23, 16, 28), (16, 19, 17, 28), (17, 0, 18, 28), (3, 17, 6, 22), (6, 15, 8, 22),
    (9, 15, 10, 21), (10, 16, 11, 21), (12, 16, 13, 19), (13, 9, 14, 19), (8, 15, 9, 18),
    (14, 13, 16, 18), (3, 11, 5, 16), (1, 12, 2, 15), (5, 7, 6, 14), (6, 6, 7, 14),
    (10, 9, 12, 14), (7, 9, 9, 13), (14, 7, 16, 12), (1, 6, 2, 10), (3, 7, 5, 10),
    (16, 0, 17, 9), (13, 0, 15, 6), (7, 0, 10, 5), (12, 0, 13, 5), (11, 0, 12, 4),
    (2, 0, 3, 3), (5, 0, 6, 3), (10, 0, 11, 3), (15, 0, 16, 3),
]
DEWFORD_GYM_FLOOR = [0x201, 0x202, 0x203, 0x205, 0x206, 0x209, 0x20A, 0x20B, 0x20D, 0x211,
                     0x212, 0x213, 0x215, 0x216, 0x218, 0x21A, 0x21B, 0x222, 0x223]


def dewford_gym():
    side = (128, 416, 144, 448)     # a stretch of the front wall's face
    # The rock the room is cut in, down its west and east edges, is a wall's
    # face each side and not a block: a block's art is its drawing three times
    # over (its hidden top and sides), and those two columns' would put the
    # layout's page past the console's 512x512.
    return [piece("block_%d" % i, [(x0 * 16, y0 * 16, x1 * 16, y1 * 16)], 32)
            for i, (x0, y0, x1, y1) in enumerate(DEWFORD_GYM_BLOCKS)
            if x0 not in (0, 17)] + [
        piece("edge_w", [], 32, side=side, walls=[((16, 448), (16, 32))]),
        piece("edge_e", [], 32, side=side, walls=[((272, 32), (272, 448))]),
    ]


# ── Professor Birch's lab ─────────────────────────────────────────────────
#
# Desks and a bookcase along the back wall, standing on its row of collision
# (so no deeper than the drawing puts them in front of it); in the room, the
# boxes, book stacks, bookcases and desks run back as far as the cells they
# block, and the machine is read column by column off its round drawing.

LAB_SHADOW = ("b4b4a4", "949494")      # the floor's shade, and its grid in it
LAB_FLOOR = ("d5d5b4",)                 # the floor itself, where it shows beside a stack


def lab_machine():
    """The machine under the lab's dome in plan: an octagon whose edges
    pass between the drawing's pixels - every centre of one of its pixels
    inside, every one of the floor's out - so that at 45 degrees no face
    behind shows at a corner. The back corners follow the drawn top's rows
    (one wider the first row, then one a row, then the drawing's kink
    where two rows end alike), the front ones the drawn foot, a slant half
    a pixel out of its pixels' corners; symmetric about x 176."""
    left = [(160, 120.5), (160, 113.5), (161, 112.5), (161, 111.5), (165, 107.5), (168, 106)]
    right = [(352 - x, z) for (x, z) in reversed(left)]
    return left + right + [(184.5, 128), (167.5, 128)]


def lab():
    sh = LAB_SHADOW
    return [
        piece("plant_s", [(48, 189, 64, 208)], 18, card=True),
        # the desk down the west side: its computer at the far end, the book
        # stacks against the wall, the chair
        # seen from behind: its back stands at the back of its seat
        piece("chair_sw_back", [(34, 160, 47, 167)], 7, base=4, leave=sh, solid=True),
        piece("chair_sw_seat", [(34, 167, 47, 176)], 4, leave=sh, solid=True),
        piece("books_sw1", [(1, 160, 16, 176)], 9, leave=sh + LAB_FLOOR, back=160),
        piece("books_sw2", [(1, 176, 16, 192)], 9, leave=sh + LAB_FLOOR, back=176),
        # the computer at its north end, inside its own outline: the desk's
        # rails run on beside it, its top back to row 148 under it
        piece("pc_sw", [(18, 145, 30, 160)], 8, base=8, solid=True),
        piece("desk_sw", [(16, 160, 32, 192), (16, 148, 18, 160), (30, 148, 32, 160)], 8,
              leave=sh, back=156),
        # and its twin down the east side
        piece("plant_se", [(192, 141, 208, 161)], 18, card=True),
        piece("boxes_se", [(192, 164, 208, 192)], 20, leave=sh, back=176),
        piece("chair_se_back", [(161, 160, 165, 176)], 9, leave=sh, solid=True),
        piece("chair_se_seat", [(165, 164, 176, 176)], 5, leave=sh, solid=True),
        piece("pc_se", [(178, 145, 190, 160)], 8, base=8, solid=True),
        piece("desk_se", [(176, 160, 192, 192), (176, 148, 178, 160), (190, 148, 192, 160)], 8,
              leave=sh, back=156),
        # the machine, its dome, the two canisters beside it: a round glass
        # dome on a machine whose sides are its casing, its shadow the floor's
        piece("dome", [("ellipse", 176, 103.5, 8.5, 7.5)], 5, base=10, round="dome"),
        # an octagon, its corners cut on the slant as drawn (lab_machine):
        # a box, its sides square to the corners' slants, stood a dark slab
        # beside it
        piece("machine", [(160, 95, 192, 128)], 10, leave=sh, side=(172, 118, 180, 128),
              plan=lab_machine()),
        piece("canisters", [(192, 109, 208, 128)], 16, leave=sh, back=112),
        # the two bookcases in the room, a box on each
        piece("box_w1", [(8, 82, 23, 96)], 6, base=21, solid=True),
        piece("box_w2", [(40, 82, 55, 96)], 6, base=21, solid=True),
        piece("bookcase_w1", [(0, 85, 32, 128)], 21, back=96),
        piece("bookcase_w2", [(32, 85, 64, 128)], 21, back=96),
        # boxes and book stacks in the north-east corner
        piece("boxes_ne1", [(144, 56, 160, 80)], 8, leave=sh),
        piece("boxes_ne2", [(160, 52, 176, 80)], 18, leave=sh, back=64),
        piece("books_ne1", [(177, 32, 193, 48)], 8, leave=sh, back=32),
        piece("books_ne2", [(193, 32, 208, 48)], 8, leave=sh, back=32),
        # its shape round the floor its top leaves beside it, under the next stack
        piece("books_ne3", [(193, 48, 205, 53), (193, 53, 208, 64)], 8, leave=sh + LAB_FLOOR,
              back=48),
        piece("chair_n_back", [(75, 48, 79, 64)], 9, leave=sh, solid=True),
        piece("chair_n_seat", [(64, 52, 75, 64)], 5, leave=sh, solid=True),
        piece("boxes_w", [(0, 41, 16, 64)], 7, leave=sh),
        # along the back wall
        piece("plant_nw", [(32, 29, 48, 48)], 18, card=True),
        # the computer: a tower and a monitor whose white tops are drawn
        # above their fronts (the drive bays, the screen from its bezel down)
        piece("computer", [(49, 10, 72, 30)], 14, base=8, back=32),
        piece("desk_pc", [(48, 20, 80, 39)], 8, leave=sh, against=32),
        # books lying on the desks: a cover on top and the pages' edges
        # below it - a big red one, a stack of two blue ones, one askew.
        # Each inside its own outline: the line down the desks' join and
        # the desk's beige below a book's corner are the desks'. Drawn
        # over the desk's rows the wall hides, so is a book's back
        piece("book_red", [(117, 19, 127, 30)], 3, base=8, against=32),
        piece("book_open", [(131, 19, 147, 31)], 3, base=8, against=32),
        piece("binder", [(147, 17, 156, 30)], 5, base=8, against=32),
        piece("desk_a", [(96, 20, 128, 39)], 8, leave=sh, against=32),
        piece("desk_b", [(128, 20, 160, 39)], 8, leave=sh, against=32),
        piece("bookcase_nw", [(0, 8, 32, 40)], 21, against=32),
        piece("wall", [(0, 0, 208, 32)], 32, fill=16, foot=32, side=(80, 0, 96, 32)),
        piece("side_w", [], 32, side=(80, 0, 96, 32), walls=[((0, 208), (0, 32))]),
        piece("side_e", [], 32, side=(80, 0, 96, 32), walls=[((208, 32), (208, 208))]),
    ]


# ── Rustboro's gym ────────────────────────────────────────────────────────
#
# A maze of low stone walls on a tiled floor: each block stands on the cells
# it blocks, its top drawn from 8 rows into the cell north of them and its
# front the last 10 rows of its own. The floor's shadows are floor. At the
# back, the gym's crenellated wall, one cell deeper at either end.

GYM_WALL_SIDE = (32, 7, 48, 32)
GYM_FLOOR = ("bdbdac",)             # the floor's shade round a statue's foot     # a stretch of the back wall without the emblem


def gym_block(name, rects):
    """A maze block: `rects` in cells (x0, y0, x1, y1), each drawn from 8 rows
    north of its cells to its foot."""
    return piece(name, [(x0 * 16, y0 * 16 - 8, x1 * 16, y1 * 16) for (x0, y0, x1, y1) in rects],
                 10, side=(56, 118, 72, 128))


def gym_statue(x):
    """A gym statue's outline, its cell's left edge at x: its pedestal's
    bottom corners are rounded off, the floor showing there."""
    return [(x, 272, x + 16, 302), (x + 1, 302, x + 15, 303), (x + 2, 303, x + 14, 304)]


def rustboro_gym():
    return [
        # the statues by the door: a ball on a pedestal. Every gym has them,
        # on its own floor: they stand in each as they are (reuse_pieces),
        # so they must not take the floor showing at their feet
        piece("statue_w_ball", [("ellipse", 40, 279, 7, 7)], 8, base=16),
        piece("statue_w", gym_statue(32), 16, back=288),
        piece("statue_e_ball", [("ellipse", 136, 279, 7, 7)], 8, base=16),
        piece("statue_e", gym_statue(128), 16, back=288),
        # the maze, front first
        gym_block("block_sw", [(2, 13, 4, 15), (2, 15, 5, 16)]),
        gym_block("block_s", [(6, 15, 8, 16)]),
        gym_block("block_w", [(4, 9, 5, 11), (2, 11, 5, 12)]),
        gym_block("block_c", [(6, 9, 8, 11)]),
        # the comb along the north and east, in three: a piece's art is its
        # whole rectangle, and one of 11 x 10 cells for this outline would
        # take a texture page of half a megabyte. Cut between columns, so no
        # column's run is split
        gym_block("block_e_arm", [(6, 12, 9, 13), (7, 13, 9, 14)]),
        gym_block("block_e", [(9, 6, 11, 16)]),
        gym_block("block_n", [(3, 6, 9, 8)]),
        gym_block("block_west", [(0, 6, 1, 16)]),
        # the back wall and its two deeper ends
        piece("wall_w", [(0, 0, 16, 48)], 25, fill=16, foot=48, side=GYM_WALL_SIDE),
        piece("wall_e", [(160, 0, 176, 48)], 25, fill=16, foot=48, side=GYM_WALL_SIDE),
        piece("wall", [(16, 0, 160, 32)], 25, fill=16, foot=32, side=GYM_WALL_SIDE),
        # the side walls in two each: one the room's length would need a
        # texture page too tall to share VRAM with the city's
        piece("side_w", [], 25, side=GYM_WALL_SIDE, walls=[((0, 176), (0, 48))]),
        piece("side_w2", [], 25, side=GYM_WALL_SIDE, walls=[((0, 320), (0, 176))]),
        piece("side_e", [], 25, side=GYM_WALL_SIDE, walls=[((176, 48), (176, 176))]),
        piece("side_e2", [], 25, side=GYM_WALL_SIDE, walls=[((176, 176), (176, 320))]),
    ]


SPECS = [
    {
        "name": "littleroot_house_w",
        "layout": "LAYOUT_LITTLEROOT_TOWN",
        "rect": (2, 4, 5, 5),
        "ground": [GRASS],
        "parts": lambda: littleroot_house(8),
        "exact": HOUSE_EXACT,
    },
    {
        "name": "littleroot_house_e",
        "layout": "LAYOUT_LITTLEROOT_TOWN",
        "rect": (13, 4, 5, 5),
        "ground": [GRASS],
        "parts": lambda: littleroot_house(64),
        "exact": HOUSE_EXACT,
    },
    {
        "name": "littleroot_lab",
        "layout": "LAYOUT_LITTLEROOT_TOWN",
        "rect": (3, 12, 7, 5),
        "ground": [GRASS],
        "parts": littleroot_lab,
        "exact": LAB_EXACT,
    },
    {
        # The canonical copy: Oldale paints its path into the top row.
        "name": "pokemon_center",
        "layout": "LAYOUT_PETALBURG_CITY",
        "rect": (19, 13, 4, 4),
        "match_rows": (1, 4),
        "ground": [GRASS],
        "parts": lambda: center_or_mart((9, 16), crown=True),
        "exact": CROWN_EXACT,
    },
    {
        # Oldale's copy has a tree's crown over its top-right corner.
        "name": "poke_mart",
        "layout": "LAYOUT_MAUVILLE_CITY",
        "rect": (22, 11, 4, 4),
        "match_rows": (1, 4),
        "ground": [GRASS],
        "parts": lambda: center_or_mart((12, 16)),
        "exact": CENTER_EXACT,
    },
    {
        "name": "oldale_house",
        "layout": "LAYOUT_OLDALE_TOWN",
        "rect": (4, 4, 4, 4),
        "ground": [GRASS],
        "parts": oldale_house,
        "exact": OLDALE_HOUSE_EXACT,
    },
    {
        "name": "dewford_house",
        "layout": "LAYOUT_DEWFORD_TOWN",
        "rect": (16, 11, 4, 4),
        "match_rows": (1, 4),
        "ground": [0x124],
        "parts": lambda: dewford_house(64),
        "exact": kit_house_exact(64),
    },
    {
        "name": "dewford_hall",
        "layout": "LAYOUT_DEWFORD_TOWN",
        "rect": (1, 0, 5, 4),
        "ground": [0x124],
        "parts": lambda: dewford_house(80),
        "exact": kit_house_exact(80),
    },
    {
        "name": "dewford_gym_exterior",
        "layout": "LAYOUT_DEWFORD_TOWN",
        "rect": (5, 13, 6, 5),
        "ground": [0x124],
        "parts": gym,
        "exact": GYM_EXACT,
    },
    {
        "name": "briney_house",
        "layout": "LAYOUT_ROUTE104",
        "rect": (15, 47, 5, 4),
        "ground": [GRASS],
        "parts": briney_house,
        "exact": BRINEY_HOUSE_EXACT,
    },
    {
        "name": "flower_shop",
        "layout": "LAYOUT_ROUTE104",
        "rect": (3, 15, 6, 4),
        # the cobbled path round it
        "ground": [GRASS, 0x206, 0x207],
        "parts": flower_shop,
        "exact": FLOWER_SHOP_EXACT,
    },
    {
        "name": "kit_house_4",
        "layout": "LAYOUT_PETALBURG_CITY",
        "rect": (9, 16, 4, 4),
        "ground": [GRASS],
        "parts": lambda: kit_house(64),
        "exact": kit_house_exact(64),
    },
    {
        "name": "kit_house_5",
        "layout": "LAYOUT_PETALBURG_CITY",
        "rect": (5, 2, 5, 4),
        "ground": [GRASS],
        "parts": lambda: kit_house(80),
        "exact": kit_house_exact(80),
    },
    {
        # The kit house in Slateport's purple: the same rows, its own tiles.
        # The copy by the harbour (20, 41) is found from this one.
        "name": "slateport_house_4",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (4, 16, 4, 4),
        "ground": [GRASS],
        "keep": kit_house_keep(64),
        "patch_own": True,
        "parts": lambda: kit_house(64),
        "exact": kit_house_exact(64),
    },
    {
        # the same house five cells wide: Route 109's Seashore House, on sand
        "name": "slateport_house_5",
        "layout": "LAYOUT_ROUTE109",
        "rect": (10, 2, 5, 4),
        "ground": [0x124],
        "parts": lambda: kit_house(80),
        "exact": kit_house_exact(80),
    },
    {
        "name": "slateport_house_6",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (24, 41, 6, 4),
        "ground": [0x202],
        "keep": kit_house_keep(96),
        "patch_own": True,
        "parts": lambda: kit_house(96),
        "exact": kit_house_exact(96),
    },
    {
        # Slateport's awning over the way to Route 110; the market's gate is
        # the same awning over the market's paving (found from this one).
        # Lifted 12 so that people walk under it.
        "name": "slateport_awning",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (16, 7, 5, 3),
        "match_rows": (0, 2),
        "ground": [GRASS],
        "ground_px": True,
        # the market's gate stands over the market's path, not on the grass
        # round it: the path runs on under the roof, in its shade as the row
        # of the posts draws it (the roof's back rows show the path still in
        # the sun above it)
        "cell_ground_at": {("LAYOUT_SLATEPORT_CITY", 8, 30): {
            (1, 0): 0x210, (2, 0): 0x211, (3, 0): 0x212,
            (1, 1): 0x286, (2, 1): 0x285, (3, 1): 0x287,
            (1, 2): 0x286, (2, 2): 0x285, (3, 2): 0x287}},
        "parts": lambda: [Awning("awning", 8, 72, top_v=1, valance_v=24, post_v=32,
                                 foot_v=47, back=0, lift=12, repeat=(3, 11),
                                 posts=[(8, 14), (66, 72)], shaft=(34, 43), foot=(43, 47))],
        "exact": [(8, 3, 72, 32)],
    },
    {
        "name": "fan_club",
        # its projected shapes stand past its drawing: cut to it
        "clip_art": True,
        "layout": "LAYOUT_SLATEPORT_CITY",
        # with the row of cells under it, where the door posts' feet are
        # drawn: as the ground's they lay flat on the paving beside the posts
        "rect": (2, 22, 5, 6),
        "ground": [GRASS, 0x209, 0x218, 0x219],
        "patch_cells": [(i, 5) for i in range(5)],
        # of that row only the posts' feet are its own: the path's edge
        # drawn there is the map's (as the model's, nothing drew it)
        "keep": [(0, 0, 80, 80), (29, 80, 33, 96), (47, 80, 51, 96)],
        "parts": fan_club,
        "exact": FAN_CLUB_EXACT,
    },
    {
        "name": "museum",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (25, 7, 7, 6),
        "ground": [GRASS],
        # the trees' feet in its top row are not its own, nor the leaves and
        # grass beside its roof's rounded back corners (rows 2-7)
        "keep": [(6, 2, 106, 3), (5, 3, 107, 4), (4, 4, 108, 5), (3, 5, 109, 6), (2, 6, 110, 7),
                 (1, 7, 111, 8), (0, 8, 112, 96)],
        "parts": museum,
        "exact": MUSEUM_EXACT,
    },
    {
        "name": "contest_hall",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (28, 22, 6, 6),
        "ground": [GRASS],
        # the seawall drawn behind it is not its own
        "keep": [(0, 9, 96, 96)],
        "parts": contest_hall,
        "exact": CONTEST_EXACT,
    },
    {
        # The battle tent: a smooth dome and its door (battle_tent). The same
        # drawing stands in Verdanturf and Fallarbor in their own tiles.
        "name": "battle_tent",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (6, 6, 3),
        # the coarse grid misses 6 pixels of its outline at 45 degrees
        "tolerance": 6,
        "anywhere": True,
        # Verdanturf paints its cliff into the top row
        "match_rows": (1, 5),
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (8, 8, 5, 5),
        "ground": [GRASS],
        # on its lawn: the paving in front of it is commoner round it; and
        # every cell as the map paints it round the tent (its grass's dots)
        "ground_fixed": {"LAYOUT_SLATEPORT_CITY": GRASS},
        "patch_own": True,
        "ground_px": True,
        "parts_art": battle_tent,
        "exact": [(0, 0, 80, 80)],
    },
    {
        # Slateport's sailboat; the copies the pier hides the hull of are
        # found from its top rows (the drawing cuts the hull there)
        "name": "sailboat",
        # its projected shapes stand past its drawing: cut to it
        "clip_art": True,
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (34, 44, 3, 3),
        "ground": [0x170],
        # on the sea, not on the pier round it
        "ground_fixed": True,
        "ground_px": True,
        "parts_art": sailboat,
        "exact": [(0, 0, 48, 48)],
    },
    {
        # the same boat with its hull behind the pier's edge
        "name": "sailboat_cut",
        # its projected shapes stand past its drawing: cut to it
        "clip_art": True,
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (36, 37, 3, 2),
        "ground": [0x170],
        # on the sea, not on the pier round it
        "ground_fixed": True,
        "ground_px": True,
        "parts_art": lambda art: sailboat(art, keel=-6),
        "exact": [(0, 0, 48, 32)],
    },
    {
        # two of them moored one behind the other, drawn 8 and 24 rows lower
        # than the whole one; the seawall's shadow across the top is not theirs
        "name": "sailboat_pair",
        # its projected shapes stand past its drawing: cut to it
        "clip_art": True,
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (33, 35, 3, 4),
        "ground": [0x170],
        # on the sea, not on the pier round it
        "ground_fixed": True,
        "ground_px": True,
        "keep": [(0, 8, 48, 64)],
        # the seawall's shadow on its top row stays as the map paints it
        "patch_cells": [(0, 0), (1, 0), (2, 0)],
        "parts_art": lambda art: sailboat(art, (8, 24)),
        "exact": [(0, 8, 48, 64)],
    },
    {
        "name": "rowboat",
        # its projected shapes stand past its drawing: cut to it
        "clip_art": True,
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (21, 35, 3, 2),
        "ground": [GRASS],
        "ground_px": True,
        "parts_art": rowboat,
        "exact": [(0, 0, 48, 32)],
    },
    {
        # the square's stone lantern; the plot's border round it is not its own
        "name": "slateport_lantern",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (32, 51, 2, 4),
        "ground": [GRASS, 0x202],
        "ground_px": True,
        "keep": [(4, 8, 28, 15), (0, 15, 32, 64)],
        "parts_art": lantern,
        # true cubes, thousands of them: drawn from its own mesh, not copied
        # into the chunk it stands in
        "static": True,
        # judged against its drawing moved back with it
        "drawing": lantern_drawing,
        "exact": [(0, 0, 32, 64 - LANTERN_BACK)],
        # its outline is a 32-sided polygon, not the drawing's pixel stair
        # (2 wrong, 8 missing, 16 extra), and its cap's far half takes the
        # near half's colours, not the thick outline the GBA draws there (66
        # wrong) - measured 2026-10-10
        "tolerance": 92,
        # under it: the plaza's bricks and the lawn's border in its top row
        # (the drawing's cap stood there), its lawn in the others
        "cell_ground": {(0, 0): 0x34C, (1, 0): 0x34C},
    },
    {
        # Slateport's round bush: a low dome of leaves
        "name": "slateport_bush",
        # its projected shapes stand past its drawing: cut to it
        "clip_art": True,
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (7, 10, 1, 1),
        "ground": [GRASS],
        # on its lawn, whatever paving is round it
        "ground_fixed": True,
        # and its cell as the map paints it round the bush (the grass
        # dots its outline leaves out)
        "patch_own": True,
        "ground_px": True,
        # the bush's own outline: the grass dots drawn round it (its top
        # row, beside its crown and foot) would stand up as part of it
        "keep": [(6, 1, 10, 2), (4, 2, 12, 3), (3, 3, 13, 4), (2, 4, 14, 6), (1, 6, 14, 10),
                 (2, 10, 14, 12), (3, 12, 13, 14), (5, 14, 11, 15)],
        # a smooth dome under its drawing: the height field cut column by
        # column left fins on its flanks and stood in steps
        "parts": lambda: [ProjDome("bush", 7.5, 8.6, 7.6, 7.0, 8.0)],
        "exact": [(0, 0, 16, 16)],
    },
    {
        # the harbour's bollard: a white cap on a short post, its shadow a
        # disc on the paving
        "name": "slateport_bollard",
        # its projected shapes stand past its drawing: cut to it
        "clip_art": True,
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (33, 34, 1, 1),
        "ground": [0x202],
        # on the pier, not on the sea beside it
        "ground_fixed": True,
        "ground_px": True,
        # its plan half as deep as wide, as the drawing's rings are (round,
        # it stood past the drawing's top and was cut flat behind)
        # turned smooth (ProjLathe): a height field cut column by column had
        # left a fin on its flank
        "parts": lambda: [ProjLathe("bollard", 8, 11.5, BOLLARD, squash=0.5, seg=10)],
        "exact": [(0, 0, 16, 16)],
    },
    {
        # Route 109's beach parasols (red, blue and green: found by their own
        # pixels from this one when the drawing matches)
        "name": "parasol",
        "layout": "LAYOUT_ROUTE109",
        "rect": (9, 6, 2, 3),
        "ground": [0x124],
        "ground_px": True,
        "ground_rgb": PARASOL_SHADOW,
        "parts_art": parasol,
        "exact": [(0, 0, 32, 48)],
    },
    {
        "name": "parasol_blue",
        "layout": "LAYOUT_ROUTE109",
        "rect": (18, 4, 2, 3),
        "ground": [0x124],
        "ground_px": True,
        "ground_rgb": PARASOL_SHADOW,
        "parts_art": parasol,
        "exact": [(0, 0, 32, 48)],
    },
    {
        "name": "parasol_green",
        "layout": "LAYOUT_ROUTE109",
        "rect": (13, 14, 2, 3),
        "ground": [0x124],
        "ground_px": True,
        "ground_rgb": PARASOL_SHADOW,
        "parts_art": parasol,
        "exact": [(0, 0, 32, 48)],
    },
    {
        # a sun lounger (art 16x48, v 9-37): a frame on legs, its back
        # raised to the north
        "name": "sun_lounger",
        "layout": "LAYOUT_ROUTE109",
        "rect": (17, 8, 1, 3),
        "ground": [0x124],
        "ground_px": True,
        "parts_art": sun_lounger,
        "exact": [(0, 0, 16, 48)],
    },
    {
        # an air mattress (art 16x48, v 9-36), flat on the sand
        "name": "beach_mat",
        "layout": "LAYOUT_ROUTE109",
        "rect": (9, 9, 1, 3),
        "ground": [0x124],
        "ground_px": True,
        "parts_art": beach_mat,
        "exact": [(0, 0, 16, 48)],
        # the pillow's square top corners (row 9), which the drawing rounds
        "tolerance": 5,
    },
    {
        # the Abandoned Ship; the rock beside its bow is the sea's (a prop)
        "name": "abandoned_ship",
        "layout": "LAYOUT_ROUTE108",
        "rect": (27, 3, 6, 4),
        "ground": [0x19E, 0x194, 0x1BE, 0x1BD, 0x1A3, 0x1A4, 0x170],
        "ground_px": True,
        # the shallows (0x19E) under it, and its bottom row as the map paints
        # it: the shallows' edge on the deep sea
        "patch_cells": [(i, 3) for i in range(6)],
        "keep": [(0, 0, 80, 64), (80, 19, 96, 64)],
        "parts_art": abandoned_ship,
        "exact": [(0, 0, 96, 64)],
    },
    {
        "name": "trick_house",
        "layout": "LAYOUT_ROUTE110",
        "rect": (9, 61, 5, 6),
        "ground": [GRASS, 0x171, 0x179, 0x190],
        # the pond's water under its back; west of its walls, two columns of
        # the lawn drawn in its own cells
        "cell_ground": {(0, 4): GRASS, (0, 5): GRASS},
        "parts": trick_house,
        "exact": TRICK_EXACT,
    },
    {
        # Route 110's cycling road gatehouses: a flat-roofed block, its roof
        # in 8-row courses (rows 10-32) behind a parapet (back rows 0-10,
        # front 32-40), the yellow facade below with its two doors
        "name": "cycling_gate",
        "layout": "LAYOUT_ROUTE110",
        "rect": (14, 12, 6, 5),
        "ground": [GRASS, 0x149],
        # the southern gate's east column, one pixel of the sea beside it
        # (the ground under it is the lawn round it)
        "cell_ground_at": {("LAYOUT_ROUTE110", 15, 84): {(5, 1): 0x170}},
        # drawn over columns 1-94 (95: its shadow)
        "parts": lambda: flat_block(96, 80, ((10, 32), (10, 18), (0, 10)), (32, 40), 40, x0=1, x1=95),
        "exact": [(1, 10, 95, 80)],
    },
    {
        "name": "pink_house_4",
        "layout": "LAYOUT_MAUVILLE_CITY",
        "rect": (18, 11, 4, 4),
        "ground": [GRASS, 0x004],
        "parts": lambda: pink_house(64),
        "exact": pink_house_exact(64),
    },
    {
        "name": "pink_house_5",
        "layout": "LAYOUT_MAUVILLE_CITY",
        "rect": (34, 2, 5, 4),
        "ground": [GRASS, 0x004],
        "parts": lambda: pink_house(80),
        "exact": pink_house_exact(80),
    },
    {
        # Rydel's cycles: the same house, its facade with a shop window
        "name": "pink_house_5b",
        "layout": "LAYOUT_MAUVILLE_CITY",
        "rect": (31, 11, 5, 4),
        "ground": [GRASS, 0x004],
        "parts": lambda: pink_house(80, x0=2),
        "exact": pink_house_exact(80),
    },
    {
        # the cycling road's gatehouse four cells wide, one door
        "name": "cycling_gate_4",
        "layout": "LAYOUT_MAUVILLE_CITY",
        "rect": (36, 11, 4, 5),
        "ground": [GRASS, 0x00E],
        "parts": lambda: flat_block(64, 80, ((10, 32), (10, 18), (0, 10)), (32, 40), 40, x0=1, x1=63),
        "exact": [(1, 10, 63, 80)],
    },
    {
        # the flowers in its bottom row are the town's
        "name": "game_corner",
        "layout": "LAYOUT_MAUVILLE_CITY",
        "rect": (5, 10, 7, 5),
        "ground": [GRASS, 0x004],
        "keep": [(0, 0, 112, 64), (40, 64, 48, 72), (64, 64, 72, 72)],
        "parts_art": game_corner,
        # its front corners are drawn rounded off at the top
        "exact": [(8, 3, 104, 9), (0, 9, 112, 64), (40, 64, 48, 72), (64, 64, 72, 72)],
    },
    {
        # Rydel's parked bicycles, two to a three-cell stand, drawn side on:
        # a card standing at their wheels' foot, like a potted plant (the
        # wheels keep their holes)
        "name": "parked_bikes",
        "layout": "LAYOUT_MAUVILLE_CITY",
        "rect": (26, 3, 3, 1),
        "ground": [GRASS, 0x004],
        "ground_px": True,
        "parts_art": lambda art: [Card("bikes", art, 15)],
        "exact": [(0, 0, 48, 16)],
    },
    {
        "name": "verdanturf_house_4",
        "layout": "LAYOUT_VERDANTURF_TOWN",
        "rect": (0, 11, 4, 4),
        "ground": [GRASS, 0x204, 0x205],
        "parts": lambda: verdanturf_house(64),
        "exact": verdanturf_house_exact(64),
    },
    {
        "name": "verdanturf_house_5",
        "layout": "LAYOUT_VERDANTURF_TOWN",
        "rect": (8, 11, 5, 4),
        "ground": [GRASS, 0x204, 0x205],
        "parts": lambda: verdanturf_house(80),
        "exact": verdanturf_house_exact(80),
    },
    {
        "name": "battle_tent_verdanturf",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (6, 6, 3),
        # the coarse grid misses 6 pixels of its outline at 45 degrees
        "tolerance": 6,
        "layout": "LAYOUT_VERDANTURF_TOWN",
        "rect": (1, 3, 5, 5),
        "ground": [GRASS],
        "ground_px": True,
        "parts_art": battle_tent_elsewhere,
        "exact": [(0, 0, 80, 80)],
    },
    {
        "name": "fallarbor_house_4",
        "layout": "LAYOUT_FALLARBOR_TOWN",
        "rect": (0, 3, 4, 4),
        "ground": [0x279, 0x269],
        "parts": lambda: fallarbor_house(64),
        "exact": fallarbor_house_exact(64),
    },
    {
        "name": "fallarbor_cozmo_house",
        "layout": "LAYOUT_FALLARBOR_TOWN",
        "rect": (5, 13, 4, 5),
        "ground": [0x279],
        "parts_art": cozmo_house,
        # the real depth raises the roof behind the antenna (behind: True)
        "exact": [(3, 55, 62, 78), (10, 29, 54, 55), (32, 2, 57, 18, True)],
    },
    {
        # a bather buried in Lavaridge's hot sand: a mound
        "name": "sand_bath",
        "layout": "LAYOUT_LAVARIDGE_TOWN",
        "rect": (3, 8, 1, 2),
        "ground": [0x2A8],
        "ground_px": True,
        "parts_art": small_round("sand_bath", Volume().dome(8, 20, 7.5, 7, 7), depth=32.0),
        "exact": [(0, 0, 16, 32)],
    },
    {
        # Route 113's glass workshop: Fallarbor's house with a roof of glass
        # (the trees' crowns overlap its top row)
        "name": "glass_workshop",
        "layout": "LAYOUT_ROUTE113",
        "rect": (32, 2, 4, 4),
        "ground": [0x218, 0x26D, 0x26E, 0x20C, 0x20D],
        "parts": lambda: fallarbor_house(64),
        "exact": fallarbor_house_exact(64),
    },
    {
        # Route 114's Fossil Maniac's house: Fallarbor's, its ridge drawn
        # against the brick cliff behind it
        "name": "fossil_maniac_house",
        "layout": "LAYOUT_ROUTE114",
        "rect": (28, 2, 4, 4),
        "ground": [0x279, 0x269],
        "parts": lambda: fallarbor_house(64),
        "exact": fallarbor_house_exact(64),
    },
    {
        # Route 111's Mirage Tower (art 48 x 96): three round stone tiers,
        # each set back on the one below - the base (r 24, rows 72-96), the
        # middle (r 22, rows 26-70) and the top with its rounded crown (r 18,
        # rows 0-44) - its door in the base
        "name": "mirage_tower",
        "layout": "LAYOUT_ROUTE111",
        "rect": (18, 53, 3, 6),
        "ground": [0x251, 0x259, 0x121, 0x25A],
        "parts_art": flat(lambda: [PlanPrism("base", rounded_plan(24, 80, 25.0, 16.6, p=3.5, n=40), 0, 24, (2, 8, 96)),
                          PlanPrism("mid", rounded_plan(24, 80, 23.6, 14.4, p=2.0, n=40), 24, 47, (2, 8, 72)),
                          PlanPrism("top", rounded_plan(24, 80, 18.4, 11.0, p=2.0, n=40), 47, 72, (8, 14, 49),
                                    shrink=1.0),
                          ] + [PlanPrism("crown", rounded_plan(24, 80, 18.4 * k, 11.0 * k, p=2.0, n=40), y, y + 2,
                                         (8, 14, 49))
                               for y, k in zip(range(72, 86, 2), (0.97, 0.91, 0.84, 0.76, 0.68, 0.61, 0.55))] + [
                          PlanPyramid("tip", rounded_plan(24, 80, 10.1, 6.05, p=2.0, n=24), 86, (24, 92, 80))]),
        "exact": [(0, 0, 48, 96)],
    },
    {
        # Route 112's cable car station (80 x 80): a block 34 high, its flat
        # roof drawn rows 0-46 (z 34-80), its east end a slope falling 24 to
        # the east - the drawing's diagonal edges at the top right and along
        # the facade's right end
        "name": "cable_car_station",
        "layout": "LAYOUT_ROUTE112",
        "rect": (27, 23, 5, 5),
        "ground": [0x271, 0x210, 0x218, 0x21A],
        "parts_art": flat(lambda: [PlatePrism("station", [(0, 0), (0, 34), (56, 34), (80.5, 9.5), (80.5, 0)], 34, 80, (2, 10, 80))]),
        "exact": [(0, 0, 80, 80)],
    },
    {
        # Mt Chimney's cable car station: Route 112's drawn the other way
        # round (96 x 80), its slope falling 31 to the west
        "name": "cable_car_station_top",
        "layout": "LAYOUT_MT_CHIMNEY",
        "rect": (14, 32, 6, 5),
        "ground": [0x271],
        "parts_art": flat(lambda: [PlatePrism("station", [(96, 0), (96, 34), (40, 34), (8.5, 2.5), (8.5, 0)], 34, 80, (86, 94, 80))]),
        "exact": [(0, 0, 96, 80)],
    },
    {
        # the Winstrates' house on Route 111: the flat-roofed block five wide
        "name": "cycling_gate_5",
        "layout": "LAYOUT_ROUTE111",
        "rect": (11, 109, 5, 5),
        "ground": [GRASS, 0x149],
        "parts": lambda: flat_block(80, 80, ((10, 32), (10, 18), (0, 10)), (32, 40), 40, x0=1, x1=79),
        "exact": [(1, 10, 79, 80)],
    },
    {
        # Route 111's Trainer Hill entrance, on the plateau (80 x 96; below
        # row 82 the cliff's own drawing): a block 36 high, its flat roof
        # drawn rows 0-46 with a hexagonal lantern in its middle
        "name": "trainer_hill_entrance",
        "layout": "LAYOUT_ROUTE111",
        "rect": (29, 106, 5, 6),
        "ground": [0x070, 0x073, 0x071, 0x07B, 0x075, 0x089],
        "keep": [(0, 0, 80, 82)],
        "parts_art": flat(lambda: [PlanPrism("hill", [(0, 36), (80, 36), (80, 82), (0, 82)], 0, 36, (2, 10, 82)),
                          PlanPyramid("lantern", [(57, 56), (48.5, 66), (31.5, 66), (23, 56), (31.5, 46),
                                                  (48.5, 46)], 36, (40, 48, 56))]),
        "exact": [(0, 0, 80, 82)],
    },
    {
        "name": "lilycove_house_4",
        "layout": "LAYOUT_LILYCOVE_CITY",
        "rect": (35, 3, 4, 4),
        "ground": [GRASS, 0x1D4, 0x1D5, 0x1DC, 0x1DD, 0x1E4, 0x1E5, 0x1E6, 0x1E7],
        # the houses below the cliff paint the cliff's foot into the roof's
        # top rows, the fenced ones the fence's ends into the eave's: the
        # facade and the eave say which house it is
        "match_rows": (2, 4),
        "parts": lambda: lilycove_house(64),
        "exact": lilycove_house_exact(64),
    },
    {
        "name": "lilycove_house_5",
        "layout": "LAYOUT_LILYCOVE_CITY",
        "rect": (37, 11, 5, 4),
        "ground": [GRASS, 0x004],
        "match_rows": (2, 4),
        "parts": lambda: lilycove_house(80),
        "exact": lilycove_house_exact(80),
    },
    {
        # Lilycove's department store (144 x 112): drawn as its tall front
        # alone, the roof above the map's edge - a block 112 high and 64
        # deep, its flat roof standing above the map's top (open_top)
        "name": "department_store",
        "layout": "LAYOUT_LILYCOVE_CITY",
        "rect": (23, 0, 9, 7),
        "ground": [GRASS, 0x233, 0x22B],
        "parts_art": flat(lambda: [PlanPrism("store", [(0, 48), (144, 48), (144, 112), (0, 112)], 0, 112, (2, 10, 112),
                                    top=Tile(4, 4, 20, 20))]),
        "exact": [(0, 0, 144, 112)],
    },
    {
        # Lilycove's museum (160 x 96): a long block 44 high, its flat roof
        # drawn rows 16-52, and its middle (x 40-120) a storey higher, 68,
        # its windowed front standing on the lower block's front edge
        "name": "lilycove_museum",
        "layout": "LAYOUT_LILYCOVE_CITY",
        "rect": (7, 1, 10, 6),
        "ground": [GRASS, 0x002, 0x208, 0x207, 0x004],
        "parts_art": flat(lambda: [PlanPrism("museum", [(0, 52), (160, 52), (160, 96), (0, 96)], 0, 44, (2, 10, 96)),
                          PlanPrism("museum_hall", [(40, 60), (120, 60), (120, 94), (40, 94)], 0, 68, (42, 50, 94))]),
        "exact": [(0, 0, 160, 96)],
    },
    {
        # Lilycove's contest hall (112 x 112): a block 30 high on a plan with
        # its four corners cut off 8, its studded flat roof drawn rows 8-82
        "name": "lilycove_contest_hall",
        "layout": "LAYOUT_LILYCOVE_CITY",
        "rect": (20, 18, 7, 7),
        "ground": [GRASS, 0x233, 0x22B, 0x23B],
        "ground_px": True,
        "parts_art": flat(lambda: [PlanPrism("hall", [(8, 38), (104, 38), (112, 46), (112, 104), (104, 112), (8, 112),
                                                (0, 104), (0, 46)], 0, 30, (16, 40, 112))]),
        "exact": [(0, 0, 112, 112)],
    },
    {
        # Lilycove's harbour (112 x 96): a white block 34 high under a
        # corrugated roof of low pitch - its red eave rows 54-62 standing 2
        # out, the slope drawn rows 1-54 up to the ridge over z 56 (the back
        # half hidden behind it)
        "name": "lilycove_harbor",
        "layout": "LAYOUT_LILYCOVE_CITY",
        "rect": (9, 27, 7, 6),
        "ground": [GRASS, 0x233, 0x22B, 0x23B],
        "parts_art": flat(lambda: [SectionPrism("harbor", [(96, 0), (96, 34), (98, 36), (98, 44), (56, 55), (14, 44),
                                                (14, 36), (16, 34), (16, 0)], 0, 112, (2, 10, 96))]),
        "exact": [(0, 0, 112, 96)],
    },
    {
        "name": "lilycove_motel",
        "layout": "LAYOUT_LILYCOVE_CITY",
        "rect": (36, 20, 6, 5),
        "ground": [GRASS, 0x004, 0x233],
        "parts": lilycove_motel,
        "exact": [(3, 56, 93, 80), (22, 50, 74, 56), (22, 40, 72, 50), (26, 16, 58, 40)],
    },
    {
        # Mossdeep's red-roofed house (64 x 64): a block 22 high (its facade
        # rows 40-62) under a hipped roof drawn rows 0-37
        "name": "mossdeep_house",
        "layout": "LAYOUT_MOSSDEEP_CITY",
        "rect": (18, 7, 4, 4),
        "ground": [GRASS, 0x004, 0x002],
        "parts": lambda: mossdeep_house_parts(64),
        "exact": [(4, 38, 60, 62), (12, 8, 52, 33)],
    },
    {
        # the same house without the reference's chimney, its roof's top
        # corners drawn over the sea, a cliff or grass (rows 1-3 must match)
        "name": "mossdeep_house_b",
        "layout": "LAYOUT_MOSSDEEP_CITY",
        "rect": (27, 6, 4, 4),
        "ground": [GRASS, 0x004, 0x002],
        "match_rows": (1, 4),
        "parts": lambda: mossdeep_house_parts(64),
        "exact": [(4, 38, 60, 62), (12, 8, 52, 33)],
    },
    {
        # Mossdeep's wide house (80 x 80), built as Oldale's: a hipped roof
        # railed in white (ridge rows 8-20, slope 20-48, fascia 48-53) over
        # the facade down to row 79, its sides the vine-grown stone
        "name": "mossdeep_house_wide",
        "layout": "LAYOUT_MOSSDEEP_CITY",
        "rect": (35, 20, 5, 5),
        "ground": [GRASS, 0x004, 0x120, 0x121, 0x129],
        "ground_px": True,
        "parts": lambda: pitched_house(80, (8, 14), (14, 21), (21, 48), (22, 38), (48, 53), 79,
                                       (22, 38), (22, 38), (1, 7)),
        "exact": [(4, 53, 76, 78), (16, 21, 64, 48)],
    },
    {
        # Pacifidlog's round hut on its raft (48 x 64): a round wall 22 high
        # (rows 41-63), a conical thatch over it (its eave r 25) and the
        # finial on its top (rows 0-14); round things drawn as the GBA draws
        # them, their plan about half as deep as wide. The raft's planks
        # round it are the ground
        "name": "pacifidlog_hut",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (4, 4, 2),
        "layout": "LAYOUT_PACIFIDLOG_TOWN",
        "rect": (1, 9, 3, 4),
        "ground": [0x221, 0x170],
        "ground_px": True,
        "keep": [(0, 0, 48, 35), (2, 35, 46, 37), (3, 37, 45, 64)],
        "parts_art": pacifidlog_hut,
        # every cell: the raft's planks and the sea round the hut as the
        # map paints them, whatever ground the placement takes
        "patch_cells": [(i, j) for j in range(4) for i in range(3)],
        "exact": [(0, 0, 48, 64)],
    },
    {
        # Sootopolis's spire house (48 x 80): an octagonal white base 22 high
        # (its front rows 56-78, its flat top rows 18-56: z 40-78, the
        # corners cut 8) under an eight-sided pyramid of dark stone rising
        # 29 from it (its apex row 8)
        "name": "sootopolis_spire_house",
        "layout": "LAYOUT_SOOTOPOLIS_CITY",
        "rect": (22, 1, 3, 5),
        "ground": [0x2D9, 0x2E1, 0x2E2, 0x2D3, 0x2DA],
        "ground_px": True,
        "parts_art": sootopolis_spire,
        "patch_cells": [(i, j) for j in range(5) for i in range(3)],
        "exact": [(0, 0, 48, 80)],
    },
    {
        # Sootopolis's flat-roofed white house (48 x 64): a block 30 high
        # under a flat roof with its skylight
        "name": "sootopolis_house",
        "layout": "LAYOUT_SOOTOPOLIS_CITY",
        "rect": (2, 6, 3, 4),
        "ground": [0x2D9, 0x2E1, 0x2E2, 0x2D3, 0x2DA],
        "ground_px": True,
        "parts_art": sootopolis_flat_house,
        "patch_cells": [(i, j) for j in range(4) for i in range(3)],
        "exact": [(0, 0, 48, 64)],
    },
    {
        # Ever Grande's Pokemon League: see pokemon_league()
        "name": "pokemon_league",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (6, 6, 3),
        "layout": "LAYOUT_EVER_GRANDE_CITY",
        "rect": (13, 0, 11, 8),
        "ground": [0x001, 0x222, 0x221, 0x270, 0x271, 0x272],
        "ground_px": True,
        "parts_art": pokemon_league,
        "exact": [(0, 0, 176, 128)],
    },
    {
        # Ever Grande's round bushes along the paths (the League's own four
        # are in its model): a low dome of leaves, on grass and on shade
        "name": "ever_grande_bush",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (6, 6, 3),
        "layout": "LAYOUT_EVER_GRANDE_CITY",
        "rect": (14, 8, 1, 1),
        "ground": [0x001, 0x222, 0x221],
        "ground_px": True,
        "owned": {(0, 0)},
        "repeat_at": [(14, 8), (15, 8), (14, 9), (15, 9), (14, 10), (15, 10), (21, 10), (22, 10),
                      (23, 24), (29, 24), (23, 25), (24, 25), (28, 25), (29, 25)],
        "parts_art": small_round("bush", Volume().dome(8, 9, 8, 6.5, 8.5)),
        "exact": [(0, 0, 16, 16)],
    },
    {
        "name": "ever_grande_bush_shade",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (6, 6, 3),
        "layout": "LAYOUT_EVER_GRANDE_CITY",
        "rect": (21, 8, 1, 1),
        "ground": [0x221, 0x222, 0x001],
        "ground_px": True,
        "owned": {(0, 0)},
        "repeat_at": [(21, 8), (22, 8), (21, 9), (22, 9)],
        "parts_art": small_round("bush", Volume().dome(8, 9, 8, 6.5, 8.5)),
        "exact": [(0, 0, 16, 16)],
    },
    {
        # the Battle Frontier's Battle Dome: see battle_dome()
        "name": "battle_dome",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (8, 8, 3),
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST",
        "rect": (13, 4, 13, 14),
        "ground": [0x001, 0x268, 0x269, 0x26A, 0x26B, 0x270, 0x271, 0x272, 0x273, 0x003],
        "ground_px": True,
        "parts_art": battle_dome,
        # the shell shows through the gaps of the glass base, as the dome behind it
        "exact": [(0, 0, 208, 160), (0, 160, 208, 224, True)],
    },
    {
        # the Battle Frontier's Battle Factory: see battle_factory()
        "name": "battle_factory",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST",
        "rect": (4, 30, 15, 9),
        "ground": [0x189, 0x170, 0x172, 0x19A, 0x198, 0x180, 0x181, 0x05E],
        "ground_px": True,
        # its platform, which its front row shows, not the sea of its back
        # corners (more of its cells, but behind it)
        "ground_fixed": {"LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST": 0x05E},
        "keep": [(0, 4, 232, 144), (88, 0, 152, 4), (232, 44, 240, 144)],
        "parts_art": flat(battle_factory),
        "exact": [(0, 0, 240, 144)],
    },
    {
        # the Battle Frontier's small houses (48 x 64): a rounded block 36
        # high (its front z 62, its flat orange roof rows 0-26, the red band
        # and the walls its front), its plan a superellipse
        "name": "frontier_house",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST",
        "rect": (43, 2, 3, 4),
        "ground": [0x001, 0x170, 0x005, 0x0E0, 0x002, 0x007, 0x101],
        "ground_px": True,
        "parts_art": flat(lambda: [PlanPrism("house", rounded_plan(24, 49.5, 24.6, 14.2, p=10.0), 0, 36, (10, 18, 63))]),
        "exact": [(0, 0, 48, 64)],
    },
    {
        # the glass-fronted one (rows 1-3 must match: a cliff's foot shows
        # above its roof at 4,17)
        "name": "frontier_house_glass",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST",
        "rect": (52, 41, 3, 4),
        "ground": [0x001, 0x170, 0x005, 0x0E0, 0x002, 0x007, 0x101],
        "ground_px": True,
        "match_rows": (1, 4),
        "parts_art": flat(lambda: [PlanPrism("house", rounded_plan(24, 49.5, 24.6, 14.2, p=10.0), 0, 36, (20, 28, 63))]),
        "exact": [(0, 0, 48, 64)],
    },
    {
        "name": "frontier_house_long",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST",
        "rect": (44, 41, 6, 4),
        "ground": [0x001, 0x170, 0x005, 0x0E0, 0x002, 0x007, 0x101],
        "ground_px": True,
        "parts_art": flat(lambda: [PlanPrism("house", rounded_plan(48, 49.5, 48.6, 14.2, p=14.0, n=48), 0, 36,
                                    (10, 18, 63))]),
        "exact": [(0, 0, 96, 64)],
    },
    {
        # the Battle Frontier's Battle Pike: see battle_pike_volume()
        "name": "battle_pike",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (8, 8, 3),
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST",
        "rect": (40, 17, 5, 13),
        "ground": [0x001, 0x204, 0x017, 0x004, 0x003],
        "ground_px": True,
        "keep": [(16, 0, 80, 208), (0, 36, 16, 208)],
        "parts_art": small_round("pike", battle_pike_volume(), depth=208.0, step=2, cell=2,
                                 side=(30, 36, 112)),
        "exact": [(0, 0, 80, 208)],
    },
    {
        # the Battle Tower (272 x 256), its glass shaft cut by the map's top
        # edge: two wings of brick and glass (x 3-46, 226-270) standing to
        # z 248, the middle set back to z 232 above the lobby, a block 36
        # high before it whose dark roof is drawn rows 196-212; the roofs
        # above the map laid with the brickwork
        "name": "battle_tower",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_EAST",
        "rect": (8, 0, 17, 16),
        "ground": [0x001, 0x201, 0x203, 0x202],
        "ground_px": True,
        # the doormat before the doors is the plaza's paint
        "keep": [(0, 0, 272, 248), (0, 248, 100, 256), (172, 248, 272, 256)],
        "patch_cells": [(i, 15) for i in range(6, 11)],
        "parts_art": flat(lambda: [
            PlanPrism("wing", [(0, 136), (46, 136), (46, 248), (0, 248)], 0, 320, (20, 40, 248),
                      top=Tile(20, 0, 40, 16)),
            PlanPrism("wing", [(226, 136), (272, 136), (272, 248), (226, 248)], 0, 320, (232, 252, 248),
                      top=Tile(20, 0, 40, 16)),
            PlanPrism("shaft", [(46, 136), (226, 136), (226, 232), (46, 232)], 0, 320, (60, 76, 196),
                      top=Tile(20, 0, 40, 16)),
            PlanPrism("lobby", [(46, 232), (226, 232), (226, 248), (46, 248)], 0, 36, (60, 76, 248))]),
        "exact": [(0, 0, 272, 256)],
    },
    {
        # the Battle Frontier's Battle Pyramid: see battle_pyramid()
        "name": "battle_pyramid",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_EAST",
        "rect": (51, 5, 15, 11),
        "ground": [0x121, 0x129, 0x120, 0x128, 0x001, 0x003],
        "ground_px": True,
        "parts_art": sand_specks(flat(battle_pyramid)),
        "exact": [(0, 0, 240, 176)],
    },
    {
        # the Battle Arena's hall (144 x 112), built as Oldale's: a hipped
        # roof of dark boards (ridge rows 0-4, the slope 8-64 with its seams
        # every 8 columns, the eave 64-69) reaching 8 over the deck either
        # side; the white walls with their posts down to row 112, 8 in
        "name": "battle_arena",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_EAST",
        "rect": (35, 23, 9, 7),
        "ground": [0x286, 0x285, 0x170, 0x189],
        "ground_px": True,
        "parts": lambda: pitched_house(144, (0, 4), (4, 8), (8, 64), (8, 16), (64, 69), 112,
                                       (18, 26), (18, 26), (16, 32), inset=8, overhang=2, back_z=32),
        "exact": [(17, 69, 127, 112), (17, 8, 127, 64)],
    },
    {
        # the Battle Arena's gate (112 x 64): two blocks of pillars and white
        # walls (x 0-32, 80-112) 36 high, and between them the lintel over
        # the path, 24 up; the two-tier roof drawn rows 0-28 their tops
        "name": "battle_arena_gate",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_EAST",
        "rect": (36, 36, 7, 4),
        "ground": [0x0C6, 0x001, 0x003, 0x00E, 0x1D9, 0x1D8, 0x1DA],
        "ground_px": True,
        "parts_art": flat(lambda: [
            PlanPrism("gate", [(0, 36), (32, 36), (32, 64), (0, 64)], 0, 36, (10, 20, 64)),
            PlanPrism("gate", [(80, 36), (112, 36), (112, 64), (80, 64)], 0, 36, (90, 100, 64)),
            PlanPrism("lintel", [(32, 36), (80, 36), (80, 64), (32, 64)], 24, 36, (40, 48, 64))]),
        "exact": [(0, 0, 112, 64)],
    },
    {
        # the Battle Palace's side pavilions (48 x 96, the flowerbeds above
        # and below them the ground's paint), built as Oldale's: a golden
        # hipped roof (ridge rows 8-16, the slope 16-46 in courses of 8,
        # the eave 46-48) over red walls with their columns down to row 80
        "name": "battle_palace_wing",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_EAST",
        "rect": (40, 50, 3, 6),
        "ground": [0x004, 0x017, 0x00E, 0x0C6, 0x003],
        "ground_px": True,
        "keep": [(0, 8, 48, 80)],
        "patch_cells": [(i, j) for j in (0, 5) for i in range(3)],
        "parts": lambda: pitched_house(48, (8, 16), (16, 20), (20, 46), (23, 31), (46, 48), 80,
                                       (16, 24), (16, 24), (2, 8), inset=2, overhang=2, back_z=40),
        "exact": [(4, 48, 36, 80), (16, 20, 24, 46)],
    },
    {
        # the Battle Palace's middle pavilion (80 x 128): the hall (walls 18
        # high to z 104) under its golden hipped roof - flat faces, the
        # drawing projected: eave z 34-106 at 20, ridge x 23-57 over z 71 at
        # 47 (the back eave drawn row 16, the ridge row 24) - and before it
        # the porch (x 18-62, 42 high to z 128) under its own (eave z 90-130
        # at 44, ridge x 31-49 at 55), the two stone plinths either side
        "name": "battle_palace",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_EAST",
        "rect": (43, 49, 5, 8),
        "ground": [0x004, 0x017, 0x00E, 0x0C6, 0x003],
        "ground_px": True,
        "keep": [(0, 16, 80, 96), (8, 96, 72, 128)],
        "patch_cells": [(i, j) for i in range(5) for j in (0, 6, 7)],
        "parts_art": flat(lambda: [
            PlanPrism("hall", [(0, 40), (80, 40), (80, 104), (0, 104)], 0, 18, (72, 78, 104)),
            PlanPrism("eave", [(-2, 34), (82, 34), (82, 106), (-2, 106)], 18, 20, (72, 78, 106)),
            PlanHip("hall_roof", -2, 82, 34, 106, 20, 23, 57, 71, 47),
            PlanPrism("porch", [(18, 88), (62, 88), (62, 128), (18, 128)], 0, 42, (20, 26, 128)),
            PlanPrism("plinth", [(8, 116), (18, 116), (18, 128), (8, 128)], 0, 32, (10, 16, 128)),
            PlanPrism("plinth", [(62, 116), (72, 116), (72, 128), (62, 128)], 0, 32, (64, 70, 128)),
            PlanPrism("porch_eave", [(14, 90), (66, 90), (66, 130), (14, 130)], 42, 44, (20, 26, 130)),
            PlanHip("porch_roof", 14, 66, 90, 130, 44, 31, 49, 111, 55)]),
        "exact": [(0, 16, 80, 128)],
    },
    {
        # the Battle Frontier's flags along its sea walls: a pole and its
        # cloth, flat - a card standing at its foot on the wall (row 39)
        "name": "frontier_flag",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_EAST",
        "rect": (40, 2, 2, 3),
        "ground": [0x170, 0x3C1, 0x3F9],
        "ground_px": True,
        "keep": [(10, 8, 32, 39)],
        # the wall it stands on, and the sea, as the map paints them
        "patch_cells": [(i, j) for j in range(3) for i in range(2)],
        "parts_art": lambda art: [Card("flag", art, 39)],
        "exact": [(10, 8, 32, 39)],
    },
    {
        "name": "frontier_flag_west",
        "layout": "LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST",
        "rect": (9, 1, 2, 3),
        "ground": [0x170, 0x26C, 0x26D, 0x3D6, 0x3D7],
        "ground_px": True,
        "keep": [(2, 8, 24, 32), (2, 32, 6, 39)],
        # the wall it stands on, and the sea, as the map paints them
        "patch_cells": [(i, j) for j in range(3) for i in range(2)],
        "parts_art": lambda art: [Card("flag", art, 39)],
        "exact": [(2, 8, 24, 32), (2, 32, 6, 39)],
    },
    {
        # Mossdeep's Space Center (144 x 224): see space_center(); the sea
        # behind the rocket stays the map's paint
        "name": "space_center",
        # a coarser grid for its round parts (2026-10-10: the console's
        # budgets), the drawing still projected wherever the view sees it
        "lod": (4, 4, 2),
        "layout": "LAYOUT_MOSSDEEP_CITY",
        "rect": (60, 2, 9, 14),
        "ground": [0x170, GRASS, 0x002, 0x004],
        "ground_px": True,
        "parts_art": space_center,
        # the sea and the shore behind the rocket, as the map paints them
        "patch_cells": [(i, j) for j in range(6) for i in range(9)],
        "exact": [(0, 0, 144, 224)],
    },
    {
        "name": "shipyard",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (24, 32, 8, 7),
        "ground": [0x202, GRASS],
        # its top row is the sea behind it, not its roof's
        "keep": [(0, 1, 128, 112)],
        "parts": shipyard,
        "exact": SHIPYARD_EXACT,
    },
    {
        # Slateport market's stalls, six of them, half a cell off the grid
        # (the east row's metatiles differ, its drawing does not). Lifted 12
        # like the awnings. Only the stall is its own: the pots and baskets
        # standing under it are not.
        "name": "slateport_stall",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (2, 41, 5, 3),
        # the paving and its shade under the stall, told apart pixel by
        # pixel from the posts drawn over it
        "ground": [0x211, 0x285],
        "ground_px": True,
        "keep": stall_keep(72),
        # its outer column as the map paints it (the paving's edge): under
        # the roof the goods stand on their own, not laid flat again
        "patch_cells": [(0, 0), (0, 1), (0, 2)],
        "parts": lambda: [stall(72, 57.5, ((24, 3, 55, 8), (9, 12, 12, 20), (60, 12, 69, 20)))],
        "exact": [(8, 11, 72, 29)],
    },
    {
        # the east row's stalls are a cell narrower
        "name": "slateport_stall_e",
        "layout": "LAYOUT_SLATEPORT_CITY",
        "rect": (10, 41, 4, 3),
        "ground": [0x211, 0x285],
        "ground_px": True,
        "keep": stall_keep(56),
        "patch_cells": [(3, 0), (3, 1), (3, 2)],
        "parts": lambda: [stall(56, 41.5, ((24, 3, 39, 8), (9, 12, 12, 20), (44, 12, 53, 20)))],
        "exact": [(8, 11, 56, 29)],
    },
    {
        "name": "gym",
        "layout": "LAYOUT_PETALBURG_CITY",
        "rect": (12, 4, 6, 5),
        # the bottom row is porch and the town's own ground
        "match_rows": (0, 4),
        "ground": [GRASS],
        "parts": gym,
        "exact": GYM_EXACT,
    },
    {
        # Petalburg's hedges. Not a building: a run of metatiles of any shape,
        # so each connected run becomes its own model, read column by column
        # off its drawing. The front, where a run ends to the south, is
        # drawn 11 rows tall. 23F is the rounded north end of a run.
        "name": "hedge",
        "components": {
            "secondary": "gTileset_Petalburg",
            "tiles": {0x23c, 0x23d, 0x23e, 0x23f, 0x244, 0x245, 0x246, 0x24c, 0x24d, 0x24e,
                      0x254, 0x255, 0x256, 0x264, 0x265, 0x266},
            "height": 11,
            # the row under the woods carries the crowns' lower leaves: the
            # trees draw those (voxel_tree.c EmitMissingRow), the hedge is
            # its plain twin
            "clean": {0x24d: 0x23d, 0x24e: 0x23d, 0x255: 0x23c, 0x256: 0x23e,
                      # an arm's inner corner (244, 246): the run turning into
                      # it draws its front across the arm's top; in 3D that
                      # front ends at the arm's side, and the arm is straight
                      0x244: 0x24c, 0x246: 0x24c},
            # and the run turning into those corners, from the east (244)
            # or the west (246), meets the arm with no slit
            "joins": {0x244: 1, 0x246: -1},
            # a run that goes on behind a house: drawn on under its roof as
            # the bare hedge is (23D over 245)
            "under": {0x23d: 0x245},
            "side_longest": True,
            # its shaded east band has texels of the lawn's colours
            "close": 2,
            # its shadow on the lawn, past its dark outline, is no hedge
            "outline": (57, 82, 0),
        },
        "ground": [GRASS],
    },
    {
        # Rustboro's stone blocks, any width and any number of storeys: every
        # one in the map is found by its corners and modelled from its own art.
        "name": "rustboro_stone",
        "kit": {"layout": "LAYOUT_RUSTBORO_CITY", "corner": 0x224, "top": {0x225},
                "end": {0x226}, "foot": 0x21c},
        "ground": [0x2BB, 0x2C3, GRASS],
        "parts": stone_block,
        "exact": lambda w, h, meta: flat_block_exact(w, h, 7),
    },
    {
        "name": "rustboro_olive",
        "kit": {"layout": "LAYOUT_RUSTBORO_CITY", "corner": 0x220, "top": {0x221, 0x222},
                "end": {0x223, 0x23F}, "foot": 0x240},
        "ground": [0x2BB, 0x2C3, GRASS],
        "parts": olive_block,
        "exact": lambda w, h, meta: flat_block_exact(w, h, 8),
    },
    {
        # The gym again: Rustboro's copy has its own roof and flanks.
        "name": "gym_rustboro",
        "layout": "LAYOUT_RUSTBORO_CITY",
        "rect": (24, 15, 6, 5),
        "ground": [0x2BB, 0x2C3, GRASS],
        "parts": gym,
        "exact": GYM_EXACT,
    },
    {
        # Route 110's Seaside Cycling Road: a deck on piers over the sea and
        # the paths it spans, read off its drawing block by block
        # (voxel_bridge.py)
        "name": "cycling_road",
        "bridge": True,
    },
    {
        # Rustboro's iron railings, read column by column like the hedges: a
        # railing along a row is all front, one down a column is a line on top
        # with its front where it ends. The bars keep their gaps (alpha).
        "name": "railing",
        "components": {
            "secondary": "gTileset_Rustboro",
            "tiles": {0x2A7, 0x2FC, 0x318, 0x319, 0x31A, 0x315, 0x31D, 0x320, 0x321,
                      0x32C, 0x32D, 0x2BE, 0x2BF, 0x2CD, 0x352, 0x2E9,
                      # the same railings over grass or a building's shade
                      0x2B7, 0x2C6, 0x2C7, 0x2D5, 0x2D7, 0x2DC, 0x2DE, 0x2DF,
                      0x2E6, 0x2E7, 0x2EC, 0x31B},
            "height": 12, "hull": 12, "bridge": 3, "block": 4,
            # drawn whole on the upper layer; the bottom one is the ground
            # with its grass edges, which the map paints under it
            "upper": True,
            # a railing down a column shows its bars on its sides: those of
            # the railing along a row
            "flank": 0x2A7,
        },
        "ground": [0x2BB, 0x2C3, GRASS],
    },
    {
        # The General tileset's grey picket fence, read column by column like
        # Rustboro's railings: along a row it is all front (posts 12 rows
        # tall), down a column a line of post tops with the row's posts and
        # rails on its sides. Every town and route that draws it shares it.
        "name": "fence",
        "components": {
            "primary": "gTileset_General",
            "tiles": {0x139, 0x13A, 0x140, 0x141, 0x142, 0x148, 0x149, 0x14A},
            "height": 12, "hull": 12, "bridge": 3, "block": 4,
            "upper": True,
            "flank": 0x149,
        },
        # under the fence its own bottom layer, the same in every one of its
        # metatiles and drawn whole by the grass: the patches lay that layer
        # a hair over the ground, and the commonest cell round a run (a path
        # beside it, the sea's edge along Slateport's market, the grass with
        # flowers) showed in a line at their edges in the game's view
        "ground": [GRASS],
        "ground_fixed": True,
    },
    {
        # the same grey fence in the Lavaridge tileset's own metatiles (Route
        # 112, Mt Chimney, Jagged Pass): drawn with the General's tiles
        "name": "fence_lavaridge",
        "components": {
            "secondary": "gTileset_Lavaridge",
            "tiles": {0x285, 0x286, 0x287, 0x28D, 0x28E, 0x28F, 0x293, 0x296, 0x297},
            "height": 12, "hull": 12, "bridge": 3, "block": 4,
            "upper": True,
            "flank": 0x296,
        },
        "ground": [0x271],
    },
    {
        # Lilycove's blue iron railings, read column by column like the fence
        "name": "railing_lilycove",
        "components": {
            "secondary": "gTileset_Lilycove",
            "tiles": {0x249, 0x250, 0x251, 0x252, 0x258, 0x25A},
            "height": 12, "hull": 12, "bridge": 3, "block": 4,
            "upper": True,
            "flank": 0x251,
        },
        "ground": [GRASS],
    },
    {
        # The General tileset's white curb stones (Route 110's paths, the
        # edges of lawns and piers in many towns): the same slabs as
        # Slateport's, a top in a grey frame and 3 rows of front.
        "name": "curb",
        "components": {
            "primary": "gTileset_General",
            "tiles": {0x03D, 0x03E, 0x03F, 0x045, 0x047, 0x04D, 0x04E, 0x04F,
                      0x055, 0x056, 0x057, 0x05D, 0x05E, 0x05F},
            "height": 3, "block": 4,
            # the slab's middle (05E) has no stone drawn in it: it is the
            # plain paving inside a frame of curb (Southern Island, Route
            # 121's landing), and on its own the road itself - Rustboro's
            # and Mauville's streets, which stood up in curb blocks
            "fill": {0x05E},
            # the Battle Frontier paves its walks with the same stones: a
            # floor to walk on, not a curb
            "skip": ("LAYOUT_BATTLE_FRONTIER_OUTSIDE_WEST", "LAYOUT_BATTLE_FRONTIER_OUTSIDE_EAST"),
        },
        # every cell as the map paints it under the slabs: one ground for a
        # whole run laid paving over the lawn drawn beside it (Slateport's
        # beach steps)
        "patch_own": True,
        "ground": [GRASS, 0x001],
    },
    {
        # Mauville's fence of short stakes (Mauville, Routes 111 and 117):
        # each stake a round top over 6 rows of trunk, read column by column
        "name": "stakes",
        "components": {
            "secondary": "gTileset_Mauville",
            "tiles": {0x273, 0x27A, 0x27B, 0x27C, 0x27D, 0x280, 0x282, 0x288, 0x28A, 0x290,
                      0x291, 0x292, 0x2B1, 0x2B2, 0x2B9, 0x2BA},
            "height": 6, "hull": 6, "bridge": 2, "block": 4, "upper": True,
        },
        "ground": [GRASS],
    },
    {
        # Lavaridge's hot spring: its rim of round stones, 4 rows of front
        "name": "spring_rim",
        "components": {
            "secondary": "gTileset_Lavaridge",
            "tiles": {0x299, 0x29A, 0x29B, 0x2A1, 0x2A5, 0x2A9, 0x2AD, 0x2B1, 0x2B2, 0x2B3,
                      0x2B4, 0x2B5},
            "height": 4,
        },
        "ground": [0x2A3, 0x31D, 0x2A8, 0x2BC],
    },
    {
        # the wooden benches by the spring, two to a cell, 4 rows of front
        "name": "spring_bench",
        "components": {
            "secondary": "gTileset_Lavaridge",
            "tiles": {0x29C, 0x29E},
            "height": 4, "block": 1,
        },
        "ground": [0x2A8, 0x274, 0x2AE],
    },
    {
        # Slateport's harbour curbs: white stone slabs along the pier's edge,
        # a top in a grey frame and 3 rows of front, read column by column
        # like the hedges; a run down a column is a line of slab tops.
        "name": "slateport_curb",
        "components": {
            "secondary": "gTileset_Slateport",
            "tiles": {0x245, 0x24D, 0x255, 0x25D, 0x2FF, 0x2FE, 0x23C, 0x234, 0x2CF},
            "height": 3, "block": 4,
        },
        # every cell as the map paints it under the slabs (the sea beside a
        # corner, the pier's bricks under a row): one ground for a whole run
        # was the sea under a pier's edge
        "patch_own": True,
        "ground": [0x202],
        # and the pier's bricks under the slabs themselves: the commonest
        # ground round a run along the edge is the sea or the pier's rocky
        # side, and a patch, lying a hair above the ground, shows a line of
        # whatever is under it along its edges in the game's view
        "ground_fixed": True,
        # the paving and the lawn beside a run (the Contest Hall's east
        # edge), drawn in the run's own cells, rose with it as a ragged
        # lump: the slabs are white, grey and their outline, never these
        "ground_rgb": {(65, 180, 131), (115, 197, 164), (164, 213, 197), (205, 205, 222),
                       (230, 230, 238)},
    },
    {
        # The grey rock in the sea, found by its four tiles wherever they are
        # drawn (voxel_props.py): over the water round it, by a sandbank, by
        # a shore. A low dome, as deep as it is drawn tall below its crest.
        # The water round it stays the map's own, animated.
        "name": "sea_rock",
        "props": "sea_rock",
        "ground": [0x170],
        "rise": 1.0,
        "step": 2,
        # its foam on the water
        "ring": [(222, 230, 238)],
    },
    {
        # A boulder on the sand or the grass, a cell across (Route 106's beach,
        # Route 111's desert, the Safari Zone).
        "name": "sand_boulder",
        "props": "sand_boulder",
        "ground": [0x124],
        "rise": 1.0,
        "step": 2,
    },
    {
        # Slateport market's crates, slatted or labelled: a lid and a front
        # of 8 rows, drawn whole on the upper layer over the paving. One
        # model per cell, placed at every crate drawn alike.
        "name": "crate",
        "components": {
            "secondary": "gTileset_Slateport",
            "tiles": {0x33E, 0x33F},
            "height": 8, "block": 1, "upper": True,
        },
        "ground": [0x211],
    },
    {
        # three crates one on another: the lid, then three fronts (24 rows)
        # down into the cell below
        "name": "crate_stack",
        "components": {
            "secondary": "gTileset_Slateport",
            "tiles": {0x32E, 0x32F, 0x336, 0x337},
            "height": 24, "upper": True,
        },
        "ground": [0x211],
    },
] + [
    # Slateport market's goods: round things, each a dome off its own
    # drawing like the rocks, on the paving's own cells
    # a pot, turned smooth (ProjLathe): its foot, its belly, a neck under the
    # lip round its dark mouth. As a Mound it was a lump in steps.
    # Static: one mesh drawn at each of its places, not copied into the
    # market's chunk at every one (it was, as a Mound, for its triangles).
    {"name": "urn", "props": "urn", "clip_art": True, "ground": [0x211], "shape": "lathe", "static": True,
     "lathe": {"cx": 8, "cz": 13, "squash": 0.7,
               "profile": [(0, 6), (3.5, 8.5), (7, 7.5), (9, 5.1), (10, 5.5)]}},
] + [
    # the baskets: an oval drawn round, its rim and goods on top, 5 rows of
    # wicker in front. A smooth drum under its drawing (ProjLathe), cut to
    # it: exact to its outline (a 24-sided cylinder missed it by a pixel)
    {"name": name, "props": name, "ground": [0x211], "shape": "lathe", "clip_art": True,
     "lathe": {"cx": 8, "cz": 11, "squash": 0.7, "seg": 16, "profile": [(0, 8.5), (5, 8.5)]}}
    for name in ("basket_green", "basket_orange", "basket_red")
] + [
    # a pile of fruit: a smooth dome under its drawing (ProjDome)
    {"name": "fruit", "props": "fruit", "clip_art": True, "ground": [0x211], "shape": "dome", "static": True,
     "dome": {"cx": 8.5, "cz": 9, "rx": 8.5, "rz": 7.5, "h": 7}},
    {
        # flowers in a low box: the box's front 3 rows, the flowers on its top
        "name": "flower_crate",
        "components": {
            "secondary": "gTileset_Slateport",
            "tiles": {0x20B, 0x28D, 0x213, 0x214},
            "height": 3, "block": 1, "upper": True,
        },
        "ground": [0x211],
    },
] + [
    {
        # The brown stack in the sea: a peak, taller than it is deep.
        "name": "sea_stack",
        "props": "sea_stack",
        "ground": [0x170],
        "rise": 1.6,
        "step": 4,
        # its grey shadow on the water
        "ring": [(131, 131, 139)],
    },
    {
        # The same stack in Fallarbor's beige (Routes 114 and 115): in the
        # sea, and on Route 115's terraces as their mounds (the cells under
        # it are the terrace's top, flat: voxel_terraces.py)
        "name": "sea_stack_fallarbor",
        "props": "sea_stack_fallarbor",
        "ground": [0x170],
        "rise": 1.6,
        "step": 4,
        "ring": [(131, 131, 139)],
    },
    {
        # the boulder in Fallarbor's beige, on the mountain's top
        "name": "sand_boulder_fallarbor",
        "props": "sand_boulder_fallarbor",
        "ground": [0x241],
        "rise": 1.0,
        "step": 2,
    },
    {
        "name": "devon_corporation",
        "layout": "LAYOUT_RUSTBORO_CITY",
        "rect": (7, 7, 10, 9),
        "ground": [0x2BB, 0x2C3, GRASS],
        "parts": devon,
        "exact": DEVON_EXACT,
    },
    {
        "name": "rustboro_fountain",
        "layout": "LAYOUT_RUSTBORO_CITY",
        "rect": FOUNTAIN_RECT + (3, 3),
        "ground": [0x2BB, 0x2C3, GRASS],
        "parts_art": fountain,
        # judged against its drawing, jet included (its texture keeps the
        # jet on a band of its own)
        "drawing": fountain_drawing,
        "exact": [(0, 0, 48, 48)],
    },
    {
        # Every town's Pokemon Center has this one ground floor.
        "name": "pc1f",
        "interior": {"layout": "LAYOUT_POKEMON_CENTER_1F", "ground": [0x202],
                     "pieces": POKEMON_CENTER_1F, "open": CENTER_1F_OPEN},
    },
    {
        "name": "pc2f",
        "interior": {"layout": "LAYOUT_POKEMON_CENTER_2F", "ground": [0x202],
                     "pieces": POKEMON_CENTER_2F, "open": CENTER_2F_OPEN},
    },
    {
        # Every Poke Mart but the department store has this one room.
        "name": "mart",
        "interior": {"layout": "LAYOUT_MART", "ground": [0x201], "pieces": MART,
                     "open": MART_OPEN,
                     "shade": [0x202, 0x204, 0x206, 0x208, 0x20A]},
    },
    {
        "name": "brendan_1f",
        "interior": {"layout": "LAYOUT_LITTLEROOT_TOWN_BRENDANS_HOUSE_1F", "ground": [0x201],
                     "pieces": brendan_1f()},
    },
    {
        "name": "brendan_2f",
        "interior": {"layout": "LAYOUT_LITTLEROOT_TOWN_BRENDANS_HOUSE_2F", "ground": [0x201],
                     "pieces": brendan_2f()},
    },
    {
        "name": "may_1f",
        "interior": {"layout": "LAYOUT_LITTLEROOT_TOWN_MAYS_HOUSE_1F", "ground": [0x201],
                     "pieces": may_1f()},
    },
    {
        "name": "may_2f",
        "interior": {"layout": "LAYOUT_LITTLEROOT_TOWN_MAYS_HOUSE_2F", "ground": [0x201],
                     "pieces": may_2f()},
    },
    {
        "name": "lab",
        "interior": {"layout": "LAYOUT_LITTLEROOT_TOWN_PROFESSOR_BIRCHS_LAB", "ground": [0x202],
                     "pieces": lab()},
    },
    {
        # after the starter: the boxes by the machine give way to a table.
        # The rest of the furniture is the lab's, tile for tile, and stands
        # here as it is, its back wall too; the side walls are its own.
        "name": "lab_table",
        "interior": {"layout": "LAYOUT_LITTLEROOT_TOWN_PROFESSOR_BIRCHS_LAB_WITH_TABLE",
                     "ground": [0x202],
                     "pieces": [piece("table", [(128, 64, 176, 89)], 8, leave=LAB_SHADOW,
                                      solid=True)]
                     + [pc for pc in lab() if pc["name"] in ("side_w", "side_e")]},
    },
    {
        "name": "lavaridge_pc1f",
        "interior": {"layout": "LAYOUT_LAVARIDGE_TOWN_POKEMON_CENTER_1F", "ground": [0x202],
                     "pieces": LAVARIDGE_CENTER_1F, "open": CENTER_1F_OPEN},
    },
    {
        # Oldale's first house, and eight more maps'
        "name": "house1",
        "interior": {"layout": "LAYOUT_HOUSE1", "ground": [0x223], "pieces": house1()},
    },
    {
        # Oldale's second house, and eleven more maps'
        "name": "house2",
        "interior": {"layout": "LAYOUT_HOUSE2", "ground": [0x223], "pieces": house2()},
    },
    {
        # Petalburg's second house, and two more maps'
        "name": "house_with_bed",
        "interior": {"layout": "LAYOUT_HOUSE_WITH_BED", "ground": [0x223], "pieces": own_shell(house_with_bed())},
    },
] + [
    {
        "name": layout[len("LAYOUT_"):].lower(),
        "interior": {"layout": layout, "ground": [ground],
                     "pieces": own_shell(plain_room(width, height, plain_x, sides))},
    }
    for layout, width, height, ground, plain_x, sides in PLAIN_ROOMS
] + [
    {
        # Mr. Briney's house, by the dock on Route 104
        "name": "briney_room",
        "interior": {"layout": "LAYOUT_ROUTE104_MR_BRINEYS_HOUSE", "ground": [0x229],
                     "pieces": own_shell(briney_room())},
    },
] + [
    {
        "name": layout[len("LAYOUT_"):].lower(),
        "interior": {"layout": layout, "ground": [ground],
                     "pieces": own_shell(stair_room(width, height, plain_x, doors, *extra))},
    }
    for layout, width, height, ground, plain_x, doors, *extra in STAIR_ROOMS
] + [
    {
        # Rustboro's Pokemon school
        "name": "school_room",
        "interior": {"layout": "LAYOUT_RUSTBORO_CITY_POKEMON_SCHOOL", "ground": [0x201],
                     "pieces": own_shell(school_room())},
    },
    {
        # The Pretty Petal flower shop on Route 104
        "name": "flower_shop_room",
        "interior": {"layout": "LAYOUT_ROUTE104_PRETTY_PETAL_FLOWER_SHOP", "ground": [0x201],
                     "pieces": own_shell(flower_shop_room())},
    },
    {
        # Rustboro's gym: Roxanne's maze. Before the other gyms: a room stands
        # only the pieces of the rooms modelled before it (reuse_pieces), and
        # the statues every gym has by its door are modelled here
        "name": "rustboro_gym",
        "interior": {"layout": "LAYOUT_RUSTBORO_CITY_GYM", "ground": [0x201],
                     "shade": [0x202, 0x203, 0x204, 0x216, 0x22f, 0x237],
                     "pieces": rustboro_gym()},
    },
    {
        # Dewford's gym: Brawly's maze
        "name": "dewford_gym",
        "interior": {"layout": "LAYOUT_DEWFORD_TOWN_GYM", "ground": [0x210],
                     "shade": DEWFORD_GYM_FLOOR, "pieces": own_shell(dewford_gym())},
    },
    {
        # Petalburg's gym: Norman's rooms
        "name": "petalburg_gym",
        "interior": {"layout": "LAYOUT_PETALBURG_CITY_GYM", "ground": [0x201, 0x22B],
                     "shade": [0x209, 0x212, 0x213, 0x214, 0x22A, 0x232,
                               0x216, 0x22C],
                     "pieces": own_shell(petalburg_gym())},
    },
]
