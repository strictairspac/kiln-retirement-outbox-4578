from redis.exceptions import RedisError
from src.config.resources import cache, engine
from src.services import cases, receipts, reviews, runs
from src.services.audit import record


def new_case(data, user):
    with engine.begin() as db:
        return cases.create(db, data, user["username"])


def new_run(case_id, data, user):
    with engine.begin() as db:
        run = runs.enqueue(db, case_id, data.parameter, user["username"])
    try:
        cache.lpush("workbench:wake", str(run["id"]))
        cache.ltrim("workbench:wake", 0, 99)
    except RedisError:
        # The database holds the durable queue. The worker also polls it.
        pass
    return run


def new_review(run_id, data, user):
    with engine.begin() as db:
        return reviews.sign(db, run_id, data, user["username"])


def receipt_overview(run_id):
    # Registration and delivery evidence must come from one instant: read the
    # frozen run result and the backfilled receipts inside a single snapshot.
    with engine.execution_options(isolation_level="REPEATABLE READ").begin() as db:
        return receipts.overview(db, run_id)


def new_receipt(run_id, data, user):
    with engine.begin() as db:
        entry, created = receipts.add(db, run_id, data, user["username"])
        if created:
            record(db, "backfill_receipt", user["username"], "run", run_id)
        return entry


def seal_round(run_id, round_id, user):
    # The round row is locked FOR UPDATE, so a concurrent backfill either joins
    # this round before the snapshot or opens the next one afterwards.
    with engine.begin() as db:
        overview = receipts.seal(db, run_id, round_id, user["username"])
        record(db, "seal_receipt_round", user["username"], "run", run_id)
    return overview
