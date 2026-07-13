"""
OpenAI-compatible proxy with model-based tool calling.

 Bridges opencode -> localhost:5051 -> local tool execution
                                  \-> deepseek-bypass -> chat.deepseek.com

Key: Returns streaming SSE responses in OpenAI format so AI SDK can parse tool calls.
"""
import json
import re
import uuid
import time
import logging
import threading
import os
import traceback
from flask import Flask, request, jsonify, Response

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.agent_swarm import ToolExecutor

app = Flask(__name__)
logger = logging.getLogger(__name__)

_tool_executor = ToolExecutor()

_client = None
_client_lock = threading.Lock()


def get_client():
    global _client
    with _client_lock:
        if _client is None:
            from src.api.client import DeepSeekClient
            _client = DeepSeekClient()
            time.sleep(2)
        return _client


# ============================================================
# Tool instructions for the model
# ============================================================

TOOL_INSTRUCTIONS_TEMPLATE = """
CRITICAL: You have tools connected to a real computer. USE THEM.

When the user asks you to perform ANY action, you MUST use a tool.
NEVER say "I can't" or "I don't have access". You DO have access through tools.

To use a tool, write EXACTLY this XML format:

<tool_call>
{{"name": "tool_name", "arguments": {{"param": "value"}}}}
</tool_call>

RULES:
- Directly do what was asked (mkdir for create folder, NOT ls)
- Only use parameters defined in the tool — NO extra params
- One tool call per response
- After getting a result, respond naturally

Available tools:
{tools_desc}
"""


def format_tools_for_model(tools):
    if not tools:
        return ""
    lines = []
    for tool in tools:
        func = tool.get("function", {})
        name = func.get("name", "")
        desc = func.get("description", "")
        params = func.get("parameters", {}).get("properties", {})
        param_str = ", ".join(f'{k} ({v.get("type","string")})' for k, v in params.items())
        lines.append(f"- {name}: {desc} | params: {param_str or 'none'}")
    return "\n".join(lines)


def extract_tool_calls(text):
    """Extract tool calls from model response - handles XML tags and plain JSON."""
    calls = []

    # 1. Try <tool_call> XML format first
    xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
    for m in re.findall(xml_pattern, text, re.DOTALL):
        try:
            calls.append(json.loads(m))
        except json.JSONDecodeError:
            pass
    if calls:
        return calls

    # 2. Try plain JSON with "name" + "arguments" keys (model sometimes outputs raw JSON)
    #    Match JSON objects that look like tool calls
    json_pattern = r'\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^{}]*\}\s*\}'
    for m in re.finditer(json_pattern, text):
        try:
            obj = json.loads(m.group())
            if "name" in obj and "arguments" in obj:
                calls.append(obj)
        except json.JSONDecodeError:
            pass
    if calls:
        return calls

    # 3. Try {"name": "...", "arguments": {...}} even with nested args
    json_pattern2 = r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{.*?\})\s*\}'
    for m in re.finditer(json_pattern2, text, re.DOTALL):
        try:
            name = m.group(1)
            args = json.loads(m.group(2))
            calls.append({"name": name, "arguments": args})
        except json.JSONDecodeError:
            pass

    return calls


# ============================================================
# Streaming SSE helper
# ============================================================

def make_sse_chunk(chat_id, created, model, delta, finish_reason=None):
    chunk = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def stream_tool_call(chat_id, created, model, tool_name, tool_args, call_id):
    """Stream a tool call in OpenAI SSE format."""
    # First chunk: role + tool call start
    yield make_sse_chunk(chat_id, created, model, {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": tool_name, "arguments": ""},
        }],
    })

    # Argument chunks (simulate streaming by sending in parts)
    args_str = json.dumps(tool_args)
    chunk_size = 50
    for i in range(0, len(args_str), chunk_size):
        yield make_sse_chunk(chat_id, created, model, {
            "tool_calls": [{
                "index": 0,
                "function": {"arguments": args_str[i:i+chunk_size]},
            }],
        })

    # Finish
    yield make_sse_chunk(chat_id, created, model, {}, finish_reason="tool_calls")
    yield "data: [DONE]\n\n"


def stream_text(chat_id, created, model, content):
    """Stream text content in OpenAI SSE format."""
    yield make_sse_chunk(chat_id, created, model, {"role": "assistant", "content": ""})

    words = content.split(" ")
    for i, word in enumerate(words):
        text = word + (" " if i < len(words) - 1 else "")
        yield make_sse_chunk(chat_id, created, model, {"content": text})

    yield make_sse_chunk(chat_id, created, model, {}, finish_reason="stop")
    yield "data: [DONE]\n\n"


# ============================================================
# Routes
# ============================================================

