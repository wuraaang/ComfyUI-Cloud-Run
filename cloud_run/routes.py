"""Allowlisted same-origin routes for the managed Cloud Run lifecycle."""

import json
import os
from pathlib import Path
import math
import re
import stat

from .artifacts import GIB
from .agent_bridge import AgentBridge
from .capture import CaptureValidationError, certified_bootstrap_workflow
from .certified_baseline import (
    CertifiedBaselineResolution,
    CertifiedBaselineResolver,
    CertifiedBaselineUnavailable,
)
from .comfy_host import ComfyHost, FORBIDDEN_TREE, HostCompatibilityError
from .dependency_repository import (
    DependencyRepository,
    MappingValidationError,
)
from .desktop_relay import DesktopRelay, DesktopRelayError
from .desktop_profile import DesktopProfileStore
from .job_repository import JobRepository
from .huggingface import HuggingFaceClient
from .lifecycle import CloudRunLifecycle
from .model_sources import WorkflowModelSourceResolver
from .models import SessionState
from .offers import HostBlacklist
from .repository import (
    AttemptRepository,
    PaidRentalConflict,
    SessionRepository,
)
from .registry import RegistryClient
from .resolver import DependencyResolver
from .r2 import R2TransferError, R2ValidationError
from .relay import (
    ArtifactVerificationError,
    LocalRelay,
    RelayError,
)
from .readiness import (
    ControllerReadinessProbe,
    LocalExecutionGuard,
    ReadinessValidator,
)
from .service import (
    AttemptNotFound,
    CloudRunService,
    CloudRunValidationError,
    QuoteUnavailable,
    SessionNotFound,
    VastProvider,
)
from .session_service import (
    IncompatibleSession,
    SessionBusy,
    SessionService,
    SessionServiceError,
    _stored_manifest,
    _transfer_catalog,
)
from .settings import (
    SettingsStore,
    SettingsValidationError,
    public_settings,
    resolve_data_directory,
)
from .vast import (
    OfferSearchConfigurationError,
    OfferSearchError,
    VastError,
)
from .worker_release import (
    WorkerRelease,
    WorkerReleaseUnavailable,
    load_worker_release,
)
from .worker_client import WorkerClient


_MODEL_CATEGORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_BASE_ENVIRONMENT_BYTES = 40 * GIB
_PROVISION_PHASES = {
    "dependency_transfer",
    "model_transfer",
    "digest_verification",
    "comfyui_startup",
    "environment_validation",
    "ready",
}
_ARTIFACT_PROVISION_PHASES = {
    "dependency_transfer",
    "model_transfer",
    "digest_verification",
}
_MAX_BASELINE_DOWNLOAD_BYTES = 192 * 1024 * 1024
_BASELINE_DOWNLOAD_HOSTS = frozenset(
    {"cdn.comfy.org", "files.pythonhosted.org"}
)


async def _fetch_certified_baseline(url):
    """Fetch one locked baseline object without redirects or ambient auth."""
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise CertifiedBaselineUnavailable() from None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _BASELINE_DOWNLOAD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise CertifiedBaselineUnavailable()
    try:
        import aiohttp
    except ImportError:
        raise CertifiedBaselineUnavailable() from None
    timeout = aiohttp.ClientTimeout(total=180)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            trust_env=False,
        ) as session:
            async with session.get(
                url,
                allow_redirects=False,
                headers={"Accept": "application/octet-stream"},
            ) as response:
                if response.status != 200:
                    raise CertifiedBaselineUnavailable()
                length = response.content_length
                if length is not None and not 0 <= length <= (
                    _MAX_BASELINE_DOWNLOAD_BYTES
                ):
                    raise CertifiedBaselineUnavailable()
                body = bytearray()
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    body.extend(chunk)
                    if len(body) > _MAX_BASELINE_DOWNLOAD_BYTES:
                        raise CertifiedBaselineUnavailable()
                return bytes(body)
    except CertifiedBaselineUnavailable:
        raise
    except Exception:
        raise CertifiedBaselineUnavailable() from None


def _lexical_path(value):
    return Path(os.path.abspath(os.fspath(value)))


def _forbidden_path(path):
    try:
        path.relative_to(FORBIDDEN_TREE)
    except ValueError:
        return False
    return True


def _runtime_resolution_context(host):
    def context(capture):
        try:
            import folder_paths
        except ImportError:
            raise HostCompatibilityError(
                "ComfyUI asset metadata is unavailable."
            ) from None
        configured = getattr(folder_paths, "folder_names_and_paths", None)
        if not isinstance(configured, dict):
            raise HostCompatibilityError(
                "ComfyUI model roots are unavailable."
            )
        model_roots = {}
        model_filenames = {}
        for category, record in configured.items():
            if (
                not isinstance(category, str)
                or not _MODEL_CATEGORY.fullmatch(category)
                or not isinstance(record, (tuple, list))
                or not record
            ):
                continue
            raw_roots = record[0]
            if isinstance(raw_roots, (str, os.PathLike)):
                raw_roots = (raw_roots,)
            if not isinstance(raw_roots, (tuple, list)):
                continue
            roots = tuple(_lexical_path(root) for root in raw_roots)
            if any(_forbidden_path(root) for root in roots):
                raise HostCompatibilityError("Forbidden model root.")
            try:
                filenames = folder_paths.get_filename_list(category)
            except Exception:
                filenames = ()
            if (
                not isinstance(filenames, (tuple, list))
                or len(filenames) > 200_000
                or not all(isinstance(name, str) for name in filenames)
            ):
                continue
            model_roots[category] = roots
            model_filenames[category] = set(filenames)
        try:
            input_root = _lexical_path(folder_paths.get_input_directory())
        except Exception:
            raise HostCompatibilityError(
                "ComfyUI input root is unavailable."
            ) from None
        if _forbidden_path(input_root):
            raise HostCompatibilityError("Forbidden input root.")
        return {
            "metadata": host.file_input_metadata(
                capture,
                model_filenames=model_filenames,
            ),
            "model_roots": model_roots,
            "input_root": input_root,
            "source_mappings": {},
            "base_bytes": _BASE_ENVIRONMENT_BYTES,
        }

    return context


