#!/usr/bin/env python3
"""
Outlook -> iCloud calendar bridge.

One-way mirror of a published Outlook/Exchange ICS feed into an iCloud
calendar, so the events can be shared with family through iCloud calendar
sharing (which a subscribed ICS calendar can't do). Recurring meetings are
expanded into concrete occurrences within a rolling window, so every run is
idempotent: it reconciles the window (create / update / delete) and nothing
else is ever touched.

Design notes:
- The iCloud app-specific password is never stored in a file. It is read
  from the macOS Keychain (or the ICLOUD_APP_PASSWORD environment variable).
- Only events that are actually BUSY/TENTATIVE/OOF are mirrored. FREE and
  CANCELLED events are always skipped.
- Managed events are tagged CATEGORIES:OutlookBusySync so this script only
  ever creates/updates/deletes its own events and never your real ones.
"""

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import caldav
import recurring_ical_events
import requests
from icalendar import Calendar, Event

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.json"
ICLOUD_CALDAV = "https://caldav.icloud.com/"
KEYCHAIN_SERVICE = "outlook-icloud-bridge"
MARKER = "OutlookBusySync"           # CATEGORIES tag identifying our events
BUSY_STATUSES = {"BUSY", "TENTATIVE", "OOF", "WORKINGELSEWHERE"}

