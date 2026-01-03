"""
FastAPI server with Microsoft Agent Framework and SSE streaming.

This module handles application setup, lifespan management, and middleware configuration.
Route handlers are organized in the routers/ package.

The API uses a workflow-based agent architecture:
- ChatAgentExecutor: Handles user-facing chat interactions
- NL2SQLAgentExecutor: Processes data queries and returns structured results
- The workflow orchestrates communication between these agents
"""

import logging
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.entities.workflow import get_workflow

from src.api.auth import azure_scheme, azure_ad_settings, AzureADAuthMiddleware
from src.api.monitoring import configure_observability, is_observability_enabled
from src.api.routers import chat_router, threads_router


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

# Check if Azure AD authentication is configured
AUTH_ENABLED = bool(azure_ad_settings.AZURE_AD_CLIENT_ID and azure_ad_settings.AZURE_AD_TENANT_ID)

# Configure observability before creating the app
configure_observability()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """
    Application lifespan handler.

    Initializes the agent workflow on startup and cleans up on shutdown.
    """
    # Startup: Initialize agent workflow from entities
    workflow, chat_executor, chat_client = get_workflow()

    application.state.workflow = workflow
    application.state.chat_executor = chat_executor
    application.state.chat_client = chat_client

    # Expose the workflow as an agent for compatibility with chat router
    application.state.agent = workflow.as_agent(name="DataExplorerAgent")

    logger.info("Data Agent Workflow initialized (Chat <-> NL2SQL)")

    # Log observability status
    if is_observability_enabled():
        logger.info("OpenTelemetry observability is ENABLED")
    else:
        logger.info("OpenTelemetry observability is disabled (set ENABLE_INSTRUMENTATION=true to enable)")

    # Log authentication status
    if AUTH_ENABLED:
        logger.info("Azure AD authentication is ENABLED")
        if azure_scheme:
            await azure_scheme.openid_config.load_config()
    else:
        logger.warning("=" * 60)
        logger.warning("WARNING: Azure AD authentication is NOT configured!")
        logger.warning("The API will respond to ANONYMOUS connections.")
        logger.warning("Set AZURE_AD_CLIENT_ID and AZURE_AD_TENANT_ID to enable auth.")
        logger.warning("=" * 60)

    yield

    # Shutdown: Cleanup
    logger.info("Application shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Enterprise Data Agent",
    lifespan=lifespan,
    swagger_ui_oauth2_redirect_url="/oauth2-redirect",
    swagger_ui_init_oauth={
        "usePkceWithAuthorizationCodeGrant": True,
        "clientId": azure_ad_settings.AZURE_AD_CLIENT_ID,
    } if AUTH_ENABLED else None,
)

# Add Azure AD authentication middleware
if AUTH_ENABLED:
    app.add_middleware(AzureADAuthMiddleware, settings=azure_ad_settings)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat_router)
app.include_router(threads_router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    agent_ready = getattr(app.state, "agent", None) is not None
    return {"status": "healthy", "agent_ready": agent_ready}


if __name__ == "__main__":
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
