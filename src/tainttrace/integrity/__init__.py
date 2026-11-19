"""Retraction and editorial-notice status for scientific papers."""

from .checker import IntegrityChecker
from .models import CheckResult, Notice, NoticeType, Status
from .remote import RemoteSources
from .store import NoticeStore, build_store, default_db_path
from .sync import sync

__all__ = [
    "CheckResult",
    "IntegrityChecker",
    "Notice",
    "NoticeStore",
    "NoticeType",
    "RemoteSources",
    "Status",
    "build_store",
    "default_db_path",
    "open_checker",
    "sync",
]


def open_checker(db_path=None, remote: bool = True) -> IntegrityChecker:
    """Open the default store with remote fallbacks switched on or off."""
    store = NoticeStore(db_path)
    return IntegrityChecker(store, RemoteSources(store) if remote else None)
