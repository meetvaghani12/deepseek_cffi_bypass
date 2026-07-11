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
