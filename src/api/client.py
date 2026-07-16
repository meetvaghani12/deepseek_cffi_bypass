import json
import uuid
import logging
from typing import Optional
from src.network.session import PersistentSession
from src.api.sse_parser import parse_stream
from src.api.models import ChatResponse, Choice, ChatMessage

logger = logging.getLogger(__name__)


class DeepSeekClient:
    """
    Thin client over chat.deepseek.com's `/api/v0/chat/completion`.

    Maintains a single DeepSeek chat session and chains `parent_message_id` so the
    server keeps (and caches) conversation context across turns. Callers that manage
    their own multi-session mapping should pass `chat_session_id`/`parent_message_id`
    explicitly and read `.message_id` off the response to chain the next turn.
    """

    def __init__(self, session: PersistentSession = None):
        self.session = session or PersistentSession()
        self._session_id = None
        self._parent_message_id = None

    def reset(self) -> None:
        """Forget the current DeepSeek session so the next chat() starts a fresh one."""
        self._session_id = None
        self._parent_message_id = None

    def create_session(self) -> str:
        """Create a fresh DeepSeek chat session and return its id."""
        return self.session.create_session()

    def chat(
        self,
        message: str,
        chat_session_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        thinking_enabled: bool = False,
        search_enabled: bool = False,
        **kwargs,
    ) -> ChatResponse:
        # Resolve the session id: explicit arg > instance state > create new.
        session_id = chat_session_id or self._session_id
        if not session_id:
            session_id = self._session_id = self.session.create_session()

        # Resolve parent: explicit arg wins (including explicit None for a fresh root
        # is expressed by passing chat_session_id with parent_message_id omitted).
        parent_id = parent_message_id if parent_message_id is not None else self._parent_message_id

        payload = {
            "chat_session_id": session_id,
            "parent_message_id": parent_id,
            "model_type": "default",
            "prompt": message,
            "ref_file_ids": [],
            "thinking_enabled": thinking_enabled,
            "search_enabled": search_enabled,
            "action": None,
            "preempt": False,
        }
        payload.update(kwargs)

        logger.info("chat request (session=%s parent=%s len=%d)", session_id, parent_id, len(message))

        resp = self.session.post(
            "https://chat.deepseek.com/api/v0/chat/completion",
            json=payload,
        )

        if resp.status_code != 200:
            logger.error("non-200 (%d): %s", resp.status_code, resp.text[:500])
            return ChatResponse(
                id="error",
                choices=[Choice(message=ChatMessage(role="assistant",
                                                    content=f"[proxy error] DeepSeek returned {resp.status_code}"))],
            )

        # A biz_code 26 means the parent_message_id is stale — reset once and retry.
        # NB: use `(head.get("data") or {})` — `.get("data", {})` returns None when the
        # JSON is literally {"data": null}, and None.get(...) would crash.
        try:
            head = resp.json()
            if isinstance(head, dict) and (head.get("data") or {}).get("biz_code") == 26:
                logger.warning("stale message id (biz_code 26) — resetting session and retrying")
                self.reset()
                return self.chat(message, thinking_enabled=thinking_enabled,
                                 search_enabled=search_enabled, **kwargs)
        except (json.JSONDecodeError, ValueError):
            pass  # SSE body isn't a single JSON object — expected on success.

        parsed = parse_stream(resp.text)

        if parsed.error:
            logger.error("stream error: %s", parsed.error)
        if not parsed.content and not parsed.thinking:
            logger.warning("empty response; raw SSE head: %s", resp.text[:800])

        # Chain the next turn only when we own the session state.
        if parsed.message_id and chat_session_id is None:
            self._parent_message_id = parsed.message_id

        logger.info("chat response (msg_id=%s content_len=%d thinking_len=%d)",
                    parsed.message_id, len(parsed.content), len(parsed.thinking))

        return ChatResponse(
            id=str(parsed.message_id or uuid.uuid4()),
            message_id=parsed.message_id,  # native type for parent_message_id chaining
            choices=[Choice(message=ChatMessage(role="assistant", content=parsed.content))],
        )
