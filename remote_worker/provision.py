"""Transactional manifest provisioning and native ComfyUI validation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hmac
import json
import math
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit

from cloud_run.artifacts import GIB, HEADROOM_BYTES
from cloud_run.manifest import (
    MANIFEST_SCHEMA_VERSION,
    PINNED_COMFYUI_CORE_VERSION,
    PINNED_COMFYUI_FRONTEND_VERSION,
    PROTOCOL_VERSION,
    ArtifactSpec,
    CustomNodeSpec,
    DependencyManifest,
    ManifestDelta,
    PythonWheelSpec,
    SourceSpec,
    validate_dependency,
)
from .transfers import (
    ArtifactIntegrityError,
    TransferError,
    TransferProgressError,
    wheel_artifact,
)


PROVISION_STALL_SECONDS = 600
MAX_REQUIRED_CLASS_TYPES = 100_000
MAX_MANIFEST_REQUEST_BYTES = 16 * 1024 * 1024
MAX_SOURCE_URL_BYTES = 8192
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_REQUEST_FIELDS = {
    "manifest",
    "manifest_digest",
    "required_class_types",
    "source_urls",
}


class ProvisionError(RuntimeError):
    """A sanitized worker provisioning failure."""


class WorkerIdentityError(ProvisionError):
    pass


class IncompatibleManifestError(ProvisionError):
    pass


class DiskReservationError(ProvisionError):
    pass


class UnapprovedRepairError(ProvisionError):
    pass


class ProvisionStalled(ProvisionError):
    pass


class UploadsRequired(ProvisionError):
    def __init__(self, artifact_ids):
        identifiers = tuple(sorted(set(artifact_ids)))
        if (
            not identifiers
            or not all(
                isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                for item in identifiers
            )
        ):
            raise ProvisionError("Provisioning upload state is invalid.")
        self.artifact_ids = identifiers
        super().__init__("Verified artifact uploads are required.")


@dataclass(frozen=True)
class ProvisionResult:
    transaction_id: str
    manifest_digest: str
    state: str
    planned_restarts: int
    repair_restarts: int
    missing_class_types: tuple[str, ...]
    missing_artifacts: tuple[str, ...]

    def __post_init__(self):
        if (
            not isinstance(self.transaction_id, str)
            or not _IDENTIFIER.fullmatch(self.transaction_id)
            or not isinstance(self.manifest_digest, str)
            or not _HEX_64.fullmatch(self.manifest_digest)
            or self.state
            not in {
                "applying",
                "awaiting_upload",
                "ready",
                "failed",
                "stalled",
            }
            or self.planned_restarts not in {0, 1}
            or isinstance(self.planned_restarts, bool)
            or self.repair_restarts not in {0, 1}
            or isinstance(self.repair_restarts, bool)
            or not isinstance(self.missing_class_types, tuple)
            or not isinstance(self.missing_artifacts, tuple)
            or not all(
                isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                for item in (
                    *self.missing_class_types,
                    *self.missing_artifacts,
                )
            )
        ):
            raise ProvisionError("Provisioning result is invalid.")

    def payload(self):
        return {
            "transaction_id": self.transaction_id,
            "manifest_digest": self.manifest_digest,
            "state": self.state,
            "planned_restarts": self.planned_restarts,
            "repair_restarts": self.repair_restarts,
            "missing_class_types": list(self.missing_class_types),
            "missing_artifacts": list(self.missing_artifacts),
        }


def _provision_error():
    return ProvisionError("Remote provisioning was rejected.")


def _https_origin(value):
    if not isinstance(value, str):
        raise _provision_error()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise _provision_error() from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise _provision_error()
    return (
        parsed.hostname.casefold(),
        port or 443,
    )


def _exact_record(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise _provision_error()
    return value


def _source_from_record(record):
    record = _exact_record(
        record,
        {"kind", "locator", "immutable_revision"},
    )
    return SourceSpec(
        kind=record["kind"],
        locator=record["locator"],
        immutable_revision=record["immutable_revision"],
    )


def _artifact_from_record(record):
    record = _exact_record(
        record,
        {
            "artifact_id",
            "kind",
            "logical_name",
            "destination",
            "size_bytes",
            "sha256",
            "source",
        },
    )
    return ArtifactSpec(
        artifact_id=record["artifact_id"],
        kind=record["kind"],
        logical_name=record["logical_name"],
        destination=record["destination"],
        size_bytes=record["size_bytes"],
        sha256=record["sha256"],
        source=_source_from_record(record["source"]),
    )


def _wheel_from_record(record):
    record = _exact_record(
        record,
        {"filename", "size_bytes", "sha256", "source"},
    )
    return PythonWheelSpec(
        filename=record["filename"],
        size_bytes=record["size_bytes"],
        sha256=record["sha256"],
        source=_source_from_record(record["source"]),
    )


def _custom_node_from_record(record):
    record = _exact_record(
        record,
        {
            "package_id",
            "repository_url",
            "revision",
            "archive",
            "wheels",
            "provided_class_types",
        },
    )
    if not isinstance(record["wheels"], list) or not isinstance(
        record["provided_class_types"],
        list,
    ):
        raise _provision_error()
    return CustomNodeSpec(
        package_id=record["package_id"],
        repository_url=record["repository_url"],
        revision=record["revision"],
        archive=_artifact_from_record(record["archive"]),
        wheels=tuple(
            _wheel_from_record(item) for item in record["wheels"]
        ),
        provided_class_types=tuple(record["provided_class_types"]),
    )


def dependency_manifest_from_record(record):
    record = _exact_record(
        record,
        {
            "schema_version",
            "protocol_version",
            "comfyui_core_version",
            "comfyui_frontend_version",
            "worker_version",
            "prompt_digest",
            "custom_nodes",
            "artifacts",
            "output_allowance_bytes",
            "disk_gb",
        },
    )
    if not isinstance(record["custom_nodes"], list) or not isinstance(
        record["artifacts"],
        list,
    ):
        raise _provision_error()
    manifest = DependencyManifest(
        schema_version=record["schema_version"],
        protocol_version=record["protocol_version"],
        comfyui_core_version=record["comfyui_core_version"],
        comfyui_frontend_version=record[
            "comfyui_frontend_version"
        ],
        worker_version=record["worker_version"],
        prompt_digest=record["prompt_digest"],
        custom_nodes=tuple(
            _custom_node_from_record(item)
            for item in record["custom_nodes"]
        ),
        artifacts=tuple(
            _artifact_from_record(item) for item in record["artifacts"]
        ),
        output_allowance_bytes=record["output_allowance_bytes"],
        disk_gb=record["disk_gb"],
    )
    try:
        validate_dependency(manifest)
    except (TypeError, ValueError):
        raise _provision_error() from None
    return manifest


def _reject_constant(_value):
    raise ValueError("Invalid JSON constant.")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def parse_manifest_request(body):
    if (
        not isinstance(body, bytes)
        or not 0 < len(body) <= MAX_MANIFEST_REQUEST_BYTES
    ):
        raise _provision_error()
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise _provision_error() from None
    payload = _exact_record(payload, _REQUEST_FIELDS)
    desired = dependency_manifest_from_record(payload["manifest"])
    manifest_digest = payload["manifest_digest"]
    if (
        not isinstance(manifest_digest, str)
        or not _HEX_64.fullmatch(manifest_digest)
        or not hmac.compare_digest(manifest_digest, desired.digest)
    ):
        raise _provision_error()
    required = payload["required_class_types"]
    if (
        not isinstance(required, list)
        or len(required) > MAX_REQUIRED_CLASS_TYPES
        or not all(
            isinstance(item, str) and _IDENTIFIER.fullmatch(item)
            for item in required
        )
        or len(set(required)) != len(required)
    ):
        raise _provision_error()
    source_urls = payload["source_urls"]
    if not isinstance(source_urls, dict):
        raise _provision_error()
    sanitized_urls = {}
    for artifact_id, url in source_urls.items():
        if (
            not isinstance(artifact_id, str)
            or not _IDENTIFIER.fullmatch(artifact_id)
            or not isinstance(url, str)
            or not 0 < len(url.encode("utf-8")) <= MAX_SOURCE_URL_BYTES
        ):
            raise _provision_error()
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError):
            raise _provision_error() from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise _provision_error()
        sanitized_urls[artifact_id] = url
    catalog = _manifest_transfer_catalog(desired)
    if any(
        artifact_id not in catalog
        or catalog[artifact_id].source.kind == "local-upload"
        for artifact_id in sanitized_urls
    ):
        raise _provision_error()
    return (
        desired,
        tuple(required),
        sanitized_urls,
    )


def _manifest_transfer_catalog(desired):
    try:
        validate_dependency(desired)
    except (TypeError, ValueError):
        raise _provision_error() from None
    candidates = [
        *desired.artifacts,
        *(node.archive for node in desired.custom_nodes),
        *(
            wheel_artifact(wheel)
            for node in desired.custom_nodes
            for wheel in node.wheels
        ),
    ]
    catalog = {}
    for artifact in candidates:
        prior = catalog.get(artifact.artifact_id)
        if prior is not None and prior != artifact:
            raise _provision_error()
        catalog[artifact.artifact_id] = artifact
    return catalog


def manifest_upload_artifacts(desired):
    catalog = _manifest_transfer_catalog(desired)
    return tuple(
        catalog[key]
        for key in sorted(catalog)
        if catalog[key].source.kind == "local-upload"
    )


class DiskReservation:
    def __init__(self, root, *, disk_usage=None):
        try:
            self.root = Path(root).resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("Provisioning disk root is unavailable.") from None
        if not self.root.is_dir():
            raise ValueError("Provisioning disk root is unavailable.")
        self.disk_usage = disk_usage or shutil.disk_usage

    def reserve(self, desired, delta):
        try:
            validate_dependency(desired)
        except (TypeError, ValueError):
            raise DiskReservationError(
                "Provisioning disk reservation failed."
            ) from None
        if not isinstance(delta, ManifestDelta):
            raise DiskReservationError(
                "Provisioning disk reservation failed."
            )
        transfer_bytes = sum(
            item.size_bytes for item in delta.artifacts
        )
        transfer_bytes += sum(
            node.archive.size_bytes for node in delta.custom_nodes
        )
        transfer_bytes += sum(
            wheel.size_bytes for wheel in delta.wheels
        )
        required_free = (
            transfer_bytes
            + desired.output_allowance_bytes
            + HEADROOM_BYTES
        )
        try:
            usage = self.disk_usage(self.root)
        except OSError:
            raise DiskReservationError(
                "Provisioning disk reservation failed."
            ) from None
        if (
            usage.total < desired.disk_gb * GIB
            or usage.free < required_free
        ):
            raise DiskReservationError(
                "Provisioning disk reservation failed."
            )


class WorkerArtifactProvider:
    def __init__(self, manager, client):
        if not callable(getattr(manager, "verify", None)) or not callable(
            getattr(client, "get", None)
        ):
            raise ValueError("Artifact provider boundary is invalid.")
        self.manager = manager
        self.client = client

    async def ensure_many(
        self,
        artifacts,
        *,
        source_urls,
        progress,
        force=False,
    ):
        artifacts = tuple(artifacts)
        if not isinstance(source_urls, Mapping):
            raise _provision_error()
        local_missing = []
        downloads = []
        download_urls = {}
        previous_progress = self.manager.progress
        self.manager.progress = (
            lambda artifact_id, offset: progress(
                "verified_bytes",
                offset,
            )
        )
        try:
            for artifact in artifacts:
                try:
                    validate_dependency(artifact)
                except (TypeError, ValueError):
                    raise _provision_error() from None
                if force:
                    self.manager.reset(artifact)
                existing = self.manager.verify(artifact)
                if existing is not None:
                    progress("verified_bytes", existing.size_bytes)
                    continue
                if artifact.source.kind == "local-upload":
                    local_missing.append(artifact.artifact_id)
                    continue
                source_url = source_urls.get(artifact.artifact_id)
                if artifact.source.kind in {"git", "r2"}:
                    if source_url is None:
                        raise _provision_error()
                    download_urls[artifact.artifact_id] = source_url
                elif source_url is not None:
                    download_urls[artifact.artifact_id] = source_url
                if (
                    source_url is not None
                    and artifact.source.kind != "r2"
                    and _https_origin(source_url)
                    != _https_origin(artifact.source.locator)
                ):
                    raise _provision_error()
                downloads.append(artifact)
            if local_missing:
                raise UploadsRequired(local_missing)
            if downloads:
                return await self.manager.download_many(
                    downloads,
                    client=self.client,
                    source_urls=download_urls,
                )
            return ()
        except TransferProgressError as error:
            raise error.original
        except TransferError:
            raise _provision_error() from None
        finally:
            self.manager.progress = previous_progress

    def validate(self, artifacts):
        missing = []
        for artifact in artifacts:
            try:
                result = self.manager.verify(artifact)
            except ArtifactIntegrityError:
                missing.append(artifact.artifact_id)
                continue
            except TransferError:
                raise _provision_error() from None
            if result is None:
                missing.append(artifact.artifact_id)
        return tuple(sorted(missing))


class _ProgressTracker:
    def __init__(self, clock):
        self.clock = clock
        self.last_progress_at = self._now()

    def _now(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ProvisionStalled("Provisioning progress clock failed.")
        return float(value)

    def check(self):
        if self._now() - self.last_progress_at > PROVISION_STALL_SECONDS:
            raise ProvisionStalled("Provisioning stalled.")

    def progress(self, _kind, _value=None):
        self.check()
        self.last_progress_at = self._now()


def _required_class_types(desired, values):
    try:
        requested = tuple(values)
    except TypeError:
        raise _provision_error() from None
    combined = set(requested)
    for node in desired.custom_nodes:
        combined.update(node.provided_class_types)
    if (
        len(combined) > MAX_REQUIRED_CLASS_TYPES
        or not all(
            isinstance(item, str) and _IDENTIFIER.fullmatch(item)
            for item in combined
        )
    ):
        raise _provision_error()
    return tuple(sorted(combined))


def _empty_installed(desired):
    return DependencyManifest(
        schema_version=desired.schema_version,
        protocol_version=desired.protocol_version,
        comfyui_core_version=desired.comfyui_core_version,
        comfyui_frontend_version=desired.comfyui_frontend_version,
        worker_version=desired.worker_version,
        prompt_digest="0" * 64,
        custom_nodes=(),
        artifacts=(),
        output_allowance_bytes=desired.output_allowance_bytes,
        disk_gb=desired.disk_gb,
    )


def _manifest_record(desired):
    return json.loads(desired.canonical_bytes().decode("utf-8"))


class Provisioner:
    def __init__(
        self,
        *,
        state_store,
        worker_version,
        comfy,
        artifacts,
        installer,
        disk,
        clock=None,
        stall_poll_interval=1,
    ):
        if (
            not isinstance(worker_version, str)
            or not _IDENTIFIER.fullmatch(worker_version)
        ):
            raise ValueError("Worker version is invalid.")
        self.state_store = state_store
        self.worker_version = worker_version
        self.comfy = comfy
        self.artifacts = artifacts
        self.installer = installer
        self.disk = disk
        self.clock = clock or time.monotonic
        if (
            isinstance(stall_poll_interval, bool)
            or not isinstance(stall_poll_interval, (int, float))
            or not math.isfinite(stall_poll_interval)
            or not 0 < stall_poll_interval <= 5
        ):
            raise ValueError("Provisioning stall poll interval is invalid.")
        self.stall_poll_interval = float(stall_poll_interval)
        for dependency, method in (
            (comfy, "ensure_running"),
            (comfy, "restart"),
            (comfy, "object_info"),
            (artifacts, "ensure_many"),
            (artifacts, "validate"),
            (installer, "install"),
            (installer, "install_wheels"),
            (disk, "reserve"),
        ):
            if not callable(getattr(dependency, method, None)):
                raise ValueError("Provisioning dependency is invalid.")
        self._lock = None
        self._lock_loop = None

    async def _await_progress(self, awaitable, tracker):
        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {task},
                    timeout=self.stall_poll_interval,
                )
                if task in done:
                    return task.result()
                tracker.check()
        except BaseException:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    def _provision_lock(self):
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _identity(self, desired):
        try:
            validate_dependency(desired)
        except (TypeError, ValueError):
            raise WorkerIdentityError(
                "Worker runtime identity does not match."
            ) from None
        if (
            desired.schema_version != MANIFEST_SCHEMA_VERSION
            or desired.protocol_version != PROTOCOL_VERSION
            or desired.comfyui_core_version
            != PINNED_COMFYUI_CORE_VERSION
            or desired.comfyui_frontend_version
            != PINNED_COMFYUI_FRONTEND_VERSION
            or desired.worker_version != self.worker_version
        ):
            raise WorkerIdentityError(
                "Worker runtime identity does not match."
            )

    def _prior_budget(self, desired):
        transaction_id = "provision-" + desired.digest
        record = self.state_store.load()["transactions"].get(
            transaction_id
        )
        if record is None:
            return 0, 0, False
        if (
            not isinstance(record, dict)
            or record.get("kind") != "provision"
            or record.get("manifest_digest") != desired.digest
            or isinstance(record.get("planned_restarts"), bool)
            or record.get("planned_restarts") not in {0, 1}
            or isinstance(record.get("repair_restarts"), bool)
            or record.get("repair_restarts") not in {0, 1}
            or not isinstance(record.get("repair_used"), bool)
            or (
                record.get("repair_restarts") == 1
                and record.get("repair_used") is not True
            )
        ):
            raise _provision_error()
        return (
            record["planned_restarts"],
            record["repair_restarts"],
            record["repair_used"],
        )

    def _installed_manifest(self, state, desired):
        installed = state.get("installed")
        if not installed:
            return _empty_installed(desired)
        if (
            not isinstance(installed, dict)
            or set(installed)
            != {
                "manifest_digest",
                "manifest",
                "ready_at",
                "readiness",
                "required_class_types",
            }
            or not isinstance(installed["manifest_digest"], str)
            or not isinstance(installed["manifest"], dict)
            or not isinstance(installed["readiness"], dict)
            or isinstance(installed["ready_at"], bool)
            or not isinstance(installed["ready_at"], (int, float))
            or not math.isfinite(installed["ready_at"])
            or not isinstance(
                installed["required_class_types"],
                list,
            )
            or installed["required_class_types"]
            != sorted(set(installed["required_class_types"]))
            or not all(
                isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                for item in installed["required_class_types"]
            )
        ):
            raise _provision_error()
        readiness = installed["readiness"]
        if (
            set(readiness)
            != {
                "protocol_version",
                "comfyui_core_version",
                "comfyui_frontend_version",
                "worker_version",
                "validated_class_types",
                "validated_artifacts",
                "completed_at",
            }
            or not isinstance(
                readiness["validated_class_types"],
                list,
            )
            or not isinstance(
                readiness["validated_artifacts"],
                list,
            )
            or isinstance(readiness["completed_at"], bool)
            or not isinstance(readiness["completed_at"], (int, float))
            or not math.isfinite(readiness["completed_at"])
        ):
            raise _provision_error()
        prior = dependency_manifest_from_record(installed["manifest"])
        if not hmac.compare_digest(
            installed["manifest_digest"],
            prior.digest,
        ) or (
            readiness["protocol_version"] != prior.protocol_version
            or readiness["comfyui_core_version"]
            != prior.comfyui_core_version
            or readiness["comfyui_frontend_version"]
            != prior.comfyui_frontend_version
            or readiness["worker_version"] != prior.worker_version
            or readiness["validated_class_types"]
            != installed["required_class_types"]
            or readiness["validated_artifacts"]
            != [item.artifact_id for item in prior.artifacts]
        ):
            raise _provision_error()
        return prior

    def _record(
        self,
        *,
        state_name,
        desired,
        required,
        planned_restarts,
        repair_restarts,
        repair_used,
        missing_classes=(),
        missing_artifacts=(),
        failure_code=None,
        tracker=None,
    ):
        transaction_id = "provision-" + desired.digest
        record = {
            "kind": "provision",
            "transaction_id": transaction_id,
            "manifest_digest": desired.digest,
            "manifest": (
                _manifest_record(desired)
                if state_name in {"applying", "awaiting_upload"}
                else None
            ),
            "required_class_types": list(required),
            "state": state_name,
            "planned_restarts": planned_restarts,
            "repair_restarts": repair_restarts,
            "repair_used": bool(repair_used),
            "missing_class_types": list(missing_classes),
            "missing_artifacts": list(missing_artifacts),
            "failure_code": failure_code,
            "updated_at": float(self.clock()),
            "last_progress_at": (
                tracker.last_progress_at if tracker is not None else None
            ),
        }
        self.state_store.record_transaction(transaction_id, record)
        return record

    def _ready(
        self,
        *,
        desired,
        required,
        planned_restarts,
        repair_restarts,
        repair_used,
        tracker,
    ):
        transaction_id = "provision-" + desired.digest
        record = {
            "kind": "provision",
            "transaction_id": transaction_id,
            "manifest_digest": desired.digest,
            "manifest": None,
            "required_class_types": list(required),
            "state": "ready",
            "planned_restarts": planned_restarts,
            "repair_restarts": repair_restarts,
            "repair_used": bool(repair_used),
            "missing_class_types": [],
            "missing_artifacts": [],
            "failure_code": None,
            "updated_at": float(self.clock()),
            "last_progress_at": tracker.last_progress_at,
        }
        installed = {
            "manifest_digest": desired.digest,
            "manifest": _manifest_record(desired),
            "ready_at": float(self.clock()),
            "readiness": {
                "protocol_version": desired.protocol_version,
                "comfyui_core_version": desired.comfyui_core_version,
                "comfyui_frontend_version": (
                    desired.comfyui_frontend_version
                ),
                "worker_version": desired.worker_version,
                "validated_class_types": list(required),
                "validated_artifacts": [
                    item.artifact_id
                    for item in sorted(
                        desired.artifacts,
                        key=lambda artifact: (
                            artifact.destination,
                            artifact.artifact_id,
                            artifact.sha256,
                        ),
                    )
                ],
                "completed_at": float(self.clock()),
            },
            "required_class_types": list(required),
        }
        self.state_store.record_transaction(
            transaction_id,
            record,
            installed=installed,
        )
        return ProvisionResult(
            transaction_id=transaction_id,
            manifest_digest=desired.digest,
            state="ready",
            planned_restarts=planned_restarts,
            repair_restarts=repair_restarts,
            missing_class_types=(),
            missing_artifacts=(),
        )

    async def _validate(self, desired, required, tracker):
        object_info = await self._await_progress(
            self.comfy.object_info(),
            tracker,
        )
        tracker.check()
        if not isinstance(object_info, dict):
            raise _provision_error()
        missing_classes = tuple(
            item for item in required if item not in object_info
        )
        missing_artifacts = tuple(
            sorted(self.artifacts.validate(desired.artifacts))
        )
        tracker.progress("validation")
        return missing_classes, missing_artifacts

    def _repair_plan(
        self,
        desired,
        missing_classes,
        missing_artifacts,
    ):
        class_to_node = {
            class_type: node
            for node in desired.custom_nodes
            for class_type in node.provided_class_types
        }
        if any(item not in class_to_node for item in missing_classes):
            raise UnapprovedRepairError(
                "Provisioning repair is not approved."
            )
        artifact_by_id = {
            item.artifact_id: item for item in desired.artifacts
        }
        if any(item not in artifact_by_id for item in missing_artifacts):
            raise UnapprovedRepairError(
                "Provisioning repair is not approved."
            )
        nodes = {
            class_to_node[item].package_id: class_to_node[item]
            for item in missing_classes
        }
        artifacts = tuple(
            artifact_by_id[item] for item in missing_artifacts
        )
        return (
            tuple(nodes[key] for key in sorted(nodes)),
            artifacts,
        )

    async def apply_manifest(
        self,
        desired,
        *,
        required_class_types=(),
        source_urls=None,
    ):
        async with self._provision_lock():
            self._identity(desired)
            required = _required_class_types(
                desired,
                required_class_types,
            )
            if source_urls is None:
                source_urls = {}
            elif isinstance(source_urls, Mapping):
                source_urls = dict(source_urls)
            else:
                raise _provision_error()
            tracker = _ProgressTracker(self.clock)
            (
                planned_restarts,
                repair_restarts,
                repair_used,
            ) = self._prior_budget(desired)
            current_missing_classes = ()
            current_missing_artifacts = ()
            self._record(
                state_name="applying",
                desired=desired,
                required=required,
                planned_restarts=planned_restarts,
                repair_restarts=repair_restarts,
                repair_used=repair_used,
                tracker=tracker,
            )
            try:
                state = self.state_store.load()
                installed = self._installed_manifest(state, desired)
                delta = desired.delta_from(installed)
                if not delta.compatible:
                    raise IncompatibleManifestError(
                        "Manifest requires a new session."
                    )
                self.disk.reserve(desired, delta)
                tracker.check()

                transfer_artifacts = [
                    *delta.artifacts,
                    *(node.archive for node in delta.custom_nodes),
                    *(wheel_artifact(wheel) for wheel in delta.wheels),
                ]
                deduplicated = {}
                for item in transfer_artifacts:
                    prior = deduplicated.get(item.artifact_id)
                    if prior is not None and prior != item:
                        raise _provision_error()
                    deduplicated[item.artifact_id] = item
                if deduplicated:
                    await self._await_progress(
                        self.artifacts.ensure_many(
                            tuple(
                                deduplicated[key]
                                for key in sorted(deduplicated)
                            ),
                            source_urls=source_urls,
                            progress=tracker.progress,
                            force=False,
                        ),
                        tracker,
                    )
                    tracker.check()

                new_node_wheels = {
                    (wheel.filename, wheel.sha256)
                    for node in delta.custom_nodes
                    for wheel in node.wheels
                }
                standalone_wheels = tuple(
                    wheel
                    for wheel in delta.wheels
                    if (wheel.filename, wheel.sha256)
                    not in new_node_wheels
                )
                for node in delta.custom_nodes:
                    await self._await_progress(
                        self.installer.install(node),
                        tracker,
                    )
                    tracker.check()
                    tracker.progress("install")
                if standalone_wheels:
                    await self._await_progress(
                        self.installer.install_wheels(
                            standalone_wheels
                        ),
                        tracker,
                    )
                    tracker.check()
                    tracker.progress("install")

                code_changed = bool(
                    delta.custom_nodes or standalone_wheels
                )
                if code_changed:
                    if planned_restarts == 0:
                        planned_restarts = 1
                        self._record(
                            state_name="applying",
                            desired=desired,
                            required=required,
                            planned_restarts=planned_restarts,
                            repair_restarts=repair_restarts,
                            repair_used=repair_used,
                            tracker=tracker,
                        )
                        await self._await_progress(
                            self.comfy.restart(),
                            tracker,
                        )
                    else:
                        await self._await_progress(
                            self.comfy.ensure_running(),
                            tracker,
                        )
                    tracker.check()
                    tracker.progress("health")
                else:
                    await self._await_progress(
                        self.comfy.ensure_running(),
                        tracker,
                    )
                    tracker.check()
                    tracker.progress("health")

                (
                    current_missing_classes,
                    current_missing_artifacts,
                ) = await self._validate(desired, required, tracker)
                missing_classes = current_missing_classes
                missing_artifacts = current_missing_artifacts
                if missing_classes or missing_artifacts:
                    if repair_used:
                        raise ProvisionError(
                            "Provisioning repair budget is exhausted."
                        )
                    repair_nodes, repair_artifacts = self._repair_plan(
                        desired,
                        missing_classes,
                        missing_artifacts,
                    )
                    repair_used = True
                    self._record(
                        state_name="applying",
                        desired=desired,
                        required=required,
                        planned_restarts=planned_restarts,
                        repair_restarts=repair_restarts,
                        repair_used=repair_used,
                        missing_classes=missing_classes,
                        missing_artifacts=missing_artifacts,
                        tracker=tracker,
                    )
                    if repair_artifacts:
                        await self._await_progress(
                            self.artifacts.ensure_many(
                                repair_artifacts,
                                source_urls=source_urls,
                                progress=tracker.progress,
                                force=True,
                            ),
                            tracker,
                        )
                        tracker.check()
                    repair_package_artifacts = {}
                    for node in repair_nodes:
                        package_artifacts = (
                            node.archive,
                            *(
                                wheel_artifact(wheel)
                                for wheel in node.wheels
                            ),
                        )
                        for item in package_artifacts:
                            prior = repair_package_artifacts.get(
                                item.artifact_id
                            )
                            if prior is not None and prior != item:
                                raise _provision_error()
                            repair_package_artifacts[
                                item.artifact_id
                            ] = item
                    if repair_package_artifacts:
                        await self._await_progress(
                            self.artifacts.ensure_many(
                                tuple(
                                    repair_package_artifacts[key]
                                    for key in sorted(
                                        repair_package_artifacts
                                    )
                                ),
                                source_urls=source_urls,
                                progress=tracker.progress,
                                force=False,
                            ),
                            tracker,
                        )
                        tracker.check()
                    for node in repair_nodes:
                        await self._await_progress(
                            self.installer.install(node),
                            tracker,
                        )
                        tracker.check()
                        tracker.progress("install")
                    if repair_nodes:
                        repair_restarts = 1
                        self._record(
                            state_name="applying",
                            desired=desired,
                            required=required,
                            planned_restarts=planned_restarts,
                            repair_restarts=repair_restarts,
                            repair_used=repair_used,
                            tracker=tracker,
                        )
                        await self._await_progress(
                            self.comfy.restart(),
                            tracker,
                        )
                        tracker.check()
                        tracker.progress("health")
                    (
                        current_missing_classes,
                        current_missing_artifacts,
                    ) = await self._validate(
                        desired,
                        required,
                        tracker,
                    )
                    missing_classes = current_missing_classes
                    missing_artifacts = current_missing_artifacts
                    if missing_classes or missing_artifacts:
                        raise ProvisionError(
                            "Provisioning validation failed."
                        )
                return self._ready(
                    desired=desired,
                    required=required,
                    planned_restarts=planned_restarts,
                    repair_restarts=repair_restarts,
                    repair_used=repair_used,
                    tracker=tracker,
                )
            except UploadsRequired as error:
                self._record(
                    state_name="awaiting_upload",
                    desired=desired,
                    required=required,
                    planned_restarts=planned_restarts,
                    repair_restarts=repair_restarts,
                    repair_used=repair_used,
                    missing_artifacts=error.artifact_ids,
                    tracker=tracker,
                )
                raise
            except ProvisionStalled:
                self._record(
                    state_name="stalled",
                    desired=desired,
                    required=required,
                    planned_restarts=planned_restarts,
                    repair_restarts=repair_restarts,
                    repair_used=repair_used,
                    missing_classes=current_missing_classes,
                    missing_artifacts=current_missing_artifacts,
                    failure_code="stalled",
                    tracker=tracker,
                )
                raise
            except (
                DiskReservationError,
                IncompatibleManifestError,
                UnapprovedRepairError,
                WorkerIdentityError,
                ProvisionError,
            ) as error:
                self._record(
                    state_name="failed",
                    desired=desired,
                    required=required,
                    planned_restarts=planned_restarts,
                    repair_restarts=repair_restarts,
                    repair_used=repair_used,
                    missing_classes=current_missing_classes,
                    missing_artifacts=current_missing_artifacts,
                    failure_code=error.__class__.__name__,
                    tracker=tracker,
                )
                raise
            except (asyncio.CancelledError, KeyboardInterrupt):
                self._record(
                    state_name="failed",
                    desired=desired,
                    required=required,
                    planned_restarts=planned_restarts,
                    repair_restarts=repair_restarts,
                    repair_used=repair_used,
                    missing_classes=current_missing_classes,
                    missing_artifacts=current_missing_artifacts,
                    failure_code="cancelled",
                    tracker=tracker,
                )
                raise
            except Exception:
                self._record(
                    state_name="failed",
                    desired=desired,
                    required=required,
                    planned_restarts=planned_restarts,
                    repair_restarts=repair_restarts,
                    repair_used=repair_used,
                    missing_classes=current_missing_classes,
                    missing_artifacts=current_missing_artifacts,
                    failure_code="internal",
                    tracker=tracker,
                )
                raise _provision_error() from None

    def transaction(self, transaction_id):
        if (
            not isinstance(transaction_id, str)
            or not _IDENTIFIER.fullmatch(transaction_id)
        ):
            return None
        record = self.state_store.load()["transactions"].get(
            transaction_id
        )
        if (
            not isinstance(record, dict)
            or record.get("kind") != "provision"
            or record.get("transaction_id") != transaction_id
        ):
            return None
        try:
            return ProvisionResult(
                transaction_id=record["transaction_id"],
                manifest_digest=record["manifest_digest"],
                state=record["state"],
                planned_restarts=record["planned_restarts"],
                repair_restarts=record["repair_restarts"],
                missing_class_types=tuple(
                    record["missing_class_types"]
                ),
                missing_artifacts=tuple(record["missing_artifacts"]),
            )
        except (KeyError, TypeError, ValueError):
            raise _provision_error() from None

    async def repair(self, transaction_id):
        result = self.transaction(transaction_id)
        if result is None:
            raise ProvisionError("Provisioning repair is unavailable.")
        record = self.state_store.load()["transactions"][transaction_id]
        if record.get("repair_used") is True or result.state != "failed":
            raise ProvisionError("Provisioning repair budget is exhausted.")
        raise ProvisionError("Provisioning repair is unavailable.")
