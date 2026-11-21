"""Usage per client, for the provider: the operator console's usage table and a CSV a billing system imports."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..tenants import metering
from .deps import platform_session  # operators only: it depends on the operator check

router = APIRouter(prefix="/api/platform/usage")


def _period(start: str | None, end: str | None) -> tuple[date, date]:
    try:
        to = date.fromisoformat(end) if end else date.today()
        frm = date.fromisoformat(start) if start else to - timedelta(days=29)
    except ValueError:
        raise HTTPException(422, "Dates are YYYY-MM-DD") from None
    return frm, to


@router.get("")
def usage(start: str | None = None, end: str | None = None, client: str | None = None, s: Session = Depends(platform_session)):
    frm, to = _period(start, end)
    try:
        return metering.usage(s, frm, to, [client] if client else None)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.get(".csv")
def usage_csv(start: str | None = None, end: str | None = None, s: Session = Depends(platform_session)):
    frm, to = _period(start, end)
    try:
        report = metering.usage(s, frm, to)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return Response(metering.as_csv(report), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="caseforge-usage-{frm}-{to}.csv"'})
