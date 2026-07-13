import json
import uuid
import logging
from typing import Optional
from src.network.session import PersistentSession
from src.api.models import ChatResponse, Choice, ChatMessage

logger = logging.getLogger(__name__)


class DeepSeekClient:
    def __init__(self, session: PersistentSession = None):
        self.session = session or PersistentSession()
        self._session_id = None
        self._parent_message_id = None

    def chat(self, message: str, **kwargs) -> ChatResponse:
        session_id = kwargs.pop("chat_session_id", None)
        if not session_id:
            if not self._session_id:
                self._session_id = self.session.create_session()
            session_id = self._session_id

        parent_id = kwargs.pop("parent_message_id", None)
        if parent_id is None:
            parent_id = self._parent_message_id
        payload = {
            "chat_session_id": session_id,
            "parent_message_id": parent_id,
            "model_type": "default",
            "prompt": message,
            "ref_file_ids": [],
            "thinking_enabled": kwargs.pop("thinking_enabled", False),
            "search_enabled": kwargs.pop("search_enabled", True),
            "action": None,
            "preempt": False,
        }
        payload.update(kwargs)

        logger.info("Sending chat request (session=%s, parent=%s)", session_id, parent_id)

        resp = self.session.post(
            "https://chat.deepseek.com/api/v0/chat/completion",
            json=payload,
        )

        logger.info("Response status: %d", resp.status_code)

        if resp.status_code != 200:
            logger.error("Non-200 response: %s", resp.text[:500])
            return ChatResponse(
                id="error",
                choices=[Choice(message=ChatMessage(role="assistant", content=f"Error: {resp.status_code}"))],
            )

        # Check for invalid message id error and reset session
        try:
            resp_json = resp.json()
            if resp_json.get("data", {}).get("biz_code") == 26:
                logger.warning("Invalid message id — resetting session")
                self._session_id = None
                self._parent_message_id = None
                return self.chat(message, **kwargs)
        except Exception:
            pass

        content = ""
        msg_id = None
        last_p = ""

        for line in resp.text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "{}":
                continue

            try:
                data = json.loads(data_str)
            except Exception:
                continue

            if not isinstance(data, dict):
                continue

            # Extract response_message_id from ready event
            if "response_message_id" in data:
                msg_id = data["response_message_id"]

            p = data.get("p", "")
            v = data.get("v", "")

            if p:
                last_p = p

            # Format 1: v contains response object directly (no p field)
            if isinstance(v, dict) and "response" in v:
                resp_obj = v.get("response", {})
                fragments = resp_obj.get("fragments", [])
                for frag in fragments:
                    frag_content = frag.get("content", "")
                    if frag_content:
                        content += frag_content

            # Format 2: p == "response" with nested v
            elif p == "response" and isinstance(v, dict):
                resp_obj = v.get("response", {})
                fragments = resp_obj.get("fragments", [])
                for frag in fragments:
                    frag_content = frag.get("content", "")
                    if frag_content:
                        content += frag_content

            # Format 3: legacy — content in v string
            elif "response/content" in last_p or "response/fragments" in last_p:
                if isinstance(v, str) and v:
                    content += v
            elif last_p == "response/status" and v == "FINISHED":
                logger.info("Response finished")

        logger.info("Chat response: %s", content[:200] if content else "(empty)")

        if not content:
            logger.warning("Empty response. Raw SSE (first 1000 chars): %s", resp.text[:1000])

        if msg_id:
            self._parent_message_id = msg_id

        logger.info("Extracted msg_id=%s, content_len=%d", msg_id, len(content))

        return ChatResponse(
            id=str(msg_id or uuid.uuid4()),
            choices=[Choice(message=ChatMessage(role="assistant", content=content))],
        )
