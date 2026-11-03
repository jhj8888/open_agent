"""Helpers for recording structured payloads without leaking credentials."""

from typing import Any

from common.otlp.trace.langfuse import serialize_langfuse_value


def serialize_trace_payload(payload: Any) -> str:
    """Recursively remove sensitive keys before serializing a trace attribute."""
    return serialize_langfuse_value(payload) or "{}"
