"""
DeepSeek → OpenAI-compatible proxy for opencode.

Architecture (see research memos): opencode does NATIVE function calling via the Vercel
AI SDK — it sends OpenAI `tools`, runs its OWN agentic loop, and executes tools itself.
The web DeepSeek model only takes text and returns text. So this proxy is a TRANSLATOR,
not an agent:

  opencode --(OpenAI request: messages + tools)-->  proxy
  proxy    --(XML tool-teaching prompt + new turns)-->  DeepSeek web session
  DeepSeek --(text, possibly an XML tool call)-->  proxy
  proxy    --(native OpenAI tool_calls OR plain text)-->  opencode
  opencode executes the tool, appends the result, and calls the proxy again...

The proxy NEVER executes tools (opencode does that). It maps opencode's stateless full-
history requests onto DeepSeek's stateful session so context is cached server-side.
"""
import json
import re
import time
import uuid
import logging
import threading

from flask import Flask, request, jsonify, Response

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.proxy.tool_protocol import (
    build_tool_system_prompt,
    parse_tool_calls,
    to_openai_tool_call,
    strip_thinking,
)
from src.proxy.conversation import ConversationTracker
from src.proxy.render import render_turns
from src.proxy.tool_filter import filter_tools, summarize_filter
from src.proxy import anthropic_api
from src.proxy import aux_requests

app = Flask(__name__)
logger = logging.getLogger(__name__)

MODEL_ID = "deepseek-chat"

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_tracker = ConversationTracker()
_client = None
_client_lock = threading.Lock()


def get_client():
    global _client
    with _client_lock:
        if _client is None:
            from src.api.client import DeepSeekClient
            _client = DeepSeekClient()
            logger.info("DeepSeek client initialized")
        return _client


# ---------------------------------------------------------------------------
# Core translation: run one opencode request against DeepSeek
# ---------------------------------------------------------------------------

def run_turn(messages, tools, conversation_id=None, model=""):
    """
    Execute one client request against DeepSeek and return
    (assistant_text, tool_calls) where exactly one is meaningful:
      - tool_calls non-empty  -> the client should execute them
      - else assistant_text   -> final text for this turn
    `conversation_id` (Claude Code's stable session id) keys the session mapping.
    """
    # Short-circuit Claude Code's auxiliary requests (title-gen, autocomplete, quota)
    # BEFORE any DeepSeek work — no chat, no PoW, no latency, no output pollution.
    upstream_system = " ".join(
        (m.get("content") if isinstance(m.get("content"), str) else "")
        for m in messages if m.get("role") == "system"
    )
    aux = aux_requests.classify_and_answer(messages, upstream_system, model=model)
    if aux is not None:
        logger.info("aux request short-circuited (%d chars)", len(aux))
        return aux, []

    # Claude Code / opencode with many MCP servers can send 300-400 tools; DeepSeek can't
    # pick from that many and the catalog drowns the task. Filter to tools relevant to
    # THIS conversation (all core tools + MCP tools whose server is mentioned).
    if tools:
        convo_text = " ".join(
            (m.get("content") if isinstance(m.get("content"), str) else "")
            for m in messages
        )
        original_tools = tools
        tools = filter_tools(tools, convo_text)
        if len(tools) != len(original_tools):
            logger.info(summarize_filter(original_tools, tools))

    system_prompt = build_tool_system_prompt(tools) if tools else ""
    plan = _tracker.plan_turn(messages, system_prompt, conversation_id=conversation_id)

    prompt = render_turns(plan.new_turns, plan.is_new_session, plan.system_prompt)
    if not prompt.strip():
        return "", []

    client = get_client()

    # Resolve the DeepSeek session for this conversation.
    if plan.is_new_session or not plan.prior_session_id:
        session_id = client.create_session()
        parent_id = None
    else:
        session_id = plan.prior_session_id
        parent_id = plan.prior_parent_id

    # Upload any image/PDF attachments on the new turns and collect ref_file_ids.
    # Images fork to the vision model; PDFs/docs use OCR text extraction.
    ref_file_ids, model_type, extra_headers = _handle_attachments(client, plan.new_turns)

    resp = client.chat(
        prompt,
        chat_session_id=session_id,
        parent_message_id=parent_id,
        thinking_enabled=False,
        search_enabled=False,
        ref_file_ids=ref_file_ids or None,
        model_type=model_type,
        extra_headers=extra_headers or None,
    )
    raw = resp.choices[0].message.content or ""
    new_parent = resp.message_id if resp.id != "error" else None
    calls = parse_tool_calls(raw, tools) if tools else []

    # Weak-model recovery: if the model NARRATED an action ("I'll…", "Let me check…")
    # instead of emitting a tool call, the turn would end doing nothing. Re-prompt once
    # on the same DeepSeek session demanding the tool call.
    if tools and not calls and _looks_like_tool_intent(raw):
        logger.info("no tool call but intent narrated — re-prompting for the tool call")
        followup = client.chat(
            _REPROMPT,
            chat_session_id=session_id,
            parent_message_id=new_parent,
            thinking_enabled=False, search_enabled=False,
        )
        raw2 = followup.choices[0].message.content or ""
        calls2 = parse_tool_calls(raw2, tools)
        if calls2:
            raw, calls, new_parent = raw2, calls2, (followup.message_id or new_parent)
        else:
            # Still no call — keep whichever reply has real content.
            raw = raw2 or raw
            new_parent = followup.message_id or new_parent

    # Record outcome so the next request continues this DeepSeek session.
    _tracker.commit(plan, session_id, new_parent)

    if os.getenv("DEEPSEEK_DEBUG"):
        _debug_dump(messages, tools, plan, prompt, raw, calls)

    if calls:
        return "", calls
    return strip_thinking(raw).strip(), []


