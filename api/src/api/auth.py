"""
Azure AD Authentication Configuration

This module sets up Azure AD token validation for the FastAPI backend.
It validates JWT tokens issued by Azure AD and extracts user information.
"""

import logging

import jwt
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer
from jwt import PyJWKClient
from pydantic_settings import BaseSettings
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class AzureADSettings(BaseSettings):
    """Azure AD configuration loaded from environment variables."""

    # The Application (client) ID of the API app registration
    AZURE_AD_CLIENT_ID: str = ""

    # The Directory (tenant) ID
    AZURE_AD_TENANT_ID: str = ""

    # Optional: The Application ID URI (if you've set one up for scopes)
    # Usually looks like: api://<client-id>
    AZURE_AD_APP_ID_URI: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


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

    def __init__(self, app, settings: AzureADSettings):
        super().__init__(app)
        self.settings = settings
        self.jwks_uri = f"https://login.microsoftonline.com/{settings.AZURE_AD_TENANT_ID}/discovery/v2.0/keys"
        self.jwks_client = PyJWKClient(self.jwks_uri) if settings.AZURE_AD_TENANT_ID else None

        # Azure AD can issue tokens with different issuer formats depending on the endpoint
        self.valid_issuers = [
            f"https://login.microsoftonline.com/{settings.AZURE_AD_TENANT_ID}/v2.0",  # v2.0 endpoint
            f"https://sts.windows.net/{settings.AZURE_AD_TENANT_ID}/",  # v1.0 endpoint
        ]

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
        logger.info("Azure AD Auth configured with issuers: %s", self.valid_issuers)

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip if auth is not configured
        if not self.settings.AZURE_AD_CLIENT_ID or not self.settings.AZURE_AD_TENANT_ID:
            logger.warning("Azure AD auth not configured, allowing request without validation")
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
        token_parts = token.split('.')
        if len(token_parts) != 3:
            logger.error("Token does not have 3 parts (has %d). This is not a valid JWT.", len(token_parts))
            for i, part in enumerate(token_parts):
                preview = part[:20] if len(part) > 20 else part
                logger.error("  Part %d: length=%d, preview=%s...", i, len(part), preview)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Invalid token format: expected JWT with 3 parts, got {len(token_parts)}"},
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
            # Store user info in request state for downstream use
            request.state.user = payload
            # Store oid claim directly for easy access
            request.state.user_id = payload.get("unique_name") or payload.get("name")
            logger.info("Auth middleware: extracted user_id (oid) = %s", request.state.user_id)
        except jwt.ExpiredSignatureError:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Token has expired"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError as e:
            logger.error("Token validation failed: %s", e)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Invalid token: {str(e)}"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception as e:
            logger.error("Unexpected auth error: %s", e)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Authentication error"},
            )

        return await call_next(request)


def get_azure_auth_scheme() -> SingleTenantAzureAuthorizationCodeBearer:
    """
    Create and return the Azure AD authentication scheme.

    This validates tokens and extracts claims from the JWT.
    """
    return SingleTenantAzureAuthorizationCodeBearer(
        app_client_id=azure_ad_settings.AZURE_AD_CLIENT_ID,
        tenant_id=azure_ad_settings.AZURE_AD_TENANT_ID,
        scopes={
            f"api://{azure_ad_settings.AZURE_AD_CLIENT_ID}/access_as_user": "Access API as user",
        } if azure_ad_settings.AZURE_AD_CLIENT_ID else {},
        allow_guest_users=False,
    )


# Create the auth scheme instance
# This will be None if credentials aren't configured
azure_scheme = None
if azure_ad_settings.AZURE_AD_CLIENT_ID and azure_ad_settings.AZURE_AD_TENANT_ID:
    azure_scheme = get_azure_auth_scheme()
