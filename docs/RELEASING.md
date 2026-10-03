# Releasing

A release is a set of assets attached to a GitHub release. None of them ever
contains a ROM, a data pack or anything extracted from the game.

| Asset | For |
|---|---|
| `Emerald3DS-vX.Y.Z-Windows.zip` | the Windows builder: builder, engine-only 3DSX, recipe, `README.txt`, `LICENSES/` |
| `Emerald3DS.3dsx`, `Emerald3DS.smdh` | quick update of the executable when the data ABI did not change |
| `Emerald3DS-WebPayload.zip` | the web builder (website): payload, builder package, licences, `web-manifest.json` |
| `web-manifest.json` | the same manifest on its own, read by the website without downloading the payload |
| `SHA256SUMS.txt` | SHA-256 of every asset above |

The website (separate repository) discovers each new release through the
GitHub API and uses these assets as they are: publishing the release is all it
takes for the site and its web builder to offer the new version. The contract
between the two is `builder/emerald3ds_builder/webmanifest.py`; keep asset
names as they are, or bump the manifest's `schemaVersion` together with the
website.

## Release checklist (with the website)

The website (`ZallaxDev/emerald-3ds-website`) builds the game in the player's
browser from the assets of the **latest published release**. Since the
release after v0.1.2 every release must follow this list; skipping a step
leaves the site without a web builder or without Quick Update for that
version.

1. **Code and version.** The release is made from `main` with the web-payload
   tooling merged (`builder/emerald3ds_builder/web.py`, `webmanifest.py`,
   `tools/build_web_payload.py`). Set `__version__` in
   `builder/emerald3ds_builder/__init__.py` and move `## Unreleased` in
   `CHANGELOG.md` to `## X.Y.Z — YYYY-MM-DD` (the site links changelog
   headings to releases by that version).
2. **Build all assets** with `tools/build_release.py` (see *Steps* below).
   `dist/` must then contain exactly:

   | File | Used by the site for |
   |---|---|
   | `Emerald3DS-WebPayload.zip` | the web builder (downloaded by the browser) |
   | `web-manifest.json` | knowing, without downloading the payload, that the release can be built on the web, which ROM it accepts, its data ABI |
   | `Emerald3DS.3dsx` | Quick Update (path 2: players who keep their `emerald3ds.pak`) |
   | `Emerald3DS.smdh` | listed with the 3DSX |
   | `Emerald3DS-vX.Y.Z-Windows.zip` | the Windows builder (not linked by the site) |
   | `SHA256SUMS.txt` | checksums of everything above |

   Never rename these files: the site recognises assets by name. The
   manifest's `releaseTag` must equal the Git tag (`vX.Y.Z`), which
   `build_release.py` guarantees.
3. **Audit** (already run by `build_release.py`; to repeat it):
   `python tools/release_audit.py --zip dist/Emerald3DS-WebPayload.zip --strict --web-payload`.
4. **Test before publishing**, with the real ROM:
   - Windows ZIP: clean machine, build, install, boot (as before).
   - Web builder: open the website's `/build?payload=local` (local
     `npm run dev` or any deployed copy of the site), choose
     `dist/Emerald3DS-WebPayload.zip` and the ROM, build, copy the ZIP to the SD
     card and boot the game. Use the payload from `dist/`, not the synthetic
     test payload.
   - Optional, once per change to the voxel generators: compare the packs of
     both runners (they must be byte-identical):

     ```
     python -c "import sys; sys.path.insert(0, 'builder'); from pathlib import Path; \
     from emerald3ds_builder.build import Payload, build_pack; \
     p = Payload(Path('dist/Emerald3DS-vX.Y.Z-Windows/payload')); \
     build_pack(Path('baserom.gba'), p, Path('/tmp/sub.pak')); \
     build_pack(Path('baserom.gba'), p, Path('/tmp/inp.pak'), runner='inprocess')"
     cmp /tmp/sub.pak /tmp/inp.pak
     ```
5. **Publish the GitHub release**: tag `vX.Y.Z`, **published** (drafts are
   invisible to the site), not marked pre-release unless it really is one (a
   pre-release is not offered as the latest version while a normal release
   exists). Attach every file of step 2, for example:

   ```
   gh release create vX.Y.Z --title "Alpha X.Y.Z" --notes-file notes.md \
       dist/Emerald3DS-WebPayload.zip dist/web-manifest.json \
       dist/Emerald3DS.3dsx dist/Emerald3DS.smdh \
       dist/Emerald3DS-vX.Y.Z-Windows.zip dist/SHA256SUMS.txt
   ```

   The release notes' bullet lists become the highlights on the site's home
   page.
