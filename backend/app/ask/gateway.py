"""The model gateway for "ask anything": one small interface that every model provider sits behind.

The answer loop only knows this interface. It starts a conversation with instructions and a set of tools,
sends the question, and gets back either tool calls to run or a final answer. Which model is behind it is a
deployment setting, so a client can be served by a hosted model, a model in their own cloud, or none at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import settings


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: Any


@dataclass(frozen=True)
class ToolOutput:
    call_id: str
    content: dict
    error: bool = False


@dataclass
class ModelTurn:
    """One reply from the model. `stop` is 'tools' (run the calls), 'answer', 'refused' or 'truncated'."""

    stop: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""


class ModelUnavailable(RuntimeError):
    """The provider could not be reached or rejected the request. The message is safe to show the person."""


class Conversation(Protocol):
    def ask(self, question: str) -> ModelTurn: ...

    def results(self, outputs: list[ToolOutput]) -> ModelTurn: ...


class ChatModel(Protocol):
    name: str  # shown to the person and written to the audit record

    def start(self, *, instructions: str, tools: list[ToolSpec], context: dict, history: list[tuple[str, str]]) -> Conversation:
        """Open a conversation. `context` is structured background (the client's vocabulary and who is asking);
        `history` is earlier question and answer pairs from the same conversation."""
        ...


def resolve_provider() -> str:
    """'auto' picks the hosted model when a key is configured, otherwise the offline planner."""
    choice = settings.ask_provider
    if choice == "auto":
        return "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "offline"
    return choice


def get_model() -> ChatModel:
    choice = resolve_provider()
    if choice == "anthropic":
        from .providers.anthropic import AnthropicChatModel

        return AnthropicChatModel(model=settings.ask_model, effort=settings.ask_effort)
    if choice == "offline":
        from .providers.offline import OfflinePlanner

        return OfflinePlanner()
    raise ModelUnavailable(f"Unknown ASK_PROVIDER '{choice}'. Use auto, anthropic or offline.")
