"""Backend-only R2 signing and explicit cache population."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

from .artifacts import ArtifactPathError, hash_file
from .constants import MAX_API_KEY_LENGTH


MAX_SIGNED_URL_SECONDS = 3600
DEFAULT_SIGNED_URL_SECONDS = 300
DEFAULT_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")


class R2ValidationError(ValueError):
    pass


class R2TransferError(RuntimeError):
    pass


def _endpoint(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise R2ValidationError("R2 endpoint is invalid.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise R2ValidationError("R2 endpoint is invalid.") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or value != f"https://{parsed.hostname}"
    ):
        raise R2ValidationError("R2 endpoint is invalid.")
    return value


def _secret(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_API_KEY_LENGTH
    ):
        raise R2ValidationError("R2 credential is invalid.")
    return value


@dataclass(frozen=True, repr=False)
class R2Config:
    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str

    def __post_init__(self):
        _endpoint(self.endpoint)
        if not isinstance(self.bucket, str) or not _BUCKET.fullmatch(
            self.bucket
        ):
            raise R2ValidationError("R2 bucket is invalid.")
        _secret(self.access_key_id)
        _secret(self.secret_access_key)

    def __repr__(self):
        return (
            "R2Config(endpoint="
            + repr(self.endpoint)
            + ", bucket="
            + repr(self.bucket)
            + ", configured=True)"
        )

    @classmethod
    def from_settings(cls, settings):
        if not isinstance(settings, dict):
            raise R2ValidationError("R2 is not configured.")
        try:
            return cls(
                endpoint=settings["r2_endpoint"],
                bucket=settings["r2_bucket"],
                access_key_id=settings["r2_access_key_id"],
                secret_access_key=settings["r2_secret_access_key"],
            )
        except (KeyError, R2ValidationError):
            raise R2ValidationError("R2 is not configured.") from None


@dataclass(frozen=True, repr=False)
class SignedRequest:
    method: str
    url: str
    expires_seconds: int

    def __repr__(self):
        return (
            "SignedRequest(method="
            + repr(self.method)
            + ", expires_seconds="
            + repr(self.expires_seconds)
            + ", url=<redacted>)"
        )


def _object_key(value):
    if not isinstance(value, str):
        raise R2ValidationError("R2 object key is invalid.")
    match = re.fullmatch(r"sha256/([0-9a-f]{2})/([0-9a-f]{64})", value)
    if match is None or match.group(1) != match.group(2)[:2]:
        raise R2ValidationError("R2 object key is invalid.")
    return value


def _expiry(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_SIGNED_URL_SECONDS
    ):
        raise R2ValidationError("R2 signed request expiry is invalid.")
    return value


def _hmac(key, value):
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _query_string(items):
    return "&".join(
        quote(str(key), safe="-_.~")
        + "="
        + quote(str(value), safe="-_.~")
        for key, value in sorted(items)
    )


class R2Signer:
    def __init__(self, config, *, now=None):
        if not isinstance(config, R2Config):
            raise R2ValidationError("R2 signer configuration is invalid.")
        self.config = config
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _sign(self, method, object_key, expires):
        key = _object_key(object_key)
        lifetime = _expiry(expires)
        try:
            current = self.now()
        except Exception:
            raise R2ValidationError("R2 signing clock is invalid.") from None
        if (
            not isinstance(current, datetime)
            or current.tzinfo is None
            or current.utcoffset() is None
        ):
            raise R2ValidationError("R2 signing clock is invalid.")
        current = current.astimezone(timezone.utc)
        date_stamp = current.strftime("%Y%m%d")
        amz_date = current.strftime("%Y%m%dT%H%M%SZ")
        scope = f"{date_stamp}/auto/s3/aws4_request"
        canonical_uri = (
            "/"
            + quote(self.config.bucket, safe="-_.~")
            + "/"
            + quote(key, safe="/-_.~")
        )
        query_items = (
            ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
            (
                "X-Amz-Credential",
                self.config.access_key_id + "/" + scope,
            ),
            ("X-Amz-Date", amz_date),
            ("X-Amz-Expires", str(lifetime)),
            ("X-Amz-SignedHeaders", "host"),
        )
        canonical_query = _query_string(query_items)
        host = urlsplit(self.config.endpoint).hostname
        canonical_headers = f"host:{host}\n"
        canonical_request = "\n".join(
            (
                method,
                canonical_uri,
                canonical_query,
                canonical_headers,
                "host",
                "UNSIGNED-PAYLOAD",
            )
        )
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(
                    canonical_request.encode("utf-8")
                ).hexdigest(),
            )
        )
        date_key = _hmac(
            ("AWS4" + self.config.secret_access_key).encode("utf-8"),
            date_stamp,
        )
        region_key = _hmac(date_key, "auto")
        service_key = _hmac(region_key, "s3")
        signing_key = _hmac(service_key, "aws4_request")
        signature = hmac.new(
            signing_key,
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return SignedRequest(
            method=method,
            url=(
                self.config.endpoint
                + canonical_uri
                + "?"
                + canonical_query
                + "&X-Amz-Signature="
                + signature
            ),
            expires_seconds=lifetime,
        )

    def sign_get(self, object_key, *, expires=DEFAULT_SIGNED_URL_SECONDS):
        return self._sign("GET", object_key, expires)

    def sign_put(self, object_key, *, expires=DEFAULT_SIGNED_URL_SECONDS):
        return self._sign("PUT", object_key, expires)


@dataclass(frozen=True, repr=False)
class LocalCacheArtifact:
    artifact_id: str
    private_path: str
    size_bytes: int
    sha256: str

    def __post_init__(self):
        if not isinstance(self.artifact_id, str) or not _ARTIFACT_ID.fullmatch(
            self.artifact_id
        ):
            raise R2ValidationError("Cache artifact identity is invalid.")
        if not isinstance(self.private_path, str) or not self.private_path:
            raise R2ValidationError("Cache artifact path is invalid.")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes <= 0
            or not isinstance(self.sha256, str)
            or not _HEX_64.fullmatch(self.sha256)
        ):
            raise R2ValidationError("Cache artifact metadata is invalid.")


@dataclass(frozen=True)
class CachedArtifact:
    artifact_id: str
    object_key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class CachePopulationResult:
    artifact_id: str
    status: str
    size_bytes: int
    sha256: str

    def public_payload(self):
        return {
            "artifact_id": self.artifact_id,
            "status": self.status,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


class R2CacheManager:
    """Populate one server-known artifact only after explicit acknowledgement."""

    def __init__(
        self,
        *,
        settings_store,
        artifact_catalog,
        transport,
        chunk_bytes=DEFAULT_UPLOAD_CHUNK_BYTES,
        signer_now=None,
    ):
        if (
            isinstance(chunk_bytes, bool)
            or not isinstance(chunk_bytes, int)
            or not 1 <= chunk_bytes <= DEFAULT_UPLOAD_CHUNK_BYTES
        ):
            raise R2ValidationError("R2 upload chunk size is invalid.")
        self.settings_store = settings_store
        self.artifact_catalog = artifact_catalog
        self.transport = transport
        self.chunk_bytes = chunk_bytes
        self.signer_now = signer_now

    def _artifact(self, artifact_id):
        if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(
            artifact_id
        ):
            raise R2ValidationError("Cache artifact identity is invalid.")
        try:
            artifact = self.artifact_catalog.get_local_artifact(artifact_id)
        except Exception:
            raise R2ValidationError(
                "Cache artifact is unavailable."
            ) from None
        if not isinstance(artifact, LocalCacheArtifact):
            raise R2ValidationError("Cache artifact is unavailable.")
        if artifact.artifact_id != artifact_id:
            raise R2ValidationError("Cache artifact is unavailable.")
        return artifact

    async def populate_cache(self, artifact_id, *, acknowledged):
        if acknowledged is not True:
            raise R2ValidationError(
                "Cache population requires explicit acknowledgement."
            )
        if self.transport is None:
            raise R2TransferError("R2 cache transfer is unavailable.")
        try:
            config = R2Config.from_settings(self.settings_store.load())
        except (AttributeError, R2ValidationError):
            raise R2ValidationError("R2 is not configured.") from None
        artifact = self._artifact(artifact_id)
        try:
            local_digest = hash_file(artifact.private_path)
        except ArtifactPathError:
            raise R2ValidationError("Cache artifact is unavailable.") from None
        if (
            local_digest.size_bytes != artifact.size_bytes
            or local_digest.sha256 != artifact.sha256
        ):
            raise R2ValidationError("Cache artifact content changed.")

        object_key = (
            "sha256/"
            + artifact.sha256[:2]
            + "/"
            + artifact.sha256
        )
        signer = R2Signer(config, now=self.signer_now)
        try:
            offset = await self.transport.resume_offset(
                config,
                object_key,
                artifact,
            )
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or not 0 <= offset <= artifact.size_bytes
            ):
                raise R2TransferError("R2 cache resume state is invalid.")
            with Path(artifact.private_path).open("rb") as stream:
                stream.seek(offset)
                while offset < artifact.size_bytes:
                    chunk = stream.read(
                        min(
                            self.chunk_bytes,
                            artifact.size_bytes - offset,
                        )
                    )
                    if not chunk:
                        raise R2TransferError(
                            "R2 cache source ended unexpectedly."
                        )
                    signed = signer.sign_put(object_key)
                    next_offset = await self.transport.upload_part(
                        config,
                        object_key,
                        offset,
                        chunk,
                        signed,
                    )
                    expected_offset = offset + len(chunk)
                    if next_offset != expected_offset:
                        raise R2TransferError(
                            "R2 cache upload made invalid progress."
                        )
                    offset = next_offset
            after = hash_file(artifact.private_path)
            if after != local_digest:
                raise R2TransferError(
                    "R2 cache source changed during upload."
                )
            metadata = await self.transport.object_metadata(
                config,
                object_key,
                signer.sign_get(object_key),
            )
        except (R2TransferError, R2ValidationError):
            raise
        except Exception:
            raise R2TransferError("R2 cache transfer failed.") from None
        if (
            not isinstance(metadata, dict)
            or metadata.get("size_bytes") != artifact.size_bytes
            or metadata.get("sha256") != artifact.sha256
        ):
            raise R2TransferError("R2 cache verification failed.")

        cached = CachedArtifact(
            artifact_id=artifact.artifact_id,
            object_key=object_key,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
        )
        try:
            self.artifact_catalog.mark_cached(cached)
        except Exception:
            raise R2TransferError(
                "R2 cache verification could not be recorded."
            ) from None
        return CachePopulationResult(
            artifact_id=artifact.artifact_id,
            status="cached",
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
        )
