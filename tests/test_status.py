from datetime import date

from tainttrace.integrity.models import Notice, NoticeType, Status
from tainttrace.integrity.status import derive_status

R = NoticeType.RETRACTION
E = NoticeType.EXPRESSION_OF_CONCERN
C = NoticeType.CORRECTION
I = NoticeType.REINSTATEMENT  # noqa: E741


def n(record_id: int, kind: NoticeType, when: date | None) -> Notice:
    return Notice(
        record_id=str(record_id),
        notice_type=kind,
        notice_date=when,
        doi="10.1000/x",
        pmid=None,
        title="t",
        journal="",
        authors="",
        original_date=None,
        notice_doi=None,
        reasons=(),
    )


def test_no_notices():
    assert derive_status([]) is Status.NO_NOTICE


def test_concern_upgraded_to_retraction():
    history = [n(1, E, date(2020, 1, 1)), n(2, R, date(2021, 1, 1))]
    assert derive_status(history) is Status.RETRACTED


def test_retraction_then_reinstatement():
    history = [n(1, R, date(2020, 1, 1)), n(2, I, date(2021, 1, 1))]
    assert derive_status(history) is Status.REINSTATED


def test_order_comes_from_dates_not_input_order():
    history = [n(2, I, date(2021, 1, 1)), n(1, R, date(2020, 1, 1))]
    assert derive_status(history) is Status.REINSTATED


def test_retracted_again_after_reinstatement():
    history = [n(1, R, date(2019, 1, 1)), n(2, I, date(2020, 1, 1)), n(3, R, date(2022, 1, 1))]
    assert derive_status(history) is Status.RETRACTED


def test_concern_after_reinstatement():
    history = [n(1, R, date(2019, 1, 1)), n(2, I, date(2020, 1, 1)), n(3, E, date(2022, 1, 1))]
    assert derive_status(history) is Status.EXPRESSION_OF_CONCERN


def test_concern_before_reinstatement_is_resolved():
    history = [n(1, E, date(2018, 1, 1)), n(2, R, date(2019, 1, 1)), n(3, I, date(2020, 1, 1))]
    assert derive_status(history) is Status.REINSTATED


def test_concern_outranks_correction():
    assert derive_status([n(1, C, date(2020, 1, 1)), n(2, E, date(2019, 1, 1))]) is (
        Status.EXPRESSION_OF_CONCERN
    )


def test_correction_only():
    assert derive_status([n(1, C, None)]) is Status.CORRECTED


def test_undated_notices_sort_first():
    history = [n(5, R, None), n(1, I, date(2020, 1, 1))]
    assert derive_status(history) is Status.REINSTATED


def test_unreliable_flag():
    assert Status.RETRACTED.is_unreliable
    assert Status.EXPRESSION_OF_CONCERN.is_unreliable
    assert not Status.CORRECTED.is_unreliable
    assert not Status.NO_NOTICE.is_unreliable
