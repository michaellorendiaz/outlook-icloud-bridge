"""Unit tests for the feed -> desired-events logic (no network, no iCloud).

Run with:  python -m unittest discover tests
"""

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from icalendar import Calendar  # noqa: E402

import bridge  # noqa: E402

NOW = dt.datetime(2026, 3, 2, 12, 0, tzinfo=dt.timezone.utc)

FEED = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VEVENT
UID:busy-1
SUMMARY:Budget review
LOCATION:Room 204
DTSTART:20260303T150000Z
DTEND:20260303T160000Z
X-MICROSOFT-CDO-BUSYSTATUS:BUSY
END:VEVENT
BEGIN:VEVENT
UID:free-1
SUMMARY:Lunch hold
DTSTART:20260303T170000Z
DTEND:20260303T180000Z
X-MICROSOFT-CDO-BUSYSTATUS:FREE
END:VEVENT
BEGIN:VEVENT
UID:cancel-1
SUMMARY:Cancelled sync
STATUS:CANCELLED
DTSTART:20260304T150000Z
DTEND:20260304T160000Z
X-MICROSOFT-CDO-BUSYSTATUS:BUSY
END:VEVENT
BEGIN:VEVENT
UID:tent-1
SUMMARY:Maybe meeting
LOCATION:https://teams.microsoft.com/l/meetup-join/abc
DTSTART:20260305T150000Z
DTEND:20260305T160000Z
X-MICROSOFT-CDO-BUSYSTATUS:TENTATIVE
END:VEVENT
BEGIN:VEVENT
UID:multi-1
SUMMARY:Room booking
DTSTART;VALUE=DATE:20260306
DTEND;VALUE=DATE:20260313
X-MICROSOFT-CDO-BUSYSTATUS:BUSY
END:VEVENT
BEGIN:VEVENT
UID:allday-1
SUMMARY:Offsite
DTSTART;VALUE=DATE:20260310
DTEND;VALUE=DATE:20260311
X-MICROSOFT-CDO-BUSYSTATUS:OOF
END:VEVENT
BEGIN:VEVENT
UID:weekly-1
SUMMARY:Team standup
DTSTART:20260302T140000Z
DTEND:20260302T143000Z
RRULE:FREQ=WEEKLY;COUNT=3
X-MICROSOFT-CDO-BUSYSTATUS:BUSY
END:VEVENT
BEGIN:VEVENT
UID:private-1
SUMMARY:Doctor appointment
DTSTART:20260311T150000Z
DTEND:20260311T160000Z
X-MICROSOFT-CDO-BUSYSTATUS:BUSY
END:VEVENT
END:VCALENDAR
"""


def desired(**overrides):
    cfg = dict(bridge.DEFAULTS)
    cfg.update(overrides)
    return bridge.build_desired(Calendar.from_ical(FEED), cfg, now=NOW)


def summaries(d):
    return sorted(v[4] for v in d.values())


class BuildDesiredTests(unittest.TestCase):
    def test_defaults_are_private_busy_blocks(self):
        d = desired()
        # busy-1, tent-1, multi-1, allday-1, 3x weekly, private-1
        self.assertEqual(len(d), 8)
        self.assertEqual(set(summaries(d)), {"Busy"})
        self.assertTrue(all(v[3] == "" for v in d.values()), "no locations by default")

    def test_free_and_cancelled_are_never_synced(self):
        d = desired(include_titles=True, label="")
        self.assertNotIn("Lunch hold", summaries(d))
        self.assertNotIn("Cancelled sync", summaries(d))

    def test_titles_and_label(self):
        self.assertIn("Work (Budget review)", summaries(desired(label="Work", include_titles=True)))
        self.assertIn("Budget review", summaries(desired(label="", include_titles=True)))

    def test_location_and_remote(self):
        d = desired(include_location=True)
        locs = {v[3] for v in d.values()}
        self.assertIn("Room 204", locs)
        self.assertIn("Remote", locs)
        d = desired(include_location=True, online_as_remote=False)
        self.assertTrue(any(v[3].startswith("https://") for v in d.values()))

    def test_tentative_toggle(self):
        self.assertNotIn("Maybe meeting",
                         summaries(desired(include_titles=True, label="", include_tentative=False)))

    def test_skip_multiday_allday_keeps_single_day(self):
        s = summaries(desired(include_titles=True, label="", skip_multiday_allday=True))
        self.assertNotIn("Room booking", s)
        self.assertIn("Offsite", s)

    def test_exclude_keywords(self):
        s = summaries(desired(include_titles=True, label="", exclude_keywords=["doctor"]))
        self.assertNotIn("Doctor appointment", s)

    def test_recurrence_expanded_with_stable_uids(self):
        s = summaries(desired(include_titles=True, label=""))
        self.assertEqual(s.count("Team standup"), 3)
        self.assertEqual(set(desired()), set(desired()), "UIDs must be deterministic")

    def test_uid_salt(self):
        plain, salted = set(desired()), set(desired(uid_salt="Bridge Test"))
        self.assertTrue(plain.isdisjoint(salted), "salt must change every UID")
        self.assertEqual(set(desired(uid_salt="")), plain, "no salt keeps original UIDs")

    def test_window_limits(self):
        self.assertEqual(len(desired(window_fwd_days=2)), 2)  # busy-1 + first standup


if __name__ == "__main__":
    unittest.main()
