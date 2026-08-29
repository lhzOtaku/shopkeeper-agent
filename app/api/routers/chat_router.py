"""Chat router for mainAgent."""

from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from app.api.dependencies import get_chat_service
from app.api.schemas.chat_schema import ChatSchema
from app.services.chat_service import ChatService


chat_router = APIRouter()


@chat_router.post("/api/chat")
async def chat_handler(
    chat: ChatSchema,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
):
    """Receive one chat turn and stream mainAgent events."""

    return StreamingResponse(
        chat_service.chat(chat.session_id, chat.message),
        media_type="text/event-stream",
    )
