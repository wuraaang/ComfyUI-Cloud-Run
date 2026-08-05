#!/usr/bin/env python3
"""Validate sanitized image evidence for the zero-model Cloud Run smoke."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
import warnings


MAX_SMOKE_FILE_BYTES = 16 * 1024 * 1024


class SmokeValidationError(RuntimeError):
    """A sanitized smoke-output validation failure."""


@dataclass(frozen=True)
class SmokeValidationResult:
    format: str
    dimensions: tuple[int, int]
    size_bytes: int
    sha256: str


def _pillow():
    try:
        from PIL import Image
    except ImportError:
        raise SmokeValidationError("pillow_unavailable") from None
    return Image


def _metadata_identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _descriptor_identity(descriptor):
    return _metadata_identity(os.fstat(descriptor))


def _pixel_values(image):
    flattened = getattr(image, "get_flattened_data", None)
    if callable(flattened):
        return flattened()
    return image.getdata()


def _decode_descriptor(descriptor):
    Image = _pillow()
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                with Image.open(stream) as probe:
                    image_format = str(probe.format or "").upper()
                    image_size = tuple(probe.size)
                    probe.verify()

        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                with Image.open(stream) as decoded:
                    if (
                        str(decoded.format or "").upper() != image_format
                        or tuple(decoded.size) != image_size
                    ):
                        raise SmokeValidationError("image_decode_failed")
                    decoded.load()
                    image = decoded.convert("RGB")
    except SmokeValidationError:
        raise
    except Exception:
        raise SmokeValidationError("image_decode_failed") from None
    return image, image_format, image_size


def validate_smoke_output(
    output_path,
    *,
    expected_size=(512, 512),
    expected_rgb=(18, 103, 163),
):
    """Validate one exact regular PNG without exposing its private path."""

    if (
        tuple(expected_size) != expected_size
        or len(expected_size) != 2
        or any(type(value) is not int or value <= 0 for value in expected_size)
        or tuple(expected_rgb) != expected_rgb
        or len(expected_rgb) != 3
        or any(
            type(value) is not int or not 0 <= value <= 255
            for value in expected_rgb
        )
    ):
        raise SmokeValidationError("validation_configuration_invalid")

    descriptor = None
    image = None
    try:
        path = Path(output_path)
        metadata = os.lstat(path)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_SMOKE_FILE_BYTES
        ):
            raise SmokeValidationError("image_file_invalid")

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = _descriptor_identity(descriptor)
        if before != _metadata_identity(metadata):
            raise SmokeValidationError("image_file_changed")

        digest = hashlib.sha256()
        bytes_read = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > MAX_SMOKE_FILE_BYTES:
                raise SmokeValidationError("image_file_invalid")
            digest.update(chunk)
        if bytes_read != before[2]:
            raise SmokeValidationError("image_file_changed")

        image, image_format, image_size = _decode_descriptor(descriptor)
        if image_format != "PNG":
            raise SmokeValidationError("image_format_mismatch")
        if image_size != expected_size:
            raise SmokeValidationError("image_dimensions_mismatch")
        if any(tuple(pixel) != expected_rgb for pixel in _pixel_values(image)):
            raise SmokeValidationError("image_pixels_mismatch")

        after = _descriptor_identity(descriptor)
        if after != before:
            raise SmokeValidationError("image_file_changed")
        return SmokeValidationResult(
            format=image_format,
            dimensions=image_size,
            size_bytes=bytes_read,
            sha256=digest.hexdigest(),
        )
    except SmokeValidationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise SmokeValidationError("image_file_invalid") from None
    finally:
        if image is not None:
            image.close()
        if descriptor is not None:
            os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate one zero-model Cloud Run smoke output.",
    )
    parser.add_argument("output_path")
    arguments = parser.parse_args(argv)
    try:
        result = validate_smoke_output(arguments.output_path)
    except SmokeValidationError as error:
        print("FAIL reason=" + str(error), file=sys.stderr)
        return 1
    print(
        "PASS format={} dimensions={}x{} size_bytes={} sha256={}".format(
            result.format,
            result.dimensions[0],
            result.dimensions[1],
            result.size_bytes,
            result.sha256,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
