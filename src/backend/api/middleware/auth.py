"""
Azure AD Authentication Configuration

This module sets up Azure AD token validation for the FastAPI backend.
It validates JWT tokens issued by Azure AD and extracts user information.
"""

import logging

import jwt
from fastapi import Request, status
from fastapi.responses import JSONResponse
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWKError, PyJWKSetError
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

JWT_PARTS_COUNT = 3
TOKEN_PREVIEW_LENGTH = 20
MULTI_TENANT_AUTHORITY = "organizations"

logger = logging.getLogger(__name__)


def parse_tenant_ids(raw_tenant_ids: str) -> list[str]:
    """Parse a comma-separated tenant allowlist into a de-duplicated list."""
    tenant_ids: list[str] = []
    seen: set[str] = set()
    for raw_tenant_id in raw_tenant_ids.split(","):
        tenant_id = raw_tenant_id.strip()
        if not tenant_id or tenant_id in seen:
            continue
        seen.add(tenant_id)
        tenant_ids.append(tenant_id)
    return tenant_ids


def build_valid_issuers(tenant_ids: list[str]) -> list[str]:
    """Build the issuer allowlist for the approved tenant IDs."""
    issuers: list[str] = []
    for tenant_id in tenant_ids:
        issuers.extend([
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://sts.windows.net/{tenant_id}/",
        ])
    return issuers


class AzureADSettings(BaseSettings):
    """Azure AD configuration loaded from environment variables."""

    # The Application (client) ID of the API app registration
    AZURE_AD_CLIENT_ID: str = ""

    # The Directory (tenant) ID
    AZURE_AD_TENANT_ID: str = ""

    # Comma-separated Directory (tenant) IDs allowed to authenticate.
    AZURE_AD_TENANT_IDS: str = ""

    # Optional: The Application ID URI (if you've set one up for scopes)
    # Usually looks like: api://<client-id>
    AZURE_AD_APP_ID_URI: str = ""

    @property
    def allowed_tenant_ids(self) -> list[str]:
        """Return the configured tenant allowlist, preserving declaration order."""
        return parse_tenant_ids(self.AZURE_AD_TENANT_IDS or self.AZURE_AD_TENANT_ID)

    @property
    def auth_enabled(self) -> bool:
        """Return whether Azure AD auth is fully configured."""
        return bool(self.AZURE_AD_CLIENT_ID and self.allowed_tenant_ids)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Load settings from environment
azure_ad_settings = AzureADSettings()


# Paths that don't require authentication
PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class AzureADAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that validates Azure AD JWT tokens on all requests.
    """

    def __init__(self, app: ASGIApp, settings: AzureADSettings) -> None:
        super().__init__(app)
        self.settings = settings
        self.allowed_tenant_ids = settings.allowed_tenant_ids
        self.allowed_tenant_id_set = set(self.allowed_tenant_ids)
        self.jwks_uri = (
            f"https://login.microsoftonline.com/{MULTI_TENANT_AUTHORITY}/discovery/v2.0/keys"
        )
        self.jwks_client = PyJWKClient(self.jwks_uri) if self.allowed_tenant_ids else None

        # Azure AD can issue tokens with different issuer formats depending on the endpoint
        self.valid_issuers = build_valid_issuers(self.allowed_tenant_ids)

        # The audience can be either the client ID or the App ID URI
        # Also support Graph API tokens for fallback scenarios
        self.valid_audiences = [
            settings.AZURE_AD_CLIENT_ID,
            f"api://{settings.AZURE_AD_CLIENT_ID}",
            "00000003-0000-0000-c000-000000000000",  # Microsoft Graph
        ]
        if settings.AZURE_AD_APP_ID_URI:
            self.valid_audiences.append(settings.AZURE_AD_APP_ID_URI)

        logger.info("Azure AD Auth configured with audiences: %s", self.valid_audiences)
        logger.info("Azure AD Auth configured with allowed tenants: %s", self.allowed_tenant_ids)
        logger.info("Azure AD Auth configured with issuers: %s", self.valid_issuers)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:  # noqa: PLR0911
        # Skip auth for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Get the Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract the token
        try:
            scheme, token = auth_header.split(" ", 1)
            if scheme.lower() != "bearer":
                raise ValueError("Invalid auth scheme")
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid Authorization header format"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check if the token looks like a JWT (should have 3 parts separated by dots)
        token_parts = token.split(".")
        if len(token_parts) != JWT_PARTS_COUNT:
            logger.error(
                "Token does not have 3 parts (has %d). This is not a valid JWT.", len(token_parts)
            )
            for i, part in enumerate(token_parts):
                preview = part[:TOKEN_PREVIEW_LENGTH] if len(part) > TOKEN_PREVIEW_LENGTH else part
                logger.error("  Part %d: length=%d, preview=%s...", i, len(part), preview)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "detail": f"Invalid token format: expected JWT with 3 parts, got {len(token_parts)}"
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate the token
        try:
            if self.jwks_client is None:
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"detail": "JWKS client not configured"},
                )

            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.valid_audiences,
                issuer=self.valid_issuers,
            )
            tenant_id = payload.get("tid")
            if not isinstance(tenant_id, str) or tenant_id not in self.allowed_tenant_id_set:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Token tenant is not allowed"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            # Store user info in request state for downstream use
            request.state.user = payload
            request.state.user_id = (
                payload.get("oid") or payload.get("unique_name") or payload.get("name")
            )
            logger.info("Auth middleware: extracted user_id (oid) = %s", request.state.user_id)
        except jwt.ExpiredSignatureError:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Token has expired"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        except (
            jwt.InvalidTokenError,
            PyJWKClientConnectionError,
            PyJWKClientError,
            PyJWKError,
            PyJWKSetError,
            TypeError,
            ValueError,
        ) as e:
            logger.exception("Token validation failed")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Invalid token: {e!s}"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