@app.route("/v1/models", methods=["GET"])
def list_models():
    return jsonify({
        "object": "list",
        "data": [{"id": "deepseek-chat", "object": "model", "created": 1686935002, "owned_by": "deepseek"}],
    })


@app.route("/v1/execute-tool", methods=["POST"])
def execute_tool():
    data = request.get_json()
    result = _tool_executor.execute(data.get("tool_name", ""), data.get("arguments", {}))
    return jsonify({"result": result})


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.get_json()
    messages = data.get("messages", [])
    model = data.get("model", "deepseek-chat")
    stream = data.get("stream", False)
    tools = data.get("tools")

    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    # Check if there's a tool result to send back to the model
    last_msg = messages[-1] if messages else {}
    if last_msg.get("role") == "tool":
        return _handle_tool_result(messages, chat_id, created, model, stream)

    # Build context with tool instructions
    context = _build_context(messages, tools)

    # Call the model (get full response, then stream it)
    try:
        client = get_client()
        resp = client.chat(context)
        content = resp.choices[0].message.content
    except Exception as e:
        logger.exception("chat error")
        if stream:
            return Response(
                stream_text(chat_id, created, model, f"Error: {e}"),
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return jsonify({"error": {"message": str(e), "type": "server_error"}}), 500

    # Check for tool calls in response (model outputs text with tool call embedded)
    if tools and content:
        tool_calls = extract_tool_calls(content)
        if tool_calls:
            tc = tool_calls[0]
            tool_name = tc.get("name", "")
            tool_args = tc.get("arguments", {})
            call_id = f"call_{uuid.uuid4().hex[:8]}"

            logger.info(f"TOOL CALL DETECTED: {tool_name}({tool_args})")
            return Response(
                stream_tool_call(chat_id, created, model, tool_name, tool_args, call_id),
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    # No tool calls — stream text response
    if stream:
        return Response(
            stream_text(chat_id, created, model, content),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return jsonify({
        "id": chat_id, "object": "chat.completion", "created": created, "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


def _handle_tool_result(messages, chat_id, created, model, stream):
    """When opencode sends tool results back, forward to model and stream response."""
    # Build context from all messages
    context = _build_context(messages, None)

    try:
        client = get_client()
        resp = client.chat(context)
        content = resp.choices[0].message.content
    except Exception as e:
        logger.exception("tool result error")
        if stream:
            return Response(
                stream_text(chat_id, created, model, f"Error: {e}"),
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return jsonify({"error": {"message": str(e), "type": "server_error"}}), 500

    # Check for more tool calls
    tool_calls = extract_tool_calls(content) if content else []
    if tool_calls:
        tc = tool_calls[0]
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        if stream:
            return Response(
                stream_tool_call(chat_id, created, model, tc["name"], tc.get("arguments", {}), call_id),
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    if stream:
        return Response(
            stream_text(chat_id, created, model, content),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return jsonify({
        "id": chat_id, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


def _build_context(messages, tools):
    """Build context with tool instructions at the end."""
    parts = []
    tool_instruction = None

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            # Skip opencode's system prompt, keep short ones
            if content and "You are opencode" not in content:
                parts.append(f"System: {content}")
        elif role == "user":
            if isinstance(content, str):
                parts.append(f"User: {content}")
            elif isinstance(content, list):
                text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
                parts.append(f"User: {text}")
        elif role == "assistant":
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    parts.append(f"[Tool called: {func.get('name', '')}({func.get('arguments', '')})]")
            elif content:
                parts.append(f"Assistant: {content[:300]}")
        elif role == "tool":
            parts.append(f"[Tool result]: {content}")

    result = "\n\n".join(parts)

    # Append tool instructions at the very end
    if tools:
        tools_desc = format_tools_for_model(tools)
        tool_instruction = TOOL_INSTRUCTIONS_TEMPLATE.format(tools_desc=tools_desc)
        result += "\n\n" + tool_instruction

    return result


@app.route("/v1/auth/token", methods=["POST"])
def auth_token():
    return jsonify({"access_token": "bypass", "token_type": "bearer"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s:%(levelname)s:%(message)s")

    print("\n  DeepSeek Proxy + Tool Calling — http://localhost:5051\n")
    print("  Tool calling: MODEL-BASED (streaming SSE)")
    print("  Chat: via PersistentSession")
    print()

    def _warmup():
        try:
            get_client()
            print("  [ready] Browser session active!")
        except Exception as e:
            print(f"  [error] Browser failed: {e}")
            traceback.print_exc()

    threading.Thread(target=_warmup, daemon=True).start()

    print("  Starting Flask...\n")
    app.run(host="0.0.0.0", port=5051, debug=False)
