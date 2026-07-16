from pydantic import BaseModel
from typing import List, Dict, Any, Optional


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = "default"
    stream: Optional[bool] = False


class Choice(BaseModel):
    message: ChatMessage


class ChatResponse(BaseModel):
    id: str
    choices: List[Choice]
    # DeepSeek's native message_id in its original type (int). Kept separate from `id`
    # (a display string) because it must round-trip as parent_message_id, which the
    # DeepSeek API validates as a u32 — a string there causes a 422.
    message_id: Optional[Any] = None
