"""Backend-owned quote confirmation and idempotent Vast instance creation."""

from __future__ import annotations

import time

from .capture import CompiledCapture
from .constants import DEFAULT_DISK_GB
from .models import AttemptState, CloudAttempt, OfferQuote
from .offers import apply_offer_policy
from .repository import ConcurrentAttemptUpdate
from . import vast


DEFAULT_QUOTE_TTL_SECONDS = 120


class CloudRunError(RuntimeError):
    """A sanitized Cloud Run service failure."""


class CloudRunValidationError(CloudRunError):
    pass


class AttemptNotFound(CloudRunError):
    pass


class QuoteUnavailable(CloudRunError):
    pass


class VastProvider:
    """Small injectable adapter around the backend-only Vast contracts."""

    async def search_offers(
        self,
        api_key,
        *,
        max_price_per_hour,
        min_vram_gb,
    ):
        return await vast.search_offers(
            api_key,
            max_price_per_hour=max_price_per_hour,
            min_vram_gb=min_vram_gb,
        )

    async def get_offer(
        self,
        api_key,
        offer_id,
        *,
        max_price_per_hour,
        min_vram_gb,
    ):
        return await vast.get_offer(
            api_key,
            offer_id=offer_id,
            max_price_per_hour=max_price_per_hour,
            min_vram_gb=min_vram_gb,
        )

    async def create_instance(
        self,
        api_key,
        *,
        offer_id,
        disk_gb,
        label,
    ):
        return await vast.create_instance(
            api_key,
            offer_id=offer_id,
            disk_gb=disk_gb,
            label=label,
        )

    async def list_instances(self, api_key):
        return await vast.list_instances(api_key)

    async def get_instance(self, api_key, instance_id):
        return await vast.get_instance(api_key, instance_id)

    async def destroy_instance(self, api_key, instance_id):
        return await vast.destroy_instance(api_key, instance_id)


def _idempotency_key(value):
    if not isinstance(value, str):
        raise CloudRunValidationError("A valid idempotency key is required.")
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise CloudRunValidationError("A valid idempotency key is required.")
    return normalized


def _offer_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CloudRunValidationError("A valid offer ID is required.")
    normalized = str(value).strip()
    if not normalized.isdigit() or len(normalized) > 32:
        raise CloudRunValidationError("A valid offer ID is required.")
    return normalized


