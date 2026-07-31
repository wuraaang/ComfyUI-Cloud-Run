import asyncio
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cloud_run.r2 import (
    LocalCacheArtifact,
    R2CacheManager,
    R2Config,
    R2Signer,
    R2TransferError,
    R2ValidationError,
)


def config():
    return R2Config(
        endpoint="https://account.r2.cloudflarestorage.com",
        bucket="cloud-run",
        access_key_id="access-id",
        secret_access_key="synthetic-secret",
    )


class FakeSettingsStore:
    def load(self):
        return {
            "r2_endpoint": config().endpoint,
            "r2_bucket": config().bucket,
            "r2_access_key_id": config().access_key_id,
            "r2_secret_access_key": config().secret_access_key,
        }


class FakeCatalog:
    def __init__(self, artifact):
        self.artifact = artifact
        self.cached = []

    def get_local_artifact(self, artifact_id):
        if artifact_id != self.artifact.artifact_id:
            return None
        return self.artifact

    def mark_cached(self, record):
        self.cached.append(record)


class FakeTransport:
    def __init__(self, content, *, resume_at=0, corrupt_metadata=False):
        self.content = content
        self.remote = bytearray(content[:resume_at])
        self.calls = []
        self.corrupt_metadata = corrupt_metadata

    async def resume_offset(self, config_value, object_key, artifact):
        self.calls.append(("resume", object_key))
        return len(self.remote)

    async def upload_part(
        self,
        config_value,
        object_key,
        offset,
        chunk,
        signed_request,
    ):
        self.calls.append(
            ("upload", object_key, offset, bytes(chunk), signed_request.method)
        )
        self.remote.extend(chunk)
        return len(self.remote)

    async def object_metadata(
        self,
        config_value,
        object_key,
        signed_request,
    ):
        self.calls.append(("metadata", object_key, signed_request.method))
        return {
            "size_bytes": len(self.remote),
            "sha256": (
                "0" * 64
                if self.corrupt_metadata
                else hashlib.sha256(self.remote).hexdigest()
            ),
        }


class R2SignerTests(unittest.TestCase):
    def test_signer_scopes_one_object_method_region_and_short_expiry(self):
        fixed = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        signer = R2Signer(config(), now=lambda: fixed)
        object_key = "sha256/aa/" + "a" * 64

        signed = signer.sign_get(object_key, expires=300)
        put = signer.sign_put(object_key, expires=300)

        self.assertEqual(signed.method, "GET")
        self.assertEqual(signed.expires_seconds, 300)
        self.assertIn("/cloud-run/" + object_key, signed.url)
        self.assertNotIn(config().secret_access_key, signed.url)
        self.assertNotIn(signed.url, repr(signed))
        query = parse_qs(urlsplit(signed.url).query)
        self.assertEqual(query["X-Amz-Expires"], ["300"])
        self.assertIn("/auto/s3/aws4_request", query["X-Amz-Credential"][0])
        self.assertNotEqual(
            query["X-Amz-Signature"],
            parse_qs(urlsplit(put.url).query)["X-Amz-Signature"],
        )

        for invalid_key in (
            "../other-object",
            "sha256/x",
            "sha256/aa/" + "b" * 64,
        ):
            with self.subTest(invalid_key=invalid_key):
                with self.assertRaises(R2ValidationError):
                    signer.sign_get(invalid_key, expires=300)
        with self.assertRaises(R2ValidationError):
            signer.sign_put(object_key, expires=3601)

    def test_config_rejects_non_https_paths_userinfo_and_incomplete_values(self):
        for endpoint in (
            "http://account.r2.cloudflarestorage.com",
            "https://user@account.r2.cloudflarestorage.com",
            "https://account.r2.cloudflarestorage.com/path",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(R2ValidationError):
                    R2Config(
                        endpoint=endpoint,
                        bucket="cloud-run",
                        access_key_id="access-id",
                        secret_access_key="synthetic-secret",
                    )
        with self.assertRaises(R2ValidationError):
            R2Config.from_settings({})


class R2CacheManagerTests(unittest.TestCase):
    def test_explicit_population_resumes_and_verifies_without_returning_url(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "artifact.bin"
            content = b"content-addressed-private-artifact"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            artifact = LocalCacheArtifact(
                artifact_id="model-" + digest,
                private_path=str(path),
                size_bytes=len(content),
                sha256=digest,
            )
            catalog = FakeCatalog(artifact)
            transport = FakeTransport(content, resume_at=5)
            manager = R2CacheManager(
                settings_store=FakeSettingsStore(),
                artifact_catalog=catalog,
                transport=transport,
                chunk_bytes=7,
                signer_now=lambda: datetime(
                    2026,
                    7,
                    31,
                    12,
                    0,
                    tzinfo=timezone.utc,
                ),
            )

            result = asyncio.run(
                manager.populate_cache(
                    artifact.artifact_id,
                    acknowledged=True,
                )
            )

        self.assertEqual(
            result.public_payload(),
            {
                "artifact_id": artifact.artifact_id,
                "status": "cached",
                "size_bytes": len(content),
                "sha256": digest,
            },
        )
        self.assertNotIn("https://", repr(result))
        self.assertEqual(bytes(transport.remote), content)
        self.assertEqual(len(catalog.cached), 1)
        self.assertTrue(
            all(call[-1] == "PUT" for call in transport.calls if call[0] == "upload")
        )
        self.assertEqual(transport.calls[-1][-1], "GET")

    def test_population_requires_acknowledgement_and_exact_remote_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "artifact.bin"
            content = b"artifact"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            artifact = LocalCacheArtifact(
                artifact_id="input-" + digest,
                private_path=str(path),
                size_bytes=len(content),
                sha256=digest,
            )
            catalog = FakeCatalog(artifact)
            manager = R2CacheManager(
                settings_store=FakeSettingsStore(),
                artifact_catalog=catalog,
                transport=FakeTransport(content, corrupt_metadata=True),
            )

            with self.assertRaises(R2ValidationError):
                asyncio.run(
                    manager.populate_cache(
                        artifact.artifact_id,
                        acknowledged=False,
                    )
                )
            with self.assertRaises(R2TransferError):
                asyncio.run(
                    manager.populate_cache(
                        artifact.artifact_id,
                        acknowledged=True,
                    )
                )

        self.assertEqual(catalog.cached, [])


if __name__ == "__main__":
    unittest.main()
