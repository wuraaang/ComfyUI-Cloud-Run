"""Validation for backend-owned Cloud Run settings."""

import json
import math
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .constants import (
    DEFAULT_MAX_PRICE_PER_HOUR,
    DEFAULT_MIN_VRAM_GB,
    MAX_API_KEY_LENGTH,
    MAX_PRICE_PER_HOUR,
    MAX_VRAM_GB,
    MIN_PRICE_PER_HOUR,
    MIN_VRAM_GB,
    OFFICIAL_TEMPLATE_ID,
    OFFICIAL_TEMPLATE_NAME,
)


class SettingsValidationError(ValueError):
    """A settings payload is invalid."""


_R2_FIELDS = (
    "r2_endpoint",
    "r2_bucket",
    "r2_access_key_id",
    "r2_secret_access_key",
)
_OPTIONAL_FIELDS = {
    *_R2_FIELDS,
    "hf_token",
    "civitai_token",
}
_R2_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")


def resolve_data_directory():
    """Resolve the private data directory without creating it."""
    override = os.environ.get("COMFYUI_CLOUD_RUN_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    try:
        import folder_paths

        user_directory = folder_paths.get_user_directory()
    except (ImportError, AttributeError):
        user_directory = Path.home() / ".comfyui"
    return Path(user_directory) / "comfyui-cloud-run"


def _default_settings():
    return {
        "max_price_per_hour": DEFAULT_MAX_PRICE_PER_HOUR,
        "min_vram_gb": DEFAULT_MIN_VRAM_GB,
    }


def _optional_value(field, value):
    if not isinstance(value, str):
        raise SettingsValidationError("Optional credential value is invalid.")
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_API_KEY_LENGTH:
        raise SettingsValidationError("Optional credential value is invalid.")
    if field == "r2_endpoint":
        try:
            parsed = urlsplit(normalized)
            port = parsed.port
        except ValueError:
            raise SettingsValidationError("R2 endpoint is invalid.") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise SettingsValidationError("R2 endpoint is invalid.")
        return f"https://{parsed.hostname}"
    if field == "r2_bucket" and not _R2_BUCKET.fullmatch(normalized):
        raise SettingsValidationError("R2 bucket is invalid.")
    return normalized


def validate_update(payload):
    """Validate and normalize a settings update from the browser."""
    if not isinstance(payload, dict):
        raise SettingsValidationError("Settings must be a JSON object.")

    allowed = {
        "api_key",
        "max_price_per_hour",
        "min_vram_gb",
        *_OPTIONAL_FIELDS,
    }
    if set(payload) - allowed:
        raise SettingsValidationError("Settings contain unsupported fields.")
    if "max_price_per_hour" not in payload or "min_vram_gb" not in payload:
        raise SettingsValidationError("Price and VRAM settings are required.")

    price = payload["max_price_per_hour"]
    if (
        isinstance(price, bool)
        or not isinstance(price, (int, float))
        or not math.isfinite(price)
        or not MIN_PRICE_PER_HOUR <= float(price) <= MAX_PRICE_PER_HOUR
    ):
        raise SettingsValidationError(
            "Maximum hourly price must be between 0.01 and 100."
        )

    vram = payload["min_vram_gb"]
    if (
        isinstance(vram, bool)
        or not isinstance(vram, (int, float))
        or not math.isfinite(vram)
        or not float(vram).is_integer()
        or not MIN_VRAM_GB <= int(vram) <= MAX_VRAM_GB
    ):
        raise SettingsValidationError(
            "Minimum VRAM must be a whole number between 1 and 1024."
        )

    normalized = {
        "max_price_per_hour": float(price),
        "min_vram_gb": int(vram),
    }

    if "api_key" in payload:
        key = payload["api_key"]
        if not isinstance(key, str):
            raise SettingsValidationError("Vast API key must be a string.")
        key = key.strip()
        if not key or len(key) > MAX_API_KEY_LENGTH:
            raise SettingsValidationError("Vast API key has an invalid length.")
        normalized["api_key"] = key

    for field in _OPTIONAL_FIELDS:
        if field in payload:
            normalized[field] = _optional_value(field, payload[field])

    return normalized


def public_settings(settings):
    """Return the exact browser-safe settings representation."""
    return {
        "configured": bool(settings.get("api_key")),
        "r2_configured": all(bool(settings.get(field)) for field in _R2_FIELDS),
        "hf_configured": bool(settings.get("hf_token")),
        "civitai_configured": bool(settings.get("civitai_token")),
        "max_price_per_hour": settings["max_price_per_hour"],
        "min_vram_gb": settings["min_vram_gb"],
        "official_template_id": OFFICIAL_TEMPLATE_ID,
        "official_template_name": OFFICIAL_TEMPLATE_NAME,
        "lifecycle_enabled": True,
    }


class SettingsStore:
    """Atomic private-file storage for Cloud Run settings."""

    def __init__(self, data_directory=None):
        self.data_directory = Path(
            data_directory if data_directory is not None else resolve_data_directory()
        )
        self.path = self.data_directory / "settings.json"

    def load(self):
        settings = _default_settings()
        if not self.path.exists():
            return settings

        try:
            os.chmod(self.path, 0o600)
            with self.path.open("r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if not isinstance(stored, dict):
                return settings

            filters = validate_update(
                {
                    "max_price_per_hour": stored.get("max_price_per_hour"),
                    "min_vram_gb": stored.get("min_vram_gb"),
                }
            )
            settings.update(filters)

            key = stored.get("api_key")
            if (
                isinstance(key, str)
                and key.strip()
                and len(key.strip()) <= MAX_API_KEY_LENGTH
            ):
                settings["api_key"] = key.strip()
            optional = {}
            for field in _OPTIONAL_FIELDS:
                if field not in stored:
                    continue
                try:
                    optional[field] = _optional_value(field, stored[field])
                except SettingsValidationError:
                    continue
            if all(field in optional for field in _R2_FIELDS):
                settings.update(
                    {field: optional[field] for field in _R2_FIELDS}
                )
            for field in ("hf_token", "civitai_token"):
                if field in optional:
                    settings[field] = optional[field]
        except (OSError, ValueError, json.JSONDecodeError, SettingsValidationError):
            return _default_settings()
        return settings

    def update(self, payload):
        updated = self.load()
        updated.update(validate_update(payload))
        if any(field in updated for field in _R2_FIELDS) and not all(
            field in updated for field in _R2_FIELDS
        ):
            raise SettingsValidationError(
                "Complete R2 settings are required."
            )
        self._write(updated)
        return updated

    def _write(self, settings):
        self.data_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.data_directory, 0o700)

        descriptor = None
        temporary_path = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".settings-",
                suffix=".tmp",
                dir=str(self.data_directory),
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = None
                json.dump(settings, handle, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            os.chmod(self.path, 0o600)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