class CloudRunService:
    def __init__(
        self,
        settings_store,
        repository,
        *,
        job_repository=None,
        provider=None,
        blacklist=None,
        clock=None,
        quote_ttl_seconds=DEFAULT_QUOTE_TTL_SECONDS,
        disk_gb=DEFAULT_DISK_GB,
        lifecycle=None,
    ):
        self.settings_store = settings_store
        self.repository = repository
        self.job_repository = job_repository
        self.provider = provider or VastProvider()
        self.blacklist = blacklist
        self.clock = clock or time.time
        self.quote_ttl_seconds = float(quote_ttl_seconds)
        self.disk_gb = int(disk_gb)
        self.lifecycle = lifecycle

    async def capture(self, payload):
        if self.job_repository is None:
            raise CloudRunError("Cloud Run capture storage is unavailable.")
        capture = CompiledCapture.from_payload(payload)
        self.job_repository.save_capture(
            capture,
            created_at=float(self.clock()),
        )
        return capture

    def _settings(self):
        settings = self.settings_store.load()
        if not isinstance(settings, dict) or not settings.get("api_key"):
            raise vast.OfferSearchConfigurationError(
                "Vast API key is not configured."
            )
        return settings

    async def search(self):
        settings = self._settings()
        offers = await self.provider.search_offers(
            settings["api_key"],
            max_price_per_hour=settings["max_price_per_hour"],
            min_vram_gb=settings["min_vram_gb"],
        )
        return apply_offer_policy(
            offers,
            blacklist=self.blacklist,
            now=float(self.clock()),
        )

    async def _eligible_offer(self, identifier, settings):
        selected = await self.provider.get_offer(
            settings["api_key"],
            identifier,
            max_price_per_hour=settings["max_price_per_hour"],
            min_vram_gb=settings["min_vram_gb"],
        )
        if selected is None:
            return None
        eligible = apply_offer_policy(
            [selected],
            blacklist=self.blacklist,
            now=float(self.clock()),
        )
        return next(
            (
                offer
                for offer in eligible
                if str(offer.get("offer_id")) == str(identifier)
            ),
            None,
        )

    async def preview_offer(self, *, offer_id, idempotency_key):
        key = _idempotency_key(idempotency_key)
        existing = self.repository.get_by_idempotency_key(key)
        if existing is not None:
            return existing

        identifier = _offer_id(offer_id)
        settings = self._settings()
        selected = await self._eligible_offer(identifier, settings)
        if selected is None:
            raise QuoteUnavailable(
                "The selected Vast offer is no longer available."
            )

        now = float(self.clock())
        quote = OfferQuote(
            offer_id=identifier,
            gpu_name=str(selected["gpu_name"]),
            gpu_ram_gb=float(selected["gpu_ram_gb"]),
            dph_total=float(selected["dph_total"]),
            reliability=(
                float(selected["reliability"])
                if selected.get("reliability") is not None
                else None
            ),
            max_price_per_hour=float(settings["max_price_per_hour"]),
            expires_at=now + self.quote_ttl_seconds,
            machine_id=selected.get("machine_id"),
            host_id=selected.get("host_id"),
            public_ipaddr=selected.get("public_ipaddr"),
        )
        candidate = CloudAttempt.new(
            idempotency_key=key,
            quote=quote,
            now=now,
            state=AttemptState.OFFER_SELECTED,
        )
        saved, _created = self.repository.create_or_get(candidate)
        return saved

    def get_attempt(self, attempt_id):
        attempt = self.repository.get(str(attempt_id))
        if attempt is None:
            raise AttemptNotFound("Cloud Run attempt was not found.")
        return attempt

    def _validated_attempt(self, attempt_id, idempotency_key):
        key = _idempotency_key(idempotency_key)
        attempt = self.get_attempt(attempt_id)
        if attempt.idempotency_key != key:
            raise CloudRunValidationError(
                "The idempotency key does not match this attempt."
            )
        return attempt

    async def _revalidated_offer(self, attempt, settings):
        selected = await self._eligible_offer(
            attempt.quote.offer_id,
            settings,
        )
        if selected is None:
            return None
        if (
            str(selected.get("gpu_name")) == attempt.quote.gpu_name
            and float(selected.get("gpu_ram_gb", 0))
            >= attempt.quote.gpu_ram_gb
            and float(selected.get("dph_total", float("inf")))
            <= attempt.quote.dph_total
        ):
            return selected
        return None

    async def _instance_for_label(self, api_key, label):
        try:
            instances = await self.provider.list_instances(api_key)
        except Exception:
            return None
        matches = [
            instance
            for instance in instances
            if isinstance(instance, dict) and instance.get("label") == label
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: str(item.get("instance_id") or ""))
        return matches[0]

    async def _finish_created_instance(self, attempt_id, instance_id):
        current = self.get_attempt(attempt_id)
        if current.state == AttemptState.CANCEL_REQUESTED:
            current = self.repository.transition(
                current.attempt_id,
                AttemptState.CANCEL_REQUESTED,
                now=float(self.clock()),
                instance_id=str(instance_id),
            )
            if self.lifecycle is not None:
                return await self.lifecycle.cancel(current.attempt_id)
            return current
        if current.state != AttemptState.CREATING:
            return current
        current = self.repository.transition(
            current.attempt_id,
            AttemptState.STARTING,
            now=float(self.clock()),
            instance_id=str(instance_id),
            sanitized_error=None,
        )
        if self.lifecycle is not None:
            self.lifecycle.schedule_watchdog(current.attempt_id)
        return current

    async def confirm(self, attempt_id, *, idempotency_key):
        attempt = self._validated_attempt(attempt_id, idempotency_key)
        if attempt.state != AttemptState.OFFER_SELECTED:
            return attempt

        now = float(self.clock())
        if now >= attempt.quote.expires_at:
            self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=now,
                sanitized_error="The quote expired before confirmation.",
            )
            raise QuoteUnavailable("The quote expired before confirmation.")

        settings = self._settings()
        selected = await self._revalidated_offer(attempt, settings)
        if selected is None:
            self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                sanitized_error=(
                    "The selected offer changed or is no longer eligible."
                ),
            )
            raise QuoteUnavailable(
                "The selected offer changed or is no longer eligible."
            )

        try:
            attempt = self.repository.transition(
                attempt.attempt_id,
                AttemptState.CONFIRMING,
                now=float(self.clock()),
            )
            attempt = self.repository.transition(
                attempt.attempt_id,
                AttemptState.CREATING,
                now=float(self.clock()),
            )
        except ConcurrentAttemptUpdate:
            return self.get_attempt(attempt.attempt_id)

        try:
            instance_id = await self.provider.create_instance(
                settings["api_key"],
                offer_id=attempt.quote.offer_id,
                disk_gb=self.disk_gb,
                label=attempt.label,
            )
        except Exception as error:
            reconciled = await self._instance_for_label(
                settings["api_key"],
                attempt.label,
            )
            if reconciled is not None and reconciled.get("instance_id"):
                return await self._finish_created_instance(
                    attempt.attempt_id,
                    str(reconciled["instance_id"]),
                )
            retryable = isinstance(error, vast.VastError) and error.retryable
            if retryable:
                return self.repository.transition(
                    attempt.attempt_id,
                    AttemptState.CREATING,
                    now=float(self.clock()),
                    sanitized_error=(
                        "Creation outcome is unknown; verify Vast inventory "
                        "before taking another action."
                    ),
                )
            return self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=float(self.clock()),
                sanitized_error=(
                    str(error)
                    if isinstance(error, vast.VastError)
                    else "Vast instance creation failed."
                ),
            )

        return await self._finish_created_instance(
            attempt.attempt_id,
            str(instance_id),
        )

    async def refresh(self, attempt_id):
        if self.lifecycle is None:
            return self.get_attempt(attempt_id)
        return await self.lifecycle.reconcile_once(attempt_id)

    async def cancel(self, attempt_id):
        if self.lifecycle is None:
            raise CloudRunError("Cloud Run lifecycle is unavailable.")
        return await self.lifecycle.cancel(attempt_id)

    async def destroy(self, attempt_id):
        if self.lifecycle is None:
            raise CloudRunError("Cloud Run lifecycle is unavailable.")
        return await self.lifecycle.destroy(attempt_id)

    async def recover(self):
        if self.lifecycle is None:
            return []
        attempts = await self.lifecycle.recover()
        for attempt in attempts:
            if attempt.state == AttemptState.STARTING:
                self.lifecycle.schedule_watchdog(attempt.attempt_id)
        return attempts
