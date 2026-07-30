"""The single read-only Vast offer-search integration."""

import math

OFFER_SEARCH_URL = "https://console.vast.ai/api/v0/bundles/"
OFFER_SEARCH_LIMIT = 20
OFFER_TIMEOUT_SECONDS = 30
MIN_RELIABILITY = 0.95


class OfferSearchError(RuntimeError):
    """A sanitized Vast offer-search failure."""


class OfferSearchConfigurationError(OfferSearchError):
    """Offer search cannot run with the saved configuration."""


def build_search_payload(max_price_per_hour, min_vram_gb):
    """Build the fixed on-demand, one-GPU, verified search query."""
    return {
        "gpu_ram": {"gte": int(min_vram_gb) * 1024},
        "reliability": {"gte": MIN_RELIABILITY},
        "rentable": {"eq": True},
        "dph_total": {"lte": float(max_price_per_hour)},
        "num_gpus": {"eq": 1},
        "verified": {"eq": True},
        "type": "ondemand",
        "limit": OFFER_SEARCH_LIMIT,
    }


def normalize_offers(payload, max_price_per_hour, min_vram_gb):
    """Reduce the provider response to the five preview-safe fields."""
    if not isinstance(payload, dict):
        raise OfferSearchError("Vast returned an invalid offer response.")
    raw_offers = payload.get("offers")
    if raw_offers is None:
        return []
    if not isinstance(raw_offers, list):
        raise OfferSearchError("Vast returned an invalid offer response.")

    offers = []
    for raw in raw_offers:
        if not isinstance(raw, dict):
            continue

        offer_id = raw.get("id")
        gpu_name = raw.get("gpu_name")
        gpu_ram = _finite_number(raw.get("gpu_ram"))
        price = _finite_number(raw.get("dph_total"))
        reliability = (
            _finite_number(raw.get("reliability2"))
            if raw.get("reliability2") is not None
            else _finite_number(raw.get("reliability"))
        )
        if (
            isinstance(offer_id, bool)
            or not isinstance(offer_id, int)
            or offer_id < 0
            or not isinstance(gpu_name, str)
            or not gpu_name.strip()
            or gpu_ram is None
            or gpu_ram < int(min_vram_gb) * 1024
            or price is None
            or price < 0
            or price > float(max_price_per_hour)
            or reliability is None
            or not MIN_RELIABILITY <= reliability <= 1
        ):
            continue

        offers.append(
            {
                "offer_id": offer_id,
                "gpu_name": gpu_name.strip()[:160],
                "gpu_ram_gb": round(gpu_ram / 1024.0, 1),
                "dph_total": price,
                "reliability": reliability,
            }
        )
    offers.sort(key=lambda offer: offer["dph_total"])
    return offers[:OFFER_SEARCH_LIMIT]


def _finite_number(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        return None
    return float(value)


async def _search_with_session(
    session,
    api_key,
    max_price_per_hour,
    min_vram_gb,
):
    try:
        async with session.post(
            OFFER_SEARCH_URL,
            headers={
                "Authorization": "Bearer " + api_key,
                "Accept": "application/json",
            },
            json=build_search_payload(max_price_per_hour, min_vram_gb),
        ) as response:
            if response.status in (401, 403):
                raise OfferSearchError("Vast rejected the saved API key.")
            if response.status != 200:
                raise OfferSearchError(
                    "Vast offer search is temporarily unavailable."
                )
            try:
                payload = await response.json()
            except Exception:
                raise OfferSearchError(
                    "Vast returned an invalid offer response."
                ) from None
    except OfferSearchError:
        raise
    except Exception:
        raise OfferSearchError(
            "Vast offer search is temporarily unavailable."
        ) from None

    try:
        return normalize_offers(payload, max_price_per_hour, min_vram_gb)
    except OfferSearchError:
        raise
    except Exception:
        raise OfferSearchError("Vast returned an invalid offer response.") from None


async def search_offers(
    api_key,
    max_price_per_hour,
    min_vram_gb,
    session=None,
):
    """Search Vast offers using an injected or host-provided aiohttp session."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise OfferSearchConfigurationError("Vast API key is not configured.")
    api_key = api_key.strip()

    if session is not None:
        return await _search_with_session(
            session,
            api_key,
            max_price_per_hour,
            min_vram_gb,
        )

    try:
        import aiohttp
    except ImportError:
        raise OfferSearchError(
            "Vast offer search is temporarily unavailable."
        ) from None

    timeout = aiohttp.ClientTimeout(total=OFFER_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        return await _search_with_session(
            client,
            api_key,
            max_price_per_hour,
            min_vram_gb,
        )
