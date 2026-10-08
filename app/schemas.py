"""Strict public API input contracts."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .temporal import parse_iso_ms

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Speed = Annotated[float, Field(strict=True, ge=0.01, le=1000, allow_inf_nan=False)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateTimeline(InputModel):
    name: Name
    start_time: str
    speed: Speed = 1.0
    status: Literal["running", "paused"] = "running"

    @field_validator("start_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        parse_iso_ms(value)
        return value


class PatchTimeline(InputModel):
    name: Name | None = None
    speed: Speed | None = None
    virtual_time: str | None = None

    @model_validator(mode="before")
    @classmethod
    def nonempty_nonnull(cls, value):
        if isinstance(value, dict) and (not value or any(item is None for item in value.values())):
            raise ValueError("Specificare almeno un campo; i valori null non sono ammessi.")
        return value

    @field_validator("virtual_time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None:
            parse_iso_ms(value)
        return value


class SetTime(InputModel):
    virtual_time: str

    @field_validator("virtual_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        parse_iso_ms(value)
        return value
