"""End-to-end cover for receipt backfill, reconciliation and round sealing.

Runs against a real PostgreSQL database given by RECEIPT_TEST_DATABASE_URL and is
skipped otherwise. Each test resets the public schema, so point it at a scratch
database only.
"""

import os
import threading

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.orm import Session

from src.constants.job_state import JobState
from src.domain.fixtures import FIXTURES
from src.models.base import metadata
from src.models.cases import cases
from src.models.runs import runs
from src.schemas.receipts import ReceiptInput
from src.services import receipts as service
from src.services.execution import service_one
from src.utils.serial import content_digest

URL = os.environ.get("RECEIPT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set RECEIPT_TEST_DATABASE_URL to a scratch database")


@pytest.fixture()
def engine():
    engine = create_engine(URL, pool_size=10)
    with engine.begin() as db:
        db.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    metadata.create_all(engine)
    yield engine
    engine.dispose()


def done_run(engine, rows):
    result = {"summary": "s", "metrics": {}, "rows": rows}
    with engine.begin() as db:
        cid = db.execute(insert(cases).values(
            code="C", title="t", description="", payload={"observe_until": 40, "requests": []}, created_by="seed",
        )).inserted_primary_key[0]
        rid = db.execute(insert(runs).values(
            case_id=cid, status=JobState.DONE, parameter=12, actor="operator",
            created_at=0, finished_at=0, result=result, digest=content_digest(result),
        )).inserted_primary_key[0]
    return rid


def input_(no, asset, certificate, archived_at=0):
    return ReceiptInput(registration_no=no, asset=asset, certificate=certificate, archived_at=archived_at)


ROWS = [{"炉体": "OV-203", "退役证书": "RET-1124-01"}, {"炉体": "OV-225", "退役证书": "RET-1124-03"}]


def test_retry_same_registration_is_idempotent(engine):
    rid = done_run(engine, ROWS)
    with engine.begin() as db:
        first, created = service.add(db, rid, input_("P1", "OV-203", "RET-1124-01", 5), "operator")
        second, again = service.add(db, rid, input_("P1", "OV-203", "RET-1124-01", 5), "operator")
    assert created is True and again is False
    assert first["id"] == second["id"]


def test_changed_content_under_same_registration_is_returned(engine):
    from fastapi import HTTPException

    rid = done_run(engine, ROWS)
    with engine.begin() as db:
        service.add(db, rid, input_("P1", "OV-203", "RET-1124-01", 5), "operator")
    with pytest.raises(HTTPException) as exc:
        with engine.begin() as db:
            service.add(db, rid, input_("P1", "OV-203", "RET-9999", 5), "operator")
    assert exc.value.status_code == 409


def test_backfill_during_replay_is_held_and_seal_blocked(engine):
    from fastapi import HTTPException

    with engine.begin() as db:
        cid = db.execute(insert(cases).values(
            code="Q", title="t", description="", payload={"observe_until": 40, "requests": []}, created_by="seed",
        )).inserted_primary_key[0]
        rid = db.execute(insert(runs).values(
            case_id=cid, status=JobState.QUEUED, parameter=12, actor="operator", created_at=0,
        )).inserted_primary_key[0]
    with engine.begin() as db:
        entry, _ = service.add(db, rid, input_("P1", "OV-203", "RET-1124-01"), "operator")
        assert entry["replay_pending"] is True
        overview = service.overview(db, rid)
    assert overview["replay"] == "replaying"
    assert overview["current"]["verdicts"][0]["判定"] == "回放挂账"
    assert overview["current"]["metrics"]["回放挂账"] == 1
    with pytest.raises(HTTPException) as exc:
        with engine.begin() as db:
            service.seal(db, rid, overview["current"]["round_id"], "operator")
    assert exc.value.status_code == 409


