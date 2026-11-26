from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, ForeignKey, Integer, String, Table, UniqueConstraint,
)
from .base import metadata


# One reconciliation round ("盘点") per run. Paper receipts that arrive after a round
# is sealed open a new round, while the sealed round keeps its frozen conclusion.
receipt_rounds = Table(
    "receipt_rounds", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", ForeignKey("runs.id"), nullable=False, index=True),
    Column("seq", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_by", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("sealed_by", String(48), nullable=True),
    Column("sealed_at", BigInteger, nullable=True),
    Column("conclusion_digest", String(64), nullable=True),
    Column("snapshot", JSON(none_as_null=True), nullable=True),
    UniqueConstraint("run_id", "seq", name="uq_receipt_round_run_seq"),
)


# A single backfilled paper receipt. `registration_no` is the ledger key: retrying the
# same key is idempotent, but reusing it with different content is rejected.
receipt_entries = Table(
    "receipt_entries", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("round_id", ForeignKey("receipt_rounds.id"), nullable=False, index=True),
    Column("run_id", ForeignKey("runs.id"), nullable=False, index=True),
    Column("registration_no", String(32), nullable=False, unique=True),
    Column("asset", String(24), nullable=False),
    Column("certificate", String(24), nullable=False),
    Column("archived_at", BigInteger, nullable=True),
    Column("replay_pending", Boolean, nullable=False, default=False),
    Column("content_digest", String(64), nullable=False),
    Column("created_by", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("run_id", "asset", "certificate", name="uq_receipt_entry_run_pair"),
)
