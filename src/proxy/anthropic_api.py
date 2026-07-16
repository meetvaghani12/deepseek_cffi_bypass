"""
Anthropic Messages API translation for Claude Code.

Claude Code (and anything pointed at ANTHROPIC_BASE_URL) speaks the Anthropic Messages
API — `POST /v1/messages` with `system` + `messages` + `tools`, and it expects either a
JSON message with content blocks, or an SSE stream of message_start / content_block_* /
message_delta / message_stop events.

This module converts between that wire format and the internal OpenAI-shaped
representation the proxy already uses (so `run_turn` is shared across opencode and Claude
Code). It does NOT talk to DeepSeek — it only reshapes requests and responses.

Anthropic request shape (relevant parts):
  {
    "model": "...", "max_tokens": N, "stream": bool,
    "system": "..." | [{"type":"text","text":"..."}],
    "tools": [{"name","description","input_schema":{...}}],
    "messages": [
      {"role":"user","content":"..." | [blocks]},
      {"role":"assistant","content":[{"type":"text",...},{"type":"tool_use","id","name","input"}]},
      {"role":"user","content":[{"type":"tool_result","tool_use_id","content"}]},
    ]
  }
"""
import json
import time
import uuid
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# Request:  Anthropic  ->  internal (OpenAI-shaped messages + tools)
# ---------------------------------------------------------------------------

def _text_from_anthropic_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def conversation_id_from_metadata(data: Dict[str, Any]) -> str:
    """
    Extract a stable per-conversation key from Claude Code's request metadata.

    Claude Code sets `metadata.user_id` to a per-session identity that stays constant across
    all turns of one `claude` session (main AND background requests) and changes between
    sessions. Two encodings are seen in the wild — handle both:
      1. A JSON string: `{"device_id":"...","account_uuid":"...","session_id":"<uuid>"}`
         (the actual v2.x wire format — verified against real captures). Use `session_id`.
      2. A template string: `user_<userID>_account_<accountUuid>_session_<sessionId>`
         (seen in the binary). Use the `_session_` tail.
    Returns "" when absent (e.g. opencode) so the tracker falls back to content-hashing.
    """
    meta = data.get("metadata") or {}
    uid = meta.get("user_id")
    if not isinstance(uid, str) or not uid:
        return ""

    # Form 1: JSON-encoded object with a session_id field.
    s = uid.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            sid = obj.get("session_id")
            if isinstance(sid, str) and sid:
                return sid
        except (json.JSONDecodeError, TypeError):
            pass  # fall through to template / raw handling

    # Form 2: `..._session_<id>` template.
    marker = "_session_"
    idx = uid.rfind(marker)
    if idx >= 0:
        tail = uid[idx + len(marker):]
        if tail:
            return tail

    # Unknown format — use the whole value (still per-user stable, better than nothing).
    return uid


def anthropic_to_internal(data: Dict[str, Any]) -> Tuple[List[Dict], List[Dict]]:
    """
    Convert an Anthropic /v1/messages request body into (messages, tools) in the
    OpenAI shape that `run_turn` consumes.
    """
    messages: List[Dict[str, Any]] = []

    # system -> a leading system message (run_turn folds it into the DeepSeek prompt).
    system = data.get("system")
    system_text = _text_from_anthropic_content(system) if system else ""
    if system_text:
        messages.append({"role": "system", "content": system_text})

    for msg in data.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")

        if role == "user":
            # A user turn may carry tool_result blocks (Anthropic puts them here).
            if isinstance(content, list):
                tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
                text = _text_from_anthropic_content(content)
                if text:
                    messages.append({"role": "user", "content": text})
                for tr in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": _tool_result_text(tr.get("content")),
                    })
            else:
                messages.append({"role": "user", "content": _text_from_anthropic_content(content)})

        elif role == "assistant":
            # Assistant turn may carry text and/or tool_use blocks.
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                if tool_uses:
                    messages.append({
                        "role": "assistant",
                        "content": "\n".join(text_parts) or None,
                        "tool_calls": [{
                            "id": tu.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tu.get("name", ""),
                                "arguments": json.dumps(tu.get("input", {}), ensure_ascii=False),
                            },
                        } for tu in tool_uses],
                    })
                elif text_parts:
                    messages.append({"role": "assistant", "content": "\n".join(text_parts)})
            else:
                messages.append({"role": "assistant", "content": _text_from_anthropic_content(content)})

    # tools: Anthropic uses top-level {name, description, input_schema};
    # normalize to the OpenAI {function:{name,description,parameters}} shape.
    tools = None
    if data.get("tools"):
        tools = [{
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}) or {},
            },
        } for t in data["tools"] if isinstance(t, dict) and t.get("name")]

    return messages, tools


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return "" if content is None else str(content)


# ---------------------------------------------------------------------------
# Response:  internal  ->  Anthropic (non-streaming JSON)
# ---------------------------------------------------------------------------

def build_anthropic_message(model: str, text: str, tool_calls: List[Any]) -> Dict[str, Any]:
    """Build a non-streaming Anthropic Messages response with content blocks."""
    content_blocks: List[Dict[str, Any]] = []
    if tool_calls:
        for tc in tool_calls:
            content_blocks.append({
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:20]}",
                "name": tc.name,
                "input": tc.arguments,
            })
        stop_reason = "tool_use"
    else:
        content_blocks.append({"type": "text", "text": text or ""})
        stop_reason = "end_turn"

    return {
        "id": f"msg_{uuid.uuid4().hex[:20]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


# ---------------------------------------------------------------------------
# Response:  internal  ->  Anthropic (streaming SSE)
# ---------------------------------------------------------------------------

def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def stream_anthropic_text(model: str, text: str):
    """Emit the Anthropic SSE event sequence for a plain text response."""
    msg_id = f"msg_{uuid.uuid4().hex[:20]}"
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield _sse("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    for i in range(0, len(text), 64):
        yield _sse("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text[i:i + 64]},
        })
    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 0},
    })
    yield _sse("message_stop", {"type": "message_stop"})


def stream_anthropic_tool_calls(model: str, tool_calls: List[Any]):
    """Emit the Anthropic SSE event sequence for one or more tool_use blocks."""
    msg_id = f"msg_{uuid.uuid4().hex[:20]}"
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    for idx, tc in enumerate(tool_calls):
        tool_id = f"toolu_{uuid.uuid4().hex[:20]}"
        yield _sse("content_block_start", {
            "type": "content_block_start", "index": idx,
            "content_block": {"type": "tool_use", "id": tool_id, "name": tc.name, "input": {}},
        })
        # input_json_delta streams the arguments as a JSON string in chunks.
        args_json = json.dumps(tc.arguments, ensure_ascii=False)
        for i in range(0, len(args_json), 128):
            yield _sse("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {"type": "input_json_delta", "partial_json": args_json[i:i + 128]},
            })
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
        "usage": {"output_tokens": 0},
    })
    yield _sse("message_stop", {"type": "message_stop"})
