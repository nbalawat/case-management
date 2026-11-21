"""Backups for platform staff; the endpoints live with the backup code in `app/backup/api.py`. Each one also checks
that the caller is the platform's own operator."""
from ..backup.api import router

__all__ = ["router"]