6. **Check the website** (within 5 minutes, its cache time):
   `/api/releases/latest` shows the new tag with `"manifestStatus": "ok"`,
   `"capabilities": {"webBuilder": true, "standalone3dsx": true, ...}` and a
   `quickUpdate` status; `/build` offers only the ROM picker; a real build on
   the deployed site boots.

### Quick Update (path 2)

The site offers "download only `Emerald3DS.3dsx`" to players of the previous
release when both releases' `web-manifest.json` have the same `dataAbi` and
`packSchema`. The data ABI is computed from the pack contents, so it changes by
itself whenever a release changes the game data: nothing to set by hand, just
always attach `web-manifest.json`. Consequences:

- First release with a web payload: v0.1.2 has no manifest, so the site cannot
  prove compatibility; it offers players a local check of their own
  `emerald3ds.pak` header instead (or the full build).
- From then on, a release that keeps the data gets a direct 3DSX download; one
  that changes it sends everyone to the web builder. The game itself also
  refuses an incompatible pack on start-up.

## Prerequisites (maintainer machine)

- A tree from `tools/bootstrap.py --make` (or the maintainer's workspace).
- The supported ROM, and the ELF of the original game built from the same
  pinned upstream (`make` in the upstream tree's root produces
  `pokeemerald.elf`): the recipe generator needs its symbol table to know
  where each table lives in the ROM.
- PyInstaller (`pip install pyinstaller`).

## Steps

```
python tools/build_release.py --version 0.1.0 --rom baserom.gba \
    --gba-elf build/upstream/pokeemerald.elf \
    --make "make -j8 PYTHON=python"
```

This runs `make release`, writes the recipe, assembles
`dist/Emerald3DS-v0.1.0-Windows/` (builder, `payload/Emerald3DS.3dsx`,
`payload/Emerald3DS.smdh`, `payload/emerald3ds.recipe`, `payload/voxelgen/`,
`README.txt`, `LICENSES/`) and zips it, copies the standalone `.3dsx` and
`.smdh` to `dist/`, writes `dist/Emerald3DS-WebPayload.zip` and
`dist/web-manifest.json` (`tools/build_web_payload.py`), audits both ZIPs
(including the ROM scan and, for the web payload, the manifest and its hashes)
and writes `dist/SHA256SUMS.txt`.

The web payload can also be made on its own from any release's `payload/`
folder, for example the one inside an already published Windows ZIP:

```
python tools/build_web_payload.py --payload path/to/payload --version 0.1.2 --out dist
python tools/release_audit.py --zip dist/Emerald3DS-WebPayload.zip --strict --web-payload
```

## Before tagging

1. On a clean Windows machine without Python or devkitPro: extract the ZIP,
   run the builder with the ROM, install to an SD card, boot the game.
2. `emerald3ds-builder-cli verify --pak <SD>/3ds/emerald3ds/emerald3ds.pak`.
3. Open the website's build page with `?payload=local`, choose
   `dist/Emerald3DS-WebPayload.zip` and the ROM, build, and check that the
   ZIP it gives boots the game too (the web builder runs the same code with
   in-process generators).
4. Update `CHANGELOG.md` (keep the `## X.Y.Z — YYYY-MM-DD` headings: the
   website links them to the releases), tag `vX.Y.Z`, and attach every file
   listed in `SHA256SUMS.txt` plus `SHA256SUMS.txt` itself.

Do not mark a release as a draft once it should be offered: the website only
reads published releases. A release marked *pre-release* is shown in the
release history but is not offered as the latest version while a normal
release exists.

## Future: HOME Menu forwarder

When a forwarder CIA exists, attach it as `Emerald3DS-Forwarder.cia` and pass
`--cia-forwarder Emerald3DS-Forwarder.cia` to `tools/build_web_payload.py`
(manifest `assets.ciaForwarder`). The website shows its download only when the
asset is really attached. The forwarder launches
`sdmc:/3ds/emerald3ds/Emerald3DS.3dsx`, so it is installed once and later
updates only replace the `.3dsx`.

The same pack must never be attached to a release or an issue: it is
generated from the player's ROM.
