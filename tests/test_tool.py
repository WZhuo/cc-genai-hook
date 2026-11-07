import json

from cc_genai_hook.config import load_config
from cc_genai_hook.hook import handle_stop, handle_tool
from cc_genai_hook.ids import span_id


def _config(tmp_path, content_mode="last_turn", tool_mode="realtime"):
    path = tmp_path / "genai-hook.json"
    path.write_text(
        json.dumps(
            {
                "exporter": {"endpoint": "http://127.0.0.1:4318"},
                "hook": {"dry_run": True, "tool_mode": tool_mode},
                "content": {"mode": content_mode},
                "state": {"dir": str(tmp_path / "state")},
            }
        )
    )
    return load_config(str(path))


def _payload(tool_id="t1", **overrides):
    payload = {
        "session_id": "s1",
        "prompt_id": "p1",
        "tool_name": "Read",
        "tool_use_id": tool_id,
        "tool_input": {"file_path": "/tmp/x"},
        "tool_response": {"stdout": "ok"},
        "duration_ms": 12,
    }
    payload.update(overrides)
    return payload


def _spans(tmp_path):
    path = tmp_path / "state" / "dry-run.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_tool_span_attributes_and_dedupe(tmp_path):
    cfg = _config(tmp_path)
    first = handle_tool(_payload(), "post_tool_use", cfg)
    assert first.emitted == 1
    assert first.flush_ok is True

    spans = _spans(tmp_path)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "execute_tool"
    expected_parent = "0x%016x" % span_id("invoke_agent", "s1:p1:0")
    assert span["parent_id"] == expected_parent
    attrs = span["attributes"]
    assert attrs["gen_ai.operation.name"] == "execute_tool"
    assert attrs["gen_ai.provider.name"] == "anthropic"
    assert attrs["gen_ai.tool.name"] == "Read"
    assert attrs["gen_ai.tool.call.id"] == "t1"
    assert json.loads(attrs["gen_ai.tool.call.arguments"]) == {
        "file_path": "/tmp/x"
    }
    assert json.loads(attrs["gen_ai.tool.call.result"]) == {"stdout": "ok"}
    assert attrs["claude_code.genai_hook.tool.duration_ms"] == 12
    assert attrs["claude_code.genai_hook.tool.success"] is True

    second = handle_tool(_payload(), "post_tool_use", cfg)
    assert second.emitted == 0
    assert second.skipped == 1
    assert len(_spans(tmp_path)) == 1


def test_tool_failure_and_interrupt(tmp_path):
    cfg = _config(tmp_path)
    result = handle_tool(
        _payload(
            "t2",
            event="post_tool_use_failure",
            error="Exit code 1\nboom",
            is_interrupt=False,
        ),
        "post_tool_use_failure",
        cfg,
    )
    assert result.emitted == 1
    span = _spans(tmp_path)[0]
    attrs = span["attributes"]
    assert span["status"]["status_code"] == "ERROR"
    assert attrs["error.type"] == "tool_error"
    assert "boom" in attrs["claude_code.genai_hook.tool.error"]
    assert attrs["claude_code.genai_hook.tool.success"] is False

    result = handle_tool(
        _payload(
            "t3",
            error="aborted",
            is_interrupt=True,
        ),
        "post_tool_use_failure",
        cfg,
    )
    assert result.emitted == 1
    attrs = _spans(tmp_path)[1]["attributes"]
    assert attrs["error.type"] == "interrupted"
    assert attrs["claude_code.genai_hook.tool.is_interrupt"] is True


def test_tool_content_none_omits_arguments_and_result(tmp_path):
    cfg = _config(tmp_path, content_mode="none")
    handle_tool(_payload("t4"), "post_tool_use", cfg)
    attrs = _spans(tmp_path)[0]["attributes"]
    assert "gen_ai.tool.call.arguments" not in attrs
    assert "gen_ai.tool.call.result" not in attrs
    assert attrs["gen_ai.tool.name"] == "Read"


def test_tool_prompt_id_falls_back_to_transcript(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "promptId": "p-from-transcript",
                "uuid": "u1",
                "origin": {"kind": "human"},
                "timestamp": "2026-11-07T10:00:00Z",
                "sessionId": "s1",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                },
            }
        )
        + "\n"
    )
    cfg = _config(tmp_path)
    payload = _payload("t5")
    payload.pop("prompt_id")
    payload["transcript_path"] = str(transcript)
    handle_tool(payload, "post_tool_use", cfg)
    attrs = _spans(tmp_path)[0]["attributes"]
    assert attrs["claude_code.genai_hook.turn.prompt_id"] == "p-from-transcript"


