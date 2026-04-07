"""
FastAPI server with Microsoft Agent Framework and SSE streaming.

This module handles application setup, lifespan management, and middleware configuration.
Route handlers are organized in the routers/ package.

The API uses a hybrid architecture:
- DataAssistant: Manages chat sessions, intent classification, and refinements
- NL2SQL Pipeline: Processes data queries via process_query
- The assistant invokes the pipeline for data questions
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from api.middleware import AzureADAuthMiddleware, azure_ad_settings
from api.monitoring import configure_observability, is_observability_enabled
from api.routers import chat_router, conversations_router
from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

load_dotenv()

# Configure logging - use force=True to prevent duplicate handlers
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, force=True)

# Reduce noise from Azure SDK and other libraries
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("azure.search.documents._generated._utils.serialization").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
# Reduce agent_framework verbosity (it logs all message content at INFO level)
logging.getLogger("agent_framework").setLevel(logging.WARNING)
# Suppress known AzureAIClient warning about runtime tool/structured_output overrides
# (non-fatal in current architecture; functionality remains intact)
logging.getLogger("agent_framework.azure").setLevel(logging.ERROR)

# Check if Azure AD authentication is configured
AUTH_ENABLED = azure_ad_settings.auth_enabled

# Explicit opt-in for anonymous access (development only)
ALLOW_ANONYMOUS = os.getenv("ALLOW_ANONYMOUS", "").lower() in {"true", "1", "yes"}

# Paths that are always accessible regardless of auth configuration
_ALWAYS_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _get_cors_origins() -> list[str]:
    """Resolve CORS origins from env, with safe local defaults."""
    default_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if not raw_origins:
        return default_origins

    parsed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    if not parsed_origins:
        return default_origins

    # Keep local development origins available unless explicitly removed.
    return list(dict.fromkeys([*parsed_origins, *default_origins]))


class _FailClosedMiddleware(BaseHTTPMiddleware):
    """Return 503 on all non-health endpoints when auth is not configured."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:  # noqa: PLR6301
        if request.url.path in _ALWAYS_PUBLIC_PATHS:
            return await call_next(request)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Authentication is not configured. "
                "Set AZURE_AD_CLIENT_ID and AZURE_AD_TENANT_IDS, "
                "or the legacy AZURE_AD_TENANT_ID, "
                "or set ALLOW_ANONYMOUS=true for development."
            },
        )


# Configure observability before creating the app
configure_observability()


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
    """
    Application lifespan handler.

    Initializes application state on startup and cleans up on shutdown.
    The DataAssistant and NL2SQL pipeline are created per-session
    by the chat router, not at startup.
    """
    # Startup logging
    logger.info("Enterprise Data Agent API starting")

    # Log observability status
    if is_observability_enabled():
        logger.info("OpenTelemetry observability is ENABLED")
    else:
        logger.info(
            "OpenTelemetry observability is disabled (set ENABLE_INSTRUMENTATION=true to enable)"
        )

    # Log authentication status
    if AUTH_ENABLED:
        logger.info("Azure AD authentication is ENABLED")
        logger.info(
            "Azure AD tenant allowlist: %s",
            azure_ad_settings.allowed_tenant_ids,
        )
    elif ALLOW_ANONYMOUS:
        logger.warning("=" * 60)
        logger.warning("WARNING: Running with ALLOW_ANONYMOUS=true")
        logger.warning("All endpoints accept unauthenticated requests.")
        logger.warning("DO NOT use this setting in production.")
        logger.warning("=" * 60)
    else:
        logger.warning("=" * 60)
        logger.warning("WARNING: Azure AD authentication is NOT configured!")
        logger.warning("Non-health endpoints will return 503.")
        logger.warning("Set ALLOW_ANONYMOUS=true for local development.")
        logger.warning("=" * 60)

    yield

    logger.info("Application shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Enterprise Data Agent",
    lifespan=lifespan,
    swagger_ui_oauth2_redirect_url="/oauth2-redirect",
    swagger_ui_init_oauth={
        "usePkceWithAuthorizationCodeGrant": True,
        "clientId": azure_ad_settings.AZURE_AD_CLIENT_ID,
    }
    if AUTH_ENABLED
    else None,
)

# Add Azure AD authentication middleware (or fail-closed middleware)
if AUTH_ENABLED:
    app.add_middleware(AzureADAuthMiddleware, settings=azure_ad_settings)
elif not ALLOW_ANONYMOUS:
    app.add_middleware(_FailClosedMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat_router)
app.include_router(conversations_router)


@app.get("/health")
async def health_check() -> dict[str, object]:
    """Health check endpoint."""
    agent_ready = getattr(app.state, "agent", None) is not None
    return {"status": "healthy", "agent_ready": agent_ready}


if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)  # noqa: S104
