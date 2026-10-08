"""Deterministic tests of UTC calendar arithmetic and virtual clocks."""

import unittest

from app.temporal import (
    MAX_MS,
    MIN_MS,
    TemporalRangeError,
    format_iso_ms,
    parse_iso_ms,
    virtual_now_ms,
)


class TemporalTests(unittest.TestCase):
    def test_three_virtual_hours_per_real_hour(self):
        start = parse_iso_ms("2000-01-01T00:00:00Z")
        current = virtual_now_ms(start, 1000, 3, "running", 3_601_000)
        self.assertEqual(format_iso_ms(current), "2000-01-01T03:00:00.000Z")

    def test_fractional_speed_and_millisecond_rounding(self):
        self.assertEqual(virtual_now_ms(0, 0, 0.5, "running", 1), 1)
        self.assertEqual(virtual_now_ms(0, 0, 0.5, "running", -1), -1)
        self.assertEqual(virtual_now_ms(0, 0, 0.01, "running", 100), 1)
        self.assertEqual(virtual_now_ms(0, 0, 0.1, "running", 35), 4)

    def test_paused_clock_ignores_real_elapsed_time(self):
        self.assertEqual(virtual_now_ms(12345, 100, 1000, "paused", 10**15), 12345)

    def test_leap_day_and_variable_month_lengths(self):
        cases = [
            ("2000-02-28T23:59:59Z", "2000-02-29T00:00:00.000Z"),
            ("2000-02-29T23:59:59Z", "2000-03-01T00:00:00.000Z"),
            ("1900-02-28T23:59:59Z", "1900-03-01T00:00:00.000Z"),
            ("2026-04-30T23:59:59Z", "2026-05-01T00:00:00.000Z"),
            ("2026-12-31T23:59:59Z", "2027-01-01T00:00:00.000Z"),
        ]
        for start, expected in cases:
            with self.subTest(start=start):
                value = virtual_now_ms(parse_iso_ms(start), 0, 1, "running", 1000)
                self.assertEqual(format_iso_ms(value), expected)

    def test_explicit_offset_normalizes_to_utc(self):
        self.assertEqual(
            format_iso_ms(parse_iso_ms("2000-01-01T01:30:00+01:30")),
            "2000-01-01T00:00:00.000Z",
        )
        self.assertEqual(parse_iso_ms("1970-01-01T00:00:00.0005Z"), 1)
        self.assertEqual(parse_iso_ms("1969-12-31T23:59:59.9995Z"), -1)

    def test_calendar_limits_round_trip(self):
        self.assertEqual(format_iso_ms(MIN_MS), "0001-01-01T00:00:00.000Z")
        self.assertEqual(format_iso_ms(MAX_MS), "9999-12-31T23:59:59.999Z")
        self.assertEqual(parse_iso_ms(format_iso_ms(MIN_MS)), MIN_MS)
        self.assertEqual(parse_iso_ms(format_iso_ms(MAX_MS)), MAX_MS)

    def test_out_of_range_results_rejected(self):
        for value in (MIN_MS - 1, MAX_MS + 1):
            with self.subTest(value=value), self.assertRaises(TemporalRangeError):
                format_iso_ms(value)
        with self.assertRaises(TemporalRangeError):
            virtual_now_ms(MAX_MS, 0, 1, "running", 1)
        with self.assertRaises(TemporalRangeError):
            virtual_now_ms(MIN_MS, 1, 1, "running", 0)

    def test_invalid_dates_and_offsets_rejected(self):
        invalid = [
            "2000-01-01T00:00:00", "2000-01-01", "2001-02-29T00:00:00Z",
            "0000-01-01T00:00:00Z", "10000-01-01T00:00:00Z",
            "2000-01-01T00:00:00+00:60", "2000-01-01T00:00:00+00:99",
            "2000-01-01T00:00:00-00:99", "2000-01-01T00:00:00+24:00",
            "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59.9995Z",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_iso_ms(value)

    def test_invalid_speed_or_status_rejected(self):
        for speed in (True, 0, -1, 0.009, 1001, float("nan"), float("inf")):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                virtual_now_ms(0, 0, speed, "running", 1)
        with self.assertRaises(ValueError):
            virtual_now_ms(0, 0, 1, "invalid", 1)


if __name__ == "__main__":
    unittest.main()
