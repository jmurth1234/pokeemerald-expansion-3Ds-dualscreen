#!/usr/bin/env python3
"""gen_build_id.py ELF ABI OUT: the build's identity for local wireless play.

A SHA-256 of the executable without its debug information (which holds the
paths it was built in) and of the game data's ABI (engine/abi.bin, itself a
digest of every game-data file). Two systems link only when theirs are the
same (3ds_port/src/3ds_local.c): any change to the code or the data is
another build, whose link messages could disagree with this one's.
"""
import hashlib
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    elf, abi, out = (Path(p) for p in sys.argv[1:])
    digest = hashlib.sha256()
    digest.update(b"emerald3ds build id 1\0")
    digest.update(elf.read_bytes())
    digest.update(abi.read_bytes())
    out.parent.mkdir(parents=True, exist_ok=True)
    data = digest.digest()
    if not out.exists() or out.read_bytes() != data:
        out.write_bytes(data)
    print("build id: %s" % data.hex()[:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
