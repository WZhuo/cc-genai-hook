"""Claude Code transcript parsing.

Claude Code writes one JSONL row per content block for a single assistant
message.  Rows repeat usage and stop_reason, so assistant rows are merged by
``message.id``.  User rows carry ``promptId``; that is the turn key used by the
hook and by the deterministic trace ID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class TimelineItem:
    kind: str  # "user" | "assistant"
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    block_indices: List[Optional[int]] = field(default_factory=list)
    message_id: Optional[str] = None
    prompt_id: Optional[str] = None
    model: Optional[str] = None
    requested_model: Optional[str] = None
    stop_reason: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    ts: Optional[str] = None
    last_ts: Optional[str] = None
    uuid: Optional[str] = None
    parent_uuid: Optional[str] = None
    sidechain: bool = False
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    version: Optional[str] = None
    request_ref: Optional[str] = None
    is_meta: bool = False
    origin_kind: Optional[str] = None
    prompt_source: Optional[str] = None


@dataclass
class ToolResult:
    content: Any
    is_error: bool
    ts: Optional[str]


def blocks_of(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    content = msg.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def load_rows(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _has_tool_result(blocks: Iterable[Dict[str, Any]]) -> bool:
    return any(block.get("type") == "tool_result" for block in blocks)


def _row_origin(row: Dict[str, Any]) -> Optional[str]:
    origin = row.get("origin")
    if isinstance(origin, dict):
        kind = origin.get("kind")
        return str(kind) if kind else None
    return None


def is_real_prompt_row(row: Dict[str, Any]) -> bool:
    if row.get("type") != "user" or row.get("isMeta"):
        return False
    msg = row.get("message") or {}
    blocks = blocks_of(msg)
    if _has_tool_result(blocks):
        return False
    if _row_origin(row) == "human" or row.get("promptSource"):
        return True
    # Fallback for older transcripts: a user row with a promptId and text that
    # is not a tool result is a real prompt.
    if row.get("promptId") and any(b.get("type") == "text" for b in blocks):
        return True
    return False


def is_prompt(item: TimelineItem) -> bool:
    if item.kind != "user" or item.is_meta:
        return False
    if _has_tool_result(item.blocks):
        return False
    if item.origin_kind == "human" or item.prompt_source:
        return True
    return bool(item.prompt_id) and any(b.get("type") == "text" for b in item.blocks)


def build_timeline(
    rows: Iterable[Dict[str, Any]]
) -> Tuple[List[TimelineItem], Dict[str, ToolResult]]:
    timeline: List[TimelineItem] = []
    assistant_index: Dict[str, int] = {}
    tool_results: Dict[str, ToolResult] = {}
    current_prompt_id: Optional[str] = None

    for row in rows:
        rtype = row.get("type")
        msg = row.get("message") or {}
        if rtype == "user":
            content = blocks_of(msg)
            row_prompt_id = row.get("promptId")
            if is_real_prompt_row(row) and row_prompt_id:
                current_prompt_id = row_prompt_id
            for block in content:
                if block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id:
                        tool_results[str(tool_use_id)] = ToolResult(
                            content=block.get("content"),
                            is_error=bool(block.get("is_error")),
                            ts=row.get("timestamp"),
                        )
            timeline.append(
                TimelineItem(
                    kind="user",
                    blocks=content,
                    block_indices=[None] * len(content),
                    message_id=msg.get("id"),
                    prompt_id=row_prompt_id or current_prompt_id,
                    ts=row.get("timestamp"),
                    last_ts=row.get("timestamp"),
                    uuid=row.get("uuid"),
                    parent_uuid=row.get("parentUuid"),
                    sidechain=bool(row.get("isSidechain")),
                    session_id=row.get("sessionId") or row.get("session_id"),
                    cwd=row.get("cwd"),
                    version=row.get("version"),
                    is_meta=bool(row.get("isMeta")),
                    origin_kind=_row_origin(row),
                    prompt_source=row.get("promptSource"),
                )
            )
        elif rtype == "assistant":
            mid = msg.get("id")
            content = blocks_of(msg)
            block_index = row.get("apiBlockIndex")
            if mid and mid in assistant_index:
                item = timeline[assistant_index[mid]]
                item.blocks.extend(content)
                item.block_indices.extend([block_index] * len(content))
                item.last_ts = row.get("timestamp") or item.last_ts
                if not item.stop_reason:
                    item.stop_reason = msg.get("stop_reason")
                if not item.usage:
                    item.usage = msg.get("usage") or {}
                if not item.model:
                    item.model = msg.get("model")
                if not item.requested_model:
                    item.requested_model = row.get("requestedModel") or msg.get("model")
                continue

            item = TimelineItem(
                kind="assistant",
                blocks=content,
                block_indices=[block_index] * len(content),
                message_id=mid,
                prompt_id=current_prompt_id,
                model=msg.get("model"),
                requested_model=row.get("requestedModel") or msg.get("model"),
                stop_reason=msg.get("stop_reason"),
                usage=msg.get("usage") or {},
                ts=row.get("timestamp"),
                last_ts=row.get("timestamp"),
                uuid=row.get("uuid"),
                parent_uuid=row.get("parentUuid"),
                sidechain=bool(row.get("isSidechain")),
                session_id=row.get("sessionId") or row.get("session_id"),
                cwd=row.get("cwd"),
                version=row.get("version"),
                request_ref=row.get("requestRef"),
            )
            if mid:
                assistant_index[mid] = len(timeline)
            timeline.append(item)

    for item in timeline:
        if item.kind == "assistant" and item.blocks:
            indexed = [
                (idx, block)
                for idx, block in zip(item.block_indices, item.blocks)
                if idx is not None
            ]
            if indexed and len(indexed) == len(item.blocks):
                indexed.sort(key=lambda pair: pair[0])
                item.blocks = [block for _, block in indexed]
                item.block_indices = [idx for idx, _ in indexed]
    return timeline, tool_results


def latest_prompt(
    timeline: List[TimelineItem], upto: int, prompt_id: Optional[str] = None
) -> Optional[TimelineItem]:
    for idx in range(min(upto, len(timeline)) - 1, -1, -1):
        item = timeline[idx]
        if is_prompt(item) and (prompt_id is None or item.prompt_id == prompt_id):
            return item
    return None


def normalize_block(
    block: Dict[str, Any], include_thinking: bool = False
) -> Optional[Dict[str, Any]]:
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": block.get("text", "")}
    if btype == "thinking":
        if not include_thinking:
            return None
        return {"type": "reasoning", "text": block.get("thinking", "")}
    if btype == "tool_use":
        return {
            "type": "tool_call",
            "id": block.get("id"),
            "name": block.get("name"),
            "arguments": block.get("input"),
        }
    if btype == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )
        return {
            "type": "tool_result",
            "tool_call_id": block.get("tool_use_id"),
            "is_error": bool(block.get("is_error")),
            "content": content,
        }
    if btype == "image":
        return {
            "type": "image",
            "media_type": (block.get("source") or {}).get("media_type"),
        }
    return {"type": btype}


def message_payload(
    item: TimelineItem, include_thinking: bool = False
) -> Dict[str, Any]:
    parts = [
        part
        for part in (normalize_block(b, include_thinking) for b in item.blocks)
        if part is not None
    ]
    if item.kind == "assistant":
        return {
            "role": "assistant",
            "content": parts,
            "finish_reason": item.stop_reason,
        }
    return {"role": "user", "content": parts}


def input_messages(
    timeline: List[TimelineItem],
    upto: int,
    mode: str = "last_turn",
    include_thinking: bool = False,
) -> List[Dict[str, Any]]:
    if mode == "none":
        return []
    start = 0
    if mode == "last_turn":
        prompt = latest_prompt(timeline, upto)
        if prompt is not None:
            for idx in range(min(upto, len(timeline)) - 1, -1, -1):
                if timeline[idx] is prompt:
                    start = idx
                    break
    out: List[Dict[str, Any]] = []
    for item in timeline[start:upto]:
        if item.is_meta:
            continue
        payload = message_payload(item, include_thinking)
        if payload["content"]:
            out.append(payload)
    return out
