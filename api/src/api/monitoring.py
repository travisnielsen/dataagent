"""
OpenTelemetry observability configuration for the Data Agent API.

This module provides telemetry configuration for monitoring agent performance,
tracing requests, and collecting metrics.

Environment variables:
- ENABLE_INSTRUMENTATION: Set to "true" to enable OpenTelemetry (default: false)
- APPLICATIONINSIGHTS_CONNECTION_STRING: Azure Monitor connection string (optional)
- OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint for Aspire Dashboard, Jaeger, etc.
- ENABLE_CONSOLE_EXPORTERS: Set to "true" to enable console output (default: false)
- ENABLE_SENSITIVE_DATA: Set to "true" to log prompts/responses (default: false)
"""

import logging
import os

logger = logging.getLogger(__name__)


def is_observability_enabled() -> bool:
    """Check if OpenTelemetry observability is enabled."""
    return os.getenv("ENABLE_INSTRUMENTATION", "false").lower() == "true"


def configure_observability() -> None:
    """
    Configure OpenTelemetry observability if enabled.

    Supports two backends:
    1. Azure Monitor (if APPLICATIONINSIGHTS_CONNECTION_STRING is set)
    2. OTLP exporters (for Aspire Dashboard, Jaeger, etc.)
    """
    if not is_observability_enabled():
        return

    try:
        # Check if Azure Monitor is configured
        azure_monitor_connection = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")

        if azure_monitor_connection:
            _configure_azure_monitor(azure_monitor_connection)
        else:
            _configure_otlp_exporters()

    except ImportError as e:
        logger.warning("OpenTelemetry packages not available: %s", e)
    except (RuntimeError, ValueError, OSError) as e:
        logger.error("Failed to configure OpenTelemetry: %s", e)


def _configure_azure_monitor(connection_string: str) -> None:
    """Configure Azure Monitor for production telemetry."""
    try:
        from azure.monitor.opentelemetry import (  # type: ignore[import-not-found]
            configure_azure_monitor,
        )
        from agent_framework.observability import create_resource, enable_instrumentation

        enable_sensitive = os.getenv("ENABLE_SENSITIVE_DATA", "false").lower() == "true"

        configure_azure_monitor(
            connection_string=connection_string,
            resource=create_resource(),
            enable_live_metrics=True,
        )
        enable_instrumentation(enable_sensitive_data=enable_sensitive)
        logger.info("OpenTelemetry configured with Azure Monitor")

    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry not installed. "
            "Install with: pip install azure-monitor-opentelemetry"
        )
        # Fall back to OTLP exporters
        _configure_otlp_exporters()


def _configure_otlp_exporters() -> None:
    """Configure OTLP exporters for local development (Aspire Dashboard, Jaeger, etc.)."""
    from agent_framework.observability import configure_otel_providers

    # Reads OTEL_EXPORTER_OTLP_ENDPOINT from environment
    # For console exporters, set ENABLE_CONSOLE_EXPORTERS=true in environment
    configure_otel_providers()

    logger.info("OpenTelemetry configured with OTLP exporters")
