"""OTLP span emission with deterministic IDs."""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from opentelemetry import trace as trace_api
from opentelemetry.trace import Status, StatusCode
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from . import __version__
from .genai import (
    ContentProcessor,
    as_text,
    chat_attributes,
    root_attributes,
    tool_attributes,
    ts_ns,
)
from .ids import (
    DeterministicIdGenerator,
    parent_context,
    set_ids,
    span_id,
    trace_id,
)
from .transcript import TimelineItem


@dataclass
class TracerBundle:
    provider: TracerProvider
    tracer: Any
    exporter: SpanExporter
    flush_timeout_ms: int
    dry_run: bool


class JsonlExporter(SpanExporter):
    """Dry-run exporter: writes readable span JSON lines to a file."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def export(self, spans):
        with open(self.path, "a", encoding="utf-8") as fh:
            for span in spans:
                try:
                    data = json.loads(span.to_json())  # type: ignore[attr-defined]
                    fh.write(
                        json.dumps(
                            data, ensure_ascii=False, separators=(",", ":")
                        )
                        + "\n"
                    )
                except Exception:
                    fh.write(
                        json.dumps(
                            {"name": getattr(span, "name", "span")},
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _compression(name: str):
    return {
        "gzip": Compression.Gzip,
        "deflate": Compression.Deflate,
        "none": Compression.NoCompression,
    }.get(name, Compression.NoCompression)


def build_tracer(config: Dict[str, Any]) -> TracerBundle:
    resource_attrs = dict(config["resource"].get("resolved_attributes") or {})
    resource_attrs.setdefault("service.name", "claude-code")
    resource_attrs.setdefault("telemetry.source", "claude-code-genai-hook")
    resource_attrs.setdefault("claude_code.genai_hook.version", __version__)
    resource_attrs.setdefault("host.name", platform.node())
    resource_attrs.setdefault("os.type", platform.system().lower())
    resource_attrs.setdefault("host.arch", platform.machine())

    provider = TracerProvider(
        resource=Resource.create(resource_attrs),
        id_generator=DeterministicIdGenerator(),
    )

    dry_run = bool(config.get("hook", {}).get("dry_run", False))
    if dry_run:
        path = os.path.join(config["state"]["dir"], "dry-run.jsonl")
        exporter: SpanExporter = JsonlExporter(path)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        exporter = OTLPSpanExporter(
            endpoint=config["exporter"]["resolved_endpoint"],
            headers=config["exporter"].get("resolved_headers") or {},
            timeout=float(config["exporter"].get("timeout_ms", 30000)) / 1000.0,
            compression=_compression(
                str(config["exporter"].get("compression") or "none")
            ),
        )
        batch = config["exporter"].get("batch") or {}
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_export_batch_size=int(batch.get("max_size") or 512),
                schedule_delay_millis=float(batch.get("schedule_delay_ms") or 500),
            )
        )

    tracer = provider.get_tracer("cc-genai-hook", __version__)
    return TracerBundle(
        provider=provider,
        tracer=tracer,
        exporter=exporter,
        flush_timeout_ms=int(config["exporter"].get("timeout_ms", 30000)),
        dry_run=dry_run,
    )


PLUS_1MS_NS = 1_000_000


def _gen_step_name(
    tool_count: int, has_text: bool, is_last: bool, step: int
) -> str:
    if tool_count:
        return "plan (%d %s) #%d" % (
            tool_count,
            "tool" if tool_count == 1 else "tools",
            step,
        )
    if has_text and is_last:
        return "response #%d" % step
    return "generation #%d" % step


def _tool_step_name(tool_name: str, step: int) -> str:
    return "tool: %s #%d" % ((tool_name or "unknown").lower(), step)


def emit_turn(
    bundle: TracerBundle,
    session_id: str,
    prompt_id: str,
    segment_seq: int,
    prompt_item: Optional[TimelineItem],
    messages: List[TimelineItem],
    timeline: List[TimelineItem],
    message_indices: Dict[int, int],
    content: ContentProcessor,
    compat: Dict[str, Any],
    agent_name: str = "claude-code",
    agent_id: Optional[str] = None,
) -> List[Tuple[str, str, int]]:
    """Emit one segment root plus its chat spans.

    Returns (message_id, prompt_id, chat_span_id) for state marking.
    """

    tid = trace_id(session_id, prompt_id)
    root_sid = span_id(
        "invoke_agent", "%s:%s:%d" % (session_id, prompt_id, segment_seq)
    )
    root_start = ts_ns(prompt_item.ts) if prompt_item else ts_ns(messages[0].ts)
    root_attrs = root_attributes(
        messages,
        prompt_item,
        session_id,
        segment_seq,
        agent_name,
        content,
        compat,
        agent_id=agent_id,
    )
    set_ids(tid, root_sid)
    root = bundle.tracer.start_span(
        "invoke_agent",
        start_time=root_start,
        attributes=root_attrs,
    )
    root_ctx = trace_api.set_span_in_context(root)

    emitted: List[Tuple[str, str, int]] = []
    max_end = root_start
    for item in messages:
        chat_sid = span_id("chat", item.message_id or "")
        set_ids(tid, chat_sid)
        index = message_indices.get(id(item), 0)
        attrs = chat_attributes(
            item, index, timeline, session_id, content, compat
        )
        start = ts_ns(item.ts)
        end = ts_ns(item.last_ts) + 1
        if end <= start:
            end = start + 1
        span = bundle.tracer.start_span(
            "chat",
            context=root_ctx,
            start_time=start,
            attributes=attrs,
        )
        span.end(end_time=end)
        max_end = max(max_end, end)
        if item.message_id:
            emitted.append((item.message_id, prompt_id, chat_sid))

    root.end(end_time=max(max_end, root_start + 1))
    return emitted


def emit_ordered_turn(
    bundle: TracerBundle,
    session_id: str,
    prompt_id: str,
    segment_seq: int,
    prompt_item: Optional[TimelineItem],
    messages: List[TimelineItem],
    tool_results: Dict[str, Any],
    timeline: List[TimelineItem],
    message_indices: Dict[int, int],
    content: ContentProcessor,
    compat: Dict[str, Any],
    agent_name: str = "claude-code",
    agent_id: Optional[str] = None,
    emitted_tool_ids: Optional[Set[str]] = None,
) -> Tuple[List[Tuple[str, str, int]], List[Tuple[str, str, int]]]:
    """Emit a turn with Litefuse-style logical ordering.

    Generations and tools share one step counter.  The next activity starts at
    the previous activity's end + 1 ms, clamped to the real first timestamp,
    so the backend always sees a stable, non-overlapping order.
    """

    tid = trace_id(session_id, prompt_id)
    root_sid = span_id(
        "invoke_agent", "%s:%s:%d" % (session_id, prompt_id, segment_seq)
    )
    root_start = ts_ns(prompt_item.ts) if prompt_item else ts_ns(messages[0].ts)
    root_attrs = root_attributes(
        messages,
        prompt_item,
        session_id,
        segment_seq,
        agent_name,
        content,
        compat,
        agent_id=agent_id,
    )
    set_ids(tid, root_sid)
    root = bundle.tracer.start_span(
        "invoke_agent",
        start_time=root_start,
        attributes=root_attrs,
    )
    root_ctx = trace_api.set_span_in_context(root)

    emitted_tools = set(emitted_tool_ids or ())
    chat_records: List[Tuple[str, str, int]] = []
    tool_records: List[Tuple[str, str, int]] = []
    counter = 0
    prev_end = root_start
    latest_end = root_start

    for msg_index, item in enumerate(messages):
        counter += 1
        step = counter
        tool_uses = [
            block
            for block in item.blocks
            if block.get("type") == "tool_use"
        ]
        has_text = any(
            block.get("type") == "text" and block.get("text")
            for block in item.blocks
        )
        step_name = _gen_step_name(
            len(tool_uses), has_text, msg_index == len(messages) - 1, step
        )

        msg_start = ts_ns(item.ts)
        gen_start = prev_end + PLUS_1MS_NS
        if msg_start and gen_start > msg_start:
            gen_start = msg_start
        gen_end = ts_ns(item.last_ts)
        if gen_end < gen_start:
            gen_end = gen_start

        chat_sid = span_id("chat", item.message_id or "")
        attrs = chat_attributes(
            item,
            message_indices.get(id(item), 0),
            timeline,
            session_id,
            content,
            compat,
            step_index=step,
            step_name=step_name,
        )
        set_ids(tid, chat_sid)
        span = bundle.tracer.start_span(
            "chat",
            context=root_ctx,
            start_time=gen_start,
            attributes=attrs,
        )
        span.end(end_time=gen_end)
        if item.message_id:
            chat_records.append((item.message_id, prompt_id, chat_sid))

        prev_end = gen_end
        latest_end = max(latest_end, gen_end)
        tool_start = gen_end + PLUS_1MS_NS
        latest_tool_end: Optional[int] = None

        for tool in tool_uses:
            counter += 1
            tool_step = counter
            tool_use_id = str(tool.get("id") or "")
            if not tool_use_id:
                continue
            result = tool_results.get(tool_use_id)
            tool_end = (
                ts_ns(result.ts) if result is not None and result.ts else tool_start
            )
            if tool_end < tool_start:
                tool_end = tool_start
            latest_tool_end = (
                tool_end
                if latest_tool_end is None
                else max(latest_tool_end, tool_end)
            )
            latest_end = max(latest_end, tool_end)

            if tool_use_id in emitted_tools:
                continue

            error = None
            tool_response = None
            if result is None:
                error = "tool result missing"
            elif result.is_error:
                error = as_text(result.content) or "tool error"
            else:
                tool_response = result.content

            tool_name = str(tool.get("name") or "")
            duration_ms = max(0, (tool_end - tool_start) // 1_000_000)
            tool_attrs = tool_attributes(
                tool_name,
                tool_use_id,
                tool.get("input"),
                tool_response,
                error,
                False,
                duration_ms,
                session_id,
                prompt_id,
                segment_seq,
                content,
                compat,
                step_index=tool_step,
                step_name=_tool_step_name(tool_name, tool_step),
                plan_step=step,
            )
            tool_sid = span_id("execute_tool", tool_use_id)
            set_ids(tid, tool_sid)
            tool_span = bundle.tracer.start_span(
                "execute_tool",
                context=root_ctx,
                start_time=tool_start,
                attributes=tool_attrs,
            )
            if error is not None:
                tool_span.set_status(Status(StatusCode.ERROR))
            else:
                tool_span.set_status(Status(StatusCode.OK))
            tool_span.end(end_time=tool_end)
            tool_records.append((tool_use_id, prompt_id, tool_sid))

        if latest_tool_end is not None:
            prev_end = max(prev_end, latest_tool_end)
            latest_end = max(latest_end, latest_tool_end)

    root.set_attribute("claude_code.genai_hook.step_count", counter)
    root.end(end_time=max(latest_end, root_start + 1))
    return chat_records, tool_records


def emit_tool_span(
    bundle: TracerBundle,
    session_id: str,
    prompt_id: str,
    tool_use_id: str,
    tool_name: str,
    tool_input: Any,
    tool_response: Any,
    error: Optional[str],
    is_interrupt: bool,
    duration_ms: Any,
    content: ContentProcessor,
    compat: Dict[str, Any],
    parent_segment: int = 0,
) -> int:
    """Emit one execute_tool span and return its deterministic span ID.

    M2 command mode parents tool spans to segment 0 of the turn.  The current
    segment for multi-Stop turns is supplied by the M3 sidecar.
    """

    tid = trace_id(session_id, prompt_id)
    parent_sid = span_id(
        "invoke_agent", "%s:%s:%d" % (session_id, prompt_id, parent_segment)
    )
    tool_sid = span_id("execute_tool", tool_use_id)
    attrs = tool_attributes(
        tool_name,
        tool_use_id,
        tool_input,
        tool_response,
        error,
        is_interrupt,
        duration_ms,
        session_id,
        prompt_id,
        parent_segment,
        content,
        compat,
    )
    end = time.time_ns()
    start = end - int(duration_ms or 0) * 1_000_000
    if start >= end:
        start = end - 1
    set_ids(tid, tool_sid)
    span = bundle.tracer.start_span(
        "execute_tool",
        context=parent_context(tid, parent_sid),
        start_time=start,
        attributes=attrs,
    )
    if error is not None:
        span.set_status(Status(StatusCode.ERROR))
    else:
        span.set_status(Status(StatusCode.OK))
    span.end(end_time=end)
    return tool_sid


def emit_test_span(bundle: TracerBundle) -> None:
    tid = trace_id("cc-genai-hook", "test")
    sid = span_id("test", "test")
    set_ids(tid, sid)
    span = bundle.tracer.start_span(
        "gen_ai.test",
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": "test-model",
            "gen_ai.conversation.id": "cc-genai-hook-test",
            "claude_code.genai_hook.synthetic": True,
        },
    )
    span.end()


def flush(bundle: TracerBundle) -> bool:
    try:
        ok = bundle.provider.force_flush(timeout_millis=bundle.flush_timeout_ms)
    except Exception:
        return False
    try:
        bundle.provider.shutdown()
    except Exception:
        pass
    return bool(ok)