def _runtime_output_root(data_directory):
    try:
        import folder_paths
    except ImportError:
        fallback = Path(data_directory) / "local-outputs"
        fallback.mkdir(mode=0o700, parents=True, exist_ok=True)
        return fallback
    try:
        configured = Path(folder_paths.get_output_directory())
        metadata = os.lstat(configured)
    except (AttributeError, OSError, TypeError):
        raise HostCompatibilityError(
            "ComfyUI output directory is unavailable."
        ) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise HostCompatibilityError(
            "ComfyUI output directory is unavailable."
        )
    return configured


class _RuntimeResolver:
    def __init__(
        self,
        dependency_repository,
        artifact_catalog=None,
        model_source_resolver=None,
        profile_store=None,
        baseline_resolver=None,
        baseline_fetcher=None,
        baseline_cache_root=None,
    ):
        self.dependency_repository = dependency_repository
        self.artifact_catalog = artifact_catalog
        self.model_source_resolver = model_source_resolver
        self.profile_store = profile_store
        self.baseline_resolver = baseline_resolver
        self.baseline_fetcher = baseline_fetcher
        self.baseline_cache_root = baseline_cache_root

    async def resolve_preflight(
        self,
        capture,
        *,
        explicit_output_allowance_bytes=None,
    ):
        host = ComfyHost.from_running_host()
        model_source_resolver = self.model_source_resolver
        if model_source_resolver is None:
            model_source_resolver = WorkflowModelSourceResolver(
                HuggingFaceClient()
            )
        context = _runtime_resolution_context(host)(capture)
        try:
            baseline_resolver = self.baseline_resolver
            if baseline_resolver is None:
                baseline_resolver = CertifiedBaselineResolver()
            fetcher = self.baseline_fetcher or _fetch_certified_baseline
            cache_root = self.baseline_cache_root
            if cache_root is None and self.profile_store is not None:
                cache_root = (
                    Path(self.profile_store.private_root)
                    / "certified-baseline"
                )
            if cache_root is None:
                raise CertifiedBaselineUnavailable()
            baseline = await baseline_resolver.resolve(
                fetcher=fetcher,
                cache_root=cache_root,
            )
            if not isinstance(baseline, CertifiedBaselineResolution):
                raise CertifiedBaselineUnavailable()
        except CertifiedBaselineUnavailable:
            raise MappingValidationError(
                "Certified Desktop baseline is unavailable."
            ) from None

        def profile_provider(observed_capture):
            if self.profile_store is None:
                return {
                    "ui_packages": baseline.ui_packages,
                    "custom_nodes": baseline.custom_nodes,
                    "local_artifacts": baseline.local_artifacts,
                    "profile": None,
                    "minimum_vram_gb": 0.0,
                }
            profile = self.profile_store.capture(
                user_root=host.comfy_root / "user",
                profile_name="default",
                input_root=context["input_root"],
                bootstrap_workflow=certified_bootstrap_workflow(
                    observed_capture
                ),
                ui_packages=baseline.ui_packages,
                ui_assets=(),
            )
            return {
                "ui_packages": baseline.ui_packages,
                "custom_nodes": baseline.custom_nodes,
                "local_artifacts": baseline.local_artifacts,
                "profile": profile.manifest_spec(),
                "minimum_vram_gb": 0.0,
            }

        resolver = DependencyResolver(
            host=host,
            repository=self.dependency_repository,
            registry=RegistryClient(),
            cache_catalog=self.artifact_catalog,
            resolution_context=context,
            model_source_resolver=model_source_resolver,
            profile_provider=profile_provider,
        )
        return await resolver.resolve_preflight(
            capture,
            explicit_output_allowance_bytes=(
                explicit_output_allowance_bytes
            ),
        )

    def register_agent_suggestion(self, payload):
        resolver = DependencyResolver(
            host=None,
            repository=self.dependency_repository,
            registry=None,
        )
        return resolver.register_agent_suggestion(payload)


