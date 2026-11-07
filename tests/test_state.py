from cc_genai_hook.ids import span_id, trace_id
from cc_genai_hook.state import StateStore


def test_segments_messages_and_idempotency(tmp_path):
    store = StateStore(str(tmp_path / "state"))
    try:
        store.ensure_session("s1", "/tmp/t.jsonl", "2.1.311", "/tmp")
        tid = trace_id("s1", "p1")
        seq, root_sid = store.begin_segment("s1", "p1", tid, 1)
        assert seq == 0
        assert root_sid == span_id("invoke_agent", "s1:p1:0")
        seq2, root_sid2 = store.begin_segment("s1", "p1", tid, 2)
        assert seq2 == 1
        assert root_sid2 == span_id("invoke_agent", "s1:p1:1")
        store.mark_messages("s1", [("m1", "p1", span_id("chat", "m1"))])
        store.mark_messages("s1", [("m1", "p1", span_id("chat", "m1"))])
        assert store.emitted_message_ids("s1") == {"m1"}
        assert store.counts() == (1, 2, 1)
    finally:
        store.close()


def test_tool_idempotency(tmp_path):
    store = StateStore(str(tmp_path / "state"))
    try:
        tool_sid = span_id("execute_tool", "t1")
        assert store.tool_emitted("s1", "t1") is False
        store.mark_tool("s1", "t1", "p1", tool_sid)
        store.mark_tool("s1", "t1", "p1", tool_sid)
        assert store.tool_emitted("s1", "t1") is True
        assert store.tool_count() == 1
    finally:
        store.close()


def test_seed_marks_all_messages(tmp_path):
    store = StateStore(str(tmp_path / "state"))
    try:
        store.mark_all_messages("s1", [("m1", "p1"), ("m2", "p1")])
        assert store.emitted_message_ids("s1") == {"m1", "m2"}
    finally:
        store.close()
