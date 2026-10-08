"""Pure UTC calendar and virtual-clock calculations, independent of I/O."""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from math import isfinite

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MIN_MS = -62_135_596_800_000
MAX_MS = 253_402_300_799_999
ISO_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)",
    re.ASCII,
)


class TemporalRangeError(ValueError):
    """The requested instant cannot be represented in years 0001 through 9999."""


def check_ms(value: int) -> int:
    if not MIN_MS <= value <= MAX_MS:
        raise TemporalRangeError("Data fuori intervallo: usare gli anni da 0001 a 9999 UTC.")
    return value


def parse_iso_ms(value: str) -> int:
    """Accept an explicit ISO 8601 offset; round sub-millisecond input half away from zero."""
    if not isinstance(value, str) or ISO_PATTERN.fullmatch(value) is None:
        raise ValueError("Usare ISO 8601 con secondi e fuso, ad esempio 2000-01-01T00:00:00Z.")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        elapsed = instant - EPOCH
        micros = (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
        sign = 1 if micros >= 0 else -1
        millis = sign * ((abs(micros) + 500) // 1000)
        return check_ms(millis)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Data ISO 8601 non valida o fuori dagli anni 0001–9999 UTC.") from exc


def format_iso_ms(value: int) -> str:
    instant = EPOCH + timedelta(milliseconds=check_ms(value))
    # strftime('%Y') is not zero-padded on every supported operating system.
    return (
        f"{instant.year:04d}-{instant.month:02d}-{instant.day:02d}T"
        f"{instant.hour:02d}:{instant.minute:02d}:{instant.second:02d}."
        f"{instant.microsecond // 1000:03d}Z"
    )


def virtual_now_ms(
    anchor_virtual_ms: int,
    anchor_real_ms: int,
    speed: float,
    status: str,
    real_now_ms: int,
) -> int:
    if status == "paused":
        return check_ms(anchor_virtual_ms)
    if status != "running":
        raise ValueError("Stato timeline non valido.")
    if isinstance(speed, bool) or not isfinite(speed) or not 0.01 <= speed <= 1000:
        raise ValueError("La velocità deve essere compresa tra 0.01 e 1000.")
    with localcontext() as context:
        context.prec = 60
        result = Decimal(anchor_virtual_ms) + Decimal(real_now_ms - anchor_real_ms) * Decimal(str(speed))
        rounded = int(result.to_integral_value(rounding=ROUND_HALF_UP))
    return check_ms(rounded)
