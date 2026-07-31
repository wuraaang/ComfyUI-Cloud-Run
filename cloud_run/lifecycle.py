"""Billing-safe cancellation, readiness, recovery, and one replacement."""

from __future__ import annotations

import asyncio
import time

from .constants import COMFYUI_CONTAINER_PORT, DEFAULT_DISK_GB
from .models import AttemptState, OfferQuote
from .offers import (
    apply_offer_policy,
    select_best_offer,
)
from .worker_release import WorkerRelease
from . import vast


DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_BOOT_DEADLINE_SECONDS = 15 * 60
RETRYABLE_START_FAILURES = {
    "boot_timeout",
    "healthcheck_failure",
    "transport_failure",
}
_WATCHDOGS = {}


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
            if matches:
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
            if not matches:
                return attempt
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.STARTING,
                now=float(self.clock()),
                instance_id=str(matches[0]["instance_id"]),
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

    async def _destroy_for_replacement(self, attempt):
        _settings, api_key = self._api_key()
        instance_id = attempt.instance_id
        if not instance_id:
            inventory = await self._inventory(api_key)
            matches = self._by_label(inventory, attempt.label)
            if inventory is None:
                return attempt, False
            if matches:
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
            destroyed = await self.provider.destroy_instance(
                api_key,
                instance_id,
            )
            if not destroyed:
                return attempt, False
        absent = await self._verified_absent(
            api_key,
            attempt,
            instance_id or "",
        )
        return attempt, absent

    async def handle_start_failure(self, attempt_id, *, failure_code):
        attempt = self._attempt(attempt_id)
        if attempt.state != AttemptState.FAILED:
            attempt = self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                sanitized_error="The managed instance did not become ready.",
            )
        if failure_code not in RETRYABLE_START_FAILURES:
            return attempt

        attempt, absent = await self._destroy_for_replacement(attempt)
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
        if attempt.retry_count >= 1:
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                instance_id=None,
                sanitized_error=(
                    "The single automatic replacement was exhausted."
                ),
            )

        if self.blacklist is not None:
            self.blacklist.add(
                attempt.quote.to_record(),
                reason=failure_code,
                now=float(self.clock()),
            )
        attempt = self.repository.transition(
            attempt.attempt_id,
            AttemptState.RETRYING,
            now=float(self.clock()),
            retry_count=1,
            instance_id=None,
            ready_url=None,
            sanitized_error=None,
        )
        settings, api_key = self._api_key()
        if (
            self.release is None
            or not attempt.quote.reviewed_release_bound
            or attempt.quote.template_hash_id
            != self.release.template_hash_id
            or attempt.quote.worker_commit != self.release.worker_commit
            or attempt.quote.worker_archive_sha256
            != self.release.worker_archive_sha256
            or attempt.quote.protocol_version
            != self.release.protocol_version
        ):
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                sanitized_error=(
                    "Reviewed worker release lock is unavailable."
                ),
            )
        try:
            offers = await self.provider.search_offers(
                api_key,
                max_price_per_hour=settings["max_price_per_hour"],
                min_vram_gb=settings["min_vram_gb"],
                disk_gb=attempt.quote.disk_gb,
            )
            eligible = apply_offer_policy(
                [
                    offer
                    for offer in offers
                    if float(offer.get("dph_total", float("inf")))
                    <= attempt.quote.max_price_per_hour
                ],
                blacklist=self.blacklist,
                now=float(self.clock()),
            )
            selected = select_best_offer(
                eligible,
                requested_gpu=attempt.quote.gpu_name,
            )
        except Exception:
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                sanitized_error=(
                    "No safe replacement offer is currently available."
                ),
            )

        replacement_quote = OfferQuote(
            offer_id=str(selected["offer_id"]),
            gpu_name=str(selected["gpu_name"]),
            gpu_ram_gb=float(selected["gpu_ram_gb"]),
            dph_total=float(selected["dph_total"]),
            reliability=(
                float(selected["reliability"])
                if selected.get("reliability") is not None
                else None
            ),
            max_price_per_hour=attempt.quote.max_price_per_hour,
            expires_at=float(self.clock()) + 120,
            disk_gb=attempt.quote.disk_gb,
            transfer_bytes=attempt.quote.transfer_bytes,
            output_allowance_bytes=(
                attempt.quote.output_allowance_bytes
            ),
            inet_down_cost=selected.get("inet_down_cost"),
            inet_up_cost=selected.get("inet_up_cost"),
            duration_seconds=attempt.quote.duration_seconds,
            deadline_mode=attempt.quote.deadline_mode,
            approximate_max_active_charge=(
                float(selected["dph_total"])
                * attempt.quote.duration_seconds
                / 3600
                if attempt.quote.duration_seconds is not None
                else None
            ),
            template_hash_id=attempt.quote.template_hash_id,
            worker_commit=attempt.quote.worker_commit,
            worker_archive_sha256=(
                attempt.quote.worker_archive_sha256
            ),
            protocol_version=attempt.quote.protocol_version,
            manifest_digest=attempt.quote.manifest_digest,
            machine_id=selected.get("machine_id"),
            host_id=selected.get("host_id"),
            public_ipaddr=selected.get("public_ipaddr"),
        )
        attempt = self.repository.transition(
            attempt.attempt_id,
            AttemptState.CREATING,
            now=float(self.clock()),
            quote=replacement_quote,
            retry_count=1,
        )
        try:
            instance_id = await self.provider.create_instance(
                api_key,
                offer_id=replacement_quote.offer_id,
                disk_gb=replacement_quote.disk_gb,
                label=attempt.label,
                release=self.release,
            )
        except Exception:
            inventory = await self._inventory(api_key)
            matches = self._by_label(inventory, attempt.label)
            if not matches:
                return self.repository.transition(
                    attempt.attempt_id,
                    AttemptState.FAILED,
                    now=float(self.clock()),
                    sanitized_error=(
                        "Replacement creation could not be confirmed."
                    ),
                )
            instance_id = matches[0].get("instance_id")
        return self.repository.transition(
            attempt.attempt_id,
            AttemptState.STARTING,
            now=float(self.clock()),
            instance_id=str(instance_id),
            sanitized_error=None,
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
            current = attempt
            if attempt.state == AttemptState.CREATING and matches:
                current = self.repository.transition(
                    attempt.attempt_id,
                    AttemptState.STARTING,
                    now=float(self.clock()),
                    instance_id=str(matches[0]["instance_id"]),
                    sanitized_error=None,
                )
            elif attempt.state == AttemptState.STARTING:
                if matches and not attempt.instance_id:
                    current = self.repository.transition(
                        attempt.attempt_id,
                        AttemptState.STARTING,
                        now=float(self.clock()),
                        instance_id=str(matches[0]["instance_id"]),
                    )
                current = await self.reconcile_once(current.attempt_id)
            elif attempt.state in {
                AttemptState.CANCEL_REQUESTED,
                AttemptState.DESTROYING,
            }:
                if matches and not attempt.instance_id:
                    current = self.repository.transition(
                        attempt.attempt_id,
                        attempt.state,
                        now=float(self.clock()),
                        instance_id=str(matches[0]["instance_id"]),
                    )
                current = await self.cancel(current.attempt_id)
            recovered.append(current)
        return recovered
