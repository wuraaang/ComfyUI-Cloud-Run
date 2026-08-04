"""Immutable evidence that one ComfyUI Vast Desktop is safe to expose."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .run_errors import sanitize_text


REQUIRED_READINESS_CHECKS = (
    "provider_instance_identity",
    "worker_authenticated",
    "worker_release_lock",
    "comfy_core_release",
    "comfy_frontend_release",
    "comfy_process_health",
    "required_object_info_classes",
    "model_and_input_digests",
    "safe_profile_digest",
    "approved_ui_asset_digests",
    "bootstrap_revision",
    "loopback_session_binding",
    "native_http_probe",
    "native_websocket_probe",
    "agent_panel_capabilities",
    "local_execution_unused",
)

_CHECK_SET = frozenset(REQUIRED_READINESS_CHECKS)
_STATUSES = frozenset({"passed", "failed", "not_required"})
READINESS_DIAGNOSTIC_CODES = frozenset(
    {
        "native_route_rejected",
        "native_http_status",
        "native_websocket_handshake",
        "profile_package_mismatch",
        "agent_bridge_unavailable",
        "legacy_readiness_failure",
    }
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def evidence_digest(value):
    """Digest one bounded canonical proof without retaining its raw value."""
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("Invalid readiness evidence.") from None
    if len(encoded) > 64 * 1024:
        raise ValueError("Invalid readiness evidence.")
    return hashlib.sha256(encoded).hexdigest()


def _identifier(value, name):
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"Invalid {name}.")
    return value


def _digest(value, name):
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise ValueError(f"Invalid {name}.")
    return value


def _timestamp(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"Invalid {name}.")
    return float(value)


def _relay_origin(value):
    if not isinstance(value, str):
        raise ValueError("Invalid readiness relay origin.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("Invalid readiness relay origin.") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or value != f"http://127.0.0.1:{port}"
    ):
        raise ValueError("Invalid readiness relay origin.")
    return value


def _safe_message(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 500
        or sanitize_text(value) != value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("Invalid readiness message.")
    return value


@dataclass(frozen=True, repr=False)
class ReadinessCheck:
    name: str
    status: str
    evidence_digest: str
    message: str
    diagnostic_code: str | None = None

    def __post_init__(self):
        _identifier(self.name, "readiness check name")
        if self.status not in _STATUSES or (
            self.status == "not_required"
            and self.name != "agent_panel_capabilities"
        ):
            raise ValueError("Invalid readiness check status.")
        _digest(self.evidence_digest, "readiness evidence digest")
        _safe_message(self.message)
        if (
            self.status == "failed"
            and self.diagnostic_code not in READINESS_DIAGNOSTIC_CODES
        ) or (
            self.status != "failed" and self.diagnostic_code is not None
        ):
            raise ValueError("Invalid readiness diagnostic code.")

    def to_record(self):
        return {
            "name": self.name,
            "status": self.status,
            "evidence_digest": self.evidence_digest,
            "message": self.message,
            "diagnostic_code": self.diagnostic_code,
        }

    @classmethod
    def from_record(cls, value):
        if not isinstance(value, dict) or set(value) != {
            "name",
            "status",
            "evidence_digest",
            "message",
            "diagnostic_code",
        }:
            raise ValueError("Invalid readiness check record.")
        return cls(**value)


@dataclass(frozen=True, repr=False)
class ReadinessReport:
    session_id: str
    instance_id: str
    worker_release_digest: str
    manifest_digest: str
    profile_revision: int
    relay_origin: str
    inventory_observed_at: float
    created_at: float
    attempt_number: int
    checks: tuple[ReadinessCheck, ...]
    report_digest: str

    def __post_init__(self):
        _identifier(self.session_id, "readiness session ID")
        _identifier(self.instance_id, "readiness instance ID")
        _digest(self.worker_release_digest, "worker release digest")
        _digest(self.manifest_digest, "readiness manifest digest")
        if (
            isinstance(self.profile_revision, bool)
            or not isinstance(self.profile_revision, int)
            or self.profile_revision < 0
        ):
            raise ValueError("Invalid readiness profile revision.")
        _relay_origin(self.relay_origin)
        observed = _timestamp(
            self.inventory_observed_at,
            "inventory observation timestamp",
        )
        created = _timestamp(self.created_at, "readiness creation timestamp")
        if observed > created:
            raise ValueError("Invalid readiness timestamps.")
        if (
            isinstance(self.attempt_number, bool)
            or not isinstance(self.attempt_number, int)
            or self.attempt_number < 1
        ):
            raise ValueError("Invalid readiness attempt number.")
        if not isinstance(self.checks, tuple) or not all(
            isinstance(item, ReadinessCheck) for item in self.checks
        ):
            raise ValueError("Invalid readiness checks.")
        names = tuple(item.name for item in self.checks)
        if names != REQUIRED_READINESS_CHECKS or set(names) != _CHECK_SET:
            raise ValueError("Readiness checks are incomplete.")
        _digest(self.report_digest, "readiness report digest")
        if self.report_digest != self._calculated_digest():
            raise ValueError("Readiness report digest does not match.")

    @property
    def ready(self):
        return all(
            item.status in {"passed", "not_required"}
            for item in self.checks
        )

    @property
    def desktop_ready(self):
        return self.ready

    def _identity_record(self):
        return {
            "session_id": self.session_id,
            "instance_id": self.instance_id,
            "worker_release_digest": self.worker_release_digest,
            "manifest_digest": self.manifest_digest,
            "profile_revision": self.profile_revision,
            "relay_origin": self.relay_origin,
            "inventory_observed_at": float(self.inventory_observed_at),
            "created_at": float(self.created_at),
            "attempt_number": self.attempt_number,
            "checks": [item.to_record() for item in self.checks],
        }

    def _calculated_digest(self):
        return hashlib.sha256(
            _canonical_json(self._identity_record()).encode("utf-8")
        ).hexdigest()

    @classmethod
    def create(cls, **values):
        required = {
            "session_id",
            "instance_id",
            "worker_release_digest",
            "manifest_digest",
            "profile_revision",
            "relay_origin",
            "inventory_observed_at",
            "created_at",
            "checks",
        }
        supplied = frozenset(values)
        allowed = {
            frozenset(required),
            frozenset(required | {"attempt_number"}),
        }
        if supplied not in allowed:
            raise ValueError("Invalid readiness report values.")
        checks = values.get("checks")
        if not isinstance(checks, tuple):
            raise ValueError("Invalid readiness checks.")
        by_name = {}
        for check in checks:
            if (
                not isinstance(check, ReadinessCheck)
                or check.name not in _CHECK_SET
                or check.name in by_name
            ):
                raise ValueError("Readiness checks are incomplete.")
            by_name[check.name] = check
        if set(by_name) != _CHECK_SET:
            raise ValueError("Readiness checks are incomplete.")
        ordered = tuple(by_name[name] for name in REQUIRED_READINESS_CHECKS)
        session_id = _identifier(values["session_id"], "readiness session ID")
        instance_id = _identifier(values["instance_id"], "readiness instance ID")
        worker_release_digest = _digest(
            values["worker_release_digest"],
            "worker release digest",
        )
        manifest_digest = _digest(
            values["manifest_digest"],
            "readiness manifest digest",
        )
        profile_revision = values["profile_revision"]
        if (
            isinstance(profile_revision, bool)
            or not isinstance(profile_revision, int)
            or profile_revision < 0
        ):
            raise ValueError("Invalid readiness profile revision.")
        relay_origin = _relay_origin(values["relay_origin"])
        inventory_observed_at = _timestamp(
            values["inventory_observed_at"],
            "inventory observation timestamp",
        )
        created_at = _timestamp(
            values["created_at"],
            "readiness creation timestamp",
        )
        if inventory_observed_at > created_at:
            raise ValueError("Invalid readiness timestamps.")
        attempt_number = values.get("attempt_number", 1)
        if (
            isinstance(attempt_number, bool)
            or not isinstance(attempt_number, int)
            or attempt_number < 1
        ):
            raise ValueError("Invalid readiness attempt number.")
        identity = {
            "session_id": session_id,
            "instance_id": instance_id,
            "worker_release_digest": worker_release_digest,
            "manifest_digest": manifest_digest,
            "profile_revision": profile_revision,
            "relay_origin": relay_origin,
            "inventory_observed_at": inventory_observed_at,
            "created_at": created_at,
            "attempt_number": attempt_number,
            "checks": [item.to_record() for item in ordered],
        }
        report_digest = hashlib.sha256(
            _canonical_json(identity).encode("utf-8")
        ).hexdigest()
        return cls(
            session_id=session_id,
            instance_id=instance_id,
            worker_release_digest=worker_release_digest,
            manifest_digest=manifest_digest,
            profile_revision=profile_revision,
            relay_origin=relay_origin,
            inventory_observed_at=inventory_observed_at,
            created_at=created_at,
            attempt_number=attempt_number,
            checks=ordered,
            report_digest=report_digest,
        )

    def to_record(self):
        return {
            **self._identity_record(),
            "report_digest": self.report_digest,
        }

    @classmethod
    def from_record(cls, value):
        fields = {
            "session_id",
            "instance_id",
            "worker_release_digest",
            "manifest_digest",
            "profile_revision",
            "relay_origin",
            "inventory_observed_at",
            "created_at",
            "attempt_number",
            "checks",
            "report_digest",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or not isinstance(value["checks"], list)
        ):
            raise ValueError("Invalid readiness report record.")
        normalized = dict(value)
        normalized["checks"] = tuple(
            ReadinessCheck.from_record(item) for item in value["checks"]
        )
        return cls(**normalized)

    def public_payload(self):
        return {
            "report_digest": self.report_digest,
            "session_id": self.session_id,
            "instance_id": self.instance_id,
            "manifest_digest": self.manifest_digest,
            "profile_revision": self.profile_revision,
            "relay_origin": self.relay_origin,
            "inventory_observed_at": self.inventory_observed_at,
            "created_at": self.created_at,
            "attempt_number": self.attempt_number,
            "desktop_ready": self.desktop_ready,
            "checks": [
                {
                    "name": item.name,
                    "status": item.status,
                    "diagnostic_code": item.diagnostic_code,
                    "message": item.message,
                }
                for item in self.checks
            ],
        }


class ReadinessValidator:
    """Turn one fixed-boundary probe into a complete immutable report."""

    def __init__(
        self,
        *,
        probe,
        worker_release_digest,
        relay_origin,
        clock=None,
    ):
        if not callable(probe):
            raise ValueError("Readiness probe is unavailable.")
        self.probe = probe
        self.worker_release_digest = _digest(
            worker_release_digest,
            "worker release digest",
        )
        if callable(relay_origin):
            self._relay_origin_source = relay_origin
        else:
            fixed_origin = _relay_origin(relay_origin)
            self._relay_origin_source = lambda: fixed_origin
        self.clock = clock or time.time

    @property
    def relay_origin(self):
        try:
            value = self._relay_origin_source()
        except Exception:
            raise ValueError("Readiness relay origin is unavailable.") from None
        return _relay_origin(value)

    def identity(self, session, manifest, profile):
        session_id = _identifier(
            getattr(session, "session_id", None),
            "readiness session ID",
        )
        instance_id = _identifier(
            getattr(session, "instance_id", None),
            "readiness instance ID",
        )
        manifest_digest = _digest(
            getattr(manifest, "digest", None),
            "readiness manifest digest",
        )
        profile_revision = 0 if profile is None else getattr(
            profile,
            "revision",
            None,
        )
        if (
            isinstance(profile_revision, bool)
            or not isinstance(profile_revision, int)
            or profile_revision < 0
        ):
            raise ValueError("Invalid readiness profile revision.")
        return {
            "session_id": session_id,
            "instance_id": instance_id,
            "worker_release_digest": self.worker_release_digest,
            "manifest_digest": manifest_digest,
            "profile_revision": profile_revision,
            "relay_origin": self.relay_origin,
        }

    async def validate(self, session, manifest, profile, *, attempt_number=1):
        identity = self.identity(session, manifest, profile)
        observed_at = _timestamp(
            self.clock(),
            "inventory observation timestamp",
        )
        checks = self.probe(session, manifest, profile)
        if inspect.isawaitable(checks):
            checks = await checks
        if not isinstance(checks, (tuple, list)):
            raise ValueError("Readiness probe result was rejected.")
        by_name = {}
        for item in checks:
            if (
                not isinstance(item, ReadinessCheck)
                or item.name not in _CHECK_SET
                or item.name in by_name
            ):
                raise ValueError("Readiness probe result was rejected.")
            by_name[item.name] = item
        for name in REQUIRED_READINESS_CHECKS:
            if name not in by_name:
                by_name[name] = ReadinessCheck(
                    name=name,
                    status="failed",
                    evidence_digest=evidence_digest(
                        {"check": name, "status": "missing"}
                    ),
                    message="Required readiness proof is unavailable.",
                    diagnostic_code="profile_package_mismatch",
                )
        created_at = _timestamp(self.clock(), "readiness creation timestamp")
        if created_at < observed_at:
            created_at = observed_at
        return ReadinessReport.create(
            **identity,
            inventory_observed_at=observed_at,
            created_at=created_at,
            attempt_number=attempt_number,
            checks=tuple(by_name[name] for name in REQUIRED_READINESS_CHECKS),
        )


class LocalExecutionGuard:
    """Counts only execution attempts made by the Cloud Vast local path."""

    def __init__(self):
        self._counts = {}

    def observe(self, session_id):
        identifier = _identifier(session_id, "local execution session ID")
        self._counts[identifier] = self._counts.get(identifier, 0) + 1

    def count(self, session_id):
        identifier = _identifier(session_id, "local execution session ID")
        return self._counts.get(identifier, 0)


class ControllerReadinessProbe:
    """Collect fixed controller/worker/relay proofs without side effects."""

    def __init__(
        self,
        *,
        worker_factory,
        inventory_probe,
        desktop_relay,
        release,
        required_class_types,
        local_execution_counter,
    ):
        callables = (
            worker_factory,
            inventory_probe,
            required_class_types,
            local_execution_counter,
        )
        if not all(callable(item) for item in callables) or not callable(
            getattr(desktop_relay, "probe_readiness", None)
        ):
            raise ValueError("Controller readiness probe is unavailable.")
        for name in (
            "worker_archive_sha256",
            "worker_commit",
            "protocol_version",
            "comfyui_core_version",
            "comfyui_frontend_version",
        ):
            if not isinstance(getattr(release, name, None), str):
                raise ValueError("Controller readiness release is invalid.")
        _digest(release.worker_archive_sha256, "worker release digest")
        self.worker_factory = worker_factory
        self.inventory_probe = inventory_probe
        self.desktop_relay = desktop_relay
        self.release = release
        self.required_class_types = required_class_types
        self.local_execution_counter = local_execution_counter

    @staticmethod
    def _check(name, passed, proof, *, not_required=False):
        status = "not_required" if not_required else (
            "passed" if passed else "failed"
        )
        label = name.replace("_", " ").capitalize()
        return ReadinessCheck(
            name=name,
            status=status,
            evidence_digest=evidence_digest(proof),
            message=(
                label + " passed."
                if passed or not_required
                else label + " failed."
            ),
            diagnostic_code=(
                None
                if passed or not_required
                else "profile_package_mismatch"
            ),
        )

    @staticmethod
    def _worker_readiness(value):
        fields = {
            "protocol_version",
            "comfyui_core_version",
            "comfyui_frontend_version",
            "worker_version",
            "validated_class_types",
            "validated_artifacts",
            "profile_revision",
            "profile_digest",
            "bootstrap_digest",
            "ui_package_digests",
            "comfy_process_healthy",
            "completed_at",
        }
        if not isinstance(value, dict) or set(value) != fields:
            return None
        classes = value.get("validated_class_types")
        artifacts = value.get("validated_artifacts")
        completed = value.get("completed_at")
        ui = value.get("ui_package_digests")
        if (
            not isinstance(classes, list)
            or classes != sorted(set(classes))
            or not isinstance(artifacts, list)
            or len(artifacts) != len(set(artifacts))
            or not all(
                isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                for item in (*classes, *artifacts)
            )
            or not isinstance(ui, dict)
            or any(
                not isinstance(key, str)
                or _IDENTIFIER.fullmatch(key) is None
                or not isinstance(digest, str)
                or _HEX_64.fullmatch(digest) is None
                for key, digest in ui.items()
            )
            or isinstance(completed, bool)
            or not isinstance(completed, (int, float))
            or not math.isfinite(completed)
            or completed < 0
            or value.get("comfy_process_healthy") is not True
        ):
            return None
        return dict(value)

    async def __call__(self, session, manifest, profile):
        checks = {}

        try:
            observation = self.inventory_probe(session)
            if inspect.isawaitable(observation):
                observation = await observation
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            observation = None
        provider_ok = bool(
            isinstance(observation, dict)
            and str(observation.get("instance_id") or "")
            == str(getattr(session, "instance_id", ""))
            and observation.get("label") == getattr(session, "label", None)
            and str(observation.get("actual_status") or "").casefold()
            in {"running", "ready"}
        )
        checks["provider_instance_identity"] = self._check(
            "provider_instance_identity",
            provider_ok,
            {
                "instance_id": getattr(session, "instance_id", None),
                "label": getattr(session, "label", None),
                "matched": provider_ok,
            },
        )

        worker = None
        health = None
        transaction = None
        try:
            worker = self.worker_factory(session)
            health = await worker.health()
            transaction = await worker.transaction(
                "provision-" + manifest.digest
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            health = None
            transaction = None
        authenticated = bool(
            isinstance(health, dict)
            and health.get("claimed") is True
            and health.get("protocol_version") == manifest.protocol_version
        )
        checks["worker_authenticated"] = self._check(
            "worker_authenticated",
            authenticated,
            {
                "claimed": authenticated,
                "protocol_version": manifest.protocol_version,
            },
        )
        worker_readiness = self._worker_readiness(
            transaction.get("readiness")
            if isinstance(transaction, dict)
            and transaction.get("state") == "ready"
            and transaction.get("manifest_digest") == manifest.digest
            else None
        )
        proof = worker_readiness or {}
        release_ok = bool(
            worker_readiness is not None
            and manifest.worker_version == self.release.worker_commit
            and proof.get("worker_version") == self.release.worker_commit
            and manifest.protocol_version == self.release.protocol_version
            and proof.get("protocol_version") == self.release.protocol_version
        )
        checks["worker_release_lock"] = self._check(
            "worker_release_lock",
            release_ok,
            {
                "archive_sha256": self.release.worker_archive_sha256,
                "worker_commit": self.release.worker_commit,
                "matched": release_ok,
            },
        )
        core_ok = bool(
            worker_readiness is not None
            and manifest.comfyui_core_version
            == self.release.comfyui_core_version
            == proof.get("comfyui_core_version")
        )
        checks["comfy_core_release"] = self._check(
            "comfy_core_release",
            core_ok,
            {"version": manifest.comfyui_core_version, "matched": core_ok},
        )
        frontend_ok = bool(
            worker_readiness is not None
            and manifest.comfyui_frontend_version
            == self.release.comfyui_frontend_version
            == proof.get("comfyui_frontend_version")
        )
        checks["comfy_frontend_release"] = self._check(
            "comfy_frontend_release",
            frontend_ok,
            {
                "version": manifest.comfyui_frontend_version,
                "matched": frontend_ok,
            },
        )
        process_ok = bool(
            worker_readiness is not None
            and proof.get("comfy_process_healthy") is True
        )
        checks["comfy_process_health"] = self._check(
            "comfy_process_health",
            process_ok,
            {"healthy": process_ok},
        )

        try:
            expected_classes = tuple(self.required_class_types(manifest))
        except Exception:
            expected_classes = ()
        classes_ok = bool(
            expected_classes
            and tuple(proof.get("validated_class_types", ()))
            == tuple(sorted(set(expected_classes)))
        )
        checks["required_object_info_classes"] = self._check(
            "required_object_info_classes",
            classes_ok,
            {
                "required": list(sorted(set(expected_classes))),
                "matched": classes_ok,
            },
        )
        required_artifacts = tuple(
            item.artifact_id
            for item in sorted(
                manifest.artifacts,
                key=lambda item: (
                    item.destination,
                    item.artifact_id,
                    item.sha256,
                ),
            )
        )
        validated_artifacts = tuple(proof.get("validated_artifacts", ()))
        artifact_ok = bool(
            worker_readiness is not None
            and all(item in validated_artifacts for item in required_artifacts)
        )
        checks["model_and_input_digests"] = self._check(
            "model_and_input_digests",
            artifact_ok,
            {
                "artifacts": [
                    {
                        "artifact_id": item.artifact_id,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                    }
                    for item in manifest.artifacts
                ],
                "matched": artifact_ok,
            },
        )
        profile_ok = bool(
            worker_readiness is not None
            and proof.get("profile_revision")
            == (profile.revision if profile is not None else None)
            and proof.get("profile_digest")
            == (profile.archive.sha256 if profile is not None else None)
        )
        checks["safe_profile_digest"] = self._check(
            "safe_profile_digest",
            profile_ok,
            {
                "profile_revision": (
                    profile.revision if profile is not None else None
                ),
                "profile_digest": (
                    profile.archive.sha256 if profile is not None else None
                ),
                "matched": profile_ok,
            },
        )
        expected_ui = {
            item.package_id: item.web_sha256
            for item in sorted(
                manifest.ui_packages,
                key=lambda item: item.package_id,
            )
        }
        ui_ok = bool(
            worker_readiness is not None
            and proof.get("ui_package_digests") == expected_ui
        )
        checks["approved_ui_asset_digests"] = self._check(
            "approved_ui_asset_digests",
            ui_ok,
            {"ui_package_digests": expected_ui, "matched": ui_ok},
        )
        bootstrap_ok = bool(
            worker_readiness is not None
            and proof.get("bootstrap_digest")
            == (profile.bootstrap_digest if profile is not None else None)
        )
        checks["bootstrap_revision"] = self._check(
            "bootstrap_revision",
            bootstrap_ok,
            {
                "bootstrap_digest": (
                    profile.bootstrap_digest if profile is not None else None
                ),
                "matched": bootstrap_ok,
            },
        )

        agent_required = any(
            item.package_id == "comfyui-agent-panel"
            for item in manifest.ui_packages
        )
        try:
            relay_checks = await self.desktop_relay.probe_readiness(
                session.session_id,
                worker,
                profile.revision if profile is not None else 0,
                agent_required=agent_required,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            relay_checks = ()
        for item in relay_checks:
            if (
                isinstance(item, ReadinessCheck)
                and item.name
                in {
                    "loopback_session_binding",
                    "native_http_probe",
                    "native_websocket_probe",
                    "agent_panel_capabilities",
                }
                and item.name not in checks
            ):
                checks[item.name] = item

        try:
            local_count = self.local_execution_counter(session.session_id)
        except Exception:
            local_count = None
        local_ok = bool(
            not isinstance(local_count, bool)
            and isinstance(local_count, int)
            and local_count == 0
        )
        checks["local_execution_unused"] = self._check(
            "local_execution_unused",
            local_ok,
            {"cloud_vast_local_execution_count": local_count},
        )
        return tuple(
            checks[name]
            for name in REQUIRED_READINESS_CHECKS
            if name in checks
        )
