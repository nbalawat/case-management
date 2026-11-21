"""Kept for the code and tests written against the first release: the interface now lives in app/ai.

`get_model()` without a session gives the deployment default with no client policy and no meter; real requests
go through `app.ai.gateway.model_for(session, purpose)`.
"""
from __future__ import annotations

from ..ai import policy as _policy
from ..ai.endpoints import get as _endpoint
from ..ai.types import ChatModel, Conversation, ModelTurn, ModelUnavailable, ToolCall, ToolOutput, ToolSpec

__all__ = ["ChatModel", "Conversation", "ModelTurn", "ModelUnavailable", "ToolCall", "ToolOutput", "ToolSpec", "get_model", "resolve_provider"]


def resolve_provider() -> str:
    """The endpoint "ask anything" uses on this server when a client has no policy of its own."""
    return _policy.deployment_default("ask")


def get_model() -> ChatModel:
    return _endpoint(resolve_provider()).connect()
