from pydantic import BaseModel, Field, field_validator

from src.domain.inputs import ASSET_CODE

LEDGER = ASSET_CODE


class ReceiptInput(BaseModel):
    registration_no: str = Field(min_length=1, max_length=32)
    asset: str
    certificate: str
    archived_at: int | None = Field(default=None, ge=0)

    @field_validator("registration_no")
    @classmethod
    def check_registration_no(cls, value):
        if not LEDGER.fullmatch(value):
            raise ValueError("登记号只允许大写字母、数字及连字符")
        return value

    @field_validator("asset", "certificate")
    @classmethod
    def check_codes(cls, value):
        if not ASSET_CODE.fullmatch(value):
            raise ValueError("炉号和证书号只允许大写字母、数字及连字符")
        return value


class ReceiptEntry(BaseModel):
    id: int
    round_id: int
    run_id: int
    registration_no: str
    asset: str
    certificate: str
    archived_at: int | None
    replay_pending: bool
    created_by: str
    created_at: int


class RoundEntry(BaseModel):
    id: int
    seq: int
    status: str
    created_by: str
    created_at: int
    sealed_by: str | None
    sealed_at: int | None
    conclusion_digest: str | None
    entries: list[dict]


class CurrentRound(BaseModel):
    round_id: int | None
    seq: int
    sealed: bool
    verdicts: list[dict]
    metrics: dict
    conclusion_digest: str | None


class ReceiptOverview(BaseModel):
    run_id: int
    replay: str
    rounds: list[RoundEntry]
    current: CurrentRound
