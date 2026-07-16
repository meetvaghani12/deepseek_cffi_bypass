"""
Detect and cheaply short-circuit Claude Code's auxiliary/background requests.

Beyond the main agent loop, Claude Code fires extra requests at the same /v1/messages
endpoint: session-title generation, autocomplete ("SUGGESTION MODE"), a "quota" probe,
and small-model background calls. Left alone, each one creates a DeepSeek chat, solves a
PoW (2-4s), adds latency, and can pollute output (the real answer once landed in the
autocomplete channel). None of them need the real model — so we detect them by their
distinctive system-prompt/user-prompt fingerprints (confirmed from live debug dumps) and
return a cheap canned/derived response without ever touching DeepSeek.

Each detector returns a ready-to-send assistant TEXT string (never a tool call), or None
if the request is not that aux type. `classify_and_answer` returns the text to short-
circuit with, or None to let the request flow to DeepSeek normally.
"""
import re
from typing import Any, Dict, List, Optional

Message = Dict[str, Any]

# Distinctive markers. The first two are verified against real dumps; the rest are the
# full set of Claude Code aux prompts documented via reverse-engineering (George Sung's
# traffic trace, cchistory, claude-code-router). All aux calls use the small "haiku"
# background model, carry no tools, and tiny max_tokens.
_TITLE_MARKER = "generate a concise"          # "Generate a concise, sentence-case title..."
_SUGGESTION_MARKER = "suggestion mode"        # "[SUGGESTION MODE: Suggest what the user..."
_TITLE_JSON_HINT = '"title"'                  # title-gen asks for JSON {"title": "..."}
_NEW_TOPIC_MARKER = "indicates a new conversation topic"   # isNewTopic detector
_TITLE_5_10_MARKER = "write a 5-10 word title"             # resume-feature title
_SUMMARIZE_MARKER = "summarize this coding conversation"   # compaction summary
_BASH_SAFETY_MARKER = "process bash commands that an ai coding agent"  # injection check
_FILEPATH_MARKER = "extract any file paths that this command"          # filepath extract
_LOGSPEW_MARKER = "repetitive logs, verbose build output"             # output summarize


def _all_text(messages: List[Message]) -> str:
    parts = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return "\n".join(parts)


def _first_user_text(messages: List[Message]) -> str:
    for m in messages:
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        return b.get("text", "")
    return ""


def classify_and_answer(messages: List[Message], system_text: str,
                        model: str = "") -> Optional[str]:
    """
    Return a short-circuit assistant text if this is a recognized auxiliary request,
    else None (let it go to DeepSeek). `system_text` is the flattened system prompt;
    `model` is the requested model id (aux calls use the small "haiku" background model).

    Strategy: match on the distinctive prompt fingerprints (works regardless of model
    remapping). The haiku model id is a corroborating hint but not required, so this still
    works when the user maps the background model to a non-haiku id.
    """
    blob = (system_text + "\n" + _all_text(messages)).lower()

    # Autocomplete / suggestion — return NOTHING (Claude Code shows no suggestion).
    if _SUGGESTION_MARKER in blob:
        return ""

    # Any title/topic/summary generator — derive a short title/summary locally as the
    # JSON or text the caller expects, without a DeepSeek round-trip.
    if (_TITLE_MARKER in blob and _TITLE_JSON_HINT in blob) or _NEW_TOPIC_MARKER in blob:
        return _derive_title_json(messages, new_topic=(_NEW_TOPIC_MARKER in blob))
    if _TITLE_5_10_MARKER in blob:
        return _derive_title_text(messages)
    if _SUMMARIZE_MARKER in blob:
        return _derive_summary(messages)

    # Bash safety / filepath / log-spew helpers — safe, conservative canned answers.
    if _BASH_SAFETY_MARKER in blob:
        return "command_injection_detected: none"
    if _FILEPATH_MARKER in blob:
        return "<filepaths></filepaths>"
    if _LOGSPEW_MARKER in blob:
        return "<should_summarize>false</should_summarize>"

    # "quota" / warm-up probes — bare tiny prompts. Answer trivially.
    fut = _first_user_text(messages).strip().lower()
    if fut in ("quota", "count"):
        return "ok"

    return None


def _session_ask(messages: List[Message]) -> str:
    """The user's actual ask — from a <session>...</session> wrapper if present, else the
    first user message. Returns the last non-empty line (usually the real request)."""
    text = _all_text(messages)
    m = re.search(r"<session>(.*?)</session>", text, re.DOTALL | re.IGNORECASE)
    src = (m.group(1) if m else _first_user_text(messages)).strip()
    lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _short_title(ask: str, n_words: int) -> str:
    words = re.sub(r"[^\w\s]", " ", ask).split()
    title = " ".join(words[:n_words]) or "New session"
    return title[0].upper() + title[1:]


def _derive_title_json(messages: List[Message], new_topic: bool = False) -> str:
    """Return the JSON shape the title/isNewTopic detectors expect."""
    import json as _json
    title = _short_title(_session_ask(messages), 6)
    if new_topic:
        return _json.dumps({"isNewTopic": True, "title": title})
    return _json.dumps({"title": title})


def _derive_title_text(messages: List[Message]) -> str:
    """Plain-text 5-10 word title (resume feature asks for text, not JSON)."""
    return _short_title(_session_ask(messages), 8)


def _derive_summary(messages: List[Message]) -> str:
    """Under-50-char summary for compaction/status."""
    ask = _session_ask(messages) or "Coding session"
    return ask[:50]