# A reply that announces intent without acting. If the model says any of these AND emits
# no tool call, it almost certainly meant to call a tool.
_INTENT_RE = re.compile(
    r"^\s*(?:sure[,!. ]|okay[,!. ]|ok[,!. ]|alright[,!. ]|"
    r"i'?ll\b|i will\b|let me\b|let's\b|lets\b|i'?m going to\b|i am going to\b|"
    r"i can\b|i'?d\b|first[,. ]|now[,. ]|to (?:do|answer|find|check|count)\b)",
    re.IGNORECASE,
)


def _looks_like_tool_intent(text: str) -> bool:
    t = strip_thinking(text or "").strip()
    if not t:
        return False
    # Narration is usually short and ends with a colon or an action promise.
    if _INTENT_RE.search(t):
        return True
    if t.rstrip().endswith(":") and len(t) < 400:
        return True
    return False


_REPROMPT = (
    "[system] You described an action but did not emit a tool call, so nothing happened. "
    "If a tool is needed, reply with ONLY the XML tool call now (e.g. "
    "<bash><command>...</command></bash>) and nothing else. If no tool is actually needed, "
    "give the complete final answer directly."
)


def _handle_attachments(client, new_turns):
    """
    Upload any image/document attachments on the new turns to DeepSeek and return
    (ref_file_ids, model_type, extra_headers). Images fork to the vision model (model_type
    'vision' + HIF headers); documents use OCR text extraction (normal model_type).
    Upload failures are logged and skipped — the turn still proceeds text-only.
    """
    attachments = []
    for m in new_turns:
        for att in (m.get("attachments") or []):
            attachments.append(att)
    if not attachments:
        return [], "default", {}

    ref_ids, any_vision = [], False
    for att in attachments:
        is_image = att.get("kind") == "image"
        try:
            result = client.session.upload_image(
                att["data"], media_type=att.get("media_type", "image/png"),
                filename=att.get("filename", "upload.bin"), vision=is_image,
            )
        except Exception as e:
            logger.warning("attachment upload raised: %s", e)
            continue
        if not isinstance(result, dict) or result.get("error"):
            logger.warning("attachment upload failed: %s", (result or {}).get("error"))
            continue
        ref_ids.append(result["file_id"])
        if result.get("vision"):
            any_vision = True
        logger.info("attachment uploaded: %s (vision=%s)", result["file_id"], result.get("vision"))

    if not ref_ids:
        return [], "default", {}

    model_type = "vision" if any_vision else "default"
    extra_headers = {}
    if any_vision:
        try:
            extra_headers = client.session.get_hif_headers() or {}
        except Exception as e:
            logger.warning("HIF header fetch failed: %s", e)
    return ref_ids, model_type, extra_headers


_DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "debug")
_debug_seq = 0


_meta_seq = 0


def _debug_request_meta(data, headers, query):
    """Dump request headers + metadata + model so we can see Claude Code's session identity."""
    global _meta_seq
    try:
        os.makedirs(_DEBUG_DIR, exist_ok=True)
        _meta_seq += 1
        # Redact the auth header; keep everything else.
        safe_headers = {k: ("<redacted>" if k.lower() in ("authorization", "x-api-key") else v)
                        for k, v in headers.items()}
        # Detect image content blocks so we can see exactly how Claude Code encodes them.
        image_blocks = []
        for m in data.get("messages", []):
            c = m.get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "image":
                        src = b.get("source", {}) or {}
                        image_blocks.append({
                            "source_type": src.get("type"),           # base64 | url | file
                            "media_type": src.get("media_type"),
                            "data_len": len(src.get("data", "")) if src.get("data") else 0,
                            "url": src.get("url"),
                            "file_id": src.get("file_id"),
                        })
        info = {
            "model": data.get("model"),
            "metadata": data.get("metadata"),
            "query": query,
            "headers": safe_headers,
            "n_messages": len(data.get("messages", [])),
            "system_type": type(data.get("system")).__name__,
            "image_blocks": image_blocks,
        }
        if image_blocks:
            logger.info("IMAGE request: %d image block(s) %s",
                        len(image_blocks), [(b["source_type"], b["media_type"]) for b in image_blocks])
        path = os.path.join(_DEBUG_DIR, f"meta_{_meta_seq:03d}.json")
        with open(path, "w") as f:
            json.dump(info, f, indent=2)
        logger.info("DEBUG meta -> %s (model=%s metadata=%s)",
                    path, data.get("model"), data.get("metadata"))
    except Exception as e:
        logger.warning("debug meta failed: %s", e)


def _debug_dump(messages, tools, plan, prompt, raw, calls):
    global _debug_seq
    try:
        os.makedirs(_DEBUG_DIR, exist_ok=True)
        _debug_seq += 1
        tool_names = [(t.get("function", t) or {}).get("name") for t in (tools or [])]
        path = os.path.join(_DEBUG_DIR, f"turn_{_debug_seq:03d}.txt")
        with open(path, "w") as f:
            f.write(f"=== is_new_session={plan.is_new_session} tools={tool_names}\n")
            f.write(f"=== incoming messages roles: {[m.get('role') for m in messages]}\n")
            f.write(f"=== parsed tool calls: {[(c.name, c.arguments) for c in calls]}\n")
            f.write(f"\n===== PROMPT SENT TO DEEPSEEK ({len(prompt)} chars) =====\n")
            f.write(prompt)
            f.write(f"\n\n===== RAW DEEPSEEK REPLY ({len(raw)} chars) =====\n")
            f.write(raw)
        logger.info("DEBUG dump -> %s (prompt=%d raw=%d calls=%d)",
                    path, len(prompt), len(raw), len(calls))
    except Exception as e:
        logger.warning("debug dump failed: %s", e)


# ---------------------------------------------------------------------------
# OpenAI SSE helpers
# ---------------------------------------------------------------------------