def build_service():
    data_directory = resolve_data_directory()
    settings_store = SettingsStore(data_directory)
    repository = AttemptRepository(data_directory / "attempts.sqlite3")
    session_repository = SessionRepository(
        data_directory / "attempts.sqlite3"
    )
    job_repository = JobRepository(data_directory / "attempts.sqlite3")
    dependency_repository = DependencyRepository(
        data_directory / "attempts.sqlite3"
    )
    profile_store = DesktopProfileStore(
        repository=job_repository,
        private_root=data_directory / "profiles",
    )
    blacklist = HostBlacklist(data_directory / "host-blacklist.json")
    provider = VastProvider()
    try:
        release = load_worker_release(
            data_directory / "worker-release.json"
        )
    except WorkerReleaseUnavailable:
        release = None
    lifecycle = CloudRunLifecycle(
        settings_store,
        repository,
        provider=provider,
        blacklist=blacklist,
        release=release,
        session_repository=session_repository,
    )
    service = CloudRunService(
        settings_store,
        repository,
        job_repository=job_repository,
        provider=provider,
        blacklist=blacklist,
        lifecycle=lifecycle,
        session_repository=session_repository,
        release=release,
    )
    resolver = _RuntimeResolver(
        dependency_repository,
        artifact_catalog=job_repository,
        profile_store=profile_store,
    )
    output_root = _runtime_output_root(data_directory)

    def worker_factory(session):
        return WorkerClient(
            base_url=session.worker_base_url,
            provider_token=session.provider_token,
            session_id=session.session_id,
            session_secret=bytes.fromhex(session.session_secret_hex),
        )

    def relay_factory(worker, _session):
        return LocalRelay(
            worker=worker,
            repository=job_repository,
            private_root=data_directory / "relay",
            output_root=output_root,
        )

    session_service_ref = {}

    def agent_bridge_journal(session_id, code, diagnostic):
        session_service = session_service_ref.get("service")
        if session_service is None:
            return False
        return session_service.record_agent_bridge_issue(
            session_id,
            code,
            diagnostic,
        )

    agent_bridge = AgentBridge(journal=agent_bridge_journal)
    service.session_service = SessionService(
        job_repository=job_repository,
        session_repository=session_repository,
        resolver=resolver,
        offer_search=service._search_without_preflight,
        mapping_repository=dependency_repository,
        release=release,
        worker_factory=worker_factory,
        relay_factory=relay_factory,
        lifecycle=lifecycle,
        profile_store=profile_store,
        agent_bridge=agent_bridge,
    )
    session_service_ref["service"] = service.session_service
    lifecycle.session_service = service.session_service
    service.relay = LocalRelay(
        worker=None,
        repository=job_repository,
        private_root=data_directory / "relay",
        output_root=output_root,
    )
    service.desktop_worker_factory = worker_factory
    service.desktop_profile_store = profile_store
    service.agent_bridge = agent_bridge
    service.desktop_relay = DesktopRelay(
        repository=job_repository,
        worker_factory=worker_factory,
        native_prompt=service.session_service.prepare_native_prompt,
        continue_guard=service.session_service._raise_if_destroy_requested,
        agent_bridge=agent_bridge,
        local_comfy_root=lambda: str(
            ComfyHost.from_running_host().comfy_root
        ),
    )
    service.session_service.desktop_relay = service.desktop_relay
    if release is not None:
        local_execution_guard = LocalExecutionGuard()

        async def inventory_probe(session):
            settings = settings_store.load()
            api_key = (
                settings.get("api_key")
                if isinstance(settings, dict)
                else None
            )
            if not api_key or session.instance_id is None:
                return None
            return await provider.get_instance(api_key, session.instance_id)

        def required_class_types(manifest):
            capture = job_repository.get_capture_by_prompt_digest(
                manifest.prompt_digest
            )
            if capture is None:
                return ()
            required = set(capture.executable_class_types)
            for node in manifest.custom_nodes:
                required.update(node.provided_class_types)
            return tuple(sorted(required))

        readiness_probe = ControllerReadinessProbe(
            worker_factory=worker_factory,
            inventory_probe=inventory_probe,
            desktop_relay=service.desktop_relay,
            release=release,
            required_class_types=required_class_types,
            local_execution_counter=local_execution_guard.count,
            continue_guard=(
                service.session_service._raise_if_destroy_requested
            ),
        )
        service.session_service.readiness_validator = ReadinessValidator(
            probe=readiness_probe,
            worker_release_digest=release.worker_archive_sha256,
            relay_origin=lambda: service.desktop_relay.status().url,
        )
        service.local_execution_guard = local_execution_guard
    return service


def _public_filename(path, fallback):
    try:
        name = Path(str(path)).name
    except (TypeError, ValueError):
        return str(fallback)
    if (
        not name
        or name in {".", ".."}
        or len(name.encode("utf-8")) > 1024
        or any(ord(character) < 32 for character in name)
    ):
        return str(fallback)
    return name


