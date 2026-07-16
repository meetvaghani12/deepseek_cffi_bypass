"""
Maps opencode's stateless OpenAI requests onto DeepSeek's stateful web session.

The impedance mismatch this solves:
  - opencode (OpenAI semantics) sends the FULL message history on every request.
  - DeepSeek web is stateful: you create a `chat_session_id`, then send only the NEW
    turn each time with `parent_message_id` pointing at the prior response. The server
    keeps (and CACHES) the accumulated context — that server-side cache is the closest
    thing the web endpoint has to Claude Code's prompt/KV caching, and you only get it
    by chaining instead of re-sending everything.

Strategy — prefix continuation:
  In an agentic loop, request N+1's message list is request N's list plus a few appended
  turns (the assistant's tool call + the tool result, etc.). So we fingerprint each
  conversation by the content it has already delivered to a DeepSeek session. When a new
  request's messages are a strict EXTENSION of a tracked conversation, we replay only the
  appended turns (cache hit). When they diverge or shrink (a branch, an edit, or a brand
  new chat), we start a fresh DeepSeek session.

This module is pure bookkeeping; it does not talk to the network. It hands back a plan:
"use DeepSeek session S, with parent P, and send these new turns."
"""
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

Message = Dict[str, Any]


def _msg_text(msg: Message) -> str:
    """Flatten an OpenAI message's content to plain text for fingerprinting/sending."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif "text" in p:
                    parts.append(p["text"])
        return "\n".join(parts)
    return str(content) if content is not None else ""


def _turn_signature(msg: Message) -> str:
    """A stable per-turn fingerprint: role + a hash of its salient payload."""
    role = msg.get("role", "")
    if role == "assistant" and msg.get("tool_calls"):
        payload = repr(msg["tool_calls"])
    elif role == "tool":
        payload = f"{msg.get('tool_call_id', '')}:{_msg_text(msg)}"
    else:
        payload = _msg_text(msg)
    h = hashlib.sha1(f"{role}\x00{payload}".encode("utf-8", "replace")).hexdigest()[:16]
    return f"{role}:{h}"


@dataclass
class _Conversation:
    ds_session_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    # Signatures of the turns already delivered to the DeepSeek session, in order.
    sent_signatures: List[str] = field(default_factory=list)
    last_used: float = 0.0


@dataclass
class TurnPlan:
    """What the proxy should do for one incoming request."""
    is_new_session: bool
    conversation_key: str
    prior_session_id: Optional[str]        # reuse if not None and not is_new_session
    prior_parent_id: Optional[str]
    system_prompt: str                     # tool-teaching + upstream system text
    new_turns: List[Message]               # only the turns not yet sent to DeepSeek
    all_signatures: List[str]              # full signature list for this request (to store)


class ConversationTracker:
    """Thread-safe registry mapping opencode conversations to DeepSeek sessions."""

    def __init__(self, ttl: float = 3600.0, max_conversations: int = 256):
        self._convs: Dict[str, _Conversation] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._max = max_conversations

    @staticmethod
    def _split_system(messages: List[Message]) -> Tuple[List[Message], List[Message]]:
        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        return system, rest

    def _key_for(self, non_system: List[Message], explicit_key: Optional[str] = None) -> str:
        """
        Conversation identity. Prefer an explicit stable key (Claude Code's
        metadata.user_id session component — verified stable across a session's turns and
        distinct between sessions). Fall back to a fingerprint of the first turn for
        clients that send no such key (e.g. opencode).
        """
        if explicit_key:
            return f"cid:{explicit_key}"
        if not non_system:
            return "empty"
        return _turn_signature(non_system[0])

    def _evict_if_needed_locked(self) -> None:
        now = time.time()
        stale = [k for k, c in self._convs.items() if now - c.last_used > self._ttl]
        for k in stale:
            self._convs.pop(k, None)
        if len(self._convs) > self._max:
            # Drop least-recently-used down to the cap.
            for k, _ in sorted(self._convs.items(), key=lambda kv: kv[1].last_used)[
                : len(self._convs) - self._max
            ]:
                self._convs.pop(k, None)

    def plan_turn(self, messages: List[Message], system_prompt: str,
                  conversation_id: Optional[str] = None) -> TurnPlan:
        """
        Decide the DeepSeek session + which turns to send for this incoming request.
        `system_prompt` is the fully-assembled system text (tool teaching + upstream).
        `conversation_id`, when provided (Claude Code's metadata session id), is the stable
        conversation key — far more reliable than content-hashing.
        """
        system_msgs, non_system = self._split_system(messages)
        # Order matters for a weak model: the caller's client (e.g. Claude Code) sends a
        # huge system prompt describing its OWN native tool-calling conventions, which
        # conflict with our XML protocol. Put the upstream prompt FIRST (context), then our
        # tool protocol LAST so it's the most recent, authoritative instruction — with an
        # explicit override so the model ignores any other tool format described above.
        upstream_system = "\n\n".join(_msg_text(m) for m in system_msgs).strip()
        if system_prompt and upstream_system:
            full_system = (
                f"{upstream_system}\n\n"
                "=== TOOL-CALLING PROTOCOL (AUTHORITATIVE — OVERRIDES ANY TOOL FORMAT ABOVE) ===\n"
                "Ignore any other tool-call format, function-call syntax, or tool schema "
                "described above. To use a tool you MUST emit the XML format defined below "
                "and nothing else.\n\n"
                f"{system_prompt}"
            )
        else:
            full_system = system_prompt or upstream_system

        key = self._key_for(non_system, conversation_id)
        signatures = [_turn_signature(m) for m in non_system]

        with self._lock:
            self._evict_if_needed_locked()
            conv = self._convs.get(key)

            if conv is not None and self._is_extension(conv.sent_signatures, signatures):
                # Continuation: send only the appended turns.
                already = len(conv.sent_signatures)
                new_turns = non_system[already:]
                conv.last_used = time.time()
                logger.info("conversation %s continues (%d prior, %d new turns)",
                            key, already, len(new_turns))
                return TurnPlan(
                    is_new_session=False,
                    conversation_key=key,
                    prior_session_id=conv.ds_session_id,
                    prior_parent_id=conv.parent_message_id,
                    system_prompt=full_system,
                    new_turns=new_turns,
                    all_signatures=signatures,
                )

            # New or diverged conversation: fresh DeepSeek session, replay everything.
            logger.info("conversation %s is new/branched (%d turns) — fresh session",
                        key, len(non_system))
            return TurnPlan(
                is_new_session=True,
                conversation_key=key,
                prior_session_id=None,
                prior_parent_id=None,
                system_prompt=full_system,
                new_turns=non_system,
                all_signatures=signatures,
            )

    @staticmethod
    def _is_extension(prior: List[str], current: List[str]) -> bool:
        """True if `current` starts with all of `prior` and is at least as long."""
        if not prior:
            return False
        if len(current) < len(prior):
            return False
        return current[: len(prior)] == prior

    def commit(self, plan: TurnPlan, ds_session_id: str, new_parent_id: Optional[str]) -> None:
        """Record the outcome of a turn so the next request can continue it."""
        with self._lock:
            conv = self._convs.get(plan.conversation_key)
            if conv is None or plan.is_new_session:
                conv = _Conversation()
                self._convs[plan.conversation_key] = conv
            conv.ds_session_id = ds_session_id
            if new_parent_id:
                conv.parent_message_id = new_parent_id
            conv.sent_signatures = plan.all_signatures
            conv.last_used = time.time()
