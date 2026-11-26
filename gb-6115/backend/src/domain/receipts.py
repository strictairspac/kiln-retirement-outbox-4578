"""Reconciliation rules for backfilled paper receipts.

Pure functions only: the models and services pass frozen run rows and receipt
entries in, and get verdicts out. Keeping this module free of database imports
lets the pairing rules be tested in isolation.
"""

from src.constants.receipt_state import (
    MATCH, MISSING_RECEIPT, MISSING_REGISTRATION, VERDICTS, WRONG,
)
from src.utils.serial import content_digest


def entry_digest(run_id, asset, certificate, archived_at):
    """Identity of a backfill attempt for idempotent retries."""
    return content_digest({
        "run_id": run_id,
        "asset": asset,
        "certificate": certificate,
        "archived_at": archived_at,
    })


def _verdict(kind, asset, certificate, receipt, note):
    return {
        "判定": kind,
        "登记炉号": asset,
        "登记证书号": certificate,
        "收据炉号": receipt["asset"] if receipt else None,
        "收据证书号": receipt["certificate"] if receipt else None,
        "登记号": receipt["registration_no"] if receipt else None,
        "收据时间 s": receipt["archived_at"] if receipt else None,
        "说明": note,
    }


def evaluate(run_rows, entries):
    """Pair registrations with paper receipts by oven number and certificate number.

    Exact pairs are matched first so a partially matching receipt can never steal
    another registration's exact match. A receipt matching only one member of the
    pair is WRONG: both sides keep their original values instead of being rewritten.
    A registration with no receipt is MISSING_RECEIPT, and a receipt with no
    registration is MISSING_REGISTRATION.
    """
    registrations = sorted(
        {(row.get("炉体"), row.get("退役证书")) for row in run_rows if row.get("退役证书")},
        key=lambda pair: (pair[0] or "", pair[1] or ""),
    )
    ordered = sorted(
        entries,
        key=lambda item: (item.get("asset") or "", item.get("certificate") or "", item.get("registration_no") or ""),
    )
    remaining = list(ordered)
    outcome = {}

    for asset, certificate in registrations:
        match = next((item for item in remaining
                      if item["asset"] == asset and item["certificate"] == certificate), None)
        if match is not None:
            remaining.remove(match)
            outcome[(asset, certificate)] = _verdict(MATCH, asset, certificate, match, "炉号与证书号均与登记一致")

    for asset, certificate in registrations:
        if (asset, certificate) in outcome:
            continue
        half = next((item for item in remaining
                     if (item["asset"] == asset) != (item["certificate"] == certificate)), None)
        if half is not None:
            remaining.remove(half)
            outcome[(asset, certificate)] = _verdict(
                WRONG, asset, certificate, half, "仅炉号或证书号之一相符，保留两边原值，待更正",
            )

    for asset, certificate in registrations:
        if (asset, certificate) not in outcome:
            outcome[(asset, certificate)] = _verdict(
                MISSING_RECEIPT, asset, certificate, None, "登记有退役证书，档案室尚无对应纸质收据",
            )

    verdicts = [outcome[key] for key in registrations]
    for item in remaining:
        verdicts.append(_verdict(
            MISSING_REGISTRATION, None, None, item, "档案室收到收据，本批次没有对应退役登记",
        ))
    metrics = {kind: sum(1 for verdict in verdicts if verdict["判定"] == kind) for kind in VERDICTS}
    metrics["登记项"] = len(registrations)
    metrics["收据项"] = len(entries)
    return verdicts, metrics
