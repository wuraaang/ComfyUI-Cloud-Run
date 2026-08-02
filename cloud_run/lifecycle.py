"""Billing-safe cancellation, readiness, and recovery."""

from __future__ import annotations

import asyncio
import time

from .constants import COMFYUI_CONTAINER_PORT, DEFAULT_DISK_GB
from .models import AttemptState, SessionState
from .session_service import TerminalProvisioningError
from .worker_protocol import is_boundary_token
from .worker_release import WorkerRelease
from . import vast


DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_BOOT_DEADLINE_SECONDS = 15 * 60
BOUNDARY_AUTHENTICATION_GRACE_SECONDS = 60
BOUNDARY_AUTHENTICATION_ERROR = (
    "Remote worker boundary authentication failed."
)
_WATCHDOGS = {}
_SESSION_WATCHDOGS = {}


async def _default_readiness_probe(base_url):
    try:
        import aiohttp
    except ImportError:
        return False
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(base_url + "/system_stats") as response:
                return response.status == 200
    except Exception:
        return False


class CloudRunLifecycle:
    def __init__(
        self,
        settings_store,
        repository,
        *,
        provider,
        blacklist,
        clock=None,
        sleep=None,
        readiness_probe=None,
        poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
        boot_deadline_seconds=DEFAULT_BOOT_DEADLINE_SECONDS,
        disk_gb=DEFAULT_DISK_GB,
        release=None,
        session_repository=None,
        session_service=None,
    ):
        self.settings_store = settings_store
        self.repository = repository
        self.provider = provider
        self.blacklist = blacklist
        self.clock = clock or time.time
        self.sleep = sleep or asyncio.sleep
        self.readiness_probe = readiness_probe or _default_readiness_probe
        self.poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self.boot_deadline_seconds = max(1.0, float(boot_deadline_seconds))
        self.disk_gb = int(disk_gb)
        self.release = release if isinstance(release, WorkerRelease) else None
        self.session_repository = session_repository
        self.session_service = session_service

    def _attempt(self, attempt_id):
        attempt = self.repository.get(str(attempt_id))
        if attempt is None:
            from .service import AttemptNotFound

            raise AttemptNotFound("Cloud Run attempt was not found.")
        return attempt

    def _api_key(self):
        settings = self.settings_store.load()
        key = settings.get("api_key") if isinstance(settings, dict) else None
        if not key:
            raise vast.VastConfigurationError(
                "Vast API key is not configured."
            )
        return settings, key

    async def _inventory(self, api_key):
        try:
            inventory = await self.provider.list_instances(api_key)
        except Exception:
            return None
        if not isinstance(inventory, list):
            return None
        return [item for item in inventory if isinstance(item, dict)]

    @staticmethod
    def _by_label(inventory, label):
        if inventory is None:
            return []
        return sorted(
            [item for item in inventory if item.get("label") == label],
            key=lambda item: str(item.get("instance_id") or ""),
        )

    @staticmethod
    def _attempt_residual_ids(matches):
        return tuple(
            str(item.get("instance_id"))
            for item in matches
            if item.get("instance_id") is not None
        )

    def _fail_attempt_on_multiple_matches(self, attempt, matches):
        return self.repository.transition(
            attempt.attempt_id,
            AttemptState.FAILED,
            now=float(self.clock()),
            instance_id=None,
            residual_inventory=self._attempt_residual_ids(matches),
            ready_url=None,
            sanitized_error=(
                "Multiple managed Vast instances match this attempt."
            ),
        )

    async def _verified_absent(self, api_key, attempt, instance_id):
        inventory = await self._inventory(api_key)
        if inventory is None:
            return False
        return not any(
            str(item.get("instance_id")) == str(instance_id)
            or item.get("label") == attempt.label
            for item in inventory
        )

    async def _destroy_from_state(self, attempt, *, terminal_error):
        _settings, api_key = self._api_key()
        instance_id = attempt.instance_id
        if not instance_id:
            inventory = await self._inventory(api_key)
            if inventory is None:
                message = (
                    "Cancellation is pending while Vast inventory is "
                    "reconciled."
                    if attempt.state == AttemptState.CANCEL_REQUESTED
                    else "Destruction could not be verified because Vast "
                    "inventory is unavailable."
                )
                return self.repository.transition(
                    attempt.attempt_id,
                    attempt.state,
                    now=float(self.clock()),
                    sanitized_error=message,
                )
            matches = self._by_label(inventory, attempt.label)
            if len(matches) > 1:
                return self._fail_attempt_on_multiple_matches(
                    attempt,
                    matches,
                )
            if len(matches) == 1:
                instance_id = str(matches[0].get("instance_id") or "")
                attempt = self.repository.transition(
                    attempt.attempt_id,
                    attempt.state,
                    now=float(self.clock()),
                    instance_id=instance_id or None,
                )
        if not instance_id:
            if attempt.state == AttemptState.CANCEL_REQUESTED:
                return self.repository.transition(
                    attempt.attempt_id,
                    AttemptState.CANCEL_REQUESTED,
                    now=float(self.clock()),
                    sanitized_error=(
                        "Cancellation is pending while Vast inventory is "
                        "reconciled."
                    ),
                )
            if attempt.state == AttemptState.READY:
                attempt = self.repository.transition(
                    attempt.attempt_id,
                    AttemptState.DESTROYING,
                    now=float(self.clock()),
                    ready_url=None,
                )
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.CANCELLED,
                now=float(self.clock()),
                instance_id=None,
                ready_url=None,
                sanitized_error=None,
            )

        if attempt.state != AttemptState.DESTROYING:
            attempt = self.repository.transition(
                attempt.attempt_id,
                AttemptState.DESTROYING,
                now=float(self.clock()),
                instance_id=str(instance_id),
                ready_url=None,
            )
        destroyed = await self.provider.destroy_instance(api_key, instance_id)
        absent = (
            await self._verified_absent(api_key, attempt, instance_id)
            if destroyed
            else False
        )
        if not destroyed or not absent:
            message = (
                "The Vast instance is still present; destroy it in the Vast "
                "console immediately."
                if destroyed
                else terminal_error
            )
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                instance_id=str(instance_id),
                sanitized_error=message,
            )
        return self.repository.transition(
            attempt.attempt_id,
            AttemptState.CANCELLED,
            now=float(self.clock()),
            instance_id=None,
            residual_inventory=(),
            ready_url=None,
            sanitized_error=None,
        )

    async def cancel(self, attempt_id):
        attempt = self._attempt(attempt_id)
        if attempt.state == AttemptState.CANCELLED:
            return attempt
        if attempt.state in {
            AttemptState.IDLE,
            AttemptState.SEARCHING,
            AttemptState.OFFER_SELECTED,
            AttemptState.CONFIRMING,
        }:
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.CANCELLED,
                now=float(self.clock()),
                cancel_requested=True,
            )
        if attempt.state in {
            AttemptState.CREATING,
            AttemptState.STARTING,
            AttemptState.RETRYING,
        }:
            attempt = self.repository.transition(
                attempt.attempt_id,
                AttemptState.CANCEL_REQUESTED,
                now=float(self.clock()),
                cancel_requested=True,
            )
        if attempt.state == AttemptState.READY:
            return attempt
        return await self._destroy_from_state(
            attempt,
            terminal_error=(
                "Vast did not confirm destruction. Destroy the residual "
                "instance in the Vast console immediately."
            ),
        )

    async def destroy(self, attempt_id):
        attempt = self._attempt(attempt_id)
        if attempt.state == AttemptState.CANCELLED:
            return attempt
        if attempt.state in {
            AttemptState.IDLE,
            AttemptState.SEARCHING,
            AttemptState.OFFER_SELECTED,
            AttemptState.CONFIRMING,
            AttemptState.CREATING,
            AttemptState.STARTING,
            AttemptState.RETRYING,
        }:
            return await self.cancel(attempt.attempt_id)
        return await self._destroy_from_state(
            attempt,
            terminal_error=(
                "Vast did not confirm destruction. Destroy the residual "
                "instance in the Vast console immediately."
            ),
        )

    async def reconcile_once(self, attempt_id):
        attempt = self._attempt(attempt_id)
        if attempt.state in {
            AttemptState.CANCEL_REQUESTED,
            AttemptState.DESTROYING,
        }:
            return await self.cancel(attempt.attempt_id)
        if attempt.state == AttemptState.CREATING:
            _settings, api_key = self._api_key()
            inventory = await self._inventory(api_key)
            matches = self._by_label(inventory, attempt.label)
            if len(matches) > 1:
                return self._fail_attempt_on_multiple_matches(
                    attempt,
                    matches,
                )
            if len(matches) != 1:
                return attempt
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.STARTING,
                now=float(self.clock()),
                instance_id=str(matches[0]["instance_id"]),
                residual_inventory=(),
                sanitized_error=None,
            )
        if attempt.state != AttemptState.STARTING or not attempt.instance_id:
            return attempt

        _settings, api_key = self._api_key()
        try:
            instance = await self.provider.get_instance(
                api_key,
                attempt.instance_id,
            )
        except Exception:
            return attempt
        if not instance:
            return attempt
        status = str(instance.get("actual_status") or "").casefold()
        if status not in {"running", "ready"}:
            return attempt
        base_url = vast.derive_base_url(instance, COMFYUI_CONTAINER_PORT)
        if not base_url:
            return attempt
        if not await self.readiness_probe(base_url):
            return attempt
        return self.repository.transition(
            attempt.attempt_id,
            AttemptState.READY,
            now=float(self.clock()),
            ready_url=base_url,
            sanitized_error=None,
        )

    async def wait_until_ready(self, attempt_id):
        initial = self._attempt(attempt_id)
        deadline = initial.updated_at + self.boot_deadline_seconds
        while True:
            attempt = await self.reconcile_once(attempt_id)
            if attempt.state in {
                AttemptState.READY,
                AttemptState.CANCELLED,
                AttemptState.FAILED,
            }:
                return attempt
            if float(self.clock()) >= deadline:
                attempt = await self.handle_start_failure(
                    attempt_id,
                    failure_code="boot_timeout",
                )
                if (
                    attempt.state == AttemptState.STARTING
                    and attempt.retry_count == 1
                ):
                    deadline = float(self.clock()) + self.boot_deadline_seconds
                    continue
                return attempt
            await self.sleep(self.poll_interval_seconds)

    def schedule_watchdog(self, attempt_id):
        key = str(attempt_id)
        existing = _WATCHDOGS.get(key)
        if existing is not None and not existing.done():
            return existing
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(self.wait_until_ready(key))
        _WATCHDOGS[key] = task

        def finished(completed):
            if _WATCHDOGS.get(key) is completed:
                _WATCHDOGS.pop(key, None)

        task.add_done_callback(finished)
        return task

    async def wait_until_session_ready(self, session_id):
        initial = self._session(session_id)
        deadline = initial.updated_at + self.boot_deadline_seconds
        boundary_authentication_started_at = None
        while True:
            try:
                session = await self.reconcile_session_once(session_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except TerminalProvisioningError as error:
                diagnostic = str(error)
                now = float(self.clock())
                if diagnostic == BOUNDARY_AUTHENTICATION_ERROR:
                    if boundary_authentication_started_at is None:
                        boundary_authentication_started_at = now
                    grace_deadline = (
                        boundary_authentication_started_at
                        + BOUNDARY_AUTHENTICATION_GRACE_SECONDS
                    )
                    if now < min(deadline, grace_deadline):
                        await self.sleep(self.poll_interval_seconds)
                        continue
                session = self._session(session_id)
                session = self.session_repository.transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=float(self.clock()),
                    sanitized_error=diagnostic,
                )
                return await self.destroy_session(
                    session.session_id,
                    terminal_error=diagnostic,
                )
            except Exception:
                session = self._session(session_id)
            else:
                boundary_authentication_started_at = None
            if session.state in {
                SessionState.READY,
                SessionState.FAILED,
                SessionState.DESTROY_REQUESTED,
                SessionState.DESTROYING,
                SessionState.DESTROYED,
            }:
                return session
            if float(self.clock()) >= deadline:
                session = await self.handle_session_boot_failure(
                    session_id,
                    failure_code="boot_timeout",
                )
                if (
                    session.state == SessionState.BOOTSTRAPPING
                    and session.retry_count == 1
                ):
                    deadline = (
                        float(self.clock())
                        + self.boot_deadline_seconds
                    )
                    continue
                return session
            await self.sleep(self.poll_interval_seconds)

    def schedule_session_watchdog(self, session_id):
        key = str(session_id)
        existing = _SESSION_WATCHDOGS.get(key)
        if existing is not None and not existing.done():
            return existing
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(self.wait_until_session_ready(key))
        _SESSION_WATCHDOGS[key] = task

        def finished(completed):
            if _SESSION_WATCHDOGS.get(key) is completed:
                _SESSION_WATCHDOGS.pop(key, None)
            if not completed.cancelled():
                try:
                    completed.exception()
                except (asyncio.CancelledError, Exception):
                    pass

        task.add_done_callback(finished)
        return task

    async def _destroy_failed_attempt(self, attempt):
        _settings, api_key = self._api_key()
        instance_id = attempt.instance_id
        if not instance_id:
            inventory = await self._inventory(api_key)
            matches = self._by_label(inventory, attempt.label)
            if inventory is None:
                return attempt, False
            if len(matches) > 1:
                return (
                    self._fail_attempt_on_multiple_matches(
                        attempt,
                        matches,
                    ),
                    False,
                )
            if len(matches) == 1:
                instance_id = str(matches[0].get("instance_id") or "")
        if attempt.state != AttemptState.DESTROYING:
            attempt = self.repository.transition(
                attempt.attempt_id,
                AttemptState.DESTROYING,
                now=float(self.clock()),
                instance_id=instance_id or None,
                ready_url=None,
            )
        if instance_id:
            try:
                await self.provider.destroy_instance(api_key, instance_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                pass
        absent = await self._verified_absent(
            api_key,
            attempt,
            instance_id or "",
        )
        return attempt, absent

    async def handle_start_failure(self, attempt_id, *, failure_code):
        del failure_code
        diagnostic = "The managed instance did not become ready."
        attempt = self._attempt(attempt_id)
        if attempt.state != AttemptState.FAILED:
            attempt = self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                sanitized_error=diagnostic,
            )
        attempt, absent = await self._destroy_failed_attempt(attempt)
        if not absent:
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                sanitized_error=(
                    "The Vast instance is still present; destroy it in the "
                    "Vast console immediately."
                ),
            )
        return self.repository.transition(
            attempt.attempt_id,
            AttemptState.FAILED,
            now=float(self.clock()),
            instance_id=None,
            provider_token=None,
            residual_inventory=(),
            ready_url=None,
            sanitized_error=diagnostic,
        )

    async def recover(self):
        attempts = self.repository.list_recoverable()
        try:
            _settings, api_key = self._api_key()
            inventory = await self._inventory(api_key)
        except Exception:
            return attempts
        if inventory is None:
            return attempts

        managed = [
            item
            for item in inventory
            if str(item.get("label") or "").startswith("comfy-cloud-run-")
        ]
        recovered = []
        for attempt in attempts:
            matches = self._by_label(managed, attempt.label)
            known_id_matches = [
                item
                for item in inventory
                if attempt.instance_id is not None
                and str(item.get("instance_id"))
                == str(attempt.instance_id)
            ]
            current = attempt
            if attempt.state == AttemptState.RETRYING:
                diagnostic = (
                    "Automatic Vast replacement is disabled. "
                    "Start a new reviewed rental."
                )
                if len(matches) > 1:
                    current = self._fail_attempt_on_multiple_matches(
                        attempt,
                        matches,
                    )
                elif len(matches) == 1:
                    current = self.repository.transition(
                        attempt.attempt_id,
                        AttemptState.FAILED,
                        now=float(self.clock()),
                        instance_id=str(matches[0]["instance_id"]),
                        sanitized_error=diagnostic,
                    )
                    current, absent = await self._destroy_failed_attempt(
                        current
                    )
                    current = self.repository.transition(
                        current.attempt_id,
                        AttemptState.FAILED,
                        now=float(self.clock()),
                        instance_id=None if absent else current.instance_id,
                        provider_token=None if absent else current.provider_token,
                        residual_inventory=(
                            () if absent else current.residual_inventory
                        ),
                        ready_url=None,
                        sanitized_error=(
                            diagnostic
                            if absent
                            else (
                                "The Vast instance is still present; destroy "
                                "it in the Vast console immediately."
                            )
                        ),
                    )
                elif known_id_matches:
                    current = self.repository.transition(
                        attempt.attempt_id,
                        AttemptState.FAILED,
                        now=float(self.clock()),
                        instance_id=str(attempt.instance_id),
                        residual_inventory=tuple(
                            str(item.get("instance_id"))
                            for item in known_id_matches
                            if item.get("instance_id") is not None
                        ),
                        sanitized_error=(
                            "The managed Vast instance identity no longer "
                            "matches its attempt label. Destroy it in the "
                            "Vast console immediately."
                        ),
                    )
                else:
                    current = self.repository.transition(
                        attempt.attempt_id,
                        AttemptState.FAILED,
                        now=float(self.clock()),
                        instance_id=None,
                        provider_token=None,
                        residual_inventory=(),
                        ready_url=None,
                        sanitized_error=diagnostic,
                    )
            elif attempt.state == AttemptState.CREATING and len(matches) > 1:
                current = self._fail_attempt_on_multiple_matches(
                    attempt,
                    matches,
                )
            elif attempt.state == AttemptState.CREATING and matches:
                current = self.repository.transition(
                    attempt.attempt_id,
                    AttemptState.STARTING,
                    now=float(self.clock()),
                    instance_id=str(matches[0]["instance_id"]),
                    residual_inventory=(),
                    sanitized_error=None,
                )
            elif attempt.state == AttemptState.STARTING:
                if len(matches) > 1:
                    current = self._fail_attempt_on_multiple_matches(
                        attempt,
                        matches,
                    )
                    recovered.append(current)
                    continue
                if len(matches) == 1 and not attempt.instance_id:
                    current = self.repository.transition(
                        attempt.attempt_id,
                        AttemptState.STARTING,
                        now=float(self.clock()),
                        instance_id=str(matches[0]["instance_id"]),
                        residual_inventory=(),
                    )
                current = await self.reconcile_once(current.attempt_id)
            elif attempt.state in {
                AttemptState.CANCEL_REQUESTED,
                AttemptState.DESTROYING,
            }:
                if len(matches) == 1 and not attempt.instance_id:
                    current = self.repository.transition(
                        attempt.attempt_id,
                        attempt.state,
                        now=float(self.clock()),
                        instance_id=str(matches[0]["instance_id"]),
                        residual_inventory=(),
                    )
                current = await self.cancel(current.attempt_id)
            recovered.append(current)
        return recovered

    def _session(self, session_id):
        repository = self.session_repository
        if repository is None or not callable(
            getattr(repository, "get", None)
        ):
            raise RuntimeError("Cloud Run session storage is unavailable.")
        session = repository.get(str(session_id))
        if session is None:
            raise RuntimeError("Cloud Run session was not found.")
        return session

    @staticmethod
    def _session_matches(inventory, label):
        return sorted(
            [
                instance
                for instance in (inventory or [])
                if isinstance(instance, dict)
                and instance.get("label") == label
            ],
            key=lambda item: str(item.get("instance_id") or ""),
        )

    def _session_connection(self, session, instance):
        if self.release is None:
            return None
        status = str(instance.get("actual_status") or "").casefold()
        base_url = vast.derive_base_url(
            instance,
            self.release.worker_port,
        )
        if (
            status not in {"running", "ready"}
            or not base_url
            or not is_boundary_token(session.provider_token)
        ):
            return None
        return base_url

    def _finalize_session_destroyed(self, session, *, terminal_error=None):
        if session.state == SessionState.DESTROYED:
            return session
        if session.state not in {
            SessionState.PREFLIGHT,
            SessionState.OFFER_SELECTED,
            SessionState.DESTROY_REQUESTED,
            SessionState.DESTROYING,
        }:
            session = self.session_repository.transition(
                session.session_id,
                SessionState.DESTROY_REQUESTED,
                now=float(self.clock()),
                destroy_requested=True,
            )
        if session.state == SessionState.DESTROY_REQUESTED:
            session = self.session_repository.transition(
                session.session_id,
                SessionState.DESTROYING,
                now=float(self.clock()),
                destroy_requested=True,
            )
        return self.session_repository.transition(
            session.session_id,
            SessionState.DESTROYED,
            now=float(self.clock()),
            destroy_requested=True,
            instance_id=None,
            worker_base_url=None,
            provider_token=None,
            session_secret_hex=None,
            residual_inventory=(),
            sanitized_error=terminal_error,
            pending_deadline_at=None,
            pending_deadline_mode=None,
            pending_deadline_action=None,
        )

    async def _activate_session_instance(self, session, instance):
        base_url = self._session_connection(session, instance)
        if base_url is None:
            return session
        instance_id = str(instance.get("instance_id") or "")
        if not instance_id or instance.get("label") != session.label:
            return session
        session = self.session_repository.transition(
            session.session_id,
            session.state,
            now=float(self.clock()),
            instance_id=instance_id,
            worker_base_url=base_url,
            residual_inventory=(),
            sanitized_error=None,
        )
        service = self.session_service
        if session.state in {
            SessionState.BOOTSTRAPPING,
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
        }:
            bootstrap = getattr(service, "bootstrap_session", None)
            if not callable(bootstrap):
                return session
            return await bootstrap(session.session_id)
        if session.state in {
            SessionState.READY,
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }:
            recover = getattr(service, "recover_session", None)
            if callable(recover):
                return await recover(session.session_id)
        return session

    async def reconcile_session_once(self, session_id):
        session = self._session(session_id)
        if session.state == SessionState.DESTROYED:
            return session
        if session.state in {
            SessionState.DESTROY_REQUESTED,
            SessionState.DESTROYING,
        }:
            return await self.destroy_session(session.session_id)
        settings, api_key = self._api_key()
        del settings
        if session.state == SessionState.CREATING:
            inventory = await self._inventory(api_key)
            matches = self._session_matches(inventory, session.label)
            if len(matches) > 1:
                return self.session_repository.transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=float(self.clock()),
                    instance_id=None,
                    residual_inventory=tuple(
                        str(item.get("instance_id"))
                        for item in matches
                        if item.get("instance_id") is not None
                    ),
                    sanitized_error=(
                        "Multiple managed Vast instances match this session."
                    ),
                )
            if not matches:
                return session
            session = self.session_repository.transition(
                session.session_id,
                SessionState.BOOTSTRAPPING,
                now=float(self.clock()),
                instance_id=str(matches[0]["instance_id"]),
                sanitized_error=None,
            )
            return await self._activate_session_instance(
                session,
                matches[0],
            )
        if session.instance_id is None:
            return session
        try:
            instance = await self.provider.get_instance(
                api_key,
                session.instance_id,
            )
        except Exception:
            return session
        if not isinstance(instance, dict):
            return session
        return await self._activate_session_instance(session, instance)

    async def destroy_session(self, session_id, *, terminal_error=None):
        session = self._session(session_id)
        if session.state == SessionState.DESTROYED:
            return session
        if (
            session.state
            in {
                SessionState.PREFLIGHT,
                SessionState.OFFER_SELECTED,
            }
            and session.instance_id is None
            and not session.destroy_requested
        ):
            return self._finalize_session_destroyed(
                session,
                terminal_error=terminal_error,
            )
        original_state = session.state
        if session.state not in {
            SessionState.DESTROY_REQUESTED,
            SessionState.DESTROYING,
        }:
            session = self.session_repository.transition(
                session.session_id,
                SessionState.DESTROY_REQUESTED,
                now=float(self.clock()),
                destroy_requested=True,
                sanitized_error=terminal_error,
            )
        _settings, api_key = self._api_key()
        instance_id = session.instance_id
        if not instance_id:
            inventory = await self._inventory(api_key)
            if inventory is None:
                return self.session_repository.transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=float(self.clock()),
                    destroy_requested=True,
                    sanitized_error=(
                        "Destruction could not be verified because Vast "
                        "inventory is unavailable."
                    ),
                )
            matches = self._session_matches(inventory, session.label)
            if len(matches) > 1:
                return self.session_repository.transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=float(self.clock()),
                    instance_id=None,
                    residual_inventory=tuple(
                        str(item.get("instance_id"))
                        for item in matches
                        if item.get("instance_id") is not None
                    ),
                    destroy_requested=True,
                    sanitized_error=(
                        "Multiple managed Vast instances match this session."
                    ),
                )
            if matches and matches[0].get("instance_id") is not None:
                instance_id = str(matches[0]["instance_id"])
                session = self.session_repository.transition(
                    session.session_id,
                    session.state,
                    now=float(self.clock()),
                    instance_id=instance_id,
                    residual_inventory=(),
                )
            elif matches:
                return self.session_repository.transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=float(self.clock()),
                    destroy_requested=True,
                    residual_inventory=(),
                    sanitized_error=(
                        "A managed Vast instance remains but its identity "
                        "cannot be verified; use the Vast console immediately."
                    ),
                )
            elif original_state in {
                SessionState.CONFIRMING,
                SessionState.CREATING,
                SessionState.DESTROY_REQUESTED,
            } and (
                float(self.clock()) - session.updated_at
                < self.boot_deadline_seconds
            ):
                return session
            else:
                return self._finalize_session_destroyed(
                    session,
                    terminal_error=terminal_error,
                )
        if session.state != SessionState.DESTROYING:
            session = self.session_repository.transition(
                session.session_id,
                SessionState.DESTROYING,
                now=float(self.clock()),
                instance_id=str(instance_id),
                destroy_requested=True,
                worker_base_url=None,
            )
        try:
            await self.provider.destroy_instance(api_key, instance_id)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            pass
        inventory = await self._inventory(api_key)
        if inventory is None:
            return self.session_repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=float(self.clock()),
                instance_id=str(instance_id),
                residual_inventory=(str(instance_id),),
                destroy_requested=True,
                sanitized_error=(
                    "Destruction could not be verified because Vast "
                    "inventory is unavailable."
                ),
            )
        residual = self._session_matches(inventory, session.label)
        residual_ids = tuple(
            str(item.get("instance_id"))
            for item in residual
            if item.get("instance_id") is not None
        )
        still_present = any(
            str(item.get("instance_id")) == str(instance_id)
            or item.get("label") == session.label
            for item in inventory
        )
        if still_present:
            return self.session_repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=float(self.clock()),
                instance_id=str(instance_id),
                residual_inventory=(
                    residual_ids or (str(instance_id),)
                ),
                destroy_requested=True,
                sanitized_error=(
                    "The Vast instance is still present; destroy it in "
                    "the Vast console immediately."
                ),
            )
        return self._finalize_session_destroyed(
            session,
            terminal_error=terminal_error,
        )

    async def enforce_session_deadline(self, session_id):
        session = self._session(session_id)
        if (
            session.deadline_mode != "finite"
            or not isinstance(session.deadline_at, (int, float))
            or isinstance(session.deadline_at, bool)
            or float(self.clock()) < session.deadline_at
            or session.state == SessionState.DESTROYED
        ):
            return session
        prepare = getattr(
            self.session_service,
            "prepare_deadline_destroy",
            None,
        )
        if callable(prepare):
            try:
                await prepare(session.session_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                pass
        destroyed = await self.destroy_session(session.session_id)
        if destroyed.state == SessionState.DESTROYED:
            confirmed = getattr(
                self.session_service,
                "confirmed_deadline_destroy",
                None,
            )
            if callable(confirmed):
                confirmed(destroyed.session_id)
        return destroyed

    async def recover_sessions(self):
        repository = self.session_repository
        if repository is None or not callable(
            getattr(repository, "list_recoverable", None)
        ):
            return []
        sessions = repository.list_recoverable()
        try:
            _settings, api_key = self._api_key()
            inventory = await self._inventory(api_key)
        except Exception:
            return sessions
        if inventory is None:
            return sessions
        managed = [
            item
            for item in inventory
            if str(item.get("label") or "").startswith(
                "comfy-cloud-run-"
            )
        ]
        recovered = []
        for original in sessions:
            session = original
            matches = self._session_matches(managed, session.label)
            known_id_matches = [
                item
                for item in inventory
                if session.instance_id is not None
                and str(item.get("instance_id"))
                == str(session.instance_id)
            ]
            if session.state in {
                SessionState.DESTROY_REQUESTED,
                SessionState.DESTROYING,
            }:
                session = await self.destroy_session(
                    session.session_id
                )
                recovered.append(session)
                continue
            if len(matches) > 1:
                session = repository.transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=float(self.clock()),
                    instance_id=None,
                    residual_inventory=tuple(
                        str(item.get("instance_id"))
                        for item in matches
                        if item.get("instance_id") is not None
                    ),
                    sanitized_error=(
                        "Multiple managed Vast instances match this session."
                    ),
                )
                recovered.append(session)
                continue
            if not matches:
                if known_id_matches:
                    session = repository.transition(
                        session.session_id,
                        SessionState.FAILED,
                        now=float(self.clock()),
                        instance_id=str(session.instance_id),
                        residual_inventory=tuple(
                            str(item.get("instance_id"))
                            for item in known_id_matches
                            if item.get("instance_id") is not None
                        ),
                        sanitized_error=(
                            "The managed Vast instance identity no longer "
                            "matches its session label."
                        ),
                    )
                    recovered.append(session)
                    continue
                deadline_expired = (
                    session.deadline_mode == "finite"
                    and isinstance(session.deadline_at, (int, float))
                    and not isinstance(session.deadline_at, bool)
                    and float(self.clock()) >= session.deadline_at
                    and session.instance_id is not None
                    and session.state
                    not in {
                        SessionState.CONFIRMING,
                        SessionState.CREATING,
                    }
                )
                if deadline_expired:
                    session = self._finalize_session_destroyed(session)
                    confirmed = getattr(
                        self.session_service,
                        "confirmed_deadline_destroy",
                        None,
                    )
                    if callable(confirmed):
                        confirmed(session.session_id)
                    recovered.append(session)
                    continue
                if session.state in {
                    SessionState.CREATING,
                    SessionState.BOOTSTRAPPING,
                    SessionState.PROVISIONING,
                    SessionState.VALIDATING,
                    SessionState.READY,
                    SessionState.RUNNING,
                    SessionState.HARVESTING,
                    SessionState.REPAIRING,
                }:
                    session = repository.transition(
                        session.session_id,
                        SessionState.FAILED,
                        now=float(self.clock()),
                        instance_id=None,
                        worker_base_url=None,
                        residual_inventory=(),
                        sanitized_error=(
                            "The managed Vast instance is absent."
                        ),
                    )
                recovered.append(session)
                continue
            instance = matches[0]
            if session.state == SessionState.CREATING:
                session = repository.transition(
                    session.session_id,
                    SessionState.BOOTSTRAPPING,
                    now=float(self.clock()),
                    instance_id=str(instance["instance_id"]),
                    sanitized_error=None,
                )
            try:
                session = await self._activate_session_instance(
                    session,
                    instance,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except TerminalProvisioningError as error:
                diagnostic = str(error)
                session = repository.get(session.session_id)
                session = repository.transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=float(self.clock()),
                    sanitized_error=diagnostic,
                )
                session = await self.destroy_session(
                    session.session_id,
                    terminal_error=diagnostic,
                )
                if session.state == SessionState.DESTROYED:
                    confirmed = getattr(
                        self.session_service,
                        "confirmed_terminal_destroy",
                        None,
                    )
                    if callable(confirmed):
                        confirmed(session.session_id)
            except Exception:
                session = repository.get(session.session_id)
            recovered.append(session)
        return recovered

    async def handle_session_boot_failure(
        self,
        session_id,
        *,
        failure_code,
    ):
        del failure_code
        diagnostic = "The managed worker did not become ready."
        session = self._session(session_id)
        if session.state != SessionState.FAILED:
            session = self.session_repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=float(self.clock()),
                sanitized_error=diagnostic,
            )
        _settings, api_key = self._api_key()
        instance_id = session.instance_id
        session = self.session_repository.transition(
            session.session_id,
            SessionState.DESTROYING,
            now=float(self.clock()),
            instance_id=instance_id,
            worker_base_url=None,
        )
        if instance_id:
            try:
                await self.provider.destroy_instance(
                    api_key,
                    instance_id,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                pass
        inventory = await self._inventory(api_key)
        absent = inventory is not None and not any(
            str(item.get("instance_id")) == str(instance_id)
            or item.get("label") == session.label
            for item in inventory
        )
        if not absent:
            residual_ids = list(session.residual_inventory)
            if inventory is not None:
                residual_ids = [
                    str(item.get("instance_id"))
                    for item in inventory
                    if item.get("instance_id") is not None
                    and (
                        item.get("label") == session.label
                        or (
                            instance_id is not None
                            and str(item.get("instance_id"))
                            == str(instance_id)
                        )
                    )
                ]
            elif instance_id is not None:
                residual_ids.append(str(instance_id))
            return self.session_repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=float(self.clock()),
                residual_inventory=tuple(sorted(set(residual_ids))),
                sanitized_error=(
                    "The Vast instance is still present; destroy it in "
                    "the Vast console immediately."
                ),
            )
        return self.session_repository.transition(
            session.session_id,
            SessionState.FAILED,
            now=float(self.clock()),
            instance_id=None,
            worker_base_url=None,
            provider_token=None,
            session_secret_hex=None,
            residual_inventory=(),
            sanitized_error=diagnostic,
        )
