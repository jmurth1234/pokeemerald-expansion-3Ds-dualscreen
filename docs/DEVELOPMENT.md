# Development

## Requirements

- devkitPro with devkitARM, libctru, citro2d, citro3d and 3dstools
  (`pacman -S --needed libctru citro2d citro3d 3dstools`), including its
  `picasso` shader assembler.
- A native host C/C++ compiler for the decomp tools and the host tests
  (on Windows, MSYS2 MinGW64's gcc/g++ on `PATH`).
- Python 3.11+ with Pillow.
- git.

## Getting a tree

The repository holds the port and the changes it makes to the decompilation,
not the decompilation itself:

```
python tools/bootstrap.py
```

clones pret/pokeemerald at the commit pinned in `upstream.lock` into
`build/upstream`, applies `patches/pokeemerald/*.patch` in order, and places
`3ds_port/`, `tools/` and `builder/` inside it. `--make` also builds the
decomp tools and the 3DSX there (`--jobs N`). Re-running it refreshes the
port files without touching the upstream checkout unless `--clean` is given.

`--upstream pokeemerald-expansion` builds the same port on the
`rh-hideout/pokeemerald-expansion` tag pinned in `upstream.lock` instead,
applying `patches/pokeemerald-expansion/*.patch`:

```
python tools/bootstrap.py --upstream pokeemerald-expansion --dir build/expansion --make
```

The port code is shared: where the trees differ it branches on
`PORT_EXPANSION` (defined for expansion builds), and the graphics and
generated headers the port reads from the tree are built on demand by the
port Makefile.

The expansion uses its own shop/trade controls and link engine. The original
port's local-wireless adapter is currently enabled only for the original game.
Both trees share the renderer, and CI builds and verifies both variants.

## Building

From `build/upstream/3ds_port` (devkitPro's shell on Windows, or `build.ps1`):

```
make -j8 PYTHON=python3          # emerald3ds.3dsx, data embedded
make verify                      # host tests + link audit
make release                     # dist/Emerald3DS.3dsx, engine files only
make pak                         # build/emerald3ds.pak from this build's data
make devdata                     # build/devdata/ for the loose backend
```

Options: `VOXEL=0`, `VOXEL_LIGHTING=0`, `SHOW_FPS=0`, `EMBED_DATA=0`,
`DATA_BACKEND=1|2|3` (force romfs, loose or pak), `SAMPLE_OVERLAY_DIR=...`
(replacement sample .bin files, for private experiments), `VOXEL_STEREO=1|0`
(what OPTIONS' 3D WORLD on AUTO does: the voxel world per eye with the 3D
slider on every model, or on none; unset, a New 3DS takes it), `FORCE_SLIDER=1.0` (a fixed 3D slider for an
emulator without one; Azahar passes its own when `factor_3d` is non-zero in
its config). Per-machine
settings can go in `3ds_port/local.mk`, which is never committed.

Game translation units have no header dependency tracking: after editing a
shared header, delete the affected `build/root/src/*.o`.

## Testing on the console

- **Development build**: copy `emerald3ds.3dsx` to `/3ds/emerald3ds/` and
  start it from the Homebrew Launcher. It reads its data from its own RomFS.
- **Loose data**: `make devdata`, then
  `python tools/dev_assets.py --dest <SD root>` (or `--ftp HOST:PORT` for an FTP
  server on the console) copies only the files that changed into
  `/3ds/emerald3ds/devdata/` and writes the marker that selects the loose
  backend. Delete the marker to go back.
- **Exactly the release**: `make release pak`, copy `dist/Emerald3DS.3dsx`
  and `build/emerald3ds.pak`.

The log is `/3ds/emerald3ds/port.log`, written only if an empty file named
`/3ds/emerald3ds/debug.txt` exists on the SD card (create it for development
builds); the previous session is kept as `port-prev.log`. Emulator runs (Azahar) are useful for
diagnosis; acceptance is on hardware — the SD card's latency, the linear heap
and VRAM behave differently there.

## Host tests

### Expansion Mega Evolution progression

The expansion patch gives the Mega Ring and the chosen starter's stone during
Steven's Route 118 meeting after the fifth badge. The ground-floor Devon employee
in Rustboro also grants these rewards, so saves past that meeting can catch up.
The starter must be fully evolved and hold its stone; press START at battle move
selection to select Mega Evolution. The engine limits it to once per trainer per
battle and restores the normal form afterward.

Devon grants additional one-time gifts according to badges:

| Badges | Stones |
| --- | --- |
| 6 | Manectite, Mawilite, Absolite |
| 7 | Gardevoirite, Aggronite, Banettite |
| 8 | Cameruptite, Sharpedonite, Glalitite, Altarianite |

Alternative sources share the same receipt flags and badge requirements:
Wattson's New Mauville reward conversation (Manectite), the Fossil Maniac's
brother on Route 114 (Mawilite), and the berry-giving woman on Route 120
(Absolite). Devon can supply any of these missed gifts. After the Hall of Fame,
the employee opens a shop containing all 92 defined Mega Stones at 20,000 each,
including duplicates. This does not make their matching species obtainable.
Primal Orbs and other battle gimmicks are not included.

Boss teams retain their species, levels and moves; one held item changes to a
Mega Stone: Winona's Altaria (including rematches), Maxie's Camerupt at the Magma
Hideout and Mossdeep, Archie's Sharpedo, Sidney's Absol, Phoebe's Sableye,
Glacia's first Glalie, Drake's Salamence, Wallace's Gyarados and Steven's
Metagross. The early Mt. Chimney Maxie fight is unchanged. Tate and Liza and
Juan retain their teams without Megas.

Reward flags occupy previously unused Emerald flags 0x21–0x2B, avoiding 0x20
(the expansion's Exp. Share flag). Successful gifts are recorded individually;
a full bag leaves the failed gift available for a later attempt. No save layout
changes are needed. Route 118's movement selector in VAR_0x8008 is preserved.

`make -C build/expansion/3ds_port verify-mega-progression PYTHON=python3`
executes the production reward scripts in a bounded host model to check starter
choices, badge gates, full-bag failures, retries, existing saves and duplicate
prevention, and audits boss items and shop stock. It is included in expansion
`make verify`. It also compiles the game's actual Mega eligibility function to
check the ring requirement, matching-stone requirement, opponent eligibility
and once-per-battle restriction. Hardware acceptance should additionally check the Steven event,
Devon shop, START selection, a player/boss transformation, and post-battle form
restoration. Host checks do not replace these in-game checks.

### Slateport–Vermilion visitor ferry

After `FLAG_SYS_GAME_CLEAR` is set, Slateport Harbour's existing ferry attendant
offers a free trip to Vermilion before the normal Hoenn destination menu.
Declining preserves access to Lilycove and the Battle Frontier. The outbound
journey uses the existing S.S. Tidal boarding/departure movement and lands at
Vermilion's southern dock. A sailor there offers free return passage to
Slateport; stepping onto any of the three dock exit tiles also offers a return
after moving the player back from the trigger. Returning requires no ticket,
money, badge or introductory-event flag.

This is a contained visitor area, not an enabled FRLG campaign. Three maps in
the appended `gMapGroup_KantoVisit` group reuse the original Vermilion, Pokémon
Center and Mart layouts. Existing map IDs and FRLG scripts remain unchanged.
The city has no connections to Routes 6/11 and only the Center and Mart doors
are open; other entrances have attendants. Original FRLG badges, story flags,
trainer IDs, ticket checks and hidden items are deliberately absent because
those namespaces overlap with Emerald's. The arrival dialogue explains the
current visitor limits and the return route.

The visitor map JSON and its three original layouts opt into Emerald generation
with `include_in_emerald: true`. The patch extends the upstream mapjson tool to
honor that opt-in without changing the layouts' FRLG tile format. Five required
tilesets are shared between game versions. When regenerating the patch, explicitly
stage the tracked upstream `tools/mapjson/mapjson.cpp` change: the usual broad
`':!tools'` exclusion would otherwise omit it. Also stage the new maps'
`scripts.inc` files explicitly, since the generated-map-include exclusion matches
those source scripts too.

The introduction runs once, recorded in the previously unused Emerald flag
0x2C (`FLAG_KANTO_FERRY_INTRO_SEEN`). The Center heals the party without changing
the Hoenn respawn location, and the Mart sells standard late-game supplies.
Fly is disabled within the visitor group: the original Kanto Fly destinations
still point to excluded FRLG campaign maps and use overlapping world-map flags.
There is no save layout change and existing Champion saves need no migration.

`make -C build/expansion/3ds_port verify-kanto-ferry PYTHON=python3` checks the
Champion gate, existing ferry menu fallback, both return entry points and
cancellation, introduction persistence, healing, reciprocal building warps,
isolated scripts and passable/unoccupied ferry landing tiles. It is included
in expansion `make verify`, including an audit of the linked map group, script
pointers, FRLG layouts and tilesets in `build/gamedata_image.elf`. Hardware
acceptance still needs the actual boarding
animation, first/repeat arrival, Center/Mart visit, saving/reloading in Kanto,
return trip and the original Lilycove/Battle Frontier destinations checked.

### Other host checks

The SDK-free day/night clock and lighting tests can also run separately:
`make -C 3ds_port verify-daynight`. They are included in `make verify` and CI.
`make -C 3ds_port verify-clock-store` exercises save-bound clock state, CRCs,
fractions, file replacement/backup recovery and failed writes. After bootstrap,
`python3 3ds_port/tests/berry_clock_test.py --tree build/upstream` compiles the
actual berry/minute update functions and checks all six speeds, midnight,
no double credit, watering/stopped-tree rules and original expiry. Repeat
with `--tree build/upstream-es` for Spanish. Native clock and storage tests
also pass ASan/UBSan on macOS.
The shared-clock consumers and timed encounter resolver are tested by
`make -C 3ds_port verify-daynight verify-encounters`. After bootstrap, run
`python3 3ds_port/tests/encounter_tables_test.py --tree build/upstream` to
check every production route rule, its pinned species, levels/rates and
original fallbacks under ASan/UBSan. Use `--tree build/upstream-es` for Spanish.
`python3 3ds_port/tests/bottom_map_test.py` compares partial/full map redraws,
including the clock footer, midnight, toggles and location changes.
`python3 3ds_port/tests/voxel_night_test.py --cc clang` checks authored window
masks and the real VXB7/VXB8 readers without a bootstrapped tree (Pillow is
required). Add `--assets build/upstream/3ds_port/romfs/voxel/buildings.bin`
to check generated pages as well, or run `verify-night-windows` in that tree.
See [day/night validation](DAY_NIGHT.md) for the hardware acceptance checklist.

`make verify` builds and runs the C host tests (input decoding, video decode,
pack parser, voxel world hashing, arena, atlas, trees, lighting, ground mesh,
signposts, relief) and audits the link. The builder's tests:

```
python -m unittest discover -s builder/tests
```

## Keeping the upstream patches small

Game-side changes belong in `3ds_port/` whenever a hook can carry them; a
change inside the decompilation should be the smallest hook that works,
behind `PORT_BRIDGE` or `PLATFORM_3DS`, and must not alter data the ROM also
holds unless the port needs it (the recipe report shows every byte of the
original game's objects that stopped matching).

## Before publishing

```
python tools/release_audit.py --repo . --strict
```

must pass; CI runs it on every push.

### Capture crash after using the Pokédex (18 October 2026)

Expansion's normal Pokédex close freed `sPokedexView` without clearing it.
Battle startup reuses the game heap; the shared caught-mon cleanup (which also
serves HGSS) could then free battle data through that dangling pointer. Hardware
`crash_dump_00000039.dmp` failed later in `FreeAllWindowBuffers` at battle end.
`Task_ClosePokedex` now uses `FREE_AND_SET_NULL`. The regression executes the
production close and capture cleanup functions across heap reuse, repeated
normal captures, HGSS cleanup, and active fades:

```sh
python3 3ds_port/tests/pokedex_lifetime_test.py --tree build/expansion
```

It is included in the expansion `make verify` target.

The follow-up `crash_dump_00000040.dmp` faulted in
`SpriteCB_ScrollArrow` at `0x00368198`: `RunTasks` closed the Pokédex and
cleared the view, but `CB2_Pokedex` still animated its interface sprites in
the same frame. The frame callback now returns after tasks if the view was
released. The regression runs the production frame callback during the fade
and on the closing frame, and rejects sprite updates after view release.

### Berry Blender graphics crash (19 October 2026)

Hardware `crash_dump_00000041.dmp` faulted in `CpuSet` at `0x00435838`,
copying a 2,048-byte berry sprite from invalid address `0xdefc9e85` during
`VBlankCB_BerryBlender`. `CreateBerrySprite` initialized the original frame
fields individually but left the port's added `offset` field as old heap
contents. The frame resolver added those contents to the graphics pointer.
The shared berry constructor now initializes the whole descriptor, covering
both the berry tag screen and spinning Blender berries. Other dynamically
allocated frame tables in trainer pictures and the slot machine already use
`AllocZeroed`.

`make -C build/expansion/3ds_port verify-berry-sprite PYTHON=python3` runs the
production constructor and frame-copy request code with 255 dirty allocation
patterns and repeated copies under ASan/UBSan. It is included in expansion
`make verify`. Hardware acceptance: view berry tags, then choose a berry and
start the Berry Blender after using other menus.

### Battle message wrapping crash (19 October 2026)

`crash_dump_00000042.dmp` failed in `BuildNewString` at `0x0031f290` while
expanding a battle message. The second line had zero words and an uninitialized
word pointer (`0x99a0aa99`). Automatic wrapping can reserve more lines than it
populates, particularly for a single word wider than the line. It now passes
the actual populated line count to the string builder after layout succeeds.
This also avoids inserting a spurious break over the string terminator.

`verify-line-break` runs the production wrapper under ASan/UBSan with poisoned
allocations, widths 1–240, one to three visible lines, both scroll modes, long
words, ordinary messages, manual breaks and clear-page separators. The old
code reproduces the same invalid word-pointer access; the fixed code preserves
characters and string bounds. This check is part of expansion `make verify`.

### Mossdeep partner-battle hang (19 October 2026)

The Space Center Maxie/Tabitha battle with Steven froze after the send-out
message without generating a dump. Reproducing the battle from a copy of the
hardware save under Azahar/GDB reached `FatalfCrashScreen` from `CreateSprite`
inside `StartSendOutAnim` for battler 3. The reported “Out of sprite slots”
also covers tile allocation failure: only 48 consecutive OBJ tiles remained
when the second opponent needed 64. `CrashScreen` then stuck in its GBA
`BusyWaitForVBlank`, explaining the frozen display and lack of continued logs.

The compressed-sheet loader had reserved the full 0x800-byte blank asset for
each health bar even though the player/opponent bars request just 0x100/0x120.
Four bars consumed 256 tiles instead of 34. The loader now honors a nonzero
requested size capped at the decompressed size. This retains the earlier
short-payload protection for the singles opponent healthbox while reclaiming
222 tiles in a four-battler battle.

`verify-compressed-sheet` covers prefix-sized health bars, undersized payloads,
matching sizes and unspecified sizes under ASan/UBSan; it is part of expansion
`make verify`. The fixed Azahar/GDB replay created all four battler sprites
(ids 22, 21, 15, 24) and reached `HandleInputChooseAction`, with health-bar
ranges of 8/9/8/9 tiles. Hardware acceptance is the Space Center battle through
all four send-outs, with intact health bars and subsequent battle turns.
