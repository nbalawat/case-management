"""Application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .api.routes import router
from .api.work import router as work_router
from .api.workflows import router as workflows_router
from .cases.publishing import hydrate
from .config import ROOT
from .db import ensure_schema, session_scope
from .domain.models import Party


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_schema()
    with session_scope() as s:
        hydrate(s)
        if not s.scalar(select(func.count()).select_from(Party)):
            from .seed import seed
            seed(s)
    yield


app = FastAPI(title="CaseForge demo API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
for r in (router, work_router, workflows_router):
    app.include_router(r)

DIST = Path(ROOT / "frontend" / "dist")
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (DIST / path).resolve()
        return FileResponse(f if path and f.is_file() and DIST.resolve() in f.parents else DIST / "index.html")
