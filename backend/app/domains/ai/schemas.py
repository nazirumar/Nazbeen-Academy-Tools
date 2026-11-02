"""AI research-layer DTOs (request/response contracts)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, model_validator

CHAT_MESSAGE_MAX_CHARS = 4_000
TITLE_MAX_CHARS = 200


class ExplanationRequest(BaseModel):
    """Explain one authorized, persisted analysis report."""

    analysis_id: uuid.UUID


class ChatRequest(BaseModel):
    """Submit one research question with validated context references."""

    message: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)
    conversation_id: uuid.UUID | None = None
    # Optional context attachments (validated against the requesting owner
    # before anything is fetched). A conversation keeps its first attached
    # artefacts; per-message references may extend them.
    analysis_id: uuid.UUID | None = None
    backtest_id: uuid.UUID | None = None
    # Phase 8: optionally attach a research/optimization run (validated
    # against the requesting owner before anything is fetched).
    research_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=TITLE_MAX_CHARS)

    @model_validator(mode="after")
    def _nonempty_message(self) -> "ChatRequest":
        if not self.message.strip():
            raise ValueError("The question must not be empty or whitespace.")
        return self


class ConversationCreateRequest(BaseModel):
    """Create an (initially empty) research conversation."""

    title: str | None = Field(default=None, max_length=TITLE_MAX_CHARS)
    analysis_id: uuid.UUID | None = None
    backtest_id: uuid.UUID | None = None
    research_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _sane_title(self) -> "ConversationCreateRequest":
        if self.title is not None and not self.title.strip():
            raise ValueError("The title must not be empty or whitespace.")
        return self


__all__ = [
    "CHAT_MESSAGE_MAX_CHARS",
    "ChatRequest",
    "ConversationCreateRequest",
    "ExplanationRequest",
]
