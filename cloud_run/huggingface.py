"""Strict read-only Hugging Face model metadata resolution."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Optional
from urllib.parse import parse_qsl, quote, urlsplit


HUGGINGFACE_ORIGIN = "https://huggingface.co"
MAX_HUGGINGFACE_RESPONSE_BYTES = 2 * 1024 * 1024
HUGGINGFACE_TIMEOUT_SECONDS = 30.0
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_REPO_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
_PATH_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}")


class HuggingFaceError(RuntimeError):
    """A sanitized Hugging Face candidate or metadata failure."""


@dataclass(frozen=True)
class HuggingFaceReference:
    repository_id: str
    revision: str
    file_path: str


@dataclass(frozen=True)
class ResolvedHuggingFaceFile:
    repository_id: str
    file_path: str
    immutable_revision: str
    locator: str
    size_bytes: int
    sha256: str


def parse_huggingface_url(value: str) -> HuggingFaceReference:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or len(value) > 8192
        or "%" in value
        or "\\" in value
        or any(ord(character) < 33 for character in value)
    ):
        raise HuggingFaceError("Hugging Face candidate URL is invalid.")

    try:
        parsed = urlsplit(value)
        port = parsed.port
        query = (
            parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
            if parsed.query
            else []
        )
    except (TypeError, ValueError):
        raise HuggingFaceError("Hugging Face candidate URL is invalid.") from None

    if (
        parsed.scheme != "https"
        or parsed.hostname != "huggingface.co"
        or parsed.netloc != "huggingface.co"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or query not in ([], [("download", "true")])
        or ("?" in value and query == [])
    ):
        raise HuggingFaceError("Hugging Face candidate URL is invalid.")

    parts = parsed.path.split("/")
    if (
        len(parts) < 6
        or parts[0] != ""
        or parts[3] != "resolve"
        or not _REPO_PART.fullmatch(parts[1])
        or not _REPO_PART.fullmatch(parts[2])
        or not _PATH_PART.fullmatch(parts[4])
        or any(not _PATH_PART.fullmatch(part) for part in parts[5:])
    ):
        raise HuggingFaceError("Hugging Face candidate URL is invalid.")

    return HuggingFaceReference(
        repository_id=parts[1] + "/" + parts[2],
        revision=parts[4],
        file_path="/".join(parts[5:]),
    )


def _model_info_url(reference, revision, *, blobs):
    base = (
        HUGGINGFACE_ORIGIN
        + "/api/models/"
        + reference.repository_id
        + "/revision/"
        + quote(revision, safe="")
    )
    return base + ("?blobs=true" if blobs else "")


def _validate_reference(reference):
    if not isinstance(reference, HuggingFaceReference):
        raise HuggingFaceError("Hugging Face candidate is invalid.")
    candidate_url = (
        HUGGINGFACE_ORIGIN
        + "/"
        + reference.repository_id
        + "/resolve/"
        + reference.revision
        + "/"
        + reference.file_path
    )
    try:
        parsed = parse_huggingface_url(candidate_url)
    except HuggingFaceError:
        raise HuggingFaceError("Hugging Face candidate is invalid.") from None
    if parsed != reference:
        raise HuggingFaceError("Hugging Face candidate is invalid.")


class HuggingFaceClient:
    def __init__(
        self,
        *,
        session=None,
        timeout=HUGGINGFACE_TIMEOUT_SECONDS,
        max_response_bytes=MAX_HUGGINGFACE_RESPONSE_BYTES,
    ):
        try:
            self.timeout = float(timeout)
            self.max_response_bytes = int(max_response_bytes)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Hugging Face transport bounds are invalid.") from None
        if (
            isinstance(timeout, bool)
            or not math.isfinite(self.timeout)
            or not 0 < self.timeout <= HUGGINGFACE_TIMEOUT_SECONDS
        ):
            raise ValueError("Hugging Face timeout is outside safe bounds.")
        if (
            isinstance(max_response_bytes, bool)
            or self.max_response_bytes != max_response_bytes
            or not 1
            <= self.max_response_bytes
            <= MAX_HUGGINGFACE_RESPONSE_BYTES
        ):
            raise ValueError("Hugging Face response bound is invalid.")
        self.session = session

    async def _get_json(self, session, request_url):
        if not request_url.startswith(HUGGINGFACE_ORIGIN + "/api/models/"):
            raise HuggingFaceError("Hugging Face request origin is invalid.")
        response = None
        try:
            response = await session.get(
                request_url,
                allow_redirects=False,
                timeout=self.timeout,
            )
            if (
                not isinstance(response.status, int)
                or isinstance(response.status, bool)
                or response.status != 200
                or str(response.url) != request_url
            ):
                raise HuggingFaceError("Hugging Face metadata lookup failed.")

            body = bytearray()
            while True:
                remaining = self.max_response_bytes + 1 - len(body)
                chunk = await response.content.read(min(64 * 1024, remaining))
                if not isinstance(chunk, (bytes, bytearray)):
                    raise HuggingFaceError(
                        "Hugging Face metadata response is invalid."
                    )
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > self.max_response_bytes:
                    raise HuggingFaceError(
                        "Hugging Face metadata response is too large."
                    )
            try:
                return json.loads(bytes(body).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise HuggingFaceError(
                    "Hugging Face metadata response is invalid."
                ) from None
        except HuggingFaceError:
            raise
        except Exception:
            raise HuggingFaceError("Hugging Face metadata lookup failed.") from None
        finally:
            if response is not None:
                release = getattr(response, "release", None)
                if callable(release):
                    release()

    @staticmethod
    def _response_identity(payload, repository_id):
        if not isinstance(payload, dict):
            raise HuggingFaceError("Hugging Face metadata is invalid.")
        response_repository = payload.get("id")
        response_revision = payload.get("sha")
        if (
            response_repository != repository_id
            or not isinstance(response_revision, str)
            or not _HEX_40.fullmatch(response_revision)
        ):
            raise HuggingFaceError("Hugging Face metadata is invalid.")
        return response_revision

    @staticmethod
    def _resolved_file(payload, reference, immutable_revision, expected_sha256):
        response_revision = HuggingFaceClient._response_identity(
            payload,
            reference.repository_id,
        )
        if (
            response_revision != immutable_revision
            or payload.get("private") is not False
            or payload.get("gated") is not False
        ):
            raise HuggingFaceError("Hugging Face metadata is invalid.")

        siblings = payload.get("siblings")
        if not isinstance(siblings, list):
            raise HuggingFaceError("Hugging Face metadata is invalid.")
        matches = [
            sibling
            for sibling in siblings
            if isinstance(sibling, dict)
            and sibling.get("rfilename") == reference.file_path
        ]
        if len(matches) != 1:
            raise HuggingFaceError("Hugging Face file metadata is invalid.")
        sibling = matches[0]
        lfs = sibling.get("lfs")
        if not isinstance(lfs, dict):
            raise HuggingFaceError("Hugging Face file metadata is invalid.")

        size_bytes = lfs.get("size")
        sha256 = lfs.get("sha256")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes <= 0
            or not isinstance(sha256, str)
            or not _HEX_64.fullmatch(sha256)
        ):
            raise HuggingFaceError("Hugging Face file metadata is invalid.")
        duplicate_size = sibling.get("size")
        if duplicate_size is not None and (
            not isinstance(duplicate_size, int)
            or isinstance(duplicate_size, bool)
            or duplicate_size <= 0
            or duplicate_size != size_bytes
        ):
            raise HuggingFaceError("Hugging Face file metadata is invalid.")
        if expected_sha256 is not None and sha256 != expected_sha256:
            raise HuggingFaceError("Hugging Face file digest does not match.")

        locator = (
            HUGGINGFACE_ORIGIN
            + "/"
            + reference.repository_id
            + "/resolve/"
            + immutable_revision
            + "/"
            + reference.file_path
        )
        resolved = ResolvedHuggingFaceFile(
            repository_id=reference.repository_id,
            file_path=reference.file_path,
            immutable_revision=immutable_revision,
            locator=locator,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        resolved_reference = parse_huggingface_url(resolved.locator)
        if (
            resolved_reference.repository_id != resolved.repository_id
            or resolved_reference.revision != resolved.immutable_revision
            or resolved_reference.file_path != resolved.file_path
            or not _HEX_40.fullmatch(resolved.immutable_revision)
            or not _HEX_64.fullmatch(resolved.sha256)
        ):
            raise HuggingFaceError("Hugging Face result is invalid.")
        return resolved

    async def resolve(
        self,
        reference: HuggingFaceReference,
        *,
        expected_sha256: Optional[str] = None,
    ) -> ResolvedHuggingFaceFile:
        _validate_reference(reference)
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or not _HEX_64.fullmatch(expected_sha256)
        ):
            raise HuggingFaceError("Hugging Face expected digest is invalid.")

        owned_session = None
        session = self.session
        if session is None:
            try:
                import aiohttp
            except ImportError:
                raise HuggingFaceError(
                    "Hugging Face metadata client is unavailable."
                ) from None
            owned_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                trust_env=False,
            )
            session = owned_session

        try:
            immutable_revision = reference.revision
            if not _HEX_40.fullmatch(immutable_revision):
                mutable_payload = await self._get_json(
                    session,
                    _model_info_url(
                        reference,
                        immutable_revision,
                        blobs=False,
                    ),
                )
                immutable_revision = self._response_identity(
                    mutable_payload,
                    reference.repository_id,
                )

            pinned_payload = await self._get_json(
                session,
                _model_info_url(
                    reference,
                    immutable_revision,
                    blobs=True,
                ),
            )
            return self._resolved_file(
                pinned_payload,
                reference,
                immutable_revision,
                expected_sha256,
            )
        finally:
            if owned_session is not None:
                await owned_session.close()
