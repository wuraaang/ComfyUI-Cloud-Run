"""Allowlisted same-origin routes for the managed Cloud Run lifecycle."""

import os
from pathlib import Path
import re
import stat

from .artifacts import GIB
from .capture import CaptureValidationError
from .comfy_host import ComfyHost, FORBIDDEN_TREE, HostCompatibilityError
from .constants import OFFICIAL_TEMPLATE_ID, OFFICIAL_TEMPLATE_NAME
from .dependency_repository import (
    DependencyRepository,
    MappingValidationError,
)
from .job_repository import JobRepository
from .lifecycle import CloudRunLifecycle
from .offers import HostBlacklist
from .repository import AttemptRepository, SessionRepository
from .registry import RegistryClient
from .resolver import DependencyResolver
from .r2 import R2TransferError, R2ValidationError
from .relay import (
    ArtifactVerificationError,
    LocalRelay,
    RelayError,
)
from .service import (
    AttemptNotFound,
    CloudRunService,
    CloudRunValidationError,
    QuoteUnavailable,
    SessionNotFound,
    VastProvider,
)
from .session_service import SessionService, SessionServiceError
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
    WorkerReleaseUnavailable,
    load_worker_release,
)


_MODEL_CATEGORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_BASE_ENVIRONMENT_BYTES = 40 * GIB


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
                continue
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
    def __init__(self, dependency_repository):
        self.dependency_repository = dependency_repository

    async def resolve_preflight(
        self,
        capture,
        *,
        explicit_output_allowance_bytes=None,
    ):
        host = ComfyHost.from_running_host()
        resolver = DependencyResolver(
            host=host,
            repository=self.dependency_repository,
            registry=RegistryClient(),
            resolution_context=_runtime_resolution_context(host),
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
    resolver = _RuntimeResolver(dependency_repository)
    service.session_service = SessionService(
        job_repository=job_repository,
        resolver=resolver,
        offer_search=service._search_without_preflight,
        mapping_repository=dependency_repository,
        release=release,
    )
    service.relay = LocalRelay(
        worker=None,
        repository=job_repository,
        private_root=data_directory / "relay",
        output_root=_runtime_output_root(data_directory),
    )
    return service


def _attempt_payload(attempt):
    payload = attempt.public_payload()
    payload["official_template_id"] = OFFICIAL_TEMPLATE_ID
    payload["official_template_name"] = OFFICIAL_TEMPLATE_NAME
    return payload


def _session_payload(session):
    return session.public_payload()


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
    make_service = service_factory or build_service

    def service_error(error):
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

    @routes.get("/cloud-run/api/settings")
    async def get_settings(_request):
        return web.json_response(public_settings(SettingsStore().load()))

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
        return web.json_response(public_settings(settings))

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

    @routes.post("/cloud-run/api/quotes")
    async def post_quote(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"offer_id", "idempotency_key"},
                required={"offer_id", "idempotency_key"},
            )
            attempt = await make_service().preview_offer(
                offer_id=payload["offer_id"],
                idempotency_key=payload["idempotency_key"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_attempt_payload(attempt))

    @routes.post("/cloud-run/api/sessions")
    async def post_session(request):
        try:
            payload = await _request_payload(
                request,
                allowed={
                    "preflight_id",
                    "offer_id",
                    "idempotency_key",
                    "deadline",
                },
                required={
                    "preflight_id",
                    "offer_id",
                    "idempotency_key",
                    "deadline",
                },
            )
            session = await make_service().preview_session(
                preflight_id=payload["preflight_id"],
                offer_id=payload["offer_id"],
                idempotency_key=payload["idempotency_key"],
                deadline=payload["deadline"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_session_payload(session))

    @routes.post("/cloud-run/api/sessions/{session_id}/confirm")
    async def post_session_confirm(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"idempotency_key"},
                required={"idempotency_key"},
            )
            session = await make_service().confirm_session(
                request.match_info.get("session_id", ""),
                idempotency_key=payload["idempotency_key"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_session_payload(session))

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

    @routes.get("/cloud-run/api/attempts/{attempt_id}")
    async def get_attempt(request):
        try:
            attempt = await make_service().refresh(
                request.match_info.get("attempt_id", "")
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_attempt_payload(attempt))

    @routes.post("/cloud-run/api/attempts/{attempt_id}/confirm")
    async def post_confirm(request):
        try:
            payload = await _request_payload(
                request,
                allowed={"idempotency_key"},
                required={"idempotency_key"},
            )
            attempt = await make_service().confirm(
                request.match_info.get("attempt_id", ""),
                idempotency_key=payload["idempotency_key"],
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_attempt_payload(attempt))

    @routes.post("/cloud-run/api/attempts/{attempt_id}/cancel")
    async def post_cancel(request):
        try:
            attempt = await make_service().cancel(
                request.match_info.get("attempt_id", "")
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_attempt_payload(attempt))

    @routes.delete("/cloud-run/api/attempts/{attempt_id}")
    async def delete_attempt(request):
        try:
            attempt = await make_service().destroy(
                request.match_info.get("attempt_id", "")
            )
        except Exception as error:
            return service_error(error)
        return web.json_response(_attempt_payload(attempt))

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
