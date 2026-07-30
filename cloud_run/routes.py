"""ComfyUI route registration for the preview-only API."""

from .settings import SettingsStore, SettingsValidationError, public_settings
from .vast import (
    OfferSearchConfigurationError,
    OfferSearchError,
    search_offers,
)


def register_routes():
    """Register the package routes when imported by a live ComfyUI host."""
    try:
        from aiohttp import web
        from server import PromptServer
    except (ImportError, AttributeError):
        return

    routes = PromptServer.instance.routes

    @routes.get("/cloud-run/api/settings")
    async def get_settings(_request):
        return web.json_response(public_settings(SettingsStore().load()))

    @routes.put("/cloud-run/api/settings")
    async def put_settings(request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body.", "preview_only": True},
                status=400,
            )

        try:
            settings = SettingsStore().update(payload)
        except SettingsValidationError as error:
            return web.json_response(
                {"error": str(error), "preview_only": True},
                status=400,
            )
        except OSError:
            return web.json_response(
                {"error": "Settings could not be saved.", "preview_only": True},
                status=500,
            )
        return web.json_response(public_settings(settings))

    @routes.post("/cloud-run/api/offers")
    async def post_offers(_request):
        settings = SettingsStore().load()
        try:
            offers = await search_offers(
                settings.get("api_key"),
                max_price_per_hour=settings["max_price_per_hour"],
                min_vram_gb=settings["min_vram_gb"],
            )
        except OfferSearchConfigurationError:
            return web.json_response(
                {
                    "error": "Vast API key is not configured.",
                    "preview_only": True,
                },
                status=400,
            )
        except OfferSearchError:
            return web.json_response(
                {
                    "error": "Vast offer search is unavailable.",
                    "preview_only": True,
                },
                status=502,
            )
        except Exception:
            return web.json_response(
                {
                    "error": "Vast offer search is unavailable.",
                    "preview_only": True,
                },
                status=502,
            )
        return web.json_response({"offers": offers, "preview_only": True})
