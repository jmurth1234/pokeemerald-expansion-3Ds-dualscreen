# City lighting

This follow-up extends PR #57. Both language builds compile and automated tests
pass. On 2026-10-12 the user reported that the supplied build works on their
physical 3DS and authorized publication. No console model, performance metrics
or itemized checklist results were supplied; this records general acceptance.

## Activation and scope

Enable voxel mode and **OPTION → ENHANCEMENTS → DAY / NIGHT → AUTO**.
In Spanish, use **MEJORAS → DIA / NOCHE → AUTO**. Emission follows the
existing eased sunset/dawn transitions and **CLOCK SPEED / VELOC. RELOJ**,
including the accumulated clock offset. No additional setting is introduced.
OFF, daytime, interiors, invalid clock and classic battle screens have no
lamp emission. Outdoor voxel battle scenery keeps global grading, but the
new lamp overlay is disabled during battle.

Global outdoor colour grading already applies to the whole visible map in
classic 2D and voxel modes. Local light coverage is more specific:

- Existing houses, Littleroot lab and Route 104 flower shop retain warm panes.
  Littleroot's east-house mask now uses its actual window coordinates.
- Shared Pokémon Centers and marts gain illuminated entrance glass.
- Shared gyms and Rustboro's gym gain authored glass masks, including doors.
- Rustboro stone/olive apartment blocks and Devon gain illuminated panes.
- Reused copies of these exterior voxel templates inherit their masks.
- The current baked sign data contains 17 original lamps, all in Rustboro.
  Their front/back glass lights up, preserving metal rims; small amber pools
  brighten eligible floor cells nearby, not walls, roofs or water.

There are 19 covered exterior source models and 29 glowing packed pages in
the current EN/ES builds. This is not coverage for every bespoke Hoenn building:
unmodelled/flat buildings and classic 2D windows remain without local emission.
No lamps are invented or placed in cities without them. The pools are stylised
overlays, not physically simulated point lights; they cast no new shadows and
do not apply local illumination to characters or neighbouring facades.

## Rendering and budgets

Building glass uses the existing VXB8 L8 material mask inside the building
render pass. Source daytime pixels are unchanged. Frames, roof decorations,
signs and opaque doors remain dark; only authored glass is eligible.

Lamps reuse VXS2 sign metadata without changing the sign asset format.
Glass and ground pools have fixed budgets of 2,046 and 4,092 vertices
respectively: approximately 96 KiB of linear memory and 144 KiB of CPU
scratch, taken when lamps are first lit and given back once they are not
(lighting off, indoors, by day, a battle). Without room the lamps stay dark
and are asked for again later. Exhausted budgets truncate optional lighting,
never overflow.
There are at most two additional draw calls per eye, with depth testing and
alpha blending; ordinary render state is restored afterwards.

Geometry is cached by map-instance signature, live-map digest, camera origin
and visible rectangle. Connected map origins and terrain base/lift/shift are
respected. Clock-only strength changes do not rebuild geometry, even at 120X.
The view rectangle includes a three-cell margin rather than a fixed camera
radius, supporting different zoom levels. Pools use a small radial falloff
and half-cell quads. Old 3DS frame pacing still requires hardware measurement.

The approach takes inspiration from [PR #45](https://github.com/ZallaxDev/pokeemerald-3Ds-dualscreen/pull/45),
especially lamp identification and warm glass. It does not merge that PR:
building emission stays in our existing material-mask pass and all effects
use our shared accelerated clock, not an independent wall clock.

## Build and automated checks

Rebuild the executable and data pack together. The expanded glass masks are
in the pack; updating only the executable cannot add them. The SMDH preview
description is updated separately for each language. Never distribute ROMs,
ROM-derived packs, graphics, or saves in the public PR.

`make pak` selects the ROM fingerprint from the staged game's language
configuration. Direct `staging.py pak` calls should pass `--game-config` with
that tree's `include/constants/global.h`, plus `--rom` when verifying the local
source ROM. A supplied ROM from another language is rejected before writing.
This fixes the earlier local Spanish pack incorrectly carrying an English
fingerprint; the executable's language guard remains unchanged.

Run `make -C 3ds_port verify-night-lamps` for synthetic lamp geometry and
`python3 3ds_port/tests/voxel_night_test.py --cc cc` for masks and renderer
state. In a staged tree, run its `make verify PYTHON=python3`, then the
production mask, encounter-table and berry-clock integration tests.

Lamp tests cover glass rims, both faces, connected-map coordinates, relief,
floor/water filtering, map/view bounds, interiors and capacity guards.
Mask tests cover unchanged source art, opaque panes, roofs, streaming pages,
legacy unmasked pages and truncated input. Native geometry/mask harnesses
also run under address/undefined-behaviour sanitizers.

## CI repair

The linked PR #57 `build-3ds` job first fails in
`tools/port_common/gen_asset_table.py`: backslash-containing expressions inside
f-strings require Python 3.12, but the build container uses Python 3.11.
Ordinary string concatenation produces the same URIs under both versions.
The build job now byte-compiles the generator and runs URI/concatenation/offset
regressions with its own interpreter before bootstrap.

Full verification also exposed stale tree API calls/stubs, old 64-pixel tree
atlas expectations, a missing Dewford tileset fixture symbol and the settings
saver fixture's missing clock-update dependency. The fixtures now match the
tileset-aware API and 128-pixel atlas, including explicit Dewford/non-Dewford
checks. The saver test verifies the old speed is sampled before a speed
change and unchanged values do not resample; assertions remain enabled.
The final engine-only ZIP now uses the existing allowed `payload/` layout:
a root-level executable previously failed the audit's 1 MiB limit. The
allowlist is not widened, and generated packs are still excluded from CI
artifacts and rejected by the publication audit.
Local fixes do not mean the remote workflow has rerun successfully: that only
happens after a push.

## Console checklist

1. Back up the existing executable, pack, settings, `.sav`, `.clock` and
   `.clock.bak` together. Do not replace or delete save/clock files.
2. Install the matching EN or ES executable, SMDH and pack, not mixed builds.
3. In Petalburg, check house panes, Pokémon Center, mart and gym glass.
4. In Rustboro, check original lamps, ground pools, apartment blocks, gym and
   Devon from multiple camera angles, zoom levels and with stereo on/off.
5. Use SHOW CLOCK and 120X to observe a full sunset and dawn; pools and glass
   must fade with the same effective time, without popping or detached rims.
6. Change AUTO to OFF at night: new glow/pools disappear and legacy lighting
   returns. Re-enable AUTO and change speed without resetting the clock.
7. Enter/leave interiors, cross connected maps and enter/leave a battle;
   check for stale overlays, flicker, water illumination and incorrect depth.
8. Check frame pacing in dense Rustboro views, especially on Old 3DS.

Report visual issues with location, game time, speed, camera angle/zoom and
console model. Software tests cannot prove hardware depth/blending or FPS.
