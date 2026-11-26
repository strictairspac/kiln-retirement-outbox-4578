from time import time

from fastapi import HTTPException
from sqlalchemy import and_, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from src.constants.job_state import JobState
from src.constants.receipt_state import PENDING, RoundState, VERDICTS
from src.domain.receipts import entry_digest, evaluate
from src.models.receipts import receipt_entries, receipt_rounds
from src.models.runs import runs
from src.utils.serial import content_digest


def _run(db, run_id):
    row = db.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "执行批次不存在")
    return dict(row)


def _rounds(db, run_id):
    return [dict(row) for row in db.execute(
        select(receipt_rounds).where(receipt_rounds.c.run_id == run_id).order_by(receipt_rounds.c.seq)
    ).mappings()]


def _entries(db, run_id):
    return [dict(row) for row in db.execute(
        select(receipt_entries).where(receipt_entries.c.run_id == run_id).order_by(receipt_entries.c.id)
    ).mappings()]


def _zero_metrics():
    metrics = {kind: 0 for kind in VERDICTS}
    metrics.update({"回放挂账": 0, "登记项": 0, "收据项": 0})
    return metrics


def _pending_view(entries):
    """While the batch is still replaying, hold every backfill as 挂账.

    No 缺件 verdict is made before replay finishes, because the registration side
    does not exist yet: a receipt can neither be paired to an oven nor declared
    to have a missing counterpart.
    """
    verdicts = [{
        "判定": PENDING,
        "登记炉号": None,
        "登记证书号": None,
        "收据炉号": entry["asset"],
        "收据证书号": entry["certificate"],
        "登记号": entry["registration_no"],
        "收据时间 s": entry["archived_at"],
        "说明": "批次仍在回放，先挂账；回放完成后再判缺件",
    } for entry in entries]
    metrics = _zero_metrics()
    metrics["回放挂账"] = len(entries)
    metrics["收据项"] = len(entries)
    return verdicts, metrics


def _live(run, entries):
    if run["status"] == JobState.DONE and isinstance(run["result"], dict):
        return evaluate(run["result"].get("rows") or [], entries)
    return _pending_view(entries)


def _round_view(item, entries):
    return {
        "id": item["id"],
        "seq": item["seq"],
        "status": item["status"],
        "created_by": item["created_by"],
        "created_at": item["created_at"],
        "sealed_by": item["sealed_by"],
        "sealed_at": item["sealed_at"],
        "conclusion_digest": item["conclusion_digest"],
        "entries": [{
            "registration_no": entry["registration_no"],
            "asset": entry["asset"],
            "certificate": entry["certificate"],
            "archived_at": entry["archived_at"],
        } for entry in entries if entry["round_id"] == item["id"]],
    }


def overview(db, run_id):
    """Registration verdicts and delivery receipts read from one snapshot."""
    run = _run(db, run_id)
    rounds = _rounds(db, run_id)
    entries = _entries(db, run_id)
    if run["status"] == JobState.FAILED:
        replay, verdicts, metrics = "failed", [], _zero_metrics()
    elif run["status"] != JobState.DONE:
        replay = "replaying"
        verdicts, metrics = _pending_view(entries)
    else:
        replay = "done"
        verdicts, metrics = _live(run, entries)
    latest = rounds[-1] if rounds else None
    sealed = bool(latest and latest["status"] == RoundState.SEALED)
    snapshot = latest["snapshot"] if sealed else None
    return {
        "run_id": run_id,
        "replay": replay,
        "rounds": [_round_view(item, entries) for item in rounds],
        "current": {
            "round_id": latest["id"] if latest else None,
            "seq": latest["seq"] if latest else 0,
            "sealed": sealed,
            "verdicts": snapshot["rows"] if sealed else verdicts,
            "metrics": snapshot["metrics"] if sealed else metrics,
            "conclusion_digest": latest["conclusion_digest"] if sealed else None,
        },
    }


