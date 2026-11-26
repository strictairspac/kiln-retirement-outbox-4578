"""Rules for pairing backfilled paper receipts with frozen run registrations."""

from src.constants.receipt_state import MATCH, MISSING_RECEIPT, MISSING_REGISTRATION, WRONG
from src.domain.receipts import entry_digest, evaluate


ROWS = [
    {"炉体": "OV-203", "退役证书": "RET-1124-01"},
    {"炉体": "OV-217", "退役证书": None},          # rolled-back retirement: no certificate
    {"炉体": "OV-225", "退役证书": "RET-1124-03"},
]


def receipt(no, asset, certificate, archived_at=0, pending=False):
    return {
        "registration_no": no, "asset": asset, "certificate": certificate,
        "archived_at": archived_at, "replay_pending": pending,
    }


def kinds(verdicts):
    return [verdict["判定"] for verdict in verdicts]


def test_exact_pair_matches():
    verdicts, metrics = evaluate(ROWS, [receipt("P1", "OV-203", "RET-1124-01")])
    assert kinds(verdicts) == [MATCH, MISSING_RECEIPT]
    assert metrics[MATCH] == 1 and metrics[MISSING_RECEIPT] == 1
    assert metrics["登记项"] == 2 and metrics["收据项"] == 1


def test_half_match_is_wrong_and_keeps_both_originals():
    verdicts, _ = evaluate(ROWS, [receipt("P1", "OV-203", "RET-0000")])
    wrong = next(v for v in verdicts if v["判定"] == WRONG)
    assert wrong["登记炉号"] == "OV-203" and wrong["登记证书号"] == "RET-1124-01"
    assert wrong["收据炉号"] == "OV-203" and wrong["收据证书号"] == "RET-0000"


def test_exact_match_wins_over_half_match():
    # A half-matching receipt must not steal the registration's exact counterpart.
    # Once OV-225 pairs with P2 exactly, P1 is a leftover receipt for an oven whose
    # certificate is already accounted for: it becomes 缺登记, not a second 错号.
    entries = [
        receipt("P1", "OV-225", "RET-9999-03"),  # half match for OV-225, but no free registration
        receipt("P2", "OV-225", "RET-1124-03"),  # exact match for OV-225
        receipt("P3", "OV-203", "RET-1124-01"),  # exact for OV-203
    ]
    verdicts, _ = evaluate(ROWS, entries)
    assert kinds(verdicts) == [MATCH, MATCH, MISSING_REGISTRATION]


def test_receipt_without_registration_is_missing_registration():
    verdicts, metrics = evaluate(ROWS, [receipt("P9", "OV-999", "RET-9")])
    assert metrics[MISSING_REGISTRATION] == 1
    assert metrics["登记项"] == 2


def test_rolled_back_retirement_receipt_is_missing_registration():
    # OV-217 has no certificate, so a paper receipt for it has no registration side.
    verdicts, metrics = evaluate(ROWS, [receipt("P2", "OV-217", "RET-1124-02")])
    assert metrics[MISSING_REGISTRATION] == 1
    assert metrics[MISSING_RECEIPT] == 2


def test_duplicate_registration_rows_are_deduplicated():
    rows = ROWS + [{"炉体": "OV-203", "退役证书": "RET-1124-01"}]
    verdicts, metrics = evaluate(rows, [receipt("P1", "OV-203", "RET-1124-01")])
    assert metrics["登记项"] == 2
    assert kinds(verdicts) == [MATCH, MISSING_RECEIPT]


def test_entry_digest_changes_with_content():
    base = entry_digest(1, "OV-203", "RET-1124-01", 5)
    assert base == entry_digest(1, "OV-203", "RET-1124-01", 5)
    assert base != entry_digest(1, "OV-203", "RET-1124-99", 5)
    assert base != entry_digest(2, "OV-203", "RET-1124-01", 5)
    assert base != entry_digest(1, "OV-203", "RET-1124-01", 6)
