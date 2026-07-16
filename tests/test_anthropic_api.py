"""Tests for the Anthropic Messages API translation (Claude Code path), mock DeepSeek."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.proxy_server as proxy
from src.proxy import anthropic_api
from src.api.models import ChatResponse, Choice, ChatMessage


class MockClient:
    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts = []
    def create_session(self):
        return "S1"
    def chat(self, prompt, chat_session_id=None, parent_message_id=None, **kw):
        self.prompts.append(prompt)
        reply = self._replies.pop(0) if self._replies else ""
        return ChatResponse(id="M1", choices=[Choice(message=ChatMessage(role="assistant", content=reply))])


def _install(replies):
    from src.proxy.conversation import ConversationTracker
    proxy._tracker = ConversationTracker()
    mock = MockClient(replies)
    proxy.get_client = lambda: mock
    proxy._client = mock
    return mock


# ---- request translation ----

def test_anthropic_request_to_internal_basic():
    data = {
        "system": "You are helpful.",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "read", "description": "Read", "input_schema": {"type": "object", "properties": {"filePath": {"type": "string"}}}}],
    }
    messages, tools = anthropic_api.anthropic_to_internal(data)
    assert messages[0] == {"role": "system", "content": "You are helpful."}
    assert messages[1] == {"role": "user", "content": "hi"}
    assert tools[0]["function"]["name"] == "read"
    assert tools[0]["function"]["parameters"]["properties"]["filePath"]["type"] == "string"


def test_anthropic_tool_result_becomes_tool_message():
    data = {"messages": [
        {"role": "user", "content": "read x"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "read", "input": {"filePath": "x"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file body"}]},
    ]}
    messages, _ = anthropic_api.anthropic_to_internal(data)
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "read"
    assert messages[2] == {"role": "tool", "tool_call_id": "toolu_1", "content": "file body"}


# ---- conversation id from metadata ----

def test_conversation_id_extracts_session_component():
    data = {"metadata": {"user_id": "user_abc123_account_uuid-999_session_SESS-42"}}
    assert anthropic_api.conversation_id_from_metadata(data) == "SESS-42"


def test_conversation_id_absent_returns_empty():
    assert anthropic_api.conversation_id_from_metadata({}) == ""
    assert anthropic_api.conversation_id_from_metadata({"metadata": {}}) == ""


def test_conversation_id_no_session_marker_falls_back_to_uid():
    data = {"metadata": {"user_id": "user_only"}}
    assert anthropic_api.conversation_id_from_metadata(data) == "user_only"


# ---- response: non-streaming ----

def test_build_anthropic_text_message():
    body = anthropic_api.build_anthropic_message("deepseek-chat", "hello there", [])
    assert body["type"] == "message"
    assert body["stop_reason"] == "end_turn"
    assert body["content"][0] == {"type": "text", "text": "hello there"}


def test_build_anthropic_tool_use_message():
    from src.proxy.tool_protocol import ToolCall
    body = anthropic_api.build_anthropic_message(
        "deepseek-chat", "", [ToolCall(name="read", arguments={"filePath": "a.py"})])
    assert body["stop_reason"] == "tool_use"
    blk = body["content"][0]
    assert blk["type"] == "tool_use"
    assert blk["name"] == "read"
    assert blk["input"] == {"filePath": "a.py"}
    assert blk["id"].startswith("toolu_")


# ---- response: streaming SSE shape ----

def test_stream_text_event_sequence():
    events = "".join(anthropic_api.stream_anthropic_text("m", "hi"))
    for ev in ["message_start", "content_block_start", "content_block_delta",
               "content_block_stop", "message_delta", "message_stop"]:
        assert f"event: {ev}" in events, ev
    assert '"text_delta"' in events
    assert '"stop_reason": "end_turn"' in events


def test_stream_tool_use_event_sequence():
    from src.proxy.tool_protocol import ToolCall
    events = "".join(anthropic_api.stream_anthropic_tool_calls(
        "m", [ToolCall(name="bash", arguments={"command": "ls"})]))
    assert '"type": "tool_use"' in events
    assert '"input_json_delta"' in events
    assert '"stop_reason": "tool_use"' in events
    # the streamed partial_json must reconstruct to the real arguments
    import re
    parts = re.findall(r'"partial_json": "((?:[^"\\]|\\.)*)"', events)
    joined = "".join(json.loads(f'"{p}"') for p in parts)
    assert json.loads(joined) == {"command": "ls"}


# ---- full HTTP round trip through the proxy ----

def test_messages_endpoint_tool_call_nonstream():
    _install(["<read><filePath>a.py</filePath></read>"])
    c = proxy.app.test_client()
    r = c.post("/v1/messages", json={
        "model": "deepseek-chat", "max_tokens": 1024, "stream": False,
        "system": "sys", "messages": [{"role": "user", "content": "read a.py"}],
        "tools": [{"name": "read", "description": "Read a file",
                   "input_schema": {"type": "object", "properties": {"filePath": {"type": "string"}},
                                    "required": ["filePath"]}}],
    })
    body = r.get_json()
    assert body["stop_reason"] == "tool_use"
    assert body["content"][0]["name"] == "read"
    assert body["content"][0]["input"] == {"filePath": "a.py"}


def test_messages_endpoint_text_stream():
    _install(["All done."])
    c = proxy.app.test_client()
    r = c.post("/v1/messages", json={
        "model": "deepseek-chat", "max_tokens": 1024, "stream": True,
        "messages": [{"role": "user", "content": "say done"}],
    })
    data = r.get_data(as_text=True)
    assert "event: message_start" in data
    assert "All done." in data.replace('"text_delta"', "")  # text present across deltas
    assert "event: message_stop" in data


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t(); print(f"  PASS  {t.__name__}"); passed += 1
        except Exception:
            print(f"  FAIL  {t.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
