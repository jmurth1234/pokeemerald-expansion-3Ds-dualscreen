import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("asset_table", ROOT / "tools/port_common/gen_asset_table.py")
asset_table = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asset_table)


class AssetTableCompatibilityTests(unittest.TestCase):
    def test_grouped_expansion_palettes_are_not_mistaken_for_text_structs(self):
        source = r'''
const u16 gTilesetPalettes_General[][16] = {
    INCGFX_U16("data/tilesets/primary/general/palettes/00.pal", ".gbapal"),
    INCGFX_U16("data/tilesets/primary/general/palettes/01.pal", ".gbapal"),
};
const u16 gMonIconPalettes[][16] = {
    INCBIN_U16("graphics/pokemon/icon_palettes/pal0.gbapal"),
    INCGFX_U16("graphics/pokemon/icon_palettes/pal1.pal", ".gbapal"),
};
const struct Floor sFloors[] = {
    { .nickname = __("TRAINER"), .tiles = INCBIN_U8("graphics/floor.bin") },
};
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src/graphics.c").write_text(source)
            with patch.object(asset_table, "ROOT", root):
                rows = {name: paths for _, name, paths, _ in asset_table.parse_incbins_by_source()}
        self.assertEqual(rows["gTilesetPalettes_General"], [
            "build/assets/data/tilesets/primary/general/palettes/00.pal.gbapal",
            "build/assets/data/tilesets/primary/general/palettes/01.pal.gbapal",
        ])
        self.assertEqual(rows["gMonIconPalettes"], [
            "graphics/pokemon/icon_palettes/pal0.gbapal",
            "build/assets/graphics/pokemon/icon_palettes/pal1.pal.gbapal",
        ])
        self.assertNotIn("sFloors", rows)

    def test_single_and_concatenated_assets_preserve_uris_and_offsets(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            first = folder / "first.bin"
            second = folder / "second.bin"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            inputs = {"graphics/first.bin": first, "graphics/second.bin": second}
            destination = folder / "romfs"
            with patch.object(asset_table, "FS_DIR", destination), \
                 patch.object(asset_table, "URI_PREFIX", "romfs:/"), \
                 patch.object(asset_table, "ensure_asset", side_effect=inputs.get):
                single = asset_table.copy_or_concat_asset("src/test.o", "test", ["graphics/first.bin"])
                self.assertEqual(single, ("romfs:/graphics/first.bin", 5, [0]))
                self.assertEqual((destination / "graphics/first.bin").read_bytes(), b"first")
                grouped = asset_table.copy_or_concat_asset("src/test.o", "test", list(inputs))
                self.assertEqual(grouped, ("romfs:/generated/assets/src_test__test.bin", 11, [0, 5]))
                self.assertEqual((destination / "generated/assets/src_test__test.bin").read_bytes(),
                                 b"firstsecond")
                self.assertIsNone(asset_table.copy_or_concat_asset("src/test.o", "test", ["missing.bin"]))


if __name__ == "__main__":
    unittest.main()
