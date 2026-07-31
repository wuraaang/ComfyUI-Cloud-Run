"""Allowlisted same-origin routes for the managed Cloud Run lifecycle."""

from .capture import CaptureValidationError
from .constants import OFFICIAL_TEMPLATE_ID, OFFICIAL_TEMPLATE_NAME
from .job_repository import JobRepository
from .lifecycle import CloudRunLifecycle
from .offers import HostBlacklist
from .repository import AttemptRepository
from .service import (
    AttemptNotFound,
    CloudRunService,
    CloudRunValidationError,
    QuoteUnavailable,
    VastProvider,
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


def build_service():
    data_directory = resolve_data_directory()
    settings_store = SettingsStore(data_directory)
    repository = AttemptRepository(data_directory / "attempts.sqlite3")
    job_repository = JobRepository(data_directory / "attempts.sqlite3")
    blacklist = HostBlacklist(data_directory / "host-blacklist.json")
    provider = VastProvider()
    lifecycle = CloudRunLifecycle(
        settings_store,
        repository,
        provider=provider,
        blacklist=blacklist,
    )
    return CloudRunService(
        settings_store,
        repository,
        job_repository=job_repository,
        provider=provider,
        blacklist=blacklist,
        lifecycle=lifecycle,
    )


def _attempt_payload(attempt):
    payload = attempt.public_payload()
    payload["official_template_id"] = OFFICIAL_TEMPLATE_ID
    payload["official_template_name"] = OFFICIAL_TEMPLATE_NAME
    return payload


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
        if isinstance(error, (CaptureValidationError, CloudRunValidationError)):
            return web.json_response({"error": str(error)}, status=400)
        if isinstance(error, AttemptNotFound):
            return web.json_response({"error": str(error)}, status=404)
        if isinstance(error, QuoteUnavailable):
            return web.json_response({"error": str(error)}, status=409)
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

    @routes.post("/cloud-run/api/offers")
    async def post_offers(_request):
        try:
            offers = await make_service().search()
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
