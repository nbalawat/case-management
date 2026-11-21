"""The event backbone as one client sees it."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..backbone import status
from .deps import get_session

router = APIRouter(prefix="/api")


@router.get("/backbone")
def backbone(s: Session = Depends(get_session)):
    """Counts only: how many of this client's events have gone out and what its inbound streams have taken in."""
    return status.view(s)
