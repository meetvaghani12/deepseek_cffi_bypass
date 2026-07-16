"""
Parser for chat.deepseek.com's `/api/v0/chat/completion` SSE stream.

The web endpoint does NOT stream OpenAI-style `choices/delta` objects. It streams
JSON-patch-like incremental ops with fields:

    p  (path)   -- target field, e.g. "response/content", "response/thinking_content",
                   "response/fragments/-1/content", "response/status".
    v  (value)  -- the value to apply: usually a string chunk to append, sometimes an
                   object (metadata) or a list (fragment insertion).
    o  (op)     -- "APPEND" or absent. Absence is treated as APPEND for content strings.

THE core rule: `p` is stated ONCE at the start of a run, then subsequent chunks carry
only `v` (and maybe `o`). You MUST keep a sticky "current target" and route bare
`{"v": "..."}` lines to whatever the last path established. Missing that is why the old
parser produced empty/truncated content.

Two formats coexist in the wild and both are handled:

  OLD (flat):
    {"p": "response/thinking_content", "v": "..."}   -> reasoning
    {"p": "response/content", "v": "..."}            -> answer
    {"v": "..."}                                     -> append to current phase

  NEW (fragments):
    {"v": {"response": {"fragments": [{"type": "THINK"|"RESPONSE", "content": "..."}], ...}}}
    {"p": "response/fragments", "o": "APPEND", "v": [{"type": "RESPONSE", "content": "..."}]}
    {"p": "response/fragments/-1/content", "o": "APPEND", "v": "..."}

`response_message_id` (needed for parent_message_id chaining) rides on the initial
metadata object as `response.message_id`.

Reference: Fly143/deepseek-free-api proxy.py `_parse_sse`.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedResponse:
    """Accumulated result of parsing a full completion stream.

    `message_id` is preserved in its native type (DeepSeek sends an integer) because it
    must round-trip back as `parent_message_id`, which the API validates as a u32 —
    stringifying it causes a 422 on the next turn.
    """
    content: str = ""
    thinking: str = ""
    message_id: Optional[Any] = None
    finished: bool = False
    error: Optional[str] = None
    search_results: List[dict] = field(default_factory=list)


class _State:
    """Sticky parse state carried across events within one stream."""

    def __init__(self) -> None:
        # OLD-format current target: "thinking" | "content"
        self.phase: str = "content"
        # NEW-format current fragment target: None | "THINK" | "RESPONSE"
        self.fragment_type: Optional[str] = None
        self._answer: List[str] = []
        self._thinking: List[str] = []
        self.message_id: Optional[Any] = None
        self.finished: bool = False
        self.error: Optional[str] = None
        self.search_results: List[dict] = []

    def emit_fragment(self, ftype: Optional[str], text: str) -> None:
        """Route text by NEW-format fragment type (THINK -> reasoning, else answer)."""
        if not text:
            return
        if ftype == "THINK":
            self._thinking.append(text)
        else:
            self._answer.append(text)

    def emit_phase(self, phase: str, text: str) -> None:
        """Route text by OLD-format phase."""
        if not text:
            return
        if phase == "thinking":
            self._thinking.append(text)
        else:
            self._answer.append(text)

    def result(self) -> ParsedResponse:
        return ParsedResponse(
            content="".join(self._answer),
            thinking="".join(self._thinking),
            message_id=self.message_id,
            finished=self.finished,
            error=self.error,
            search_results=self.search_results,
        )


def _iter_data_objects(raw_text: str) -> Iterator[dict]:
    """Yield parsed JSON objects from SSE `data:` lines, skipping comments/events/[DONE]."""
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # SSE comment lines and non-data events (event:/id:/retry:) are metadata -> skip.
        if line.startswith(":") or line.startswith("event:") or line.startswith("id:") \
                or line.startswith("retry:"):
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
        else:
            # Some proxies emit bare JSON lines without the "data:" prefix.
            payload = line
        if not payload or payload == "{}":
            continue
        if payload == "[DONE]":
            yield {"__done__": True}
            return
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _apply_event(state: _State, obj: dict) -> bool:
    """
    Apply one SSE event object to the parse state.
    Returns True if the stream should stop (terminal signal).
    """
    if obj.get("__done__"):
        state.finished = True
        return True

    v = obj.get("v")

    # (a) Metadata object: {"v": {"response": {fragments, message_id, ...}}}
    if isinstance(v, dict):
        resp = v.get("response")
        if isinstance(resp, dict):
            mid = resp.get("message_id")
            if mid is not None:
                state.message_id = mid  # keep native type (int) for parent_message_id chaining
            for frag in resp.get("fragments", []) or []:
                if not isinstance(frag, dict):
                    continue
                ftype = frag.get("type")
                if ftype:
                    state.fragment_type = ftype
                state.emit_fragment(state.fragment_type, frag.get("content", ""))
        # An explicit error object may also arrive as a dict value.
        if v.get("type") == "error" or (isinstance(resp, dict) and resp.get("type") == "error"):
            state.error = json.dumps(v)[:500]
            return True
        return False

    p = obj.get("p", "")

    # (b) NEW: a fragment was inserted (v is a list of fragment dicts).
    if p == "response/fragments" and isinstance(v, list) and v:
        last = v[-1]
        if isinstance(last, dict):
            ftype = last.get("type")
            if ftype:
                state.fragment_type = ftype
            state.emit_fragment(state.fragment_type, last.get("content", ""))
        return False

    # (c) NEW: append to the current (most-recent) fragment's content.
    #     Real streams use the literal "-1"; tolerate any fragments/<idx>/content too.
    if p.startswith("response/fragments/") and p.endswith("/content"):
        if isinstance(v, str):
            state.emit_fragment(state.fragment_type, v)
        return False

    # (d) OLD: explicit content / thinking paths set the sticky phase, then append.
    if p == "response/content":
        state.phase = "content"
        if isinstance(v, str):
            state.emit_phase("content", v)
        return False
    if p == "response/thinking_content":
        state.phase = "thinking"
        if isinstance(v, str):
            state.emit_phase("thinking", v)
        return False

    # (e) Search results side-channel (only when search_enabled).
    if p == "response/search_results" and isinstance(v, list):
        state.search_results.extend(x for x in v if isinstance(x, dict))
        return False

    # (f) Terminal status.
    if p == "response/status":
        if v == "FINISHED":
            state.finished = True
            return True
        return False

    # (g) Other explicit metadata paths (elapsed_secs, BATCH, search_status, ...) -> skip.
    if p:
        return False

    # (h) Pathless continuation: route the bare string `v` to the CURRENT sticky target.
    if isinstance(v, str):
        if state.fragment_type is not None:
            state.emit_fragment(state.fragment_type, v)
        else:
            state.emit_phase(state.phase, v)
    return False


def parse_stream(raw_text: str) -> ParsedResponse:
    """Parse a complete (buffered) SSE response body into a ParsedResponse."""
    state = _State()
    for obj in _iter_data_objects(raw_text):
        if _apply_event(state, obj):
            break
    return state.result()


class StreamAccumulator:
    """
    Incremental parser for true streaming: feed raw SSE chunks as they arrive and read
    the growing answer/thinking buffers. Reuses the same sticky-state engine.
    """

    def __init__(self) -> None:
        self._state = _State()
        self._buf = ""

    def feed(self, chunk: str) -> ParsedResponse:
        """Feed a raw text chunk; returns a snapshot of the current parsed result.

        Only complete lines are consumed; a trailing partial line is retained until the
        next chunk completes it.
        """
        self._buf += chunk
        # Consume only up to the last newline; keep the remainder buffered.
        if "\n" in self._buf:
            complete, self._buf = self._buf.rsplit("\n", 1)
            for obj in _iter_data_objects(complete):
                if _apply_event(self._state, obj):
                    break
        return self._state.result()

    def finish(self) -> ParsedResponse:
        """Flush any buffered final line and return the final result."""
        if self._buf.strip():
            for obj in _iter_data_objects(self._buf):
                if _apply_event(self._state, obj):
                    break
            self._buf = ""
        return self._state.result()
