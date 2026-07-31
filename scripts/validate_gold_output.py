#!/usr/bin/env python3
"""Deterministic structural evidence for a separately authorized Gold run."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import stat
import statistics
import warnings


MAX_IMAGE_FILE_BYTES = 16 * 1024 * 1024 * 1024
MAX_IMAGE_DIMENSION = 32_768
MAX_IMAGE_PIXELS = 268_435_456
_FORMATS = {"JPEG", "PNG", "WEBP"}


class GoldValidationError(RuntimeError):
    """A sanitized local decode or structural-validation failure."""


@dataclass(frozen=True)
class GoldValidationResult:
    passed: bool
    reasons: tuple[str, ...]
    source_format: str | None
    source_size: tuple[int, int] | None
    source_sha256: str | None
    output_format: str | None
    output_size: tuple[int, int] | None
    output_sha256: str | None
    perceptual_distance: int | None


@dataclass(frozen=True)
class _DecodedImage:
    image: object
    format: str
    size: tuple[int, int]
    sha256: str


def _hash_regular_file(path):
    descriptor = None
    try:
        metadata = os.lstat(path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_IMAGE_FILE_BYTES
        ):
            raise GoldValidationError("image_file_invalid")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_IMAGE_FILE_BYTES:
                raise GoldValidationError("image_file_invalid")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            size != before.st_size
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
        ):
            raise GoldValidationError("image_file_changed")
        return digest.hexdigest()
    except GoldValidationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise GoldValidationError("image_file_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _pillow():
    try:
        from PIL import Image
    except ImportError:
        raise GoldValidationError("pillow_unavailable") from None
    return Image


def _pixel_values(image):
    flattened = getattr(image, "get_flattened_data", None)
    if callable(flattened):
        return flattened()
    return image.getdata()


def _decode(path, *, role):
    Image = _pillow()
    digest = _hash_regular_file(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with Image.open(path) as probe:
                image_format = str(probe.format or "").upper()
                image_size = tuple(probe.size)
                probe.verify()
            with Image.open(path) as decoded:
                if (
                    str(decoded.format or "").upper() != image_format
                    or tuple(decoded.size) != image_size
                ):
                    raise GoldValidationError(role + "_decode_failed")
                decoded.load()
                image = decoded.convert("RGB")
    except GoldValidationError:
        raise
    except Exception:
        raise GoldValidationError(role + "_decode_failed") from None
    if (
        image_format not in _FORMATS
        or len(image_size) != 2
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= MAX_IMAGE_DIMENSION
            for value in image_size
        )
        or image_size[0] * image_size[1] > MAX_IMAGE_PIXELS
    ):
        image.close()
        raise GoldValidationError(role + "_metadata_invalid")
    return _DecodedImage(
        image=image,
        format=image_format,
        size=image_size,
        sha256=digest,
    )


def _phash(image):
    Image = _pillow()
    luminance = image.convert("L").resize(
        (32, 32),
        Image.Resampling.LANCZOS,
    )
    pixels = list(_pixel_values(luminance))
    coefficients = []
    scale = math.pi / 32
    for vertical in range(8):
        for horizontal in range(8):
            value = 0.0
            for y in range(32):
                y_factor = math.cos((y + 0.5) * vertical * scale)
                row = y * 32
                for x in range(32):
                    value += (
                        pixels[row + x]
                        * math.cos((x + 0.5) * horizontal * scale)
                        * y_factor
                    )
            coefficients.append(value)
    median = statistics.median(coefficients[1:])
    return tuple(value >= median for value in coefficients)


def _cell_bounds(size, column, row):
    width, height = size
    return (
        column * width // 8,
        row * height // 8,
        (column + 1) * width // 8,
        (row + 1) * height // 8,
    )


def _black_block_introduced(reference, output):
    for row in range(8):
        for column in range(8):
            bounds = _cell_bounds(output.size, column, row)
            output_pixels = list(
                _pixel_values(output.crop(bounds).convert("L"))
            )
            reference_pixels = list(
                _pixel_values(reference.crop(bounds).convert("L"))
            )
            output_black = sum(value <= 12 for value in output_pixels) / len(
                output_pixels
            )
            reference_black = sum(
                value <= 12 for value in reference_pixels
            ) / len(reference_pixels)
            if output_black >= 0.92 and reference_black < 0.75:
                return True
    return False


def _tile_signatures(image):
    Image = _pillow()
    signatures = []
    for row in range(8):
        for column in range(8):
            signatures.append(
                tuple(
                    _pixel_values(
                        image.crop(
                            _cell_bounds(image.size, column, row)
                        )
                        .convert("L")
                        .resize((8, 8), Image.Resampling.BILINEAR)
                    )
                )
            )
    return tuple(signatures)


def _mean_absolute_difference(first, second):
    return sum(
        abs(left - right) for left, right in zip(first, second)
    ) / len(first)


def _repeated_tile_introduced(reference, output):
    reference_tiles = _tile_signatures(reference)
    output_tiles = _tile_signatures(output)
    for first in range(len(output_tiles)):
        for second in range(first + 1, len(output_tiles)):
            if (
                _mean_absolute_difference(
                    output_tiles[first],
                    output_tiles[second],
                )
                <= 1.5
                and _mean_absolute_difference(
                    reference_tiles[first],
                    reference_tiles[second],
                )
                >= 12.0
            ):
                return True
    return False


def _vertical_difference(pixels, width, height, boundary):
    return sum(
        abs(
            pixels[row * width + boundary - 1]
            - pixels[row * width + boundary]
        )
        for row in range(height)
    ) / height


def _horizontal_difference(pixels, width, boundary):
    top = (boundary - 1) * width
    bottom = boundary * width
    return sum(
        abs(pixels[top + column] - pixels[bottom + column])
        for column in range(width)
    ) / width


def _seam_scores(image):
    grayscale = image.convert("L")
    pixels = list(_pixel_values(grayscale))
    width, height = grayscale.size
    scores = []
    for index in range(1, 8):
        boundary = index * width // 8
        candidates = range(
            max(1, boundary - 3),
            min(width, boundary + 4),
        )
        scores.append(
            max(
                _vertical_difference(
                    pixels,
                    width,
                    height,
                    candidate,
                )
                for candidate in candidates
            )
        )
    for index in range(1, 8):
        boundary = index * height // 8
        candidates = range(
            max(1, boundary - 3),
            min(height, boundary + 4),
        )
        scores.append(
            max(
                _horizontal_difference(
                    pixels,
                    width,
                    candidate,
                )
                for candidate in candidates
            )
        )
    return tuple(scores)


def _discontinuous_seam_introduced(reference, output):
    reference_scores = _seam_scores(reference)
    output_scores = _seam_scores(output)
    return any(
        output_score > max(55.0, reference_score * 2.5 + 20.0)
        for reference_score, output_score in zip(
            reference_scores,
            output_scores,
        )
    )


def _result_for_error(reason, *, source=None, output=None):
    return GoldValidationResult(
        passed=False,
        reasons=(reason,),
        source_format=source.format if source is not None else None,
        source_size=source.size if source is not None else None,
        source_sha256=source.sha256 if source is not None else None,
        output_format=output.format if output is not None else None,
        output_size=output.size if output is not None else None,
        output_sha256=output.sha256 if output is not None else None,
        perceptual_distance=None,
    )


def validate_gold_output(
    source_path,
    output_path,
    *,
    expected_size,
    expected_format,
    require_enlargement,
):
    if (
        not isinstance(expected_size, tuple)
        or len(expected_size) != 2
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= MAX_IMAGE_DIMENSION
            for value in expected_size
        )
        or expected_size[0] * expected_size[1] > MAX_IMAGE_PIXELS
        or not isinstance(expected_format, str)
        or expected_format.upper() not in _FORMATS
        or not isinstance(require_enlargement, bool)
    ):
        raise ValueError("Invalid Gold validation contract.")
    expected_format = expected_format.upper()
    source = None
    output = None
    try:
        try:
            source = _decode(Path(source_path), role="source")
        except GoldValidationError as error:
            return _result_for_error(str(error))
        try:
            output = _decode(Path(output_path), role="output")
        except GoldValidationError as error:
            return _result_for_error(str(error), source=source)

        reasons = []
        if output.format != expected_format:
            reasons.append("output_format_mismatch")
        if output.size != expected_size:
            reasons.append("output_dimensions_mismatch")
        if require_enlargement and not (
            output.size[0] > source.size[0]
            and output.size[1] > source.size[1]
        ):
            reasons.append("output_is_not_an_enlargement")

        Image = _pillow()
        reference = source.image.resize(
            output.size,
            Image.Resampling.NEAREST,
        )
        try:
            source_hash = _phash(source.image)
            output_hash = _phash(output.image)
            perceptual_distance = sum(
                left != right
                for left, right in zip(source_hash, output_hash)
            )
            if perceptual_distance > 20:
                reasons.append("composition_mismatch")
            if _black_block_introduced(reference, output.image):
                reasons.append("new_nearly_black_block")
            if _repeated_tile_introduced(reference, output.image):
                reasons.append("new_repeated_tile")
            if _discontinuous_seam_introduced(reference, output.image):
                reasons.append("new_discontinuous_seam")
        finally:
            reference.close()
        return GoldValidationResult(
            passed=not reasons,
            reasons=tuple(reasons),
            source_format=source.format,
            source_size=source.size,
            source_sha256=source.sha256,
            output_format=output.format,
            output_size=output.size,
            output_sha256=output.sha256,
            perceptual_distance=perceptual_distance,
        )
    finally:
        if source is not None:
            source.image.close()
        if output is not None:
            output.image.close()


def _dimensions(value):
    width, separator, height = value.lower().partition("x")
    if separator != "x":
        raise argparse.ArgumentTypeError("dimensions must be WIDTHxHEIGHT")
    try:
        result = (int(width), int(height))
    except ValueError:
        raise argparse.ArgumentTypeError(
            "dimensions must be WIDTHxHEIGHT"
        ) from None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate structural evidence for one local Gold output."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--expected-format",
        required=True,
        choices=sorted(_FORMATS),
    )
    parser.add_argument(
        "--expected-size",
        required=True,
        type=_dimensions,
    )
    parser.add_argument(
        "--require-enlargement",
        action="store_true",
    )
    arguments = parser.parse_args(argv)
    try:
        result = validate_gold_output(
            arguments.source,
            arguments.output,
            expected_size=arguments.expected_size,
            expected_format=arguments.expected_format,
            require_enlargement=arguments.require_enlargement,
        )
    except (GoldValidationError, ValueError):
        print("FAIL validation_contract")
        return 2
    fields = [
        "PASS" if result.passed else "FAIL",
        "format=" + str(result.output_format or "unavailable"),
        "dimensions="
        + (
            "{}x{}".format(*result.output_size)
            if result.output_size is not None
            else "unavailable"
        ),
        "source_sha256=" + str(result.source_sha256 or "unavailable"),
        "output_sha256=" + str(result.output_sha256 or "unavailable"),
    ]
    if result.reasons:
        fields.append("reasons=" + ",".join(result.reasons))
    print(" ".join(fields))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
