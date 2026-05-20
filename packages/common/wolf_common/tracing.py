"""OpenTelemetry tracing setup.

Call `configure_tracing(service_name, otlp_endpoint)` once at service startup.
Pass an empty string for `otlp_endpoint` to disable export (useful in dev/test).
"""

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


def configure_tracing(
    service_name: str,
    otlp_endpoint: str = "",
    environment: str = "production",
) -> None:
    """Set up OTel tracing for the calling service.

    When `otlp_endpoint` is empty, spans are exported to stdout (dev mode).
    When non-empty, they are sent to the OTLP collector at that endpoint.
    """
    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-untyped]
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    elif environment == "development":
        exporter = ConsoleSpanExporter()  # type: ignore[assignment]
    else:
        # production with no endpoint configured — no-op
        trace.set_tracer_provider(provider)
        return

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
