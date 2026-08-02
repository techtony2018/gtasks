import struct
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FaviconAssetTests(unittest.TestCase):
    def test_svg_favicon_is_the_exact_application_logo_artwork(self) -> None:
        app_logo = (
            PROJECT_ROOT / "static" / "assets" / "mission-control-command-mark.svg"
        ).read_bytes()

        self.assertEqual(
            (PROJECT_ROOT / "static" / "favicon.svg").read_bytes(),
            app_logo,
        )
        self.assertEqual(
            (
                PROJECT_ROOT
                / "design"
                / "mission-control-brand"
                / "source"
                / "favicon.svg"
            ).read_bytes(),
            app_logo,
        )

    def test_ico_favicon_contains_standard_sizes_rendered_as_png(self) -> None:
        favicon = (PROJECT_ROOT / "static" / "favicon.ico").read_bytes()
        design_export = (
            PROJECT_ROOT
            / "design"
            / "mission-control-brand"
            / "exports"
            / "favicon.ico"
        ).read_bytes()
        self.assertEqual(favicon, design_export)

        reserved, image_type, image_count = struct.unpack_from("<HHH", favicon)
        self.assertEqual((reserved, image_type, image_count), (0, 1, 3))

        sizes = []
        for index in range(image_count):
            offset = 6 + index * 16
            width_byte, height_byte, _, _, planes, bits, length, image_offset = (
                struct.unpack_from("<BBBBHHII", favicon, offset)
            )
            width = width_byte or 256
            height = height_byte or 256
            sizes.append((width, height))
            # PNG-backed ICO directory entries may leave these metadata fields
            # as zero; the embedded PNG header below is the authoritative format.
            self.assertIn(planes, (0, 1))
            self.assertIn(bits, (0, 32))
            self.assertEqual(
                favicon[image_offset : image_offset + 8],
                b"\x89PNG\r\n\x1a\n",
            )
            self.assertEqual(image_offset + length <= len(favicon), True)

        self.assertEqual(sizes, [(16, 16), (32, 32), (48, 48)])


if __name__ == "__main__":
    unittest.main()
