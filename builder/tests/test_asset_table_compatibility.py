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