def test_tool_span_links_to_later_stop_root(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    rows = [
        {
            "type": "user",
            "promptId": "p1",
            "uuid": "u1",
            "origin": {"kind": "human"},
            "timestamp": "2026-11-07T10:00:00Z",
            "sessionId": "s1",
            "version": "2.1.311",
            "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "requestedModel": "claude-sonnet",
            "timestamp": "2026-11-07T10:00:01Z",
            "sessionId": "s1",
            "version": "2.1.311",
            "message": {
                "id": "m1",
                "role": "assistant",
                "model": "claude-sonnet-4",
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/x"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "promptId": "p1",
            "uuid": "u2",
            "timestamp": "2026-11-07T10:00:02Z",
            "sessionId": "s1",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "ok"}],
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "uuid": "a2",
            "requestedModel": "claude-sonnet",
            "timestamp": "2026-11-07T10:00:03Z",
            "sessionId": "s1",
            "version": "2.1.311",
            "message": {
                "id": "m2",
                "role": "assistant",
                "model": "claude-sonnet-4",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 20, "output_tokens": 7},
                "content": [{"type": "text", "text": "done"}],
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    cfg = _config(tmp_path)

    handle_tool(
        _payload("t1", transcript_path=str(transcript)),
        "post_tool_use",
        cfg,
    )
    handle_stop(
        {"session_id": "s1", "transcript_path": str(transcript)},
        "stop",
        cfg,
    )

    spans = _spans(tmp_path)
    assert [span["name"] for span in spans] == [
        "execute_tool",
        "chat",
        "chat",
        "invoke_agent",
    ]
    root = spans[-1]
    for span in spans[:-1]:
        assert span["context"]["trace_id"] == root["context"]["trace_id"]
        assert span["parent_id"] == root["context"]["span_id"]


def test_ordered_mode_assigns_steps_and_monotonic_times(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    rows = [
        {
            "type": "user",
            "promptId": "p1",
            "uuid": "u1",
            "origin": {"kind": "human"},
            "timestamp": "2026-11-07T10:00:00Z",
            "sessionId": "s1",
            "version": "2.1.311",
            "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "requestedModel": "claude-sonnet",
            "timestamp": "2026-11-07T10:00:01Z",
            "sessionId": "s1",
            "version": "2.1.311",
            "message": {
                "id": "m1",
                "role": "assistant",
                "model": "claude-sonnet-4",
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/x"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "promptId": "p1",
            "uuid": "u2",
            "timestamp": "2026-11-07T10:00:02Z",
            "sessionId": "s1",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "ok"}],
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "uuid": "a2",
            "requestedModel": "claude-sonnet",
            "timestamp": "2026-11-07T10:00:03Z",
            "sessionId": "s1",
            "version": "2.1.311",
            "message": {
                "id": "m2",
                "role": "assistant",
                "model": "claude-sonnet-4",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 20, "output_tokens": 7},
                "content": [{"type": "text", "text": "done"}],
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    cfg = _config(tmp_path, tool_mode="ordered")
    handle_stop(
        {"session_id": "s1", "transcript_path": str(transcript)},
        "stop",
        cfg,
    )

    spans = _spans(tmp_path)
    assert [span["name"] for span in spans] == [
        "chat",
        "execute_tool",
        "chat",
        "invoke_agent",
    ]
    assert [span["attributes"]["claude_code.genai_hook.step_index"] for span in spans[:3]] == [
        1,
        2,
        3,
    ]
    assert spans[0]["attributes"]["claude_code.genai_hook.step_name"] == "plan (1 tool) #1"
    assert spans[1]["attributes"]["claude_code.genai_hook.step_name"] == "tool: read #2"
    assert spans[1]["attributes"]["claude_code.genai_hook.plan_step"] == 1
    assert spans[2]["attributes"]["claude_code.genai_hook.step_name"] == "response #3"
    assert spans[-1]["attributes"]["claude_code.genai_hook.step_count"] == 3

    root = spans[-1]
    for span in spans[:-1]:
        assert span["parent_id"] == root["context"]["span_id"]

    assert spans[0]["end_time"] <= spans[1]["start_time"]
    assert spans[1]["end_time"] <= spans[2]["start_time"]
