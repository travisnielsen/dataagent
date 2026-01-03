"""
Entities package for DevUI auto-discovery.

Each subdirectory represents a discoverable entity:
- chat_agent/: User-facing agent that renders data results
- data_agent/: NL2SQL agent for database queries
- workflow/: Orchestrated workflow combining chat and data agents

Shared models are available at the package level.

Usage with DevUI:
    devui ./src/entities
"""

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from models import NL2SQLResponse
except ImportError:
    from src.entities.models import NL2SQLResponse

__all__ = ["NL2SQLResponse"]
