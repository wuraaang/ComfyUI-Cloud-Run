"""Read-only, origin-confined Comfy Registry lookup client."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.parse import quote

from .manifest import ManifestValidationError, normalize_github_repository


REGISTRY_ORIGIN = "https://api.comfy.org"
MAX_REGISTRY_RESPONSE_BYTES = 2 * 1024 * 1024
REGISTRY_TIMEOUT_SECONDS = 30.0
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_PACKAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistryCandidate:
    package_id: str
    repository_url: str
    revision: str
    version: str


class RegistryClient:
    def __init__(
        self,
        *,
        session=None,
        timeout=REGISTRY_TIMEOUT_SECONDS,
        max_response_bytes=MAX_REGISTRY_RESPONSE_BYTES,
    ):
        self.session = session
        self.timeout = float(timeout)
        self.max_response_bytes = int(max_response_bytes)
        if not 0 < self.timeout <= REGISTRY_TIMEOUT_SECONDS:
            raise ValueError("Registry timeout is outside safe bounds.")
        if not 1 <= self.max_response_bytes <= MAX_REGISTRY_RESPONSE_BYTES:
            raise ValueError("Registry response bound is invalid.")

    async def _get_json(self, url, *, not_found_none=False):
        if not url.startswith(REGISTRY_ORIGIN + "/"):
            raise RegistryError("Comfy Registry request origin is invalid.")
        owned_session = None
        session = self.session
        if session is None:
            try:
                import aiohttp
            except ImportError:
                raise RegistryError(
                    "Comfy Registry client is unavailable."
                ) from None
            owned_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
            session = owned_session
        response = None
        try:
            response = await session.get(
                url,
                allow_redirects=False,
                timeout=self.timeout,
            )
            status = int(response.status)
            if status == 404 and not_found_none:
                return None
            if status != 200:
                raise RegistryError("Comfy Registry lookup failed.")
            body = await response.read()
            if not isinstance(body, (bytes, bytearray)):
                raise RegistryError("Comfy Registry response is invalid.")
            if len(body) > self.max_response_bytes:
                raise RegistryError("Comfy Registry response is too large.")
            try:
                return json.loads(bytes(body).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise RegistryError("Comfy Registry response is invalid.") from None
        except RegistryError:
            raise
        except Exception:
            raise RegistryError("Comfy Registry lookup failed.") from None
        finally:
            if response is not None:
                release = getattr(response, "release", None)
                if callable(release):
                    release()
            if owned_session is not None:
                await owned_session.close()

    async def infer_package(self, class_type):
        identifier = str(class_type or "")
        if (
            not identifier
            or len(identifier) > 256
            or any(ord(character) < 32 for character in identifier)
        ):
            raise RegistryError("Comfy Registry node identity is invalid.")
        node_url = (
            REGISTRY_ORIGIN
            + "/comfy-nodes/"
            + quote(identifier, safe="")
            + "/node"
        )
        node = await self._get_json(node_url, not_found_none=True)
        if node is None:
            return None
        if not isinstance(node, dict):
            raise RegistryError("Comfy Registry node response is invalid.")
        package_id = node.get("id")
        repository = node.get("repository")
        if (
            not isinstance(package_id, str)
            or not _PACKAGE_ID.fullmatch(package_id)
            or not isinstance(repository, str)
        ):
            raise RegistryError("Comfy Registry node response is invalid.")
        try:
            repository = normalize_github_repository(repository)
        except ManifestValidationError:
            raise RegistryError(
                "Comfy Registry repository is not approved."
            ) from None

        versions_url = (
            REGISTRY_ORIGIN
            + "/nodes/"
            + quote(package_id, safe="")
            + "/versions"
        )
        versions = await self._get_json(versions_url, not_found_none=True)
        if versions is None:
            return None
        if not isinstance(versions, list):
            raise RegistryError("Comfy Registry versions response is invalid.")
        for version in versions:
            if not isinstance(version, dict):
                continue
            revision = version.get("git_commit")
            version_name = version.get("version")
            if (
                version.get("status") == "NodeVersionStatusActive"
                and isinstance(revision, str)
                and _HEX_40.fullmatch(revision)
                and isinstance(version_name, str)
                and 0 < len(version_name) <= 100
            ):
                return RegistryCandidate(
                    package_id=package_id,
                    repository_url=repository,
                    revision=revision,
                    version=version_name,
                )
        return None
