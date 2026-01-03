"""
API package for the Data Agent FastAPI server.

Contains:
- main.py: FastAPI application with lifespan management
- auth.py: Azure AD authentication middleware
- models.py: Pydantic models for API requests/responses
- dependencies.py: FastAPI dependencies
- routers/: Route handlers for chat and threads
- util/: Utility modules (search client)
"""

from src.api.main import app

__all__ = ["app"]
