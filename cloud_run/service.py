"""Backend-owned quote confirmation and idempotent Vast instance creation."""

from __future__ import annotations

import asyncio
import json
import math
import secrets
import time
import uuid

from .capture import CompiledCapture
from .constants import (
    DEFAULT_DISK_GB,
    MIN_VAST_INET_DOWN_MBPS,
    PREFERRED_VAST_INET_DOWN_MBPS,
)
from .models import (
    AttemptState,
    CloudAttempt,
    CloudJob,
    CloudSession,
    ExecutionState,
    HarvestState,
    JobState,
    OfferQuote,
    SessionState,
)
from .offers import (
    apply_offer_policy,
    decide_offers,
    offer_meets_connection_quality_policy,
)
from .repository import (
    ConcurrentAttemptUpdate,
    ConcurrentSessionUpdate,
    PaidRentalConflict,
)
from .worker_release import WorkerRelease, WorkerReleaseUnavailable
from . import vast


DEFAULT_QUOTE_TTL_SECONDS = 120


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise ValueError("Invalid JSON constant.")


def _native_prompt_intent_class():
    if "." in (__package__ or ""):
        from ..remote_worker.native_jobs import NativePromptIntent
    else:
        from remote_worker.native_jobs import NativePromptIntent
    return NativePromptIntent


class CloudRunError(RuntimeError):
    """A sanitized Cloud Run service failure."""


class CloudRunValidationError(CloudRunError):
    pass


class AttemptNotFound(CloudRunError):
    pass


