from enum import StrEnum


class RoundState(StrEnum):
    OPEN = "open"
    SEALED = "sealed"


# Reconciliation verdicts. A receipt that matches the run on both the oven number and
# the certificate number is MATCH; matching only one of the pair is WRONG and keeps
# both sides' original values; otherwise the missing side decides the verdict.
MATCH = "一致"
WRONG = "错号"
MISSING_REGISTRATION = "缺登记"
MISSING_RECEIPT = "缺收据"
PENDING = "回放挂账"

VERDICTS = (MATCH, WRONG, MISSING_RECEIPT, MISSING_REGISTRATION, PENDING)
