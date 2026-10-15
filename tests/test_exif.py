from datetime import datetime, timezone

import pytest
from PIL.TiffImagePlugin import IFDRational

from photomap.exif import dms_to_decimal, read_gps, read_time


@pytest.mark.parametrize("ref, sign", [("N", 1), ("S", -1), ("E", 1), ("W", -1), (b"S\x00", -1)])
def test_dms_sign_follows_hemisphere(ref, sign):
    assert dms_to_decimal((39, 44, 21.12), ref) == pytest.approx(sign * 39.7392)


def test_null_island_and_missing_gps_are_rejected():
    zero = (IFDRational(0, 1),) * 3
    assert read_gps({1: "N", 2: zero, 3: "E", 4: zero}) == (None, None, None)
    assert read_gps({}) == (None, None, None)


def test_zero_denominator_is_rejected():
    bad = (IFDRational(1, 0), IFDRational(0, 1), IFDRational(0, 1))
    assert read_gps({1: "N", 2: bad, 3: "W", 4: bad})[0] is None


def test_zero_altitude_treated_as_unknown():
    deg = (IFDRational(39, 1), IFDRational(0, 1), IFDRational(0, 1))
    _, _, alt = read_gps({1: "N", 2: deg, 3: "W", 4: deg, 6: IFDRational(0, 1)})
    assert alt is None


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_time_prefers_recorded_offset():
    local, u, src = read_time({36867: "2026:07:04 18:30:00", 36881: "-06:00"}, {}, "America/Denver")
    assert (u, src) == (utc(2026, 7, 5, 0, 30), "offset")
    assert local == datetime(2026, 7, 4, 18, 30)


def test_time_falls_back_to_gps_clock():
    gps = {29: "2026:01:10", 7: (IFDRational(16, 1), IFDRational(15, 1), IFDRational(0, 1))}
    _, u, src = read_time({36867: "2026:01:10 09:15:00"}, gps, "America/Denver")
    assert (u, src) == (utc(2026, 1, 10, 16, 15), "gps")


@pytest.mark.parametrize("taken, expected_hour", [
    ("2026:02:14 12:00:00", 19),  # winter, UTC-7
    ("2026:07:14 12:00:00", 18),  # summer daylight saving, UTC-6
])
def test_fallback_timezone_handles_daylight_saving(taken, expected_hour):
    _, u, src = read_time({36867: taken}, {}, "America/Denver")
    assert src == "fallback" and u.hour == expected_hour


def test_missing_or_garbage_date():
    assert read_time({}, {}, "America/Denver") == (None, None, "none")
    assert read_time({36867: "0000:00:00 00:00:00"}, {}, "America/Denver")[2] == "none"


def test_subseconds_kept_so_burst_shots_differ():
    local, _, _ = read_time({36867: "2026:07:04 18:30:00", 37521: "45"}, {}, "America/Denver")
    assert local.microsecond == 450000
