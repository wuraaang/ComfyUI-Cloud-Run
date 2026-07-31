"""Synthetic-only structural checks for a future separately authorized Gold run."""

import hashlib
from pathlib import Path
import tempfile
import unittest

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
    ImageDraw = None

from scripts.validate_gold_output import validate_gold_output


def make_synthetic_source(path, size=(64, 96)):
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            checker = 37 if ((x // 7) + (y // 11)) % 2 else 0
            pixels[x, y] = (
                (x * 3 + y + checker) % 256,
                (x + y * 2 + checker // 2) % 256,
                (x * 2 + y * 3 + checker) % 256,
            )
    image.save(path, format="PNG")
    return path


def coherent_upscale(source_path, output_path, size=(128, 192)):
    with Image.open(source_path) as source:
        source.resize(size, Image.Resampling.NEAREST).save(
            output_path,
            format="PNG",
        )
    return output_path


def with_black_block(source_path, output_path):
    with Image.open(source_path) as image:
        image.load()
        altered = image.copy()
    draw = ImageDraw.Draw(altered)
    cell_width = altered.width // 8
    cell_height = altered.height // 8
    draw.rectangle(
        (
            2 * cell_width,
            3 * cell_height,
            3 * cell_width - 1,
            4 * cell_height - 1,
        ),
        fill=(0, 0, 0),
    )
    altered.save(output_path, format="PNG")
    return output_path


def with_repeated_tile(source_path, output_path):
    with Image.open(source_path) as image:
        image.load()
        altered = image.copy()
    cell_width = altered.width // 8
    cell_height = altered.height // 8
    tile = altered.crop((0, 0, cell_width, cell_height))
    altered.paste(tile, (6 * cell_width, 5 * cell_height))
    altered.save(output_path, format="PNG")
    return output_path


def with_discontinuous_seam(source_path, output_path):
    with Image.open(source_path) as image:
        image.load()
        altered = image.copy()
    draw = ImageDraw.Draw(altered)
    boundary = altered.width // 2
    draw.rectangle(
        (boundary - 1, 0, boundary + 1, altered.height - 1),
        fill=(255, 255, 255),
    )
    altered.save(output_path, format="PNG")
    return output_path


@unittest.skipUnless(
    Image is not None,
    "Pillow supplied by ComfyUI is required for Gold validation.",
)
class GoldOutputValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = make_synthetic_source(self.root / "source.png")
        self.coherent = coherent_upscale(
            self.source,
            self.root / "coherent.png",
        )

    def validate(self, output):
        return validate_gold_output(
            self.source,
            output,
            expected_size=(128, 192),
            expected_format="PNG",
            require_enlargement=True,
        )

    def test_gold_validator_accepts_coherent_upscale(self):
        result = self.validate(self.coherent)

        self.assertTrue(result.passed, result.reasons)
        self.assertEqual(result.output_size, (128, 192))
        self.assertEqual(result.output_format, "PNG")
        self.assertEqual(
            result.output_sha256,
            hashlib.sha256(self.coherent.read_bytes()).hexdigest(),
        )

    def test_gold_validator_rejects_black_block_repeated_tile_and_seam(self):
        outputs = (
            with_black_block(
                self.coherent,
                self.root / "black.png",
            ),
            with_repeated_tile(
                self.coherent,
                self.root / "repeat.png",
            ),
            with_discontinuous_seam(
                self.coherent,
                self.root / "seam.png",
            ),
        )

        for output in outputs:
            with self.subTest(output=output.name):
                result = self.validate(output)
                self.assertFalse(result.passed)
                self.assertTrue(result.reasons)

    def test_gold_validator_rejects_truncation_dimensions_format_and_non_enlargement(self):
        truncated = self.root / "truncated.png"
        content = self.coherent.read_bytes()
        truncated.write_bytes(content[: len(content) // 2])
        wrong_size = coherent_upscale(
            self.source,
            self.root / "wrong-size.png",
            size=(127, 192),
        )
        jpeg = self.root / "wrong-format.jpg"
        with Image.open(self.coherent) as image:
            image.convert("RGB").save(jpeg, format="JPEG")
        same_size = coherent_upscale(
            self.source,
            self.root / "same-size.png",
            size=(64, 96),
        )

        for output, expected_size, expected_format, enlargement in (
            (truncated, (128, 192), "PNG", True),
            (wrong_size, (128, 192), "PNG", True),
            (jpeg, (128, 192), "PNG", True),
            (same_size, (64, 96), "PNG", True),
        ):
            with self.subTest(output=output.name):
                result = validate_gold_output(
                    self.source,
                    output,
                    expected_size=expected_size,
                    expected_format=expected_format,
                    require_enlargement=enlargement,
                )
                self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
