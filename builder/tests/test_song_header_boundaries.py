import contextlib
import importlib.util
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("verify_song_boundaries", ROOT / "3ds_port/scripts/verify_scripts.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class SongHeaderBoundaryTests(unittest.TestCase):
    def verify(self, sites):
        start = 0x1000
        payload = bytearray(36)
        payload[16] = 1
        struct.pack_into("<II", payload, 20, 8, 12)
        symbols = {"AgbMain": 0x800, "__ctr_song_blob": start, "__ctr_song_blob_end": start + len(payload),
                   "__ctr_script_blob_end": start, "mus_dummy": start + 16}

        def symbol_fixture(elf, global_names):
            global_names.update(symbols)
            return symbols

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "songs.bin").write_bytes(payload)
            relocations = VERIFIER.HEADER.pack(b"C3AB", 1, len(payload), symbols["AgbMain"], len(sites), 0)
            relocations += struct.pack("<" + "I" * len(sites), *sites)
            (folder / "songs.rel").write_bytes(relocations)
            arguments = ["verify_scripts.py", "--elf", "fixture.elf", "--out-dir", str(folder),
                         "--magic", "C3AB", "--payload", "songs.bin", "--reloc", "songs.rel",
                         "--blob-symbol", "__ctr_song_blob", "--require", "mus_dummy", "--song-headers"]
            output = io.StringIO()
            with patch("sys.argv", arguments), patch.object(VERIFIER, "elf_symbols", symbol_fixture), \
                    patch.object(VERIFIER, "loaded_range", return_value=(start, start + len(payload))), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                return VERIFIER.main(), output.getvalue()

    def test_adjacent_script_end_is_not_a_song_header(self):
        status, output = self.verify([20, 24])
        self.assertEqual(status, 0, output)

    def test_unrelocated_song_track_still_fails(self):
        status, output = self.verify([20])
        self.assertEqual(status, 1, output)
        self.assertIn("unrelocated pointers: mus_dummy", output)


if __name__ == "__main__":
    unittest.main()
