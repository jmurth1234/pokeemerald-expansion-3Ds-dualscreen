"""Authored exterior glass regions, without recolouring frames or roof art."""

NIGHT_WINDOWS = {
    "dewford_house": ((34, 46, 39, 52), (41, 46, 46, 52)),
    "dewford_hall": ((50, 46, 55, 52), (57, 46, 62, 52)),
    "dewford_gym_exterior": ((11, 50, 21, 53), (27, 50, 37, 53), (75, 50, 85, 53),
                             (49, 62, 55, 77), (57, 62, 63, 77)),
    "littleroot_house_w": ((17, 62, 23, 68), (25, 62, 31, 68)),
    "littleroot_house_e": ((49, 62, 55, 68), (57, 62, 63, 68)),
    "littleroot_lab": tuple((xpos, top, xpos + 5, bottom)
                           for xpos in (18, 25, 34, 41) for top, bottom in ((61, 63), (64, 68))),
    "oldale_house": tuple((xpos, top, xpos + 5, bottom)
                          for xpos in (34, 41) for top, bottom in ((43, 49), (50, 53)))
                    + ((20, 48, 28, 52),),
    "kit_house_4": tuple((xpos, top, xpos + 5, bottom)
                         for xpos in (34, 41) for top, bottom in ((43, 45), (46, 49), (50, 53)))
                   + ((20, 48, 28, 52),),
    "kit_house_5": tuple((xpos, top, xpos + 5, bottom)
                         for xpos in (18, 25, 50, 57) for top, bottom in ((43, 45), (46, 49), (50, 53)))
                   + ((36, 48, 44, 52),),
    "flower_shop": tuple((xpos, top, xpos + 6, bottom)
                         for xpos in (9, 65, 81) for top, bottom in ((42, 48), (49, 52))),
    "pokemon_center": ((17, 45, 31, 61),),
    "poke_mart": ((17, 45, 31, 61),),
    "gym": ((11, 50, 21, 53), (27, 50, 37, 53), (75, 50, 85, 53),
            (49, 62, 55, 77), (57, 62, 63, 77)),
    "gym_rustboro": ((11, 50, 21, 53), (27, 50, 37, 53), (75, 50, 85, 53),
                     (49, 62, 55, 77), (57, 62, 63, 77)),
    "devon_corporation": tuple((xpos, top, xpos + 4, top + 12)
                               for xpos in (18, 42, 122, 138) for top in (42, 74))
                         + tuple((xpos, top, xpos + 2, top + 5)
                                 for xpos in (55, 71, 87, 103) for top in (58, 66, 90, 98))
                         + tuple((xpos, 98, xpos + 4, 102)
                                 for xpos in (10, 26, 42, 114, 130, 146))
                         + tuple((xpos, 105, xpos + 4, 110)
                                 for xpos in (10, 18, 26, 34, 42, 114, 122, 130, 138, 146)),
}


def window_mask(art, rectangles):
    width, height = art.size
    mask = bytearray(width * height)
    pixels = art.load()
    for left, top, right, bottom in rectangles:
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("window rectangle is outside the building art")
        for ypos in range(top, bottom):
            for xpos in range(left, right):
                red, green, blue, alpha = pixels[xpos, ypos]
                if alpha >= 128 and red >= 90 and green > red and blue > red + 12:
                    mask[ypos * width + xpos] = min(255, 128 + (red + green + blue) // 6)
    return mask


def component_rectangles(art, predicate, top):
    pixels = art.load()
    remaining = {(xpos, ypos) for ypos in range(top, art.height)
                 for xpos in range(art.width) if predicate(pixels[xpos, ypos])}
    rectangles = []
    while remaining:
        pending = [remaining.pop()]
        component = []
        while pending:
            xpos, ypos = pending.pop()
            component.append((xpos, ypos))
            for neighbor in ((xpos - 1, ypos), (xpos + 1, ypos),
                             (xpos, ypos - 1), (xpos, ypos + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        rectangles.append((min(xpos for xpos, ypos in component),
                           min(ypos for xpos, ypos in component),
                           max(xpos for xpos, ypos in component) + 1,
                           max(ypos for xpos, ypos in component) + 1))
    return sorted(rectangles)


def building_mask(art, name):
    name = name.removesuffix("_bare")
    rectangles = NIGHT_WINDOWS.get(name, ())
    if name.startswith("rustboro_stone_"):
        frames = component_rectangles(art, lambda pixel: pixel == (65, 74, 106, 255), 48)
        rectangles = tuple(rect for rect in frames
                           if rect[2] - rect[0] == 10 and rect[3] - rect[1] == 10)
    elif name.startswith("rustboro_olive_"):
        panes = component_rectangles(art, lambda pixel: pixel[3] >= 128 and pixel[0] >= 90
                                     and pixel[1] > pixel[0] and pixel[2] > pixel[0] + 12, 48)
        rectangles = tuple(rect for rect in panes
                           if 2 <= rect[2] - rect[0] <= 12 and 2 <= rect[3] - rect[1] <= 12)
    mask = window_mask(art, rectangles)
    if name == "devon_corporation":
        for left, top, right, bottom in rectangles:
            for ypos in range(top, bottom):
                for xpos in range(left, right):
                    red, green, blue, alpha = art.getpixel((xpos, ypos))
                    if alpha >= 128 and red >= 32 and green > red + 2 and blue > red + 12:
                        mask[ypos * art.width + xpos] = 180
    if rectangles and not any(mask):
        raise ValueError("%s has no glass pixels in its authored windows" % name)
    return mask
