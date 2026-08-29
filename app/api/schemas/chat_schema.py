"""Chat endpoint request schemas."""

from pydantic import BaseModel


class ChatSchema(BaseModel):
    """`/api/chat` request body."""

    session_id: str
    message: str
