"""
Render opencode message turns into the single `prompt` string DeepSeek expects per call.

DeepSeek's web `chat()` takes one prompt at a time and keeps prior context server-side
(via parent_message_id). So for a continuation we only render the NEW turns, and we do
NOT replay the assistant's own previous tool_call — DeepSeek already produced that text
(it is the parent message). What DeepSeek needs to see next is the tool RESULT.
"""
import json
from typing import Any, Dict, List

from src.proxy.tool_protocol import format_tool_result

Message = Dict[str, Any]

# Repeated as the final line of every prompt so the tool format is the freshest thing the
# model sees, even after a huge CLAUDE.md / system-reminder in the user turn.
_PROTOCOL_FOOTER = (
    "[REMINDER] If this task needs a tool, emit the XML tool call IMMEDIATELY as your "
    "entire reply — e.g. <read><filePath>path</filePath></read> or "
    "<bash><command>ls</command></bash>. One tool per message, nothing before or after "
    "the tags. Do NOT announce or narrate the action first: never reply with 'I'll…', "
    "'Let me…', 'Sure, I'll check…' — that wastes the turn because the environment only "
    "acts on the tool call, not on your description of it. Do NOT use JSON/function-call "
    "syntax like Read({...}). Only answer in plain text when NO tool is needed."
)


def _render_tool_call_as_xml(tool_calls: List[Dict[str, Any]]) -> str:
    """Render assistant tool_calls back into the XML the model would have emitted.

    Needed when replaying history to a FRESH DeepSeek session: the model has no memory
    of making the call, so the transcript must show the call before its result or the
    model re-issues it.
    """
    blocks = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        try:
            args = json.loads(fn.get("arguments", "") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        inner = "".join(
            f"<{k}>{v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}</{k}>"
            for k, v in args.items()
        )
        blocks.append(f"<{name}>{inner}</{name}>")
    return "\n".join(blocks)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and (p.get("type") == "text" or "text" in p)
        )
    return "" if content is None else str(content)


def _arg_hint(arguments: str) -> str:
    """Best-effort short label for a tool result header (first string arg value)."""
    try:
        obj = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except (json.JSONDecodeError, TypeError):
        return ""
    for v in obj.values():
        if isinstance(v, str) and v:
            return v if len(v) <= 80 else v[:80]
    return ""


def render_turns(new_turns: List[Message], is_new_session: bool, system_prompt: str) -> str:
    """
    Fold the new turns into one prompt string.

    On a NEW session we prepend the system prompt (tool teaching + upstream system text)
    once — DeepSeek web has no separate system role, so it rides in the first prompt.
    """
    # Map assistant tool_call id -> (name, arg_hint) so we can label tool results.
    call_meta: Dict[str, tuple] = {}
    for m in new_turns:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                call_meta[tc.get("id", "")] = (fn.get("name", "tool"), _arg_hint(fn.get("arguments", "")))

    parts: List[str] = []
    for m in new_turns:
        role = m.get("role")
        if role == "user":
            t = _text(m.get("content"))
            if t:
                parts.append(t)
        elif role == "tool":
            cid = m.get("tool_call_id", "")
            name, hint = call_meta.get(cid, (m.get("name", "tool"), ""))
            parts.append(format_tool_result(name, hint, _text(m.get("content"))))
        elif role == "assistant":
            t = _text(m.get("content"))
            if t:
                parts.append(t)
            # Replay the assistant's tool call as XML ONLY on a fresh session, so the
            # replayed transcript shows the call before its result. On a true
            # continuation DeepSeek already emitted this call (it is the parent
            # message); re-sending it as a user turn would look like the user issuing
            # tool calls, so we send only the result and let session state carry the call.
            if is_new_session and m.get("tool_calls"):
                xml = _render_tool_call_as_xml(m["tool_calls"])
                if xml:
                    parts.append(xml)
        # system messages are handled via system_prompt, not here.

    body = "\n\n".join(p for p in parts if p)

    if is_new_session and system_prompt:
        head = f"{system_prompt}\n\n---\n\n{body}" if body else system_prompt
        # The user turn can carry a huge CLAUDE.md / system-reminder that buries the tool
        # protocol stated above it. Repeat a compact, authoritative reminder as the LAST
        # thing the model reads, so recency reinforces the format rather than fighting it.
        return f"{head}\n\n{_PROTOCOL_FOOTER}"
    # Continuation turns: DeepSeek already has the protocol in session context, but a
    # one-line reminder keeps it on-format across long tool loops.
    return f"{body}\n\n{_PROTOCOL_FOOTER}" if body else body
