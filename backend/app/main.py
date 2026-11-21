"""Application entry point."""
from __future__ import annotations

import asyncio
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
from .api.developer import router as developer_router
from .public import app as public_api
from .api.decisions import router as decisions_router
from .api.forms import router as forms_router
from .api.lake import router as lake_router
from .api.navigation import router as navigation_router
from .api.case_list import router as case_list_router
from .api.platform import router as platform_router
from .api.routes import router
from .api.work import router as work_router
from .api.workflows import router as workflows_router
from . import workflow
from .config import ROOT, settings
from .db import ensure_schema, session_scope
from .tenants.onboarding import hydrate


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_schema()
    with session_scope(platform=True) as s:
        clients = hydrate(s)
    if not clients and settings.demo_tenants:
        from .seed import seed_demo

        seed_demo()
    task = None
    if settings.pipeline_scheduler and workflow.engine().in_process:  # on Temporal a schedule per client starts due pipelines instead
        from .ingest import scheduler

        task = asyncio.create_task(scheduler.run_forever())
    hooks = None
    if settings.webhook_workers:
        from .public.webhooks import worker as webhook_worker

        hooks = asyncio.create_task(webhook_worker.run_forever())
    backbone = None
    if settings.backbone_workers:
        from .backbone import worker

        backbone = worker.start_in_process()
    yield
    if task:
        task.cancel()
    if hooks:
        hooks.cancel()
    if backbone:
        backbone[1].set()


app = FastAPI(title="CaseForge demo API", version="0.26.0", lifespan=lifespan)


@app.exception_handler(workflow.EngineUnavailable)
async def _engine_unavailable(_: Request, e: workflow.EngineUnavailable) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=503, headers={"Retry-After": "5"})
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
for r in (router, work_router, workflows_router, access_router, platform_router, ask_router, data_router, agents_router, entities_router, filings_router, operations_router, kyc_router, fraud_router, sanctions_router, detection_router, governance_router, backbone_router, navigation_router, case_list_router, decisions_router, forms_router, lake_router):
    app.include_router(r, dependencies=[Depends(signed_in)])
app.include_router(ai_router, dependencies=[Depends(signed_in)])  # the model gateway's set-up and usage
app.include_router(auth_router)
app.include_router(dev_idp.router)  # answers only when DEV_IDP=1
app.include_router(developer_router, dependencies=[Depends(signed_in)])
# The public API: its own application, credentials, OpenAPI document (/api/v1/openapi.json) and reference (/api/v1/docs).
app.mount(public_api.PREFIX, public_api.asgi)

DIST = Path(ROOT / "frontend" / "dist")
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (DIST / path).resolve()
        return FileResponse(f if path and f.is_file() and DIST.resolve() in f.parents else DIST / "index.html")
