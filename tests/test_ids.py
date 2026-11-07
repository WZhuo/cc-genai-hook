from cc_genai_hook.ids import span_id, trace_id


def test_trace_id_is_deterministic_and_non_zero():
    first = trace_id("session-1", "prompt-1")
    second = trace_id("session-1", "prompt-1")
    assert first == second
    assert 0 < first < (1 << 128)
    assert trace_id("session-1", "prompt-2") != first


def test_span_id_is_deterministic_and_non_zero():
    first = span_id("chat", "message-1")
    second = span_id("chat", "message-1")
    assert first == second
    assert 0 < first < (1 << 64)
    assert span_id("chat", "message-2") != first


def test_kind_and_key_are_namespaced():
    assert span_id("chat", "shared") != span_id("execute_tool", "shared")
