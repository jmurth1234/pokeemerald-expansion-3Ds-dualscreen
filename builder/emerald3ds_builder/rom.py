"""Recognise the player's ROM.

A release's recipe names the exact ROM its data was built from; the builder
accepts that ROM and nothing else. The stock release expects Pokémon Emerald
(USA, Europe), 16 MiB, SHA-1 f3ae088181bf583e55daf962a92bb46f4f1d07b7, and an
expansion-based release expects the expansion ROM it was built from. A trimmed
dump (trailing 0xFF removed) is padded back before it is checked; a .zip
holding a single .gba is opened directly. The ROM is only ever read into
memory: it is never copied, written or sent anywhere.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .errors import BuilderError

SUPPORTED_SHA1 = "f3ae088181bf583e55daf962a92bb46f4f1d07b7"
ROM_SIZE = 16 * 1024 * 1024
# A stock Emerald build is 16 MiB; a pokeemerald-expansion build does not fit
# and is padded to the GBA's largest cartridge, 32 MiB. A trimmed dump is
# padded back to whichever of the two sizes makes its SHA-1 match.
ROM_SIZES = (16 * 1024 * 1024, 32 * 1024 * 1024)
KNOWN_CODES = {
    "BPEE": "Pokemon Emerald (USA, Europe)",
    "BPEJ": "Pokemon Emerald (Japan)",
    "BPES": "Pokemon Emerald (Spain)",
    "BPED": "Pokemon Emerald (Germany)",
    "BPEF": "Pokemon Emerald (France)",
    "BPEI": "Pokemon Emerald (Italy)",
    "AXVE": "Pokemon Ruby",
    "AXPE": "Pokemon Sapphire",
    "BPRE": "Pokemon FireRed",
    "BPGE": "Pokemon LeafGreen",
}


@dataclass
class Rom:
    data: bytes
    sha1: str
    title: str
    code: str
    source: Path


def _read(path: Path) -> bytes:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".gba", ".agb", ".bin"))]
            if len(names) != 1:
                raise BuilderError("The ZIP file must contain exactly one .gba file.")
            return zf.read(names[0])
    return path.read_bytes()


def header(data: bytes) -> tuple[str, str]:
    if len(data) < 0xC0:
        return "", ""
    title = data[0xA0:0xAC].split(b"\0")[0].decode("ascii", "replace")
    code = data[0xAC:0xB0].decode("ascii", "replace")
    return title, code


def load_rom(path: Path, expected_sha1: str = SUPPORTED_SHA1) -> Rom:
    path = Path(path)
    if not path.is_file():
        raise BuilderError("The ROM file was not found:\n%s" % path.name)
    try:
        data = _read(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuilderError("The ROM file could not be read.", str(exc)) from exc
    if len(data) > ROM_SIZES[-1]:
        raise BuilderError("This file is larger than a GBA cartridge; it is not the supported ROM.")
    # A trimmed dump had the cartridge's trailing 0xFF fill cut off; try the
    # file as it is and padded back to each cartridge size, and take the one
    # whose SHA-1 the release expects.
    candidates = [data]
    candidates += [data + b"\xff" * (size - len(data)) for size in ROM_SIZES if size > len(data)]
    sha1 = ""
    for candidate in candidates:
        if hashlib.sha1(candidate).hexdigest() == expected_sha1:
            data, sha1 = candidate, expected_sha1
            break
    else:
        data, sha1 = candidates[-1], hashlib.sha1(candidates[-1]).hexdigest()
    title, code = header(data)
    if sha1 != expected_sha1:
        what = KNOWN_CODES.get(code)
        if what and code != "BPEE":
            raise BuilderError("This ROM is %s. Only Pokemon Emerald (USA, Europe) is supported." % what)
        if code == "BPEE":
            raise BuilderError(
                "This is a Pokemon Emerald (USA, Europe) ROM, but not the one this release was built from.",
                "Patched, hacked or bad dumps are not supported. Use the exact ROM the release expects "
                "(SHA-1 %s)." % expected_sha1)
        raise BuilderError("This file is not the ROM this release expects.",
                           "Expected a Pokemon Emerald ROM with SHA-1 %s." % expected_sha1)
    return Rom(data=data, sha1=sha1, title=title, code=code, source=path)
