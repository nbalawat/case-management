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
from .api import feature_modules
from .api.deps import signed_in
from .auth import dev_idp
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
# Every feature's router, found by convention (see app/api/__init__.py): a new feature is a new file, not a line here.
for feature in feature_modules():
    app.include_router(feature.router, dependencies=[] if getattr(feature, "PUBLIC", False) else [Depends(signed_in)])
app.include_router(dev_idp.router)  # answers only when DEV_IDP=1
observability.install(app)  # /healthz, /readyz, /metrics, request ids, structured logs, traces when configured

DIST = Path(ROOT / "frontend" / "dist")
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (DIST / path).resolve()
        return FileResponse(f if path and f.is_file() and DIST.resolve() in f.parents else DIST / "index.html")
