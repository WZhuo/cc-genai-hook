import json

from cc_genai_hook.config import load_config
from cc_genai_hook.hook import handle_stop


def _config(tmp_path):
    path = tmp_path / "genai-hook.json"
    path.write_text(
        json.dumps(
            {
                "exporter": {"endpoint": "http://127.0.0.1:4318"},
                "hook": {"dry_run": True, "tool_mode": "ordered"},
                "state": {"dir": str(tmp_path / "state")},
            }
        )
    )
    return load_config(str(path))


def _write_rows(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_parent_stop_builds_subagent_subtree(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    parent = projects / "s1.jsonl"
    sub_dir = projects / "s1" / "subagents"
    sub_dir.mkdir(parents=True)
    subagent = sub_dir / "agent-abc.jsonl"

    _write_rows(
        parent,
        [
            {
                "type": "user",
                "promptId": "p1",
                "uuid": "u1",
                "origin": {"kind": "human"},
                "timestamp": "2026-11-08T10:00:00Z",
                "sessionId": "s1",
                "version": "2.1.311",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "delegate"}],
                },
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "requestedModel": "claude-sonnet",
                "timestamp": "2026-11-08T10:00:01Z",
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
                            "id": "agent-tool-1",
                            "name": "Agent",
                            "input": {"prompt": "explore"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "promptId": "p1",
                "uuid": "u2",
                "timestamp": "2026-11-08T10:00:04Z",
                "sessionId": "s1",
                "toolUseResult": {"agentId": "abc", "agentType": "Explore"},
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "agent-tool-1",
                            "content": [{"type": "text", "text": "report"}],
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "uuid": "a2",
                "requestedModel": "claude-sonnet",
                "timestamp": "2026-11-08T10:00:05Z",
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
        ],
    )
    _write_rows(
        subagent,
        [
            {
                "type": "user",
                "promptId": "sp1",
                "uuid": "su1",
                "origin": {"kind": "human"},
                "timestamp": "2026-11-08T10:00:02Z",
                "sessionId": "s1",
                "version": "2.1.311",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "explore"}],
                },
            },
            {
                "type": "assistant",
                "uuid": "sa1",
                "requestedModel": "claude-sonnet",
                "timestamp": "2026-11-08T10:00:03Z",
                "sessionId": "s1",
                "version": "2.1.311",
                "message": {
                    "id": "sm1",
                    "role": "assistant",
                    "model": "claude-sonnet-4",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                    "content": [{"type": "text", "text": "sub result"}],
                },
            },
        ],
    )

    cfg = _config(tmp_path)
    handle_stop(
        {"session_id": "s1", "transcript_path": str(parent)},
        "stop",
        cfg,
    )

    spans = [
        json.loads(line)
        for line in (tmp_path / "state" / "dry-run.jsonl").read_text().splitlines()
        if line
    ]
    by_name = {}
    for span in spans:
        by_name.setdefault(span["name"], []).append(span)

    assert len(by_name["chat"]) == 3
    assert len(by_name["execute_tool"]) == 1
    assert len(by_name["subagent"]) == 1
    assert len(by_name["invoke_agent"]) == 1

    root = by_name["invoke_agent"][0]
    tool = by_name["execute_tool"][0]
    container = by_name["subagent"][0]
    sub_chat = [
        span
        for span in by_name["chat"]
        if span["attributes"].get("claude_code.genai_hook.step_name")
        == "subagent response #1"
    ][0]

    assert tool["parent_id"] == root["context"]["span_id"]
    assert container["parent_id"] == tool["context"]["span_id"]
    assert sub_chat["parent_id"] == container["context"]["span_id"]
    assert container["attributes"]["gen_ai.agent.id"] == "abc"
    assert container["attributes"]["gen_ai.agent.name"] == "Explore"
    assert container["attributes"]["claude_code.genai_hook.subagent"] is True
    assert (
        container["attributes"]["claude_code.genai_hook.parent.tool_use_id"]
        == "agent-tool-1"
    )
    assert sub_chat["attributes"]["claude_code.genai_hook.step_index"] == 1
    assert tool["end_time"] >= container["end_time"]

    for span in spans:
        assert span["context"]["trace_id"] == root["context"]["trace_id"]