class SessionNotFound(CloudRunError):
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
        disk_gb,
    ):
        return await vast.search_offers(
            api_key,
            max_price_per_hour=max_price_per_hour,
            min_vram_gb=min_vram_gb,
            disk_gb=disk_gb,
        )

    async def get_offer(
        self,
        api_key,
        offer_id,
        *,
        max_price_per_hour,
        min_vram_gb,
        disk_gb,
    ):
        return await vast.get_offer(
            api_key,
            offer_id=offer_id,
            max_price_per_hour=max_price_per_hour,
            min_vram_gb=min_vram_gb,
            disk_gb=disk_gb,
        )

    async def create_instance(
        self,
        api_key,
        *,
        offer_id,
        disk_gb,
        label,
        release,
        boundary_token,
        session_id,
    ):
        return await vast.create_instance(
            api_key,
            offer_id=offer_id,
            disk_gb=disk_gb,
            label=label,
            release=release,
            boundary_token=boundary_token,
            session_id=session_id,
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


def _max_instance_creates(value):
    if type(value) is not int or value != 1:
        raise CloudRunValidationError(
            "Maximum total instance creates must be 1."
        )
    return value


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
        cache_manager=None,
        session_service=None,
        session_repository=None,
        release=None,
        desktop_relay=None,
        desktop_worker_factory=None,
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
        self.cache_manager = cache_manager
        self.session_service = session_service
        self.session_repository = session_repository
        self.release = release if isinstance(release, WorkerRelease) else None
        self.desktop_relay = desktop_relay
        self.desktop_worker_factory = desktop_worker_factory

    def _desktop_components(self):
        relay = self.desktop_relay
        if relay is None or not all(
            callable(getattr(relay, method, None))
            for method in ("start", "activate", "deactivate", "status")
        ):
            raise CloudRunValidationError(
                "ComfyUI Vast Desktop relay is unavailable."
            )
        if not callable(self.desktop_worker_factory):
            raise CloudRunValidationError(
                "ComfyUI Vast Desktop worker is unavailable."
            )
        return relay

    def desktop_setup(self):
        relay = self._desktop_components()
        status = relay.status().public_payload()
        return {
            **status,
            "manual_setup_required": True,
            "instructions": [
                "Open Remote Connections in ComfyUI Desktop.",
                "Add the loopback URL with the name ComfyUI Vast.",
                "Open ComfyUI Vast only after this status is ready.",
            ],
        }

    async def activate_desktop_relay(self, session_id):
        relay = self._desktop_components()
        session = self.get_session(session_id)
        if session.state != SessionState.READY:
            raise CloudRunValidationError(
                "The ComfyUI Vast session is not ready."
            )
        certified = getattr(
            self.session_service,
            "readiness_certified",
            None,
        )
        if callable(certified) and certified(session.session_id) is not True:
            raise CloudRunValidationError(
                "The ComfyUI Vast readiness report is incomplete."
            )
        await relay.start()
        try:
            worker = self.desktop_worker_factory(session)
        except Exception:
            raise CloudRunValidationError(
                "ComfyUI Vast Desktop worker is unavailable."
            ) from None
        profile_revision = 0
        if self.job_repository is not None and callable(
            getattr(self.job_repository, "get_desktop_relay", None)
        ):
            config = self.job_repository.get_desktop_relay()
            if (
                config is not None
                and config.active_session_id == session.session_id
                and config.profile_revision is not None
            ):
                profile_revision = config.profile_revision
        return await relay.activate(
            session.session_id,
            worker,
            profile_revision,
        )

    async def deactivate_desktop_relay(self, session_id):
        relay = self._desktop_components()
        self.get_session(session_id)
        return await relay.deactivate(str(session_id))

    def prepare_native_prompt(self, session_id, body):
        if self.job_repository is None:
            raise CloudRunValidationError(
                "Native prompt storage is unavailable."
            )
        session = self.get_session(session_id)
        if session.state not in {
            SessionState.READY,
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }:
            raise CloudRunValidationError(
                "The ComfyUI Vast session is not ready."
            )
        manifest_digest = session.installed_manifest_digest
        if manifest_digest != session.manifest_digest:
            raise CloudRunValidationError(
                "The ComfyUI Vast environment is not validated."
            )
        if not isinstance(body, bytes):
            raise CloudRunValidationError("Invalid native prompt.")
        try:
            NativePromptIntent = _native_prompt_intent_class()

            parsed = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
            job_id = uuid.uuid4().hex
            request_id = uuid.uuid4().hex
            intent = NativePromptIntent.from_http(
                job_id=job_id,
                request_id=request_id,
                manifest_digest=manifest_digest,
                body=parsed,
            )
        except Exception:
            raise CloudRunValidationError("Invalid native prompt.") from None
        now = self.clock()
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now < 0
        ):
            raise CloudRunValidationError(
                "Native prompt storage clock is unavailable."
            )
        now = float(now)
        self.job_repository.save_capture(intent.capture, created_at=now)
        job = CloudJob(
            job_id=job_id,
            session_id=session.session_id,
            idempotency_key=request_id,
            state=JobState.QUEUED,
            prompt_digest=intent.capture.prompt_digest,
            capture_json=intent.capture.canonical_payload(),
            manifest_digest=manifest_digest,
            remote_prompt_id=None,
            sanitized_error=None,
            created_at=now,
            updated_at=now,
            version=1,
            execution_state=ExecutionState.QUEUED,
            harvest_state=HarvestState.PENDING,
        )
        saved, created = self.job_repository.create_job(job)
        if not created or saved.job_id != job.job_id:
            raise CloudRunValidationError(
                "Native prompt identity could not be persisted."
            )
        return {
            "job_id": job_id,
            "request_id": request_id,
            "manifest_digest": manifest_digest,
        }

    async def capture(self, payload):
        if self.job_repository is None:
            raise CloudRunError("Cloud Run capture storage is unavailable.")
        capture = CompiledCapture.from_payload(payload)
        self.job_repository.save_capture(
            capture,
            created_at=float(self.clock()),
        )
        return capture

    async def populate_cache(self, artifact_id, *, acknowledged):
        if self.cache_manager is None:
            raise CloudRunValidationError(
                "R2 cache population is unavailable."
            )
        return await self.cache_manager.populate_cache(
            artifact_id,
            acknowledged=acknowledged,
        )

    async def preflight(
        self,
        capture_id,
        *,
        explicit_output_allowance_bytes=None,
    ):
        if self.session_service is None:
            raise CloudRunValidationError(
                "Dependency preflight is unavailable."
            )
        return await self.session_service.preflight(
            capture_id,
            explicit_output_allowance_bytes=(
                explicit_output_allowance_bytes
            ),
        )

    async def approve_mapping(self, mapping_id, candidate_digest):
        if self.session_service is None:
            raise CloudRunValidationError(
                "Dependency mappings are unavailable."
            )
        return self.session_service.approve_mapping(
            mapping_id,
            candidate_digest,
        )

    async def register_agent_suggestion(self, payload):
        if self.session_service is None:
            raise CloudRunValidationError(
                "Agent Panel integration is unavailable."
            )
        return self.session_service.register_agent_suggestion(payload)

    def _settings(self):
        settings = self.settings_store.load()
        if not isinstance(settings, dict) or not settings.get("api_key"):
            raise vast.OfferSearchConfigurationError(
                "Vast API key is not configured."
            )
        return settings

    def _reviewed_release(self):
        if self.release is None:
            raise WorkerReleaseUnavailable(
                "Reviewed worker release lock is unavailable."
            )
        return self.release

    @staticmethod
    def _deadline_contract(deadline):
        if (
            not isinstance(deadline, dict)
            or set(deadline) != {"mode", "duration_seconds"}
        ):
            raise CloudRunValidationError(
                "A valid session deadline is required."
            )
        mode = deadline.get("mode")
        duration = deadline.get("duration_seconds")
        if mode == "finite":
            if (
                isinstance(duration, bool)
                or not isinstance(duration, int)
                or duration <= 0
            ):
                raise CloudRunValidationError(
                    "A valid session deadline is required."
                )
            return mode, duration
        if mode == "none" and duration is None:
            return mode, None
        raise CloudRunValidationError(
            "A valid session deadline is required."
        )

    def _session_repository(self):
        if self.session_repository is None:
            raise CloudRunValidationError(
                "Cloud Run session storage is unavailable."
            )
        return self.session_repository

    def get_session(self, session_id):
        session = self._session_repository().get(str(session_id))
        if session is None:
            raise SessionNotFound("Cloud Run session was not found.")
        return session

    async def refresh_session(self, session_id):
        session = self.get_session(session_id)
        enforce = getattr(
            self.lifecycle,
            "enforce_session_deadline",
            None,
        )
        if (
            callable(enforce)
            and session.deadline_mode == "finite"
            and isinstance(session.deadline_at, (int, float))
            and not isinstance(session.deadline_at, bool)
            and float(self.clock()) >= session.deadline_at
            and session.state != SessionState.DESTROYED
        ):
            session = await enforce(session.session_id)
            return session
        refresh = getattr(self.lifecycle, "reconcile_session_once", None)
        if callable(refresh) and session.state in {
            SessionState.CREATING,
            SessionState.BOOTSTRAPPING,
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
            SessionState.DESTROY_REQUESTED,
            SessionState.DESTROYING,
        }:
            session = await refresh(session.session_id)
        reconciler = getattr(
            self.session_service,
            "reconciler",
            None,
        )
        schedule = getattr(reconciler, "schedule", None)
        if callable(schedule) and session.state in {
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }:
            try:
                schedule(session.session_id)
                await asyncio.sleep(0)
            except (RuntimeError, ValueError):
                pass
            session = self.get_session(session.session_id)
        sync_profile = getattr(self.session_service, "sync_profile", None)
        if callable(sync_profile) and session.state in {
            SessionState.READY,
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }:
            await sync_profile(session.session_id)
        return session

    async def session_profile(self, session_id):
        sync = getattr(self.session_service, "sync_profile", None)
        if not callable(sync):
            raise CloudRunValidationError(
                "Desktop profile synchronization is unavailable."
            )
        return await sync(session_id)

    async def resolve_session_profile_conflict(
        self,
        session_id,
        conflict_id,
        *,
        winner,
    ):
        resolve = getattr(
            self.session_service,
            "resolve_profile_conflict",
            None,
        )
        if not callable(resolve):
            raise CloudRunValidationError(
                "Desktop profile synchronization is unavailable."
            )
        return await resolve(
            session_id,
            conflict_id,
            winner=winner,
        )

    async def submit_job(
        self,
        session_id,
        *,
        capture_id,
        idempotency_key,
    ):
        if self.session_service is None or not callable(
            getattr(self.session_service, "submit_job", None)
        ):
            raise CloudRunValidationError(
                "Cloud Run job execution is unavailable."
            )
        return await self.session_service.submit_job(
            session_id,
            capture_id=capture_id,
            idempotency_key=idempotency_key,
        )

    def get_job(self, session_id, job_id):
        if self.session_service is None or not callable(
            getattr(self.session_service, "get_job", None)
        ):
            raise CloudRunValidationError(
                "Cloud Run job storage is unavailable."
            )
        return self.session_service.get_job(session_id, job_id)

    async def retry_harvest(self, session_id, job_id):
        if self.session_service is None or not callable(
            getattr(self.session_service, "retry_harvest", None)
        ):
            raise CloudRunValidationError(
                "Cloud Vast output retrieval is unavailable."
            )
        return await self.session_service.retry_harvest(
            session_id,
            job_id,
        )

    async def update_session_deadline(self, session_id, payload):
        update = getattr(self.session_service, "update_deadline", None)
        if not callable(update):
            raise CloudRunValidationError(
                "Cloud Run deadline controls are unavailable."
            )
        return await update(session_id, payload)

    async def review_session_destroy(self, session_id):
        review = getattr(self.session_service, "review_destroy", None)
        if not callable(review):
            raise CloudRunValidationError(
                "Cloud Run destruction review is unavailable."
            )
        return await review(session_id)

    async def destroy_session(self, session_id, confirmation):
        destroy = getattr(self.session_service, "destroy", None)
        if not callable(destroy):
            raise CloudRunValidationError(
                "Verified Cloud Run destruction is unavailable."
            )
        return await destroy(session_id, confirmation)

    def _validated_session(self, session_id, idempotency_key):
        key = _idempotency_key(idempotency_key)
        session = self.get_session(session_id)
        if session.idempotency_key != key:
            raise CloudRunValidationError(
                "The idempotency key does not match this session."
            )
        return session

    async def preview_session(
        self,
        *,
        preflight_id,
        offer_id,
        idempotency_key,
        deadline,
        max_instance_creates,
        estimate_digest=None,
    ):
        release = self._reviewed_release()
        repository = self._session_repository()
        if (
            self.session_service is None
            or not callable(
                getattr(
                    self.session_service,
                    "require_rentable_preflight",
                    None,
                )
            )
        ):
            raise CloudRunValidationError(
                "Dependency preflight is unavailable."
            )
        key = _idempotency_key(idempotency_key)
        existing = repository.get_by_idempotency_key(key)
        if existing is not None:
            return existing
        identifier = _offer_id(offer_id)
        mode, duration = self._deadline_contract(deadline)
        create_limit = _max_instance_creates(max_instance_creates)
        preflight = self.session_service.require_rentable_preflight(
            preflight_id
        )
        settings = self._settings()
        selected = await self._eligible_offer(
            identifier,
            settings,
            disk_gb=preflight.disk_gb,
            min_vram_gb=max(
                1,
                math.ceil(
                    float(getattr(preflight, "minimum_vram_gb", 0.0))
                ),
            ),
        )
        if selected is None:
            raise QuoteUnavailable(
                "The selected Vast offer is no longer available."
            )
        decisions = decide_offers(
            [selected],
            workflow_min_vram_gb=float(
                getattr(preflight, "minimum_vram_gb", 0.0)
            ),
            workflow_disk_gb=preflight.disk_gb,
            preferred_vram_gb=(
                None
                if settings["min_vram_gb"] <= 1
                else settings["min_vram_gb"]
            ),
            max_price_per_hour=(
                None
                if settings["max_price_per_hour"] >= 100
                else settings["max_price_per_hour"]
            ),
            transfer_bytes=preflight.transfer_bytes,
            cached_bytes=int(getattr(preflight, "cached_bytes", 0)),
            source_ready=True,
        )
        decision = next(
            (item for item in decisions if item.included),
            None,
        )
        if decision is None or decision.estimate is None:
            raise QuoteUnavailable(
                "The selected Vast offer is no longer available."
            )
        if estimate_digest is not None and (
            not isinstance(estimate_digest, str)
            or estimate_digest != decision.estimate.digest
        ):
            raise QuoteUnavailable(
                "The readiness estimate changed before review."
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
            inet_down_mbps=selected.get("inet_down_mbps"),
            disk_bw_mbps=selected.get("disk_bw_mbps"),
            dlperf=selected.get("dlperf"),
            max_price_per_hour=float(settings["max_price_per_hour"]),
            expires_at=now + self.quote_ttl_seconds,
            disk_gb=int(preflight.disk_gb),
            transfer_bytes=int(preflight.transfer_bytes),
            output_allowance_bytes=int(
                preflight.output_allowance_bytes
            ),
            inet_down_cost=selected.get("inet_down_cost"),
            inet_up_cost=selected.get("inet_up_cost"),
            duration_seconds=duration,
            deadline_mode=mode,
            approximate_max_active_charge=(
                float(selected["dph_total"]) * duration / 3600
                if duration is not None
                else None
            ),
            template_hash_id=release.template_hash_id,
            worker_commit=release.worker_commit,
            worker_archive_sha256=release.worker_archive_sha256,
            protocol_version=release.protocol_version,
            manifest_digest=preflight.manifest_digest,
            execution_baseline_digest=(
                preflight.execution_baseline_digest
            ),
            randomized_seed_node_ids=(
                preflight.randomized_seed_node_ids
            ),
            machine_id=selected.get("machine_id"),
            host_id=selected.get("host_id"),
            public_ipaddr=selected.get("public_ipaddr"),
            max_instance_creates=create_limit,
            readiness_estimate=decision.estimate,
        )
        candidate = CloudSession.new(
            key,
            quote=quote,
            manifest_digest=preflight.manifest_digest,
            execution_baseline_digest=(
                preflight.execution_baseline_digest
            ),
            randomized_seed_node_ids=(
                preflight.randomized_seed_node_ids
            ),
            deadline_at=(now + duration if duration is not None else None),
            deadline_mode=mode,
            disk_gb=preflight.disk_gb,
            now=now,
            state=SessionState.OFFER_SELECTED,
        )
        saved, _created = repository.create_or_get(candidate)
        return saved

    @staticmethod
    def _release_matches_quote(release, quote):
        return (
            quote.template_hash_id == release.template_hash_id
            and quote.worker_commit == release.worker_commit
            and quote.worker_archive_sha256
            == release.worker_archive_sha256
            and quote.protocol_version == release.protocol_version
        )

    @staticmethod
    def _bandwidth_cost_not_increased(current, quoted):
        if quoted is None:
            return current is None
        return current is not None and float(current) <= float(quoted)

    @classmethod
    def _same_optional_number(cls, current, quoted):
        if current is None or quoted is None:
            return current is None and quoted is None
        current_value = cls._finite_metric(current)
        quoted_value = cls._finite_metric(quoted)
        return (
            current_value is not None
            and quoted_value is not None
            and current_value == quoted_value
        )

    @staticmethod
    def _finite_metric(value):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return None
        return float(value)

    @classmethod
    def _connection_quality_is_eligible(cls, offer):
        return offer_meets_connection_quality_policy(offer)

    @classmethod
    def _connection_speed_matches_quote(cls, current, quoted):
        current_speed = cls._finite_metric(current)
        quoted_speed = cls._finite_metric(quoted)
        return (
            current_speed is not None
            and quoted_speed is not None
            and quoted_speed >= MIN_VAST_INET_DOWN_MBPS
            and current_speed >= MIN_VAST_INET_DOWN_MBPS
            and current_speed
            >= min(quoted_speed, PREFERRED_VAST_INET_DOWN_MBPS)
        )

    async def _revalidated_session_offer(self, session, settings):
        if float(settings["max_price_per_hour"]) != float(
            session.quote.max_price_per_hour
        ):
            return None
        selected = await self._eligible_offer(
            session.quote.offer_id,
            settings,
            disk_gb=session.quote.disk_gb,
            min_vram_gb=1,
        )
        if selected is None:
            return None
        if (
            self._connection_quality_is_eligible(selected)
            and str(selected.get("offer_id")) == session.quote.offer_id
            and str(selected.get("gpu_name")) == session.quote.gpu_name
            and self._same_optional_number(
                selected.get("gpu_ram_gb"),
                session.quote.gpu_ram_gb,
            )
            and self._same_optional_number(
                selected.get("dph_total"),
                session.quote.dph_total,
            )
            and self._same_optional_number(
                selected.get("reliability"),
                session.quote.reliability,
            )
            and self._same_optional_number(
                selected.get("inet_down_mbps"),
                session.quote.inet_down_mbps,
            )
            and self._same_optional_number(
                selected.get("disk_bw_mbps"),
                session.quote.disk_bw_mbps,
            )
            and self._same_optional_number(
                selected.get("dlperf"),
                session.quote.dlperf,
            )
            and self._same_optional_number(
                selected.get("inet_down_cost"),
                session.quote.inet_down_cost,
            )
            and self._same_optional_number(
                selected.get("inet_up_cost"),
                session.quote.inet_up_cost,
            )
            and (
                session.quote.machine_id is None
                or selected.get("machine_id")
                == session.quote.machine_id
            )
            and (
                session.quote.host_id is None
                or selected.get("host_id") == session.quote.host_id
            )
        ):
            estimate = session.quote.readiness_estimate
            if estimate is None:
                return None
            decisions = decide_offers(
                [selected],
                workflow_min_vram_gb=1,
                workflow_disk_gb=session.quote.disk_gb,
                preferred_vram_gb=None,
                max_price_per_hour=session.quote.max_price_per_hour,
                transfer_bytes=session.quote.transfer_bytes,
                cached_bytes=estimate.cached_bytes,
                source_ready=estimate.source_ready,
            )
            if (
                len(decisions) == 1
                and decisions[0].included
                and decisions[0].estimate is not None
                and decisions[0].estimate.digest == estimate.digest
            ):
                return selected
        return None

    async def _finish_created_session(self, session_id, instance_id):
        current = self.get_session(session_id)
        if current.destroy_requested and current.state in {
            SessionState.DESTROY_REQUESTED,
            SessionState.DESTROYING,
            SessionState.FAILED,
        }:
            current = self._session_repository().transition(
                current.session_id,
                current.state,
                now=float(self.clock()),
                instance_id=str(instance_id),
            )
            destroy = getattr(
                self.lifecycle,
                "destroy_session",
                None,
            )
            if callable(destroy):
                return await destroy(current.session_id)
            return current
        if current.state != SessionState.CREATING:
            return current
        session = self._session_repository().transition(
            current.session_id,
            SessionState.BOOTSTRAPPING,
            now=float(self.clock()),
            instance_id=str(instance_id),
            sanitized_error=None,
        )
        schedule = getattr(
            self.lifecycle,
            "schedule_session_watchdog",
            None,
        )
        if callable(schedule):
            schedule(session.session_id)
        return session

    async def confirm_session(
        self,
        session_id,
        *,
        idempotency_key,
        estimate_digest=None,
        accepted_longer_estimate=False,
        current_preflight_id=None,
    ):
        session = self._validated_session(session_id, idempotency_key)
        if session.state not in {
            SessionState.OFFER_SELECTED,
            SessionState.CONFIRMING,
        }:
            return session
        repository = self._session_repository()
        now = float(self.clock())
        estimate = session.quote.readiness_estimate
        expected_digest = estimate.digest if estimate is not None else None
        supplied_digest = (
            expected_digest if estimate_digest is None else estimate_digest
        )
        if (
            estimate is None
            or not isinstance(supplied_digest, str)
            or supplied_digest != expected_digest
        ):
            repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=now,
                sanitized_error=(
                    "The readiness estimate changed before confirmation."
                ),
                failure_code="quote_expired",
            )
            raise QuoteUnavailable(
                "The readiness estimate changed before confirmation."
            )
        if current_preflight_id is not None:
            matches = getattr(
                self.session_service,
                "matches_paid_preflight",
                None,
            )
            if not callable(matches) or not matches(
                current_preflight_id,
                reviewed_manifest_digest=session.quote.manifest_digest,
                reviewed_execution_baseline_digest=(
                    session.quote.execution_baseline_digest
                ),
                reviewed_randomized_seed_node_ids=(
                    session.quote.randomized_seed_node_ids
                ),
                reviewed_transfer_bytes=session.quote.transfer_bytes,
                reviewed_cached_bytes=estimate.cached_bytes,
                reviewed_output_allowance_bytes=(
                    session.quote.output_allowance_bytes
                ),
                reviewed_disk_gb=session.quote.disk_gb,
            ):
                repository.transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=now,
                    sanitized_error=(
                        "The canvas or profile changed before confirmation."
                    ),
                    failure_code="quote_expired",
                )
                raise QuoteUnavailable(
                    "The canvas or profile changed before confirmation."
                )
        if (
            not estimate.source_ready
            or (
                not estimate.ten_minute_eligible
                and accepted_longer_estimate is not True
            )
        ):
            raise CloudRunValidationError(
                "Accept the reviewed longer readiness estimate before rental."
            )
        if (
            session.retry_count != 0
            or 1 + session.retry_count
            > session.quote.max_instance_creates
        ):
            return repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=now,
                sanitized_error=(
                    "The authorized total instance-create limit was reached."
                ),
            )
        release = self._reviewed_release()
        if now >= session.quote.expires_at:
            repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=now,
                sanitized_error="The quote expired before confirmation.",
                failure_code="quote_expired",
            )
            raise QuoteUnavailable("The quote expired before confirmation.")
        if not self._release_matches_quote(release, session.quote):
            repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=now,
                sanitized_error=(
                    "The reviewed worker release changed before confirmation."
                ),
                failure_code="quote_expired",
            )
            raise QuoteUnavailable(
                "The reviewed worker release changed before confirmation."
            )
        settings = self._settings()
        selected = await self._revalidated_session_offer(session, settings)
        if selected is None:
            repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=float(self.clock()),
                sanitized_error=(
                    "The selected offer changed or is no longer eligible."
                ),
                failure_code="quote_expired",
            )
            raise QuoteUnavailable(
                "The selected offer changed or is no longer eligible."
            )
        if session.state == SessionState.OFFER_SELECTED:
            try:
                session = repository.save(
                    session.transition(
                        SessionState.CONFIRMING,
                        now=float(self.clock()),
                    )
                )
            except ConcurrentSessionUpdate:
                return self.get_session(session.session_id)
        claim_now = float(self.clock())
        provider_token = secrets.token_hex(32)
        session_secret_hex = secrets.token_hex(32)
        try:
            session = repository.claim_create_intent(
                session.session_id,
                now=claim_now,
                provider_token=provider_token,
                session_secret_hex=session_secret_hex,
            )
        except ConcurrentSessionUpdate:
            return self.get_session(session.session_id)
        try:
            instance_id = await self.provider.create_instance(
                settings["api_key"],
                offer_id=session.quote.offer_id,
                disk_gb=session.quote.disk_gb,
                label=session.label,
                release=release,
                boundary_token=session.provider_token,
                session_id=session.session_id,
            )
        except Exception as error:
            matches = await self._instance_for_label(
                settings["api_key"],
                session.label,
            )
            if (
                matches is not None
                and len(matches) == 1
                and matches[0].get("instance_id")
            ):
                return await self._finish_created_session(
                    session.session_id,
                    str(matches[0]["instance_id"]),
                )
            if matches is not None and len(matches) > 1:
                return repository.transition(
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
            retryable = isinstance(error, vast.VastError) and error.retryable
            if retryable:
                session = repository.transition(
                    session.session_id,
                    SessionState.CREATING,
                    now=float(self.clock()),
                    sanitized_error=(
                        "Creation outcome is unknown; verify Vast inventory "
                        "before taking another action."
                    ),
                )
                schedule = getattr(
                    self.lifecycle,
                    "schedule_session_watchdog",
                    None,
                )
                if callable(schedule):
                    schedule(session.session_id)
                return session
            return repository.transition(
                session.session_id,
                SessionState.FAILED,
                now=float(self.clock()),
                sanitized_error=(
                    str(error)
                    if isinstance(error, vast.VastError)
                    else "Vast instance creation failed."
                ),
            )
        return await self._finish_created_session(
            session.session_id,
            str(instance_id),
        )

    async def search(self, preflight_id=None):
        if self.session_service is not None:
            return await self.session_service.search_offers(preflight_id)
        return await self._search_without_preflight()

    async def _search_without_preflight(
        self,
        *,
        disk_gb=DEFAULT_DISK_GB,
        workflow_min_vram_gb=None,
        transfer_bytes=0,
        cached_bytes=0,
        source_ready=True,
    ):
        settings = self._settings()
        hard_vram = (
            settings["min_vram_gb"]
            if workflow_min_vram_gb is None
            else max(1, math.ceil(float(workflow_min_vram_gb)))
        )
        offers = await self.provider.search_offers(
            settings["api_key"],
            max_price_per_hour=settings["max_price_per_hour"],
            min_vram_gb=hard_vram,
            disk_gb=disk_gb,
        )
        decisions = decide_offers(
            offers,
            workflow_min_vram_gb=hard_vram,
            workflow_disk_gb=disk_gb,
            preferred_vram_gb=(
                None
                if settings["min_vram_gb"] <= 1
                else settings["min_vram_gb"]
            ),
            max_price_per_hour=(
                None
                if settings["max_price_per_hour"] >= 100
                else settings["max_price_per_hour"]
            ),
            transfer_bytes=transfer_bytes,
            cached_bytes=cached_bytes,
            source_ready=source_ready,
            blacklist=self.blacklist,
            now=float(self.clock()),
        )
        return [decision.public_payload() for decision in decisions]

    async def _eligible_offer(
        self,
        identifier,
        settings,
        *,
        disk_gb=DEFAULT_DISK_GB,
        min_vram_gb=None,
    ):
        selected = await self.provider.get_offer(
            settings["api_key"],
            identifier,
            max_price_per_hour=settings["max_price_per_hour"],
            min_vram_gb=(
                settings["min_vram_gb"]
                if min_vram_gb is None
                else min_vram_gb
            ),
            disk_gb=disk_gb,
        )
        if selected is None:
            return None
        eligible = apply_offer_policy(
            [selected],
            blacklist=self.blacklist,
            now=float(self.clock()),
        )
        selected = next(
            (
                offer
                for offer in eligible
                if str(offer.get("offer_id")) == str(identifier)
            ),
            None,
        )
        if selected is None or not self._connection_quality_is_eligible(
            selected
        ):
            return None
        return selected

    async def preview_offer(self, *, offer_id, idempotency_key):
        release = self._reviewed_release()
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
            inet_down_mbps=selected.get("inet_down_mbps"),
            disk_bw_mbps=selected.get("disk_bw_mbps"),
            dlperf=selected.get("dlperf"),
            max_price_per_hour=float(settings["max_price_per_hour"]),
            expires_at=now + self.quote_ttl_seconds,
            disk_gb=self.disk_gb,
            transfer_bytes=0,
            output_allowance_bytes=1,
            inet_down_cost=selected.get("inet_down_cost"),
            inet_up_cost=selected.get("inet_up_cost"),
            duration_seconds=None,
            deadline_mode="none",
            approximate_max_active_charge=None,
            template_hash_id=release.template_hash_id,
            worker_commit=release.worker_commit,
            worker_archive_sha256=release.worker_archive_sha256,
            protocol_version=release.protocol_version,
            manifest_digest="0" * 64,
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
            self._connection_quality_is_eligible(selected)
            and self._connection_speed_matches_quote(
                selected.get("inet_down_mbps"),
                attempt.quote.inet_down_mbps,
            )
            and str(selected.get("gpu_name")) == attempt.quote.gpu_name
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
        if not isinstance(instances, list):
            return None
        matches = [
            instance
            for instance in instances
            if isinstance(instance, dict) and instance.get("label") == label
        ]
        matches.sort(key=lambda item: str(item.get("instance_id") or ""))
        return tuple(matches)

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
            residual_inventory=(),
            sanitized_error=None,
        )
        if self.lifecycle is not None:
            self.lifecycle.schedule_watchdog(current.attempt_id)
        return current

    async def confirm(self, attempt_id, *, idempotency_key):
        attempt = self._validated_attempt(attempt_id, idempotency_key)
        if attempt.state not in {
            AttemptState.OFFER_SELECTED,
            AttemptState.CONFIRMING,
        }:
            return attempt

        release = self._reviewed_release()
        now = float(self.clock())
        if now >= attempt.quote.expires_at:
            self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=now,
                sanitized_error="The quote expired before confirmation.",
            )
            raise QuoteUnavailable("The quote expired before confirmation.")
        if not self._release_matches_quote(release, attempt.quote):
            self.repository.transition(
                attempt.attempt_id,
                AttemptState.FAILED,
                now=now,
                sanitized_error=(
                    "The reviewed worker release changed before confirmation."
                ),
            )
            raise QuoteUnavailable(
                "The reviewed worker release changed before confirmation."
            )

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
            if attempt.state == AttemptState.OFFER_SELECTED:
                attempt = self.repository.save(
                    attempt.transition(
                        AttemptState.CONFIRMING,
                        now=float(self.clock()),
                    )
                )
            attempt = self.repository.save(
                attempt.transition(
                    AttemptState.CREATING,
                    now=float(self.clock()),
                    provider_token=secrets.token_hex(32),
                )
            )
        except ConcurrentAttemptUpdate:
            return self.get_attempt(attempt.attempt_id)

        try:
            instance_id = await self.provider.create_instance(
                settings["api_key"],
                offer_id=attempt.quote.offer_id,
                disk_gb=self.disk_gb,
                label=attempt.label,
                release=release,
                boundary_token=attempt.provider_token,
                session_id=attempt.attempt_id,
            )
        except Exception as error:
            matches = await self._instance_for_label(
                settings["api_key"],
                attempt.label,
            )
            if (
                matches is not None
                and len(matches) == 1
                and matches[0].get("instance_id")
            ):
                return await self._finish_created_instance(
                    attempt.attempt_id,
                    str(matches[0]["instance_id"]),
                )
            if matches is not None and len(matches) > 1:
                return self.repository.transition(
                    attempt.attempt_id,
                    AttemptState.FAILED,
                    now=float(self.clock()),
                    instance_id=None,
                    residual_inventory=tuple(
                        str(item.get("instance_id"))
                        for item in matches
                        if item.get("instance_id") is not None
                    ),
                    sanitized_error=(
                        "Multiple managed Vast instances match this attempt."
                    ),
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
        recovered = []
        if self.lifecycle is not None:
            recover_sessions = getattr(
                self.lifecycle,
                "recover_sessions",
                None,
            )
            if (
                getattr(self.lifecycle, "session_repository", None)
                is not None
                and callable(recover_sessions)
            ):
                recovered = await recover_sessions()
            else:
                recovered = await self.lifecycle.recover()
                for attempt in recovered:
                    if attempt.state == AttemptState.STARTING:
                        self.lifecycle.schedule_watchdog(
                            attempt.attempt_id
                        )
        reconciler = getattr(
            self.session_service,
            "reconciler",
            None,
        )
        recover_jobs = getattr(reconciler, "recover", None)
        if callable(recover_jobs):
            await recover_jobs()
        relay = self.desktop_relay
        start = getattr(relay, "start", None)
        if callable(start):
            await start()
            config = (
                self.job_repository.get_desktop_relay()
                if self.job_repository is not None
                and callable(
                    getattr(
                        self.job_repository,
                        "get_desktop_relay",
                        None,
                    )
                )
                else None
            )
            if config is not None and config.active_session_id is not None:
                session = (
                    self.session_repository.get(config.active_session_id)
                    if self.session_repository is not None
                    else None
                )
                if (
                    session is not None
                    and session.state == SessionState.READY
                    and callable(self.desktop_worker_factory)
                    and callable(getattr(relay, "activate", None))
                ):
                    try:
                        worker = self.desktop_worker_factory(session)
                        await relay.activate(
                            session.session_id,
                            worker,
                            config.profile_revision,
                        )
                    except Exception:
                        pass
        return recovered

    async def close(self):
        reconciler = getattr(
            self.session_service,
            "reconciler",
            None,
        )
        close = getattr(reconciler, "close", None)
        if callable(close):
            await close()
        relay_close = getattr(self.desktop_relay, "close", None)
        if callable(relay_close):
            await relay_close()
