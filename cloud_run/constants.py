"""Fixed values for the managed Vast.ai Cloud Run lifecycle."""

OFFICIAL_TEMPLATE_ID = "027fba7753c024be019030fb42aed900"
OFFICIAL_TEMPLATE_NAME = "Official ComfyUI"
COMFYUI_CONTAINER_PORT = 8188
DEFAULT_DISK_GB = 80
MIN_SESSION_DISK_GB = 80
MAX_SESSION_DISK_GB = 2048

WORKER_RELEASE_SCHEMA_VERSION = 1
PINNED_PYTHON_VERSION = "3.12"
REMOTE_WORKER_PORT = 8765

DEFAULT_MAX_PRICE_PER_HOUR = 1.0
DEFAULT_MIN_VRAM_GB = 16

MIN_PRICE_PER_HOUR = 0.01
MAX_PRICE_PER_HOUR = 100.0
MIN_VRAM_GB = 1
MAX_VRAM_GB = 1024
MAX_API_KEY_LENGTH = 4096

MIN_VAST_RELIABILITY = 0.99
MIN_VAST_INET_DOWN_MBPS = 500
PREFERRED_VAST_INET_DOWN_MBPS = 1000

VAST_CREATE_CONFIGURATION_REVISION = "typed-env-object-v1"
VAST_CREATE_FAILURE_CODES = frozenset(
    {
        "configuration_rejected",
        "api_key_rejected",
        "offer_unavailable",
        "rate_limited",
        "retryable_http",
        "timeout",
        "connection",
        "tls",
        "server_disconnected",
        "invalid_response",
        "confirmation_interrupted",
        "quote_expired",
        "transport_unknown",
    }
)