def _sse(chat_id, created, delta, finish_reason=None):
    chunk = {
        "id": chat_id, "object": "chat.completion.chunk", "created": created,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def stream_text(chat_id, created, text):
    yield _sse(chat_id, created, {"role": "assistant", "content": ""})
    for i in range(0, len(text), 64):
        yield _sse(chat_id, created, {"content": text[i:i + 64]})
    yield _sse(chat_id, created, {}, finish_reason="stop")
    yield "data: [DONE]\n\n"


def stream_tool_calls(chat_id, created, calls):
    openai_calls = [to_openai_tool_call(c, f"call_{uuid.uuid4().hex[:12]}") for c in calls]
    # First delta announces the assistant role.
    yield _sse(chat_id, created, {"role": "assistant", "content": None})
    for idx, oc in enumerate(openai_calls):
        # Announce the call (name), then stream its arguments.
        yield _sse(chat_id, created, {"tool_calls": [{
            "index": idx, "id": oc["id"], "type": "function",
            "function": {"name": oc["function"]["name"], "arguments": ""},
        }]})
        args = oc["function"]["arguments"]
        for i in range(0, len(args), 128):
            yield _sse(chat_id, created, {"tool_calls": [{
                "index": idx, "function": {"arguments": args[i:i + 128]},
            }]})
    yield _sse(chat_id, created, {}, finish_reason="tool_calls")
    yield "data: [DONE]\n\n"


# NB: no "Connection" / "Keep-Alive" here — those are hop-by-hop headers a WSGI app must
# not set (PEP 3333); waitress rejects them with an AssertionError. The server manages the
# connection. X-Accel-Buffering:no disables proxy buffering so SSE streams promptly.
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class _CollapseDoubledV1:
    """
    WSGI middleware that collapses a doubled `/v1/v1/` path prefix to `/v1/`.

    Tolerates a base URL written with a trailing /v1: clients (Claude Code, some OpenAI
    SDKs) append their own /v1/... path, so a base of http://host:5051/v1 produces
    /v1/v1/messages. This runs BEFORE Flask routing (a before_request hook is too late —
    Flask matches the route first), so the rewritten path is what gets matched.
    """

    def __init__(self, wsgi_app):
        self._app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        while path.startswith("/v1/v1/"):
            path = path[len("/v1"):]
        environ["PATH_INFO"] = path
        return self._app(environ, start_response)


app.wsgi_app = _CollapseDoubledV1(app.wsgi_app)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# Claude Code probes /v1/models for the exact model id it's configured with and refuses
# to start if it's absent. The proxy ignores the model field entirely (everything routes
# to DeepSeek), so we advertise the common Claude ids alongside deepseek-chat. Add more
# here if Claude Code is pointed at a different model string.
_ADVERTISED_MODELS = [
    MODEL_ID,
    "claude-opus-4-8", "claude-opus-4-8[1m]",
    "claude-opus-4-7", "claude-sonnet-5", "claude-haiku-4-5",
    "claude-3-5-haiku-20241022",  # Claude Code's small/background model
]


@app.route("/v1/models", methods=["GET"])
def list_models():
    return jsonify({"object": "list", "data": [
        {"id": m, "object": "model", "created": 1686935002, "owned_by": "deepseek-proxy"}
        for m in _ADVERTISED_MODELS
    ]})


@app.route("/v1/models/<path:model_id>", methods=["GET"])
def get_model(model_id):
    # Claude Code may GET a specific model to confirm it exists — always affirm.
    return jsonify({"id": model_id, "object": "model", "created": 1686935002,
                    "owned_by": "deepseek-proxy"})


@app.route("/v1/messages/count_tokens", methods=["POST"])
def count_tokens():
    # The web endpoint has no token accounting; Claude Code only needs a number back.
    data = request.get_json(force=True, silent=True) or {}
    approx = len(json.dumps(data.get("messages", []))) // 4
    return jsonify({"input_tokens": max(approx, 1)})


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.get_json(force=True, silent=True) or {}
    messages = data.get("messages", [])
    tools = data.get("tools")
    stream = data.get("stream", False)
    req_model = data.get("model", "")

    if not messages:
        return jsonify({"error": {"message": "no messages provided"}}), 400

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    try:
        text, calls = run_turn(messages, tools, model=req_model)
    except Exception as e:
        logger.exception("run_turn failed")
        err = f"[proxy error] {e}"
        if stream:
            return Response(stream_text(chat_id, created, err),
                            content_type="text/event-stream", headers=SSE_HEADERS)
        return jsonify(_completion_body(chat_id, created, err, None)), 200

    if calls:
        if stream:
            return Response(stream_tool_calls(chat_id, created, calls),
                            content_type="text/event-stream", headers=SSE_HEADERS)
        return jsonify(_completion_body(chat_id, created, None, calls)), 200

    if stream:
        return Response(stream_text(chat_id, created, text),
                        content_type="text/event-stream", headers=SSE_HEADERS)
    return jsonify(_completion_body(chat_id, created, text, None)), 200


def _completion_body(chat_id, created, text, calls):
    if calls:
        openai_calls = [to_openai_tool_call(c, f"call_{uuid.uuid4().hex[:12]}") for c in calls]
        message = {"role": "assistant", "content": None, "tool_calls": openai_calls}
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": text or ""}
        finish = "stop"
    return {
        "id": chat_id, "object": "chat.completion", "created": created, "model": MODEL_ID,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.route("/v1/messages", methods=["POST"])
def anthropic_messages():
    """Anthropic Messages API endpoint — lets Claude Code use DeepSeek as its provider."""
    data = request.get_json(force=True, silent=True) or {}
    model = data.get("model", MODEL_ID)
    stream = data.get("stream", False)

    # One-shot capture of what Claude Code actually sends (headers + metadata + model),
    # to design a stable conversation key. Enable with DEEPSEEK_DEBUG=1.
    if os.getenv("DEEPSEEK_DEBUG"):
        _debug_request_meta(data, dict(request.headers), request.args.to_dict())

    messages, tools = anthropic_api.anthropic_to_internal(data)
    if not messages:
        return jsonify({"type": "error",
                        "error": {"type": "invalid_request_error", "message": "no messages"}}), 400

    conversation_id = anthropic_api.conversation_id_from_metadata(data)

    try:
        text, calls = run_turn(messages, tools, conversation_id=conversation_id, model=model)
    except Exception as e:
        logger.exception("run_turn failed (anthropic)")
        text, calls = f"[proxy error] {e}", []

    if stream:
        gen = (anthropic_api.stream_anthropic_tool_calls(model, calls) if calls
               else anthropic_api.stream_anthropic_text(model, text))
        return Response(gen, content_type="text/event-stream", headers=SSE_HEADERS)

    return jsonify(anthropic_api.build_anthropic_message(model, text, calls)), 200


@app.route("/health", methods=["GET"])
def health():
    info = {"status": "ok", "client_ready": _client is not None}
    try:
        if _client is not None:
            sess = _client.session
            pool = getattr(sess, "_pow_pool", None)
            if pool is not None:
                info["pow_pool_ready"] = pool._count_valid()
            info["tracked_conversations"] = len(_tracker._convs)
    except Exception:
        pass
    return jsonify(info)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    port = int(os.getenv("PROXY_PORT", "5051"))

    print(f"\n  DeepSeek → OpenAI proxy on http://localhost:{port}\n")
    print("  Warming up browser session (login may open a window)...")

    def _warmup():
        try:
            get_client()
            print("  [ready] DeepSeek session active\n")
        except Exception as e:
            print(f"  [error] warmup failed: {e}\n")

    threading.Thread(target=_warmup, daemon=True).start()

    # Prefer a production WSGI server (waitress: pure-Python, real concurrency) over
    # Flask's single-threaded dev server. Fall back to app.run if waitress isn't installed.
    try:
        from waitress import serve
        print(f"  Serving via waitress on http://0.0.0.0:{port}\n")
        serve(app, host="0.0.0.0", port=port, threads=8, channel_timeout=300)
    except ImportError:
        print("  waitress not installed — using Flask dev server (pip install waitress)\n")
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
