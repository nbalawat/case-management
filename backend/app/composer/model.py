"""The design document: a workflow as the designer draws it, before it is compiled into a case type.

A design is a graph of blocks (nodes) joined by steps (edges), plus workflow-wide settings. It is plain JSON so it can
be saved as a draft, exported to a file, put under version control and imported elsewhere. The compiler turns it into
the ordinary case-type definition that both workflow engines run; nothing downstream knows a design existed.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

FORMAT = "caseforge.workflow/v1"
KEY = re.compile(r"^[a-z][a-z0-9_]{1,58}$")


class Position(BaseModel):
    x: float = 0
    y: float = 0


class Node(BaseModel):
    """One block on the canvas. `type` names a block in the catalog; `config` holds that block's settings."""

    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=60)
    type: str = Field(min_length=1, max_length=40)
    position: Position = Position()
    config: dict[str, Any] = {}


class Edge(BaseModel):
    """A step from one block to another. From a decision, `handle` names the branch the step leaves by."""

    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=80)
    source: str
    target: str
    handle: str | None = None
    config: dict[str, Any] = {}


class Settings(BaseModel):
    """Workflow-wide behaviour: the overall deadline, what a late stage does, QA sampling and agent autonomy."""

    model_config = ConfigDict(extra="ignore")
    deadline_label: str = "Case deadline"
    deadline_days: int = Field(30, ge=1, le=3650)
    escalation_raise_priority: bool = True
    escalation_notify: str = "team_lead"
    qa_sample_rate: float = Field(0.0, ge=0, le=1)
    qa_dispositions: list[str] = []
    qa_reopen_to: str | None = None  # a node id
    agents: list[str] = []
    autonomy: str = "L2"


class Design(BaseModel):
    model_config = ConfigDict(extra="ignore")
    format: str = FORMAT
    key: str
    title: str = Field(min_length=1, max_length=120)
    domain: str = Field("", max_length=40)
    description: str = Field("", max_length=2000)
    version: str = "1.0.0"
    nodes: list[Node] = []
    edges: list[Edge] = []
    settings: Settings = Settings()

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not KEY.match(v):
            raise ValueError("The workflow key is lower case letters, digits and underscores, starting with a letter, such as edd_review")
        return v

    @field_validator("version")
    @classmethod
    def _version(cls, v: str) -> str:
        if not re.fullmatch(r"\d+(\.\d+){0,2}", str(v)):
            raise ValueError("Versions look like 1.0.0")
        return str(v)

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def out_of(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id]

    def into(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.target == node_id]