def _job_payload(service, job):
    payload = job.public_payload()
    payload["execution_status"] = payload.get("execution_state")
    payload["harvest_status"] = payload.get("harvest_state")
    repository = getattr(service, "job_repository", None)
    if not isinstance(repository, JobRepository):
        return payload
    previews = []
    outputs = []
    transfers = repository.list_transfers(job.job_id)
    for transfer in transfers:
        if transfer.direction != "download":
            continue
        if transfer.artifact_id.startswith("preview:"):
            if transfer.state.value == "verified":
                previews.append(
                    {
                        "id": transfer.artifact_id.removeprefix(
                            "preview:"
                        ),
                        "state": "verified",
                        "size_bytes": transfer.expected_size,
                    }
                )
            continue
        outputs.append(
            {
                "id": transfer.artifact_id,
                "state": (
                    "local_verified"
                    if transfer.state.value == "verified"
                    else transfer.state.value
                ),
                "filename": (
                    _public_filename(
                        transfer.private_path,
                        transfer.artifact_id,
                    )
                    if transfer.state.value == "verified"
                    else transfer.artifact_id
                ),
                "size_bytes": transfer.expected_size,
                "transferred_bytes": transfer.offset,
                "sha256": transfer.sha256,
                "node_id": transfer.source_node_id,
                "local_verified": transfer.state.value == "verified",
            }
        )
    payload["previews"] = previews
    payload["outputs"] = outputs
    payload["transfers"] = [
        {
            "id": transfer.artifact_id,
            "direction": transfer.direction,
            "state": transfer.state.value,
            "transferred_bytes": transfer.offset,
            "size_bytes": transfer.expected_size,
        }
        for transfer in transfers
    ]
    node_titles = {}
    try:
        capture = json.loads(job.capture_json)
        workflow = capture.get("workflow", {})
        nodes = workflow.get("nodes", [])
        if isinstance(nodes, list) and len(nodes) <= 100_000:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if not isinstance(node_id, (str, int)):
                    continue
                identifier = str(node_id)
                if not _MODEL_CATEGORY.fullmatch(identifier):
                    continue
                title = node.get("title")
                if (
                    isinstance(title, str)
                    and title
                    and len(title.encode("utf-8")) <= 512
                    and not any(ord(character) < 32 for character in title)
                ):
                    node_titles[identifier] = title
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        node_titles = {}
    current_node_id = None
    progress = None
    progress_text = None
    for event in repository.list_events(job.job_id, 0):
        if event.event_type == "executing":
            node_id = event.payload.get("node_id")
            if isinstance(node_id, str) and _MODEL_CATEGORY.fullmatch(node_id):
                current_node_id = node_id
        elif event.event_type == "progress":
            value = event.payload.get("value")
            maximum = event.payload.get(
                "max",
                event.payload.get("total"),
            )
            progress = {
                "value": (
                    value
                    if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    else None
                ),
                "max": (
                    maximum
                    if isinstance(maximum, (int, float))
                    and not isinstance(maximum, bool)
                    and math.isfinite(maximum)
                    else None
                ),
            }
        elif event.event_type == "progress_text":
            text = event.payload.get("text")
            if isinstance(text, str):
                progress_text = text
        elif event.event_type in {
            "execution_success",
            "execution_error",
            "execution_interrupted",
        }:
            current_node_id = None
    payload["current_node"] = (
        {
            "id": current_node_id,
            "title": node_titles.get(current_node_id),
        }
        if current_node_id is not None
        else None
    )
    payload["progress"] = progress
    payload["progress_text"] = (
        progress_text
        or (
            "Execution succeeded — retrieving outputs."
            if job.execution_state.value == "succeeded"
            and job.harvest_state.value in {"running", "failed"}
            else None
        )
    )
    payload["last_sequence"] = repository.last_event_sequence(
        job.job_id
    )
    return payload


