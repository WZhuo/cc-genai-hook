from cc_genai_hook.genai import ContentProcessor, chat_attributes, root_attributes
from cc_genai_hook.transcript import TimelineItem

COMPAT = {
    "messages_format": "json",
    "cache_write_attribute": "cache_creation",
    "emit_provider_name": True,
    "emit_system_legacy": True,
}


def _items():
    prompt = TimelineItem(
        kind="user",
        prompt_id="p1",
        blocks=[{"type": "text", "text": "say secret"}],
        ts="2026-11-04T10:00:00Z",
        last_ts="2026-11-04T10:00:00Z",
    )
    assistant = TimelineItem(
        kind="assistant",
        message_id="m1",
        prompt_id="p1",
        model="claude-sonnet-4",
        requested_model="claude-sonnet",
        stop_reason="end_turn",
        usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 1,
        },
        blocks=[{"type": "text", "text": "my secret answer"}],
        ts="2026-11-04T10:00:01Z",
        last_ts="2026-11-04T10:00:01Z",
    )
    return prompt, assistant


def test_chat_attributes_use_inclusive_input_tokens():
    prompt, assistant = _items()
    timeline = [prompt, assistant]
    content = ContentProcessor({"mode": "last_turn", "include_thinking": False})
    attrs = chat_attributes(assistant, 1, timeline, "s1", content, COMPAT)
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.provider.name"] == "anthropic"
    assert attrs["gen_ai.request.model"] == "claude-sonnet"
    assert attrs["gen_ai.response.model"] == "claude-sonnet-4"
    assert attrs["gen_ai.response.finish_reasons"] == ["end_turn"]
    assert attrs["gen_ai.usage.input_tokens"] == 13
    assert attrs["gen_ai.usage.output_tokens"] == 5
    assert attrs["gen_ai.usage.cache_read.input_tokens"] == 2
    assert attrs["gen_ai.usage.cache_creation.input_tokens"] == 1
    assert "say secret" in attrs["gen_ai.input.messages"]
    assert "my secret answer" in attrs["gen_ai.output.messages"]


def test_chat_attributes_redact_and_truncate():
    prompt, assistant = _items()
    timeline = [prompt, assistant]
    content = ContentProcessor(
        {
            "mode": "last_turn",
            "redact_patterns": ["secret"],
            "max_message_chars": 200,
        }
    )
    attrs = chat_attributes(assistant, 1, timeline, "s1", content, COMPAT)
    assert "secret" not in attrs["gen_ai.output.messages"]
    assert "[REDACTED]" in attrs["gen_ai.output.messages"]

    truncated = ContentProcessor(
        {
            "mode": "last_turn",
            "max_message_chars": 10,
        }
    )
    attrs = chat_attributes(assistant, 1, timeline, "s1", truncated, COMPAT)
    assert attrs["claude_code.genai_hook.output.messages.truncated"] is True


def test_root_attributes_sum_usage():
    prompt, assistant = _items()
    content = ContentProcessor({"mode": "last_turn"})
    attrs = root_attributes(
        [assistant], prompt, "s1", 0, "claude-code", content, COMPAT
    )
    assert attrs["gen_ai.operation.name"] == "invoke_agent"
    assert attrs["gen_ai.agent.name"] == "claude-code"
    assert attrs["gen_ai.usage.input_tokens"] == 13
    assert attrs["gen_ai.usage.output_tokens"] == 5
    assert attrs["claude_code.genai_hook.turn.prompt_id"] == "p1"
    assert attrs["claude_code.genai_hook.synthetic"] is True


def test_content_mode_none_omits_messages():
    prompt, assistant = _items()
    content = ContentProcessor({"mode": "none"})
    attrs = chat_attributes(assistant, 1, [prompt, assistant], "s1", content, COMPAT)
    assert "gen_ai.input.messages" not in attrs
    assert "gen_ai.output.messages" not in attrs
