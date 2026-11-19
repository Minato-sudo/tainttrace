"""Derive a paper's current status from its full notice history."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from .models import Notice, NoticeType, Status


def _order_key(notice: Notice) -> tuple[date, int]:
    # Undated notices sort first; record ids break ties (RW assigns them
    # in entry order).
    record = int(notice.record_id) if notice.record_id.isdigit() else 0
    return (notice.notice_date or date.min, record)


def derive_status(notices: Iterable[Notice]) -> Status:
    """Collapse a paper's notices into one current status.

    The latest retraction or reinstatement decides whether the paper is
    retracted. A concern raised after a reinstatement still counts. Without
    either, an expression of concern outranks a correction.
    """
    history = sorted(notices, key=_order_key)
    if not history:
        return Status.NO_NOTICE

    decisive = [
        n
        for n in history
        if n.notice_type in (NoticeType.RETRACTION, NoticeType.REINSTATEMENT)
    ]
    if decisive:
        last = decisive[-1]
        if last.notice_type is NoticeType.RETRACTION:
            return Status.RETRACTED
        later = [n for n in history if _order_key(n) > _order_key(last)]
        if any(n.notice_type is NoticeType.EXPRESSION_OF_CONCERN for n in later):
            return Status.EXPRESSION_OF_CONCERN
        return Status.REINSTATED

    types = {n.notice_type for n in history}
    if NoticeType.EXPRESSION_OF_CONCERN in types:
        return Status.EXPRESSION_OF_CONCERN
    return Status.CORRECTED
