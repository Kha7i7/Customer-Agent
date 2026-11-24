"""API 请求 / 响应模型"""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = Field(None, description="不传则新建会话")
    message: str = Field(..., min_length=1, description="用户问题")


class ChatResponse(BaseModel):
    session_id: str
    answer: str = ""
    intent: str = ""
    sources: list[str] = Field(default_factory=list)
    status: str = "ai"
    human: bool = False
    last_id: int = 0