def add(db, run_id, data, actor):
    """Backfill one paper receipt, idempotently keyed by registration number."""
    run = _run(db, run_id)
    if run["status"] == JobState.FAILED:
        raise HTTPException(409, "批次回放失败，无法补录对账")
    digest = entry_digest(run_id, data.asset, data.certificate, data.archived_at)
    existing = db.execute(
        select(receipt_entries).where(receipt_entries.c.registration_no == data.registration_no)
    ).mappings().first()
    if existing is not None:
        if existing["content_digest"] == digest:
            return dict(existing), False
        raise HTTPException(409, "同一登记号已入账且内容有变，已退回")
    duplicate = db.execute(select(receipt_entries).where(and_(
        receipt_entries.c.run_id == run_id,
        receipt_entries.c.asset == data.asset,
        receipt_entries.c.certificate == data.certificate,
    ))).mappings().first()
    if duplicate is not None:
        raise HTTPException(409, "该炉号与证书号的收据已入账，请核对登记号")
    round_id = _attach_round(db, run_id, actor)
    values = {
        "round_id": round_id,
        "run_id": run_id,
        "registration_no": data.registration_no,
        "asset": data.asset,
        "certificate": data.certificate,
        "archived_at": data.archived_at,
        # Received mid-replay: held on the books until the replay settles.
        "replay_pending": run["status"] != JobState.DONE,
        "content_digest": digest,
        "created_by": actor,
        "created_at": int(time()),
    }
    try:
        with db.begin_nested():
            db.execute(insert(receipt_entries).values(**values))
    except IntegrityError:
        # A concurrent retry of the same ledger key: resolve it idempotently.
        retry = db.execute(
            select(receipt_entries).where(receipt_entries.c.registration_no == data.registration_no)
        ).mappings().first()
        if retry is not None and retry["content_digest"] == digest:
            return dict(retry), False
        raise HTTPException(409, "同一登记号已入账且内容有变，已退回") from None
    entry = dict(db.execute(
        select(receipt_entries).where(receipt_entries.c.registration_no == data.registration_no)
    ).mappings().one())
    return entry, True


def _attach_round(db, run_id, actor):
    """Return the open round to receive a backfill, locking it against sealing.

    The round row is locked FOR UPDATE so a concurrent seal cannot close it
    between the status check and the insert. A distinct ledger key that lands
    after the previous round was sealed opens a fresh round.
    """
    latest = db.execute(
        select(receipt_rounds).where(receipt_rounds.c.run_id == run_id)
        .order_by(receipt_rounds.c.seq.desc()).limit(1).with_for_update()
    ).mappings().first()
    if latest is not None and latest["status"] == RoundState.OPEN:
        return latest["id"]
    seq = (latest["seq"] + 1) if latest is not None else 1
    db.execute(pg_insert(receipt_rounds).values(
        run_id=run_id, seq=seq, status=RoundState.OPEN, created_by=actor, created_at=int(time()),
    ).on_conflict_do_nothing(index_elements=["run_id", "seq"]))
    return db.execute(select(receipt_rounds.c.id).where(
        receipt_rounds.c.run_id == run_id, receipt_rounds.c.seq == seq,
    ).with_for_update()).scalar_one()


def seal(db, run_id, round_id, actor):
    """Seal a reconciliation round, freezing its conclusion.

    The run result (registration side) and the backfilled receipts are read in one
    snapshot transaction, so both pieces of evidence come from the same instant.
    The round row is locked FOR UPDATE so a concurrent backfill either lands
    before the snapshot (and is included) or opens the next round (and is not).
    """
    run = _run(db, run_id)
    if run["status"] != JobState.DONE:
        raise HTTPException(409, "批次仍在回放，先挂账；回放完成后再封存")
    round_row = db.execute(
        select(receipt_rounds).where(
            receipt_rounds.c.id == round_id, receipt_rounds.c.run_id == run_id,
        ).with_for_update()
    ).mappings().first()
    if round_row is None:
        raise HTTPException(404, "盘点不存在")
    if round_row["status"] == RoundState.SEALED:
        raise HTTPException(409, "该盘点已经封存")
    later = db.scalar(select(receipt_rounds.c.id).where(
        receipt_rounds.c.run_id == run_id, receipt_rounds.c.seq > round_row["seq"],
    ).limit(1))
    if later is not None:
        raise HTTPException(409, "请封存最新一次盘点")
    entries = [entry for entry in _entries(db, run_id) if entry["round_id"] == round_id]
    verdicts, metrics = evaluate(run["result"].get("rows") or [], entries)
    snapshot = {
        "rows": verdicts,
        "metrics": metrics,
        "run_digest": run["digest"],
        "run_finished_at": run["finished_at"],
        "sealed_entries": [entry["registration_no"] for entry in entries],
    }
    digest = content_digest(snapshot)
    db.execute(update(receipt_rounds).where(
        receipt_rounds.c.id == round_id,
    ).values(
        status=RoundState.SEALED, sealed_by=actor, sealed_at=int(time()),
        conclusion_digest=digest, snapshot=snapshot,
    ))
    return overview(db, run_id)


def bundle(db, run_id):
    """Evidence carried by export: every round, its entries and frozen conclusion."""
    entries = _entries(db, run_id)
    return [{
        **_round_view(item, entries),
        "snapshot": item["snapshot"],
    } for item in _rounds(db, run_id)]