def _session_payload(session, service=None):
    payload = session.public_payload()
    if service is None:
        return payload
    try:
        now = float(service.clock())
    except (AttributeError, TypeError, ValueError):
        now = session.updated_at
    if not math.isfinite(now) or now < 0:
        now = session.updated_at
    public_state = payload
    if session.state in {
        SessionState.PREFLIGHT,
        SessionState.OFFER_SELECTED,
    }:
        elapsed = 0.0
    else:
        billing_active = (
            session.state
            not in {
                SessionState.DESTROYED,
                SessionState.FAILED,
            }
            or public_state["billing_may_continue"]
        )
        end = (
            now if billing_active else session.updated_at
        )
        elapsed = max(0.0, end - session.created_at)
    payload["elapsed_seconds"] = elapsed
    payload["approximate_spend"] = (
        session.quote.dph_total * elapsed / 3600
        if session.quote is not None
        else None
    )
    alerts = getattr(
        getattr(service, "session_service", None),
        "alerts",
        None,
    )
    try:
        payload["deadline_alerts"] = (
            list(alerts(session, now=now))
            if callable(alerts)
            else []
        )
    except Exception:
        payload["deadline_alerts"] = []

    readiness = getattr(
        getattr(service, "session_service", None),
        "desktop_readiness",
        None,
    )
    try:
        desktop = (
            readiness(session.session_id)
            if callable(readiness)
            else {
                "desktop_ready": False,
                "readiness_report": None,
            }
        )
        if not isinstance(desktop, dict) or set(desktop) != {
            "desktop_ready",
            "readiness_report",
        }:
            raise ValueError("Invalid readiness payload.")
        payload.update(desktop)
    except Exception:
        payload["desktop_ready"] = False
        payload["readiness_report"] = None

    repository = getattr(service, "job_repository", None)
    if not isinstance(repository, JobRepository):
        seconds_without_progress = max(
            0.0,
            now - session.updated_at,
        )
        payload["current_job"] = None
        payload["history"] = []
        payload["provisioning"] = {
            "phase": session.state.value,
            "current_model": None,
            "transferred_bytes": 0,
            "total_bytes": 0,
            "installed_units": 0,
            "validated_units": 0,
            "seconds_without_progress": seconds_without_progress,
            "stall_budget_seconds": 600,
            "stall_active": seconds_without_progress >= 600,
        }
        return payload

    jobs = repository.list_jobs(session.session_id)[-100:]
    job_payloads = [_job_payload(service, job) for job in jobs]
    active_states = {
        "captured",
        "resolving",
        "queued",
        "running",
        "harvesting",
    }
    current = next(
        (
            item
            for item in reversed(job_payloads)
            if item["status"] in active_states
        ),
        job_payloads[-1] if job_payloads else None,
    )
    payload["current_job"] = current
    payload["history"] = job_payloads

    transfer_records = []
    for transfer_job_id in (
        "bootstrap:" + session.session_id,
        "recovery:" + session.session_id,
    ):
        transfer_records.extend(
            repository.list_transfers(transfer_job_id)
        )
    if current is not None:
        transfer_records.extend(
            repository.list_transfers(current["job_id"])
        )
    installed = repository.installed_set(session.session_id)
    manifest = None
    catalog = {}
    try:
        manifest = _stored_manifest(
            repository,
            session.manifest_digest,
        )
        catalog = _transfer_catalog(manifest)
    except Exception:
        manifest = None
        catalog = {}

    exact_total = (
        sum(artifact.size_bytes for artifact in catalog.values())
        if manifest is not None
        else sum(transfer.expected_size for transfer in transfer_records)
    )
    local_offsets = {}
    if manifest is not None:
        for transfer in transfer_records:
            artifact = catalog.get(transfer.artifact_id)
            if (
                artifact is None
                or artifact.source.kind != "local-upload"
                or transfer.direction != "upload"
                or transfer.expected_size != artifact.size_bytes
                or transfer.sha256 != artifact.sha256
                or isinstance(transfer.offset, bool)
                or not isinstance(transfer.offset, int)
                or not 0 <= transfer.offset <= artifact.size_bytes
            ):
                continue
            local_offsets[artifact.artifact_id] = max(
                local_offsets.get(artifact.artifact_id, 0),
                transfer.offset,
            )

    phase = session.state.value
    current_model = None
    worker_bytes = 0
    last_progress_at = session.updated_at
    try:
        transaction = repository.latest_provision_transaction(
            session.session_id
        )
    except Exception:
        transaction = None
    progress_valid = False
    if transaction is not None and manifest is not None:
        current_artifact = catalog.get(
            transaction.current_dependency_id
        )
        progress_valid = (
            transaction.manifest_digest == manifest.digest
            and transaction.phase in _PROVISION_PHASES
            and not isinstance(transaction.transferred_bytes, bool)
            and isinstance(transaction.transferred_bytes, int)
            and not isinstance(transaction.total_bytes, bool)
            and isinstance(transaction.total_bytes, int)
            and transaction.total_bytes == exact_total
            and 0 <= transaction.transferred_bytes <= exact_total
            and (
                (
                    transaction.phase in _ARTIFACT_PROVISION_PHASES
                    and current_artifact is not None
                )
                or (
                    transaction.phase not in _ARTIFACT_PROVISION_PHASES
                    and transaction.current_dependency_id is None
                )
            )
            and not (
                transaction.phase == "model_transfer"
                and current_artifact.kind != "model"
            )
            and not (
                transaction.phase == "dependency_transfer"
                and current_artifact.kind == "model"
            )
            and (
                transaction.phase != "ready"
                or (
                    transaction.state == "ready"
                    and transaction.transferred_bytes == exact_total
                )
            )
            and isinstance(transaction.last_progress_at, (int, float))
            and not isinstance(transaction.last_progress_at, bool)
            and math.isfinite(transaction.last_progress_at)
            and transaction.last_progress_at >= 0
        )
        if progress_valid:
            phase = transaction.phase
            worker_bytes = transaction.transferred_bytes
            last_progress_at = transaction.last_progress_at
            if (
                current_artifact is not None
                and current_artifact.kind == "model"
            ):
                current_model = current_artifact.logical_name

    if manifest is not None:
        transferred_bytes = min(
            exact_total,
            worker_bytes + sum(local_offsets.values()),
        )
    else:
        transferred_bytes = sum(
            transfer.offset for transfer in transfer_records
        )
    provision_ready = (
        transaction is not None
        and progress_valid
        and phase == "ready"
        and transaction.state == "ready"
    )
    seconds_without_progress = (
        None
        if provision_ready
        else max(0.0, now - last_progress_at)
    )
    payload["provisioning"] = {
        "phase": phase,
        "current_model": current_model,
        "transferred_bytes": transferred_bytes,
        "total_bytes": exact_total,
        "installed_units": len(installed),
        "validated_units": (
            len(installed)
            if provision_ready
            or session.state
            in {
                SessionState.READY,
                SessionState.RUNNING,
                SessionState.HARVESTING,
            }
            else 0
        ),
        "seconds_without_progress": seconds_without_progress,
        "stall_budget_seconds": 600,
        "stall_active": (
            seconds_without_progress is not None
            and seconds_without_progress >= 600
        ),
    }
    return payload


_ACTIVE_SESSION_DETAILS_ERROR = (
    "Active session details are temporarily unavailable."
)


def _minimal_session_safety_card(session):
    error = session.sanitized_error
    if (
        error is not None
        and (
            not isinstance(error, str)
            or len(error.encode("utf-8")) > 500
            or any(ord(character) < 32 for character in error)
        )
    ):
        error = None
    rate = session.quote.dph_total if session.quote is not None else None
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not math.isfinite(rate)
        or rate < 0
    ):
        rate = None
    return {
        "session_id": session.session_id,
        "instance_id": session.instance_id,
        "status": session.state.value,
        "billing_may_continue": session.billing_may_continue,
        "can_destroy": session.can_destroy,
        "rate": rate,
        "error": error,
    }


def _active_session_payloads(service):
    repository = getattr(service, "session_repository", None)
    if not isinstance(repository, SessionRepository):
        return [], None
    try:
        sessions = repository.list_recoverable()[-20:]
    except Exception:
        return [], _ACTIVE_SESSION_DETAILS_ERROR
    payloads = []
    detail_error = None
    for session in sessions:
        try:
            payload = _session_payload(session, service)
            if not isinstance(payload, dict):
                raise ValueError("Invalid active session payload.")
        except Exception:
            payload = _minimal_session_safety_card(session)
            detail_error = _ACTIVE_SESSION_DETAILS_ERROR
        payloads.append(payload)
    return payloads, detail_error


async def _request_payload(request, *, allowed, required):
    try:
        payload = await request.json()
    except Exception:
        raise CloudRunValidationError("Invalid JSON body.") from None
    if (
        not isinstance(payload, dict)
        or set(payload) - set(allowed)
        or not set(required).issubset(payload)
    ):
        raise CloudRunValidationError("Invalid request body.")
    return payload


