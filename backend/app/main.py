"""Application entry point."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .api.access import router as access_router
from .api.agents import router as agents_router
from .api.ai import router as ai_router
from .api.knowledge import router as knowledge_router
from .mcp import http as mcp_http
from .api.ask import router as ask_router
from .api.auth import router as auth_router
from .api.backbone import router as backbone_router
from .api.deps import signed_in
from .api.entities import router as entities_router
from .api.filings import router as filings_router
from .api.kyc import router as kyc_router
from .api.detection import router as detection_router
from .api.fraud import router as fraud_router
from .api.governance import router as governance_router
from .api.sanctions import router as sanctions_router
from .api.operations import router as operations_router
from .auth import dev_idp
from .api.data import router as data_router
from .api.decisions import router as decisions_router
from .api.forms import router as forms_router
from .api.lake import router as lake_router
from .api.navigation import router as navigation_router
from .api.case_list import router as case_list_router
from .api.managed import router as managed_router
from .api.platform import router as platform_router
from .api.routes import router
from .api.work import router as work_router
from .api.workflows import router as workflows_router
from .api.composer import router as composer_router
from . import workflow
from .config import ROOT, settings
from .db import ensure_schema, session_scope
from .tenants import provider
from . import observability
from .tenants.onboarding import hydrate


@asynccontextmanager
async def lifespan(_: FastAPI):
    from .observability import metrics

    metrics.enable_platform_gauges()
    if os.getenv("METRICS_PORT"):
        metrics.serve(int(os.environ["METRICS_PORT"]))
    ensure_schema()
    with session_scope(platform=True) as s:
        provider.bootstrap(s, settings.bootstrap_operator)
        clients = hydrate(s)
    if not clients and settings.demo_tenants:
        from .seed import seed_demo

        seed_demo()
    task = None
    if settings.pipeline_scheduler and workflow.engine().in_process:  # on Temporal a schedule per client starts due pipelines instead
        from .ingest import scheduler

        task = asyncio.create_task(scheduler.run_forever())
    backbone = None
    if settings.backbone_workers:
        from .backbone import worker

        backbone = worker.start_in_process()
    async with mcp_running():  # the MCP server's request handling (stateless; see app/mcp/http.py)
        yield
    if task:
        task.cancel()
    if backbone:
        backbone[1].set()


app = FastAPI(title="CaseForge demo API", version="0.26.0", lifespan=lifespan)


@app.exception_handler(workflow.EngineUnavailable)
async def _engine_unavailable(_: Request, e: workflow.EngineUnavailable) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=503, headers={"Retry-After": "5"})
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
for r in (router, work_router, workflows_router, access_router, platform_router, ask_router, data_router, agents_router, entities_router, filings_router, operations_router, kyc_router, fraud_router, sanctions_router, detection_router, governance_router, backbone_router, navigation_router, case_list_router, decisions_router, forms_router, lake_router, composer_router):
    app.include_router(r, dependencies=[Depends(signed_in)])
app.include_router(managed_router, dependencies=[Depends(signed_in)])  # on its own line: the tuple above is a merge hotspot
app.include_router(ai_router, dependencies=[Depends(signed_in)])  # the model gateway's set-up and usage
app.include_router(knowledge_router, dependencies=[Depends(signed_in)])  # the library Ask and the agents cite
mcp_running = mcp_http.mount(app)  # Ask's tools for MCP clients at /mcp, signed in like the rest of the API
app.include_router(auth_router)
app.include_router(dev_idp.router)  # answers only when DEV_IDP=1
observability.install(app)  # /healthz, /readyz, /metrics, request ids, structured logs, traces when configured

DIST = Path(ROOT / "frontend" / "dist")
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (DIST / path).resolve()
        return FileResponse(f if path and f.is_file() and DIST.resolve() in f.parents else DIST / "index.html")
