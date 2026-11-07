"""Deterministic trace/span IDs.

Claude Code hooks run as independent processes, so a tool span may be emitted
before the chat/root span it belongs to.  Deterministic IDs let every process
agree on the same trace tree without sharing an OTel span context.
"""

from __future__ import annotations

import contextvars
import hashlib
from dataclasses import dataclass
from typing import Optional

from opentelemetry.sdk.trace.id_generator import IdGenerator


@dataclass(frozen=True)
class Ids:
    trace_id: int
    span_id: int


_CURRENT: contextvars.ContextVar[Ids] = contextvars.ContextVar("cc_genai_hook_ids")


def _hash_int(seed: str, nbytes: int) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()[:nbytes]
    if not any(digest):
        digest = bytes([1]) + digest[1:]
    return int.from_bytes(digest, "big")


def trace_id(session_id: str, turn_key: str) -> int:
    return _hash_int("cc:trace:%s:%s" % (session_id, turn_key), 16)


def span_id(kind: str, key: str) -> int:
    return _hash_int("cc:span:%s:%s" % (kind, key), 8)


def set_ids(trace: int, span: int) -> None:
    _CURRENT.set(Ids(trace_id=trace, span_id=span))


def current_ids() -> Optional[Ids]:
    try:
        return _CURRENT.get()
    except LookupError:
        return None


class DeterministicIdGenerator(IdGenerator):
    """IdGenerator that reads the IDs set by :func:`set_ids`."""

    def generate_trace_id(self) -> int:
        ids = current_ids()
        if ids is None:
            raise RuntimeError("deterministic trace id requested before set_ids()")
        return ids.trace_id

    def generate_span_id(self) -> int:
        ids = current_ids()
        if ids is None:
            raise RuntimeError("deterministic span id requested before set_ids()")
        return ids.span_id


def parent_context(trace: int, parent_span: int):
    from opentelemetry import trace as trace_api
    from opentelemetry.trace import SpanContext, TraceFlags

    return trace_api.set_span_in_context(
        trace_api.NonRecordingSpan(
            SpanContext(
                trace_id=trace,
                span_id=parent_span,
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        )
    )