DEFAULTS = {
    "label": "Busy",
    "include_titles": False,
    "include_location": False,
    "online_as_remote": True,
    "include_tentative": True,
    "skip_multiday_allday": False,
    "exclude_keywords": [],
    "window_back_days": 7,
    "window_fwd_days": 90,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("outlook-icloud-bridge")


def load_config(path):
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Config not found: {path} (run install.sh, or copy config.example.json)")
    cfg = dict(DEFAULTS)
    cfg.update(json.loads(path.read_text()))
    for key in ("feed_url", "apple_id", "target_calendar"):
        if not cfg.get(key):
            raise SystemExit(f"Config is missing {key!r}")
    return cfg


def icloud_password(apple_id):
    """App-specific password from $ICLOUD_APP_PASSWORD, else the macOS Keychain."""
    pw = os.environ.get("ICLOUD_APP_PASSWORD")
    if pw:
        return pw
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", apple_id, "-w"],
            capture_output=True, text=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise SystemExit(
            f"No iCloud password found. Add it to the Keychain with:\n"
            f"  security add-generic-password -U -s {KEYCHAIN_SERVICE} -a {apple_id} "
            f"-T /usr/bin/security -w"
        )
    return out.stdout.strip()


def to_utc(value):
    """Normalize a date/datetime to an aware UTC datetime (date -> midnight)."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc), False
    # all-day (date)
    return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc), True


def is_busy(component, cfg):
    status = str(component.get("STATUS", "")).upper()
    if status == "CANCELLED":
        return False
    cdo = str(component.get("X-MICROSOFT-CDO-BUSYSTATUS", "")).upper()
    if cdo:
        if cdo == "TENTATIVE" and not cfg["include_tentative"]:
            return False
        return cdo in BUSY_STATUSES
    if status == "TENTATIVE" and not cfg["include_tentative"]:
        return False
    # Fallback: TRANSP OPAQUE == busy
    return str(component.get("TRANSP", "OPAQUE")).upper() == "OPAQUE"


def clean_location(loc, cfg):
    """Keep human locations (room/building); optionally collapse meeting URLs to 'Remote'."""
    if not cfg["include_location"]:
        return ""
    loc = (loc or "").strip()
    if not loc:
        return ""
    low = loc.lower()
    if cfg["online_as_remote"] and ("http" in low or "teams.microsoft" in low or "zoom.us" in low):
        return "Remote"
    return loc


def stable_uid(source_uid, start_utc):
    h = hashlib.sha1(f"{source_uid}|{start_utc.isoformat()}".encode()).hexdigest()[:24]
    return f"obs-{h}@outlook-busy-bridge"


def fetch_feed(url, attempts=3, delay=20):
    """Download the ICS feed, retrying briefly (the network is often not up right after wake)."""
    for i in range(1, attempts + 1):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "outlook-icloud-bridge/1.0"})
            r.raise_for_status()
            return Calendar.from_ical(r.content)
        except requests.RequestException:
            if i == attempts:
                raise
            time.sleep(delay)


def event_summary(title, cfg):
    """'Label (Title)', 'Label', or just 'Title' when label is empty."""
    label = (cfg["label"] or "").strip()
    title = title if cfg["include_titles"] else ""
    if label and title:
        return f"{label} ({title})"
    return label or title or "Busy"


def excluded(title, cfg):
    low = title.lower()
    return any(k.lower() in low for k in cfg["exclude_keywords"] if k)


def build_desired(cal, cfg, now=None):
    """Expand the feed into the rolling window -> {uid: (start_utc, end_utc, allday, loc, summary)}."""
    now = now or dt.datetime.now(dt.timezone.utc)
    win_start = now - dt.timedelta(days=cfg["window_back_days"])
    win_end = now + dt.timedelta(days=cfg["window_fwd_days"])
    desired = {}
    for occ in recurring_ical_events.of(cal).between(win_start, win_end):
        if occ.name != "VEVENT" or not is_busy(occ, cfg):
            continue
        s = occ.get("DTSTART")
        e = occ.get("DTEND")
        if not s:
            continue
        start_utc, allday = to_utc(s.dt)
        if e:
            end_utc, _ = to_utc(e.dt)
        else:
            end_utc = start_utc + dt.timedelta(minutes=30)
        if cfg["skip_multiday_allday"] and allday and (end_utc - start_utc) > dt.timedelta(days=1):
            continue
        title = str(occ.get("SUMMARY", "")).strip()
        if excluded(title, cfg):
            continue
        loc = clean_location(str(occ.get("LOCATION", "")), cfg)
        uid = stable_uid(str(occ.get("UID", "")), start_utc)
        desired[uid] = (start_utc, end_utc, allday, loc, event_summary(title, cfg))
    return desired


def make_ics(uid, start_utc, end_utc, allday, location, summary):
    cal = Calendar()
    cal.add("prodid", "-//outlook-icloud-bridge//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", uid)
    ev.add("summary", summary)
    if location:
        ev.add("location", location)
    ev.add("categories", [MARKER])
    ev.add("transp", "OPAQUE")
    ev.add("dtstamp", dt.datetime.now(dt.timezone.utc))
    if allday:
        ev.add("dtstart", start_utc.date())
        ev.add("dtend", (end_utc if end_utc > start_utc else start_utc + dt.timedelta(days=1)).date())
    else:
        ev.add("dtstart", start_utc)
        ev.add("dtend", end_utc)
    cal.add_component(ev)
    return cal.to_ical()


def load_existing(calendar, start, end):
    """Return {uid: caldav.Event} for events we manage (tagged MARKER) between start and end."""
    found = {}
    for ev in calendar.search(start=start, end=end, event=True, expand=False):
        comp = ev.icalendar_component
        cats = comp.get("CATEGORIES")
        names = []
        if cats is not None:
            try:
                names = [str(c) for c in cats.cats]
            except AttributeError:
                names = [str(cats)]
        if MARKER in names:
            found[str(comp.get("UID"))] = ev
    return found


def existing_props(ev):
    comp = ev.icalendar_component
    s, _ = to_utc(comp.get("DTSTART").dt)
    e = comp.get("DTEND")
    e = to_utc(e.dt)[0] if e else s
    loc = str(comp.get("LOCATION", "")).strip()
    summary = str(comp.get("SUMMARY", "")).strip()
    return s, e, loc, summary


def find_calendar(cfg):
    client = caldav.DAVClient(url=ICLOUD_CALDAV, username=cfg["apple_id"],
                              password=icloud_password(cfg["apple_id"]))
    names = []
    for c in client.principal().calendars():
        try:
            name = c.get_display_name()
        except Exception:
            name = c.name
        if name == cfg["target_calendar"]:
            return c
        names.append(name)
    raise SystemExit(f"iCloud calendar {cfg['target_calendar']!r} not found. "
                     f"Available: {', '.join(map(repr, names))}")


def fmt(start, end, allday):
    if allday:
        return f"{start.date()} (all day)"
    s, e = start.astimezone(), end.astimezone()
    return f"{s:%Y-%m-%d %H:%M}-{e:%H:%M}"


def check_feed(cfg):
    """Print what would be synced, without touching iCloud."""
    feed = fetch_feed(cfg["feed_url"])
    desired = build_desired(feed, cfg)
    for s, e, allday, loc, summary in sorted(desired.values()):
        print(f"{fmt(s, e, allday):<28} {summary}" + (f"  @ {loc}" if loc else ""))
    print(f"\n{len(desired)} event(s) would be synced "
          f"({len(feed.walk('VEVENT'))} in feed, window -{cfg['window_back_days']}/+{cfg['window_fwd_days']} days)")


def sync(cfg, dry_run=False):
    calendar = find_calendar(cfg)
    feed = fetch_feed(cfg["feed_url"])
    desired = build_desired(feed, cfg)
    now = dt.datetime.now(dt.timezone.utc)
    existing = load_existing(calendar,
                             now - dt.timedelta(days=cfg["window_back_days"] + 1),
                             now + dt.timedelta(days=cfg["window_fwd_days"] + 1))

    created = updated = deleted = 0

    # create / update
    for uid, (s, e, allday, loc, summary) in desired.items():
        if uid not in existing:
            if dry_run:
                log.info("would create: %s %s", fmt(s, e, allday), summary)
            else:
                calendar.save_event(make_ics(uid, s, e, allday, loc, summary).decode())
            created += 1
        else:
            cs, ce, cloc, csum = existing_props(existing[uid])
            if (cs != s) or (ce != e) or (cloc != loc) or (csum != summary):
                if dry_run:
                    log.info("would update: %s %s", fmt(s, e, allday), summary)
                else:
                    existing[uid].delete()
                    calendar.save_event(make_ics(uid, s, e, allday, loc, summary).decode())
                updated += 1

    # delete stale managed events
    for uid, ev in existing.items():
        if uid not in desired:
            if dry_run:
                log.info("would delete: %s", existing_props(ev)[3])
            else:
                ev.delete()
            deleted += 1

    log.info("%s: feed=%d desired=%d existing=%d  created=%d updated=%d deleted=%d",
             "dry run" if dry_run else "sync ok",
             len(feed.walk("VEVENT")), len(desired), len(existing),
             created, updated, deleted)


def purge(cfg, dry_run=False):
    """Delete every event this tool created (used by uninstall.sh)."""
    calendar = find_calendar(cfg)
    now = dt.datetime.now(dt.timezone.utc)
    existing = load_existing(calendar, now - dt.timedelta(days=730), now + dt.timedelta(days=730))
    for ev in existing.values():
        if not dry_run:
            ev.delete()
    log.info("%s %d synced event(s)", "would delete" if dry_run else "deleted", len(existing))


def main():
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--config", default=DEFAULT_CONFIG, help="path to config.json")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check-feed", action="store_true",
                      help="list the events that would be synced; don't touch iCloud")
    mode.add_argument("--purge", action="store_true",
                      help="delete every event this tool created")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would change in iCloud without writing anything")
    args = p.parse_args()

    cfg = load_config(args.config)
    if args.check_feed:
        check_feed(cfg)
    elif args.purge:
        purge(cfg, dry_run=args.dry_run)
    else:
        sync(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        log.error("sync FAILED: %s", exc, exc_info=True)
        sys.exit(1)
