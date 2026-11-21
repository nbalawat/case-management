"""The provider-neutral conversation interface every model sits behind.

A caller starts a conversation with instructions and a set of tools, sends a question, and gets back either tool
calls to run or a final answer. Which model is behind it is configuration, so a client can be served by a hosted
model, an open-weight model in its own cloud, or none at all, without the caller changing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
    """The provider could not be reached, rejected the request, or policy forbids the call. The message is safe to show."""


class Conversation(Protocol):
    def ask(self, question: str) -> ModelTurn: ...

    def results(self, outputs: list[ToolOutput]) -> ModelTurn: ...


class ChatModel(Protocol):
    name: str  # shown to the person and written to the audit record

    def start(self, *, instructions: str, tools: list[ToolSpec], context: dict, history: list[tuple[str, str]]) -> Conversation:
        """Open a conversation. `context` is structured background (the client's vocabulary and who is asking);
        `history` is earlier question and answer pairs from the same conversation."""
        ...