def test_worker_run_then_reconcile_and_seal(engine):
    with engine.begin() as db:
        for fixture in FIXTURES:
            db.execute(insert(cases).values(**fixture, created_by="seed"))
        cid = db.scalar(text("SELECT id FROM cases WHERE code = 'OV-1124-CLOSE'"))
        db.execute(insert(runs).values(case_id=cid, status=JobState.QUEUED, parameter=12, actor="operator", created_at=0))
    while service_one():
        pass
    with engine.connect() as db:
        run = dict(db.execute(text("SELECT * FROM runs ORDER BY id DESC LIMIT 1")).mappings().one())
        assert run["status"] == JobState.DONE
        rid = run["id"]
    with engine.begin() as db:
        service.add(db, rid, input_("P1", "OV-203", "RET-1124-01"), "operator")
        service.add(db, rid, input_("P2", "OV-225", "RET-1124-03"), "operator")
        # OV-217's retirement rolled back: its receipt has no registration side.
        service.add(db, rid, input_("P3", "OV-217", "RET-1124-02"), "operator")
        overview = service.overview(db, rid)
        assert overview["current"]["metrics"]["一致"] == 2
        assert overview["current"]["metrics"]["缺登记"] == 1
        sealed = service.seal(db, rid, overview["current"]["round_id"], "operator")
    assert sealed["current"]["sealed"] is True and sealed["current"]["conclusion_digest"]


def test_sealed_round_stays_frozen_and_late_backfill_opens_new_round(engine):
    from fastapi import HTTPException

    rid = done_run(engine, ROWS)
    with engine.begin() as db:
        service.add(db, rid, input_("P1", "OV-203", "RET-1124-01"), "operator")
        first = service.overview(db, rid)
        sealed = service.seal(db, rid, first["current"]["round_id"], "operator")
        frozen = sealed["current"]["conclusion_digest"]
    with engine.begin() as db:
        service.add(db, rid, input_("P2", "OV-999", "RET-9"), "operator")
        after = service.overview(db, rid)
    assert after["current"]["seq"] == 2 and after["current"]["sealed"] is False
    assert after["rounds"][0]["status"] == "sealed"
    assert after["rounds"][0]["conclusion_digest"] == frozen
    with pytest.raises(HTTPException) as exc:
        with engine.begin() as db:
            service.seal(db, rid, first["current"]["round_id"], "operator")
    assert exc.value.status_code == 409


def test_concurrent_retries_create_one_row_and_one_round(engine):
    rid = done_run(engine, ROWS)
    outcomes = []

    def retry():
        with engine.begin() as db:
            entry, created = service.add(db, rid, input_("P1", "OV-203", "RET-1124-01"), "operator")
            outcomes.append((entry["id"], created))

    threads = [threading.Thread(target=retry) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({row_id for row_id, _ in outcomes}) == 1
    assert sum(1 for _, created in outcomes if created) == 1
    with engine.connect() as db:
        overview = service.overview(db, rid)
    assert len(overview["rounds"]) == 1 and len(overview["rounds"][0]["entries"]) == 1


def test_backfill_racing_seal_lands_in_new_round(engine):
    rid = done_run(engine, ROWS)
    with engine.begin() as db:
        service.add(db, rid, input_("P1", "OV-203", "RET-1124-01"), "operator")
        round_id = service.overview(db, rid)["current"]["round_id"]
    barrier = threading.Barrier(2)

    def seal():
        with engine.begin() as db:
            service.seal(db, rid, round_id, "operator")
        barrier.wait()

    def backfill():
        barrier.wait()
        with engine.begin() as db:
            service.add(db, rid, input_("P2", "OV-225", "RET-1124-03"), "operator")

    threads = [threading.Thread(target=seal), threading.Thread(target=backfill)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with engine.connect() as db:
        overview = service.overview(db, rid)
    assert overview["rounds"][0]["status"] == "sealed"
    assert len(overview["rounds"][0]["entries"]) == 1
    assert len(overview["rounds"]) == 2 and len(overview["rounds"][1]["entries"]) == 1
