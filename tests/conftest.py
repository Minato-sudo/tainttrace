from pathlib import Path

import pytest

from tainttrace.integrity import IntegrityChecker, NoticeStore, build_store
from tainttrace.integrity.rw import read_rw_csv

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "rw_sample.csv"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "integrity.sqlite"
    build_store(read_rw_csv(FIXTURE_CSV), path, source_file=FIXTURE_CSV)
    return path


@pytest.fixture
def store(db_path: Path):
    s = NoticeStore(db_path)
    yield s
    s.close()


@pytest.fixture
def checker(store: NoticeStore) -> IntegrityChecker:
    return IntegrityChecker(store)
