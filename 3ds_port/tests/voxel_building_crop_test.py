"""What of a model's texture the export puts on its page
(gen_voxel_buildings.read_crop, pages_crops, decal_cells; voxel_building.Decal
`boxes`), on synthetic models.

A page packs many models' images side by side; a face reads its model's
image at an offset (the image's spot less its crop's corner). Cropped to
what is opaque alone, a face that reads clear texels beyond it - a floor
decal's cell round a few marks, a card's margin - reads the next image on
the page instead: on the console, the laboratory's desk tops in a strip on
its floor, a sliver of another model under Brendan's sink. Here:

- read_box spans exactly the texels a face's uv can sample;
- read_crop keeps every texel a face reads, and the old crop (the opaque
  box) did not - the case the console showed;
- packed on one page, every face of every model reads inside its own
  image, and with the old crop a face read its neighbour's;
- a page that the read crops would make larger than the console's page
  falls back, largest growth first, to the opaque box, and says which;
- a floor decal's quad covers only what its cell paints (decal_cells,
  Decal `boxes`), so its reads add no clear texels to the page."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_voxel_buildings as g  # noqa: E402
import voxel_building as vb  # noqa: E402
from PIL import Image  # noqa: E402

FAILED = []
MARK, OTHER = (197, 74, 65, 255), (98, 98, 197, 255)


def check(name, ok, detail=""):
    print("%s %-50s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILED.append(name)


def decal_model(name, colour, marks, cells=(1, 1)):
    """A model that is only a floor decal: its art a cell's worth of clear
    texels per cell with `marks` ((x0, y0, x1, y1), opaque) in them, each
    cell's quad over the whole cell - as Decal drew it before `boxes`."""
    w, h = cells
    art = Image.new("RGBA", (w * 16, h * 16), (0, 0, 0, 0))
    for box in marks:
        art.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), colour), box[:2])
    m = vb.Model(name, art, [vb.Decal(name + "_floor", [(i, j) for j in range(h)
                                                       for i in range(w)], 0)],
                 cells, 0)
    m.spec = {}
    return m


def inside(box, outer):
    return (box[0] >= outer[0] and box[1] >= outer[1]
            and box[2] <= outer[2] and box[3] <= outer[3])


def page_reads(models, crops):
    """Each face's read box on a page packed with `crops`, against its own
    image's rectangle there: the faces reading outside it."""
    used = list(range(len(models)))
    art_key, art_img, _, _ = g.page_images(models, used, crops)
    (_, _), spots = g.pack_atlas(list(art_img.items()))
    stray = []
    for k in used:
        m = models[k]
        sx, sy = spots[art_key[k]]
        cx, cy = crops[m.name][:2]
        w, h = art_img[art_key[k]].size
        own = (sx, sy, sx + w, sy + h)
        for (tri, _, tag) in m.mesh.tris:
            u0, v0, u1, v1 = g.read_box([(p[3], p[4]) for p in tri])
            on_page = (u0 - cx + sx, v0 - cy + sy, u1 - cx + sx, v1 - cy + sy)
            if not inside(on_page, own):
                stray.append((m.name, tag, on_page, own))
    return stray


def test_read_box():
    check("read_box: a whole cell", g.read_box([(0, 0), (16, 0), (16, 16), (0, 16)])
          == (0, 0, 16, 16))
    check("read_box: a pixel's middle reads that pixel",
          g.read_box([(3.5, 4.5), (3.5, 4.5), (3.5, 4.5)]) == (3, 4, 4, 5))
    check("read_box: past the art's edge, unclipped",
          g.read_box([(-2, 0), (20, 0), (20, 3)]) == (-2, 0, 20, 3))


def test_read_crop():
    m = decal_model("desk", MARK, [(6, 6, 10, 10)])
    opaque = m.art.getbbox()
    crop = g.read_crop(m)
    reads = [g.read_box([(p[3], p[4]) for p in tri]) for (tri, _, _) in m.mesh.tris]
    check("read_crop keeps every texel a face reads",
          all(inside(r, crop) for r in reads), "crop %s" % (crop,))
    check("the opaque box (the old crop) did not",
          not all(inside(r, opaque) for r in reads), "opaque %s" % (opaque,))
    check("read_crop keeps what is opaque", inside(opaque, crop))


def test_page():
    # two decals side by side on one page, as the laboratory's desks and its
    # floor: a mark in a corner of each one's cell, the rest clear
    models = [decal_model("desk", MARK, [(0, 12, 16, 16)]),
              decal_model("rug", OTHER, [(0, 0, 4, 16)])]
    old = {m.name: m.art.getbbox() for m in models}
    new = {m.name: g.read_crop(m) for m in models}
    stray = page_reads(models, old)
    check("old crop: a face reads past its image on the page", len(stray) > 0,
          "%d face(s)" % len(stray))
    check("read crop: every face reads inside its own image",
          page_reads(models, new) == [], str(page_reads(models, new)[:1]))


def test_fallback():
    # a model whose faces read far past a small drawing (a tower's facade
    # projected over rows it does not draw) and does not fit the page so
    small = decal_model("wide", MARK, [(0, 0, 8, 8)])
    wide = vb.Model("wide", small.art, [], (1, 1), 0)
    wide.spec = {}
    wide.mesh.tri((0, 0, 0, 0, 0), (1, 0, 0, 600, 0), (1, 1, 0, 600, 600), 1.0, "facade")
    other = decal_model("plain", OTHER, [(0, 0, 16, 16)])
    models = [wide, other]
    crops = {m.name: g.read_crop(m) for m in models}
    opaque = {m.name: m.art.getbbox() for m in models}
    page, narrowed = g.pages_crops(models, [0, 1], crops, opaque, {}, "test")
    check("too big a page: the model reading most falls back", narrowed == ["wide"],
          str(narrowed))
    check("  to its opaque box", page["wide"] == opaque["wide"], str(page["wide"]))
    check("  the others keep what they read", page["plain"] == crops["plain"])
    page, narrowed = g.pages_crops([other], [0], {"plain": crops["plain"]},
                                   {"plain": opaque["plain"]}, {}, "test")
    check("a page that fits narrows nothing", narrowed == [])


def test_decal_boxes():
    art = Image.new("RGBA", (32, 16), (0, 0, 0, 0))
    art.paste(Image.new("RGBA", (5, 3), MARK), (18, 9))      # in cell (1, 0)
    cells, boxes = g.decal_cells(art, [(10, 4), (11, 4)], (10, 4), 0)
    check("decal_cells: only the cells that paint", cells == [(1, 0)], str(cells))
    check("decal_cells: the box of what a cell paints", boxes == {(1, 0): (2, 9, 7, 12)},
          str(boxes))
    m = vb.Model("d", art, [vb.Decal("d_floor", cells, 0, boxes=boxes)], (2, 1), 0)
    xs = [p[0] for (tri, _, _) in m.mesh.tris for p in tri]
    zs = [p[2] for (tri, _, _) in m.mesh.tris for p in tri]
    # (lifted half a pixel, and as far south: the same pixels at 45 degrees)
    check("Decal boxes: the quad covers only the marks",
          (min(xs), max(xs), min(zs), max(zs)) == (18.0, 23.0, 9.5, 12.5),
          str((min(xs), max(xs), min(zs), max(zs))))
    m.spec = {}
    check("  and reads nothing clear: its crop is its marks",
          g.read_crop(m) == art.getbbox(), str(g.read_crop(m)))


def main():
    test_read_box()
    test_read_crop()
    test_page()
    test_fallback()
    test_decal_boxes()
    print("%d failure(s)" % len(FAILED))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
