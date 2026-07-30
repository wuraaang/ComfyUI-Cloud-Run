import math
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class SettingsValidationTests(unittest.TestCase):
    def test_accepts_valid_filters_with_optional_trimmed_key(self):
        from cloud_run.settings import validate_update

        self.assertEqual(
            validate_update(
                {
                    "api_key": "  synthetic-value  ",
                    "max_price_per_hour": 0.75,
                    "min_vram_gb": 24,
                }
            ),
            {
                "api_key": "synthetic-value",
                "max_price_per_hour": 0.75,
                "min_vram_gb": 24,
            },
        )
        self.assertEqual(
            validate_update(
                {
                    "max_price_per_hour": 1,
                    "min_vram_gb": 16.0,
                }
            ),
            {
                "max_price_per_hour": 1.0,
                "min_vram_gb": 16,
            },
        )

    def test_rejects_invalid_price_values(self):
        from cloud_run.settings import SettingsValidationError, validate_update

        invalid_values = [
            True,
            "1.00",
            math.nan,
            math.inf,
            0,
            0.001,
            100.01,
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(SettingsValidationError):
                    validate_update(
                        {
                            "max_price_per_hour": value,
                            "min_vram_gb": 16,
                        }
                    )

    def test_rejects_invalid_vram_values(self):
        from cloud_run.settings import SettingsValidationError, validate_update

        invalid_values = [True, "16", 16.5, 0, -1, 1025]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(SettingsValidationError):
                    validate_update(
                        {
                            "max_price_per_hour": 1.0,
                            "min_vram_gb": value,
                        }
                    )

    def test_rejects_missing_filters_unknown_fields_and_invalid_key(self):
        from cloud_run.settings import SettingsValidationError, validate_update

        invalid_payloads = [
            None,
            {},
            {"max_price_per_hour": 1.0},
            {"min_vram_gb": 16},
            {
                "max_price_per_hour": 1.0,
                "min_vram_gb": 16,
                "offer_id": 99,
            },
            {
                "api_key": "",
                "max_price_per_hour": 1.0,
                "min_vram_gb": 16,
            },
            {
                "api_key": None,
                "max_price_per_hour": 1.0,
                "min_vram_gb": 16,
            },
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(SettingsValidationError):
                    validate_update(payload)

    def test_validation_errors_do_not_echo_key_material(self):
        from cloud_run.settings import SettingsValidationError, validate_update

        sensitive_marker = "do-not-echo-this-marker"
        with self.assertRaises(SettingsValidationError) as raised:
            validate_update(
                {
                    "api_key": sensitive_marker * 500,
                    "max_price_per_hour": 1.0,
                    "min_vram_gb": 16,
                }
            )
        self.assertNotIn(sensitive_marker, str(raised.exception))


class SettingsPersistenceTests(unittest.TestCase):
    def test_atomic_file_is_private_and_public_settings_are_redacted(self):
        from cloud_run.settings import SettingsStore, public_settings

        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory) / "cloud-run-data"
            store = SettingsStore(data_directory)
            saved = store.update(
                {
                    "api_key": "synthetic-value",
                    "max_price_per_hour": 0.8,
                    "min_vram_gb": 24,
                }
            )

            self.assertEqual(store.path, data_directory / "settings.json")
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(data_directory.stat().st_mode), 0o700)
            self.assertEqual(
                sorted(path.name for path in data_directory.iterdir()),
                ["settings.json"],
            )
            self.assertEqual(json.loads(store.path.read_text()), saved)

            public = public_settings(saved)
            self.assertEqual(
                public,
                {
                    "configured": True,
                    "max_price_per_hour": 0.8,
                    "min_vram_gb": 24,
                    "official_template_id": "57808457573e32120301649763d8e019",
                    "official_template_name": "Official ComfyUI",
                    "preview_only": True,
                },
            )
            self.assertNotIn("api_key", public)
            self.assertFalse(any("mask" in key for key in public))

    def test_update_without_key_preserves_saved_key(self):
        from cloud_run.settings import SettingsStore

        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SettingsStore(Path(temporary_directory))
            store.update(
                {
                    "api_key": "synthetic-value",
                    "max_price_per_hour": 0.8,
                    "min_vram_gb": 24,
                }
            )
            updated = store.update(
                {
                    "max_price_per_hour": 0.5,
                    "min_vram_gb": 32,
                }
            )

            self.assertEqual(updated["api_key"], "synthetic-value")
            self.assertEqual(updated["max_price_per_hour"], 0.5)
            self.assertEqual(updated["min_vram_gb"], 32)
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)

    def test_missing_or_corrupt_file_loads_safe_defaults(self):
        from cloud_run.settings import SettingsStore, public_settings

        expected = {
            "configured": False,
            "max_price_per_hour": 1.0,
            "min_vram_gb": 16,
            "official_template_id": "57808457573e32120301649763d8e019",
            "official_template_name": "Official ComfyUI",
            "preview_only": True,
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            store = SettingsStore(data_directory)
            self.assertEqual(public_settings(store.load()), expected)

            store.path.write_text("{not valid JSON")
            os.chmod(store.path, 0o644)
            self.assertEqual(public_settings(store.load()), expected)
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)

    def test_data_directory_prefers_env_override_then_comfy_user_directory(self):
        from cloud_run.settings import resolve_data_directory

        with tempfile.TemporaryDirectory() as temporary_directory:
            override = Path(temporary_directory) / "override"
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": str(override)},
                clear=False,
            ):
                self.assertEqual(resolve_data_directory(), override)

        fake_folder_paths = types.ModuleType("folder_paths")
        fake_folder_paths.get_user_directory = lambda: "/comfy/user"
        prior = sys.modules.get("folder_paths")
        with mock.patch.dict(
            os.environ,
            {"COMFYUI_CLOUD_RUN_DATA_DIR": ""},
            clear=False,
        ):
            sys.modules["folder_paths"] = fake_folder_paths
            try:
                self.assertEqual(
                    resolve_data_directory(),
                    Path("/comfy/user/comfyui-cloud-run"),
                )
            finally:
                if prior is None:
                    sys.modules.pop("folder_paths", None)
                else:
                    sys.modules["folder_paths"] = prior


if __name__ == "__main__":
    unittest.main()
