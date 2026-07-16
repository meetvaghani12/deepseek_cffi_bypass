"""Unit tests for the DeepSeek web SSE parser — pure logic, no network/browser."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.sse_parser import parse_stream, StreamAccumulator


def test_flat_format_sticky_path():
    """OLD flat format: path stated once, then bare `v` chunks must append to it."""
    raw = "\n".join([
        'data: {"p": "response/thinking_content", "v": "Let me"}',
        'data: {"o": "APPEND", "v": " think"}',
        'data: {"v": " about it"}',
        'data: {"p": "response/content", "v": "Hello"}',
        'data: {"v": ", world"}',
        'data: {"v": "!"}',
        'data: {"p": "response/status", "v": "FINISHED"}',
    ])
    r = parse_stream(raw)
    assert r.content == "Hello, world!", repr(r.content)
    assert r.thinking == "Let me think about it", repr(r.thinking)
    assert r.finished is True


def test_fragments_format():
    """NEW fragments format: typed THINK/RESPONSE fragments + -1/content appends."""
    raw = "\n".join([
        'data: {"v": {"response": {"message_id": 42, "fragments": [{"type": "THINK", "content": "hmm"}]}}}',
        'data: {"p": "response/fragments/-1/content", "o": "APPEND", "v": " ok"}',
        'data: {"p": "response/fragments", "o": "APPEND", "v": [{"type": "RESPONSE", "content": "Answer"}]}',
        'data: {"p": "response/fragments/-1/content", "v": " text"}',
        'data: [DONE]',
    ])
    r = parse_stream(raw)
    assert r.thinking == "hmm ok", repr(r.thinking)
    assert r.content == "Answer text", repr(r.content)
    assert r.message_id == 42, repr(r.message_id)  # native int, not stringified
    assert r.finished is True


def test_message_id_capture_preserves_int_type():
    """message_id must stay an int — it round-trips as parent_message_id (u32); a
    string there 422s the real DeepSeek API."""
    raw = 'data: {"v": {"response": {"message_id": 123456, "fragments": []}}}\ndata: {"p":"response/content","v":"hi"}\ndata: [DONE]'
    r = parse_stream(raw)
    assert r.message_id == 123456
    assert isinstance(r.message_id, int), "message_id must not be stringified"
    assert r.content == "hi"


def test_skips_events_and_comments():
    raw = "\n".join([
        ': ping',
        'event: update_session',
        'data: {"p": "response/content", "v": "x"}',
        'data: {}',
        'data: {"v": "y"}',
    ])
    r = parse_stream(raw)
    assert r.content == "xy", repr(r.content)


def test_pathless_before_any_path_defaults_to_content():
    """Defensive: a bare v before any path should land in answer (phase defaults to content)."""
    raw = 'data: {"v": "orphan"}'
    r = parse_stream(raw)
    assert r.content == "orphan"
    assert r.thinking == ""


def test_streaming_accumulator_partial_lines():
    """Feeding split chunks (mid-line) must not lose or corrupt data."""
    acc = StreamAccumulator()
    acc.feed('data: {"p": "response/content", "v": "Hel')
    acc.feed('lo"}\ndata: {"v": " wor')
    acc.feed('ld"}\n')
    r = acc.finish()
    assert r.content == "Hello world", repr(r.content)


def test_streaming_accumulator_snapshot_grows():
    acc = StreamAccumulator()
    r1 = acc.feed('data: {"p":"response/content","v":"A"}\n')
    assert r1.content == "A"
    r2 = acc.feed('data: {"v":"B"}\n')
    assert r2.content == "AB"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