def register_routes(service_factory=None):
    """Register package routes when imported by a live ComfyUI host."""
    try:
        from aiohttp import web
        from server import PromptServer
    except (ImportError, AttributeError):
        return

    prompt_server = PromptServer.instance
    routes = prompt_server.routes
    if service_factory is not None:
        make_service = service_factory
    else:
        shared_service = None

        def make_service():
            nonlocal shared_service
            if shared_service is None:
                shared_service = build_service()
            return shared_service

    def service_error(error):
        if isinstance(
            error,
            (PaidRentalConflict, SessionBusy, IncompatibleSession),
        ):
            return web.json_response({"error": str(error)}, status=409)
        if isinstance(
            error,
            (
                CaptureValidationError,
                CloudRunValidationError,
                SessionServiceError,
                MappingValidationError,
            ),
        ):
            return web.json_response({"error": str(error)}, status=400)
        if isinstance(error, R2ValidationError):
            return web.json_response({"error": str(error)}, status=400)
        if isinstance(error, R2TransferError):
            return web.json_response(
                {"error": "R2 cache transfer is unavailable."},
                status=502,
            )
        if isinstance(error, DesktopRelayError):
            return web.json_response(
                {"error": "ComfyUI Vast Desktop relay is unavailable."},
                status=503,
            )
        if isinstance(error, AttemptNotFound):
            return web.json_response({"error": str(error)}, status=404)
        if isinstance(error, SessionNotFound):
            return web.json_response({"error": str(error)}, status=404)
        if isinstance(error, QuoteUnavailable):
            return web.json_response({"error": str(error)}, status=409)
        if isinstance(error, WorkerReleaseUnavailable):
            return web.json_response({"error": str(error)}, status=503)
        if isinstance(error, OfferSearchConfigurationError):
            return web.json_response(
                {"error": "Vast API key is not configured."},
                status=400,
            )
        if isinstance(error, (OfferSearchError, VastError)):
            return web.json_response(
                {"error": "Vast offer search is unavailable."},
                status=502,
            )
        return web.json_response(
            {"error": "Cloud Run is temporarily unavailable."},
            status=500,
        )

    def owned_job(service, session_id, job_id):
        repository = getattr(service, "job_repository", None)
        if (
            not isinstance(repository, JobRepository)
            or not isinstance(session_id, str)
            or not _MODEL_CATEGORY.fullmatch(session_id)
            or not isinstance(job_id, str)
            or not _MODEL_CATEGORY.fullmatch(job_id)
        ):
            return None
        job = repository.get_job(job_id)
        if job is None or job.session_id != session_id:
            return None
        return job

    def event_cursor(request):
        query = getattr(request, "query", {})
        try:
            keys = set(query)
        except (TypeError, ValueError):
            raise CloudRunValidationError(
                "Invalid event cursor."
            ) from None
        if not keys:
            return 0
        if keys != {"after_sequence"}:
            raise CloudRunValidationError("Invalid event cursor.")
        try:
            values = query.getall("after_sequence")
        except AttributeError:
            values = [query.get("after_sequence")]
        if (
            len(values) != 1
            or not isinstance(values[0], str)
            or not re.fullmatch(r"0|[1-9][0-9]{0,19}", values[0])
        ):
            raise CloudRunValidationError("Invalid event cursor.")
        return int(values[0])

    def browser_settings(settings):
        payload = public_settings(settings)
        try:
            service = make_service()
        except Exception:
            service = None
        release = getattr(service, "release", None)
        payload["worker_release"] = (
            release.to_record()
            if isinstance(release, WorkerRelease)
            else None
        )
        active_sessions, active_sessions_error = (
            _active_session_payloads(service)
            if service is not None
            else ([], _ACTIVE_SESSION_DETAILS_ERROR)
        )
        payload["active_sessions"] = active_sessions
        payload["active_sessions_error"] = active_sessions_error
        return payload

    @routes.get("/cloud-run/api/settings")
    async def get_settings(_request):
        return web.json_response(
            browser_settings(SettingsStore().load())
        )

    @routes.get("/cloud-run/api/desktop-context")
    async def get_local_desktop_context(_request):
        return web.json_response({"role": "local"})

    @routes.get("/cloud-run/api/desktop-setup")
    async def get_desktop_setup(_request):
        try:
            payload = make_service().desktop_setup()
        except Exception as error:
            return service_error(error)
        return web.json_response(payload)

    @routes.put("/cloud-run/api/settings")
    async def put_settings(request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body."},
                status=400,
            )

        try:
            settings = SettingsStore().update(payload)
        except SettingsValidationError as error:
            return web.json_response({"error": str(error)}, status=400)
        except OSError:
            return web.json_response(
                {"error": "Settings could not be saved."},
                status=500,
            )
        return web.json_response(browser_settings(settings))

    @routes.post("/cloud-run/api/captures")
    async def post_capture(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"workflow", "output", "queue_options"},
                required={"workflow", "output", "queue_options"},
            )
            capture = await make_service().capture(payload)
        except Exception as error:
            return service_error(error)
        return web.json_response(
            {
                "capture_id": capture.capture_id,
                "prompt_digest": capture.prompt_digest,
                "status": "captured",
            }
        )

    @routes.post("/cloud-run/api/preflights")
    async def post_preflight(request):
        try:
            payload = await _request_payload(
                request,
                allowed={
                    "capture_id",
                    "explicit_output_allowance_bytes",
                },
                required={"capture_id"},
            )
            result = await make_service().preflight(
                payload["capture_id"],
                explicit_output_allowance_bytes=payload.get(
                    "explicit_output_allowance_bytes"
                ),
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(result.public_payload())

    @routes.put("/cloud-run/api/mappings/{mapping_id}")
    async def put_mapping(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"candidate_digest"},
                required={"candidate_digest"},
            )
            result = await make_service().approve_mapping(
                request.match_info.get("mapping_id", ""),
                payload["candidate_digest"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(result)

    @routes.post(
        "/cloud-run/api/integrations/agent-panel/suggestions"
    )
    async def post_agent_suggestion(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"class_type", "candidate"},
                required={"class_type", "candidate"},
            )
            result = await make_service().register_agent_suggestion(
                payload
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(result, status=202)

    @routes.post("/cloud-run/api/cache/artifacts/{artifact_id}")
    async def post_cache_artifact(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"acknowledged"},
                required={"acknowledged"},
            )
            if payload != {"acknowledged": True}:
                raise CloudRunValidationError(
                    "Cache population requires explicit acknowledgement."
                )
            result = await make_service().populate_cache(
                request.match_info.get("artifact_id", ""),
                acknowledged=True,
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(result.public_payload())

    @routes.post("/cloud-run/api/offers")
    async def post_offers(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"preflight_id"},
                required={"preflight_id"},
            )
            offers = await make_service().search(payload["preflight_id"])
        except Exception as error:
            return service_error(error)
        return web.json_response({"offers": offers})

    @routes.post("/cloud-run/api/sessions")
    async def post_session(request):
        service = make_service()
        try:
            payload = await _request_payload(
                request,
                allowed={
                    "preflight_id",
                    "offer_id",
                    "idempotency_key",
                    "deadline",
                    "max_instance_creates",
                    "estimate_digest",
                },
                required={
                    "preflight_id",
                    "offer_id",
                    "idempotency_key",
                    "deadline",
                    "max_instance_creates",
                    "estimate_digest",
                },
            )
            if (
                not isinstance(payload["estimate_digest"], str)
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    payload["estimate_digest"],
                ) is None
            ):
                raise CloudRunValidationError(
                    "A valid readiness estimate is required."
                )
            session = await service.preview_session(
                preflight_id=payload["preflight_id"],
                offer_id=payload["offer_id"],
                idempotency_key=payload["idempotency_key"],
                deadline=payload["deadline"],
                max_instance_creates=payload["max_instance_creates"],
                estimate_digest=payload["estimate_digest"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_session_payload(session, service))

    @routes.post("/cloud-run/api/sessions/{session_id}/confirm")
    async def post_session_confirm(request):
        service = make_service()
        try:
            payload = await _request_payload(
                request,
                allowed={
                    "idempotency_key",
                    "estimate_digest",
                    "accepted_longer_estimate",
                    "preflight_id",
                },
                required={
                    "idempotency_key",
                    "estimate_digest",
                    "accepted_longer_estimate",
                    "preflight_id",
                },
            )
            if (
                not isinstance(payload["estimate_digest"], str)
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    payload["estimate_digest"],
                ) is None
                or type(payload["accepted_longer_estimate"]) is not bool
            ):
                raise CloudRunValidationError(
                    "A valid readiness confirmation is required."
                )
            session = await service.confirm_session(
                request.match_info.get("session_id", ""),
                idempotency_key=payload["idempotency_key"],
                estimate_digest=payload["estimate_digest"],
                accepted_longer_estimate=(
                    payload["accepted_longer_estimate"]
                ),
                current_preflight_id=payload["preflight_id"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_session_payload(session, service))

    @routes.get("/cloud-run/api/sessions/{session_id}")
    async def get_session(request):
        service = make_service()
        try:
            session = await service.refresh_session(
                request.match_info.get("session_id", "")
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_session_payload(session, service))

    @routes.get("/cloud-run/api/sessions/{session_id}/profile")
    async def get_session_profile(request):
        try:
            payload = await make_service().session_profile(
                request.match_info.get("session_id", "")
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(payload)

    @routes.post(
        "/cloud-run/api/sessions/{session_id}/profile/conflicts/{conflict_id}"
    )
    async def post_session_profile_conflict(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"winner"},
                required={"winner"},
            )
            if payload["winner"] not in {"local", "cloud_vast"}:
                raise CloudRunValidationError(
                    "Invalid Desktop profile conflict choice."
                )
            result = await make_service().resolve_session_profile_conflict(
                request.match_info.get("session_id", ""),
                request.match_info.get("conflict_id", ""),
                winner=payload["winner"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(result)

    @routes.post(
        "/cloud-run/api/sessions/{session_id}/desktop-relay"
    )
    async def post_session_desktop_relay(request):
        service = make_service()
        try:
            await _request_payload(
                request,
                allowed=set(),
                required=set(),
            )
            status = await service.activate_desktop_relay(
                request.match_info.get("session_id", "")
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(status.public_payload())

    @routes.delete(
        "/cloud-run/api/sessions/{session_id}/desktop-relay"
    )
    async def delete_session_desktop_relay(request):
        service = make_service()
        try:
            status = await service.deactivate_desktop_relay(
                request.match_info.get("session_id", "")
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(status.public_payload())

    @routes.post("/cloud-run/api/sessions/{session_id}/jobs")
    async def post_session_job(request):
        service = make_service()
        try:
            payload = await _request_payload(
                request,
                allowed={"capture_id", "idempotency_key"},
                required={"capture_id", "idempotency_key"},
            )
            job = await service.submit_job(
                request.match_info.get("session_id", ""),
                capture_id=payload["capture_id"],
                idempotency_key=payload["idempotency_key"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_job_payload(service, job))

    @routes.get(
        "/cloud-run/api/sessions/{session_id}/jobs/{job_id}"
    )
    async def get_session_job(request):
        service = make_service()
        try:
            job = service.get_job(
                request.match_info.get("session_id", ""),
                request.match_info.get("job_id", ""),
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_job_payload(service, job))

    @routes.post(
        (
            "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/"
            "harvest"
        )
    )
    async def post_session_job_harvest(request):
        service = make_service()
        try:
            await _request_payload(
                request,
                allowed=set(),
                required=set(),
            )
            job = await service.retry_harvest(
                request.match_info.get("session_id", ""),
                request.match_info.get("job_id", ""),
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_job_payload(service, job))

    @routes.put("/cloud-run/api/sessions/{session_id}/deadline")
    async def put_session_deadline(request):
        service = make_service()
        try:
            payload = await _request_payload(
                request,
                allowed={"action", "acknowledged"},
                required={"action"},
            )
            session = await service.update_session_deadline(
                request.match_info.get("session_id", ""),
                payload,
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_session_payload(session, service))

    @routes.post(
        "/cloud-run/api/sessions/{session_id}/destroy-review"
    )
    async def post_session_destroy_review(request):
        try:
            await _request_payload(
                request,
                allowed=set(),
                required=set(),
            )
            review = await make_service().review_session_destroy(
                request.match_info.get("session_id", "")
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(review.public_payload())

    @routes.delete("/cloud-run/api/sessions/{session_id}")
    async def delete_session(request):
        service = make_service()
        try:
            payload = await _request_payload(
                request,
                allowed={
                    "review_token",
                    "acknowledge_data_loss",
                },
                required={
                    "review_token",
                    "acknowledge_data_loss",
                },
            )
            session = await service.destroy_session(
                request.match_info.get("session_id", ""),
                payload,
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_session_payload(session, service))

    @routes.get(
        "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/events"
    )
    async def get_job_events(request):
        service = make_service()
        session_id = request.match_info.get("session_id", "")
        job_id = request.match_info.get("job_id", "")
        job = owned_job(service, session_id, job_id)
        if job is None:
            return web.json_response(
                {"error": "Local job was not found."},
                status=404,
            )
        try:
            cursor = event_cursor(request)
            events = service.job_repository.list_events(job_id, cursor)
        except Exception as error:
            return service_error(error)
        return web.json_response(
            {
                "job_id": job_id,
                "events": [
                    {
                        "sequence": event.sequence,
                        "type": event.event_type,
                        "data": event.payload,
                        "created_at": event.created_at,
                    }
                    for event in events
                ],
                "last_sequence": (
                    events[-1].sequence if events else cursor
                ),
            }
        )

    @routes.get(
        (
            "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/"
            "previews/{preview_id}"
        )
    )
    async def get_job_preview(request):
        service = make_service()
        session_id = request.match_info.get("session_id", "")
        job_id = request.match_info.get("job_id", "")
        if owned_job(service, session_id, job_id) is None:
            return web.json_response(
                {"error": "Local media was not found."},
                status=404,
            )
        relay = getattr(service, "relay", None)
        if not isinstance(relay, LocalRelay):
            return web.json_response(
                {"error": "Local media is unavailable."},
                status=503,
            )
        try:
            media = relay.preview_content(
                job_id,
                request.match_info.get("preview_id", ""),
            )
        except RelayError:
            return web.json_response(
                {"error": "Local media was not found."},
                status=404,
            )
        return web.Response(
            body=media.content,
            headers={
                "Content-Type": media.mime_type,
                "Content-Length": str(len(media.content)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @routes.get(
        (
            "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/"
            "artifacts/{artifact_id}"
        )
    )
    async def get_job_artifact(request):
        service = make_service()
        session_id = request.match_info.get("session_id", "")
        job_id = request.match_info.get("job_id", "")
        if owned_job(service, session_id, job_id) is None:
            return web.json_response(
                {"error": "Local media was not found."},
                status=404,
            )
        relay = getattr(service, "relay", None)
        if not isinstance(relay, LocalRelay):
            return web.json_response(
                {"error": "Local media is unavailable."},
                status=503,
            )
        try:
            artifact = relay.published_artifact(
                job_id,
                request.match_info.get("artifact_id", ""),
            )
        except ArtifactVerificationError:
            return web.json_response(
                {"error": "Local media is unavailable."},
                status=503,
            )
        except RelayError:
            return web.json_response(
                {"error": "Local media was not found."},
                status=404,
            )
        return web.FileResponse(
            artifact.path,
            headers={
                "Content-Type": artifact.mime_type,
                "Content-Length": str(artifact.size_bytes),
                "ETag": '"' + artifact.sha256 + '"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    app = getattr(prompt_server, "app", None)
    startup = getattr(app, "on_startup", None)
    marker = "_comfyui_cloud_run_recovery_registered"
    if (
        startup is not None
        and hasattr(startup, "append")
        and not getattr(prompt_server, marker, False)
    ):
        async def recover_managed_attempts(_app):
            try:
                await make_service().recover()
            except Exception:
                return

        startup.append(recover_managed_attempts)
        setattr(prompt_server, marker, True)
    cleanup = getattr(app, "on_cleanup", None)
    cleanup_marker = "_comfyui_cloud_run_cleanup_registered"
    if (
        cleanup is not None
        and hasattr(cleanup, "append")
        and not getattr(prompt_server, cleanup_marker, False)
    ):
        async def close_managed_service(_app):
            try:
                close = getattr(make_service(), "close", None)
                if callable(close):
                    await close()
            except Exception:
                return

        cleanup.append(close_managed_service)
        setattr(prompt_server, cleanup_marker, True)
