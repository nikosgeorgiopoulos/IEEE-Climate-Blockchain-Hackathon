"""Pydantic models for API input/output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

CheckStatus = Literal["pass", "fail", "pending"]


class Check(BaseModel):
    step: int
    name: str
    ok: bool
    status: CheckStatus
    detail: str


class ProofStepOut(BaseModel):
    pos: Literal["L", "R"]
    hash: str


class AnchorInfo(BaseModel):
    backend: str | None = None
    status: str | None = None
    tx: str | None = None
    url: str | None = None
    block: int | None = None
    anchored_at: str | None = None
    explorer_label: str | None = None
    onchain_root: str | None = None


class VerifyReport(BaseModel):
    reading_id: int
    device_id: str
    ts: str
    overall_ok: bool
    status: Literal["verified", "tampered", "pending"]
    first_failed_step: int | None = None
    checks: list[Check]
    canonical: str
    signature_hex: str
    pubkey_hex: str | None = None
    signer: str | None = None
    leaf_hash: str | None = None
    stored_leaf_hash: str | None = None
    batch_id: int | None = None
    leaf_index: int | None = None
    merkle_root: str | None = None
    proof: list[ProofStepOut] = Field(default_factory=list)
    anchor: AnchorInfo | None = None


class DeviceReading(BaseModel):
    """Signed reading POSTed by a device (ESP32). Values may be numbers or strings."""

    device_id: str = Field(min_length=1, max_length=64)
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    pm10: float | None = None
    pm25: float | None = None
    temperature: float | None = None
    humidity: float | None = None
    signature_hex: str = Field(pattern=r"^[0-9a-fA-F]{128}$")

    @field_validator("pm10", "pm25", "temperature", "humidity", mode="before")
    @classmethod
    def _numeric_strings(cls, v):
        if v is None or v == "":
            return None
        return float(v)


class TamperRequest(BaseModel):
    pm25: float | None = None  # explicit new value; default: original * 0.3
    resign: bool = False  # insider attack: also re-sign with the stolen key
