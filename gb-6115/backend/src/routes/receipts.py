from fastapi import APIRouter, Depends

from src.controllers.access import identity, operator
from src.controllers.workbench import new_receipt, receipt_overview, seal_round
from src.schemas.receipts import ReceiptEntry, ReceiptInput, ReceiptOverview
from src.services import receipts

router = APIRouter(prefix="/runs/{run_id}/receipts", tags=["Receipts"])


@router.get("", response_model=ReceiptOverview, dependencies=[Depends(identity)])
def overview(run_id: int):
    return receipt_overview(run_id)


@router.post("", response_model=ReceiptEntry, status_code=201)
def backfill(run_id: int, form: ReceiptInput, user=Depends(operator)):
    return new_receipt(run_id, form, user)


@router.post("/rounds/{round_id}/seal", response_model=ReceiptOverview)
def seal(run_id: int, round_id: int, user=Depends(operator)):
    return seal_round(run_id, round_id, user)
