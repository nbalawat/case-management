"""One Temporal namespace per client, or one shared by all of them.

TEMPORAL_NAMESPACE_MODE=per_client (the default) gives each client its own namespace, TEMPORAL_NAMESPACE_PREFIX +
client id: its workflow histories, search, retention and quotas are its own, an operator can archive or move one
client without touching another, and a large client's namespace can live on a Temporal cluster of its own.
TEMPORAL_NAMESPACE_MODE=shared puts everyone in TEMPORAL_NAMESPACE; workflow ids still carry the client, so nothing
is confused, but isolation is only by name.
"""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta

from ..config import settings

TENANT = re.compile(r"[a-z][a-z0-9-]{2,30}")


class NamespaceError(RuntimeError):
    pass


def per_client() -> bool:
    mode = settings.temporal_namespace_mode.lower()
    if mode not in ("per_client", "shared"):
        raise NamespaceError("TEMPORAL_NAMESPACE_MODE must be per_client or shared")
    return mode == "per_client"


def namespace_for(tenant_id: str) -> str:
    if not TENANT.fullmatch(tenant_id or ""):
        raise NamespaceError("That is not a client id.")
    return f"{settings.temporal_namespace_prefix}{tenant_id}" if per_client() else settings.temporal_namespace


async def exists(client, namespace: str) -> bool:
    from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
    from temporalio.service import RPCError, RPCStatusCode

    try:
        await client.workflow_service.describe_namespace(DescribeNamespaceRequest(namespace=namespace))
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return False
        raise
    return True


async def ensure(client, namespace: str, wait: float = 30.0) -> bool:
    """Register a namespace if it is not there, and wait until the server will accept work in it. True when it was created."""
    from google.protobuf.duration_pb2 import Duration
    from temporalio.api.workflowservice.v1 import ListWorkflowExecutionsRequest, RegisterNamespaceRequest
    from temporalio.service import RPCError, RPCStatusCode

    created = False
    if not await exists(client, namespace):
        try:
            await client.workflow_service.register_namespace(RegisterNamespaceRequest(
                namespace=namespace, description="CaseForge client workflows",
                workflow_execution_retention_period=Duration(seconds=int(timedelta(days=settings.temporal_retention_days).total_seconds()))))
            created = True
        except RPCError as e:
            if e.status != RPCStatusCode.ALREADY_EXISTS:
                raise
    # A namespace registered a moment ago (here, or by another process) takes a second or so to reach every part of
    # the server. Wait until it answers, so the first workflow started in it is not refused.
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        try:
            await client.workflow_service.list_workflow_executions(ListWorkflowExecutionsRequest(namespace=namespace, page_size=1))
            return created
        except RPCError:
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(0.5)
