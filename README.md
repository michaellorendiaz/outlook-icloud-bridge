# Outlook → iCloud Calendar Bridge

Copy your work Outlook calendar into an **iCloud calendar you can share with family**, so it shows up in Apple Calendar on everyone's iPhone, iPad and Mac.

Outlook can publish your calendar as an internet calendar (ICS) link, and Apple Calendar can subscribe to it. But a subscribed calendar is read-only, refreshes slowly, and **can't be shared through iCloud**. This tool fixes that. A small script on your Mac reads the Outlook feed every 15 minutes and copies your busy time into a normal iCloud calendar. You can share that calendar like any other.

```
Outlook (work)  ──ICS feed──▶  your Mac (every 15 min)  ──CalDAV──▶  iCloud "Work" calendar  ──▶  family
```

**What you get**

- Your meetings appear as `Busy` blocks. Optionally they can show `Work (Meeting title)` and a location, with Teams/Zoom links shown as "Remote".
- Recurring meetings, reschedules and cancellations stay in sync. Events that are removed in Outlook are removed in iCloud too.
- Only busy time is copied. Events marked *Free* and cancelled events are always skipped. Tentative events are optional.
- It only ever touches events it created. Each one is tagged, so your personal events are never changed, even if you sync into an existing calendar.
- No passwords are stored in files. Your iCloud password lives in the macOS Keychain.

**What you need**

- A Mac (macOS 12 or later is fine) with **Python 3.10+**. The `python3` that comes with macOS is too old. Install a newer one with `brew install python` ([Homebrew](https://brew.sh)) or from [python.org](https://www.python.org/downloads/macos/).
- An Outlook / Microsoft 365 account that is allowed to **publish calendars**. Most organizations allow this; some turn it off.
- An Apple ID with iCloud Calendar turned on.

> **Laptop note:** it syncs only while your Mac is awake and online. When the Mac wakes up it catches up right away. While the lid is closed, your family sees the calendar as of the last sync.

---

## Setup

### 1. Publish your Outlook calendar

1. Open **Outlook on the web** (outlook.office.com) → ⚙️ **Settings** → **Calendar** → **Shared calendars**.
2. Under **Publish a calendar**, choose your calendar and set permissions:
   - **Can view all details**: needed if you want meeting titles or locations.
   - **Can view titles and locations**: also works for titles and locations.
   - **Can view when I'm busy**: only plain busy blocks. Titles and locations won't be available.
3. Click **Publish** and copy the **ICS** link (it ends in `.ics`, not `.html`).

> 🔒 **Treat this link like a password.** Anyone who has it can read your calendar. Don't paste it into chats or commit it anywhere. If it ever leaks, click **Reset links** on the same screen and re-run `bash install.sh --reconfigure`.

### 2. Create the iCloud calendar and share it

1. In the Calendar app on your Mac: **File → New Calendar → iCloud**. Name it, for example, `Work`.
2. Right-click the calendar → **Share Calendar…** and add your family members.

We recommend a dedicated calendar for this. It's easy to share, hide or delete, and it keeps work events separate from your own.

### 3. Create an iCloud app-specific password

1. Go to [account.apple.com](https://account.apple.com) → **Sign-In and Security** → **App-Specific Passwords**.
2. Create one named `Outlook bridge` and keep it handy. The installer asks for it once and stores it in your Keychain.

### 4. Install

Open **Terminal** and either clone the repo:

```bash
git clone https://github.com/michaellorendiaz/outlook-icloud-bridge.git
cd outlook-icloud-bridge
bash install.sh
```

…or, without git, click **Code → Download ZIP** at the top of this page, unzip it, and run:

```bash
cd ~/Downloads/outlook-icloud-bridge-main
bash install.sh
```

The installer will:

1. Find Python 3.10+ and set up a private environment in `~/Library/Application Support/outlook-icloud-bridge/`.
2. Ask for your ICS link, Apple ID, calendar name and display options (see below).
3. Save the app-specific password in your Keychain.
4. **Show a preview** of exactly what it would create, without changing anything yet.
5. After you confirm, install a background job that syncs now and every 15 minutes.

Events appear in iCloud within a minute. They reach family devices whenever those devices next refresh.

---

## Options

The installer asks about the common options. All of them live in `~/Library/Application Support/outlook-icloud-bridge/config.json`, and you can edit that file at any time. Changes apply on the next sync, and existing events are relabeled automatically.

| Option | Default | What it does |
|---|---|---|
| `label` | `"Busy"` | Text shown on each event. Set it to `""` to show only the meeting title. |
| `include_titles` | `false` | Add the meeting title: `Work (Budget review)`. |
| `include_location` | `false` | Copy the meeting location (room, building, address). |
| `online_as_remote` | `true` | When showing locations, replace Teams/Zoom/web links with `Remote`. |
| `include_tentative` | `true` | Copy meetings you've marked Tentative. |
| `skip_multiday_allday` | `false` | Skip all-day events that span more than one day, such as room or resource bookings that land on your calendar. Single-day all-day events still sync. |
| `exclude_keywords` | `[]` | Skip events whose title contains any of these words (not case-sensitive), e.g. `["Focus time", "Lunch"]`. |
| `window_back_days` / `window_fwd_days` | `7` / `90` | How far back and ahead to keep in sync. |

Example: a fully private setup where family sees only "Busy":

```json
{ "label": "Busy", "include_titles": false, "include_location": false }
```

Example: show where you need to be:

```json
{ "label": "Work", "include_titles": true, "include_location": true }
```

To redo the questions (new ICS link, different calendar, new password), run `bash install.sh --reconfigure`.

> ⚠️ Titles and locations become visible to **everyone you share the iCloud calendar with**. Check your workplace's rules before sharing meeting details outside work.

---

## Everyday use

| Task | Command |
|---|---|
| Watch the log | `tail -f ~/Library/Logs/outlook-icloud-bridge.log` |
| Sync right now | `launchctl kickstart gui/$(id -u)/local.outlook-icloud-bridge` |
| Preview what the feed contains (no iCloud) | `~/Library/Application\ Support/outlook-icloud-bridge/venv/bin/python ~/Library/Application\ Support/outlook-icloud-bridge/bridge.py --check-feed` |
| Preview changes to iCloud without writing | `~/Library/Application\ Support/outlook-icloud-bridge/venv/bin/python ~/Library/Application\ Support/outlook-icloud-bridge/bridge.py --dry-run` |
| Update to a newer version | `git pull && bash install.sh` (or download the new ZIP and run `bash install.sh`) |

A healthy log line looks like:

```
2026-03-02 09:15:01 INFO sync ok: feed=82 desired=16 existing=16  created=0 updated=0 deleted=0
```

## Uninstall

```bash
bash uninstall.sh
```

This stops the background job. It also asks whether to delete the events it added (your own events are never touched) and whether to remove the Keychain password. Afterwards, revoke the app-specific password at account.apple.com and, if you like, stop publishing the calendar in Outlook.

---

## Troubleshooting

**"Python 3.10 or newer is required"**: run `brew install python` (or use the python.org installer), then re-run `bash install.sh`.

**`iCloud calendar 'Work' not found`**: the name must match exactly, including capitalization. The error lists the calendars it can see. The calendar must be an **iCloud** calendar, not "On My Mac" or a subscription.

**401 / authentication errors**: the app-specific password is wrong or was revoked. Create a new one and run `bash install.sh --reconfigure`. Use your Apple ID email, not a phone number.

**Feed errors (403 / 404)**: the published link was reset or unpublished in Outlook. Copy the new ICS link and run `bash install.sh --reconfigure`.

**Titles show as just "Busy"**: either `include_titles` is `false`, or the calendar was published with **Can view when I'm busy**. Re-publish it with more detail.

**A meeting is missing**: run with `--check-feed` to see what Outlook is actually publishing. Events marked *Free* are skipped on purpose. Outlook's published feed can also lag a few minutes behind your real calendar.

**I deleted a synced event in iCloud and it came back**: that's expected, because Outlook is the source of truth. Delete or decline it in Outlook, or add a word from its title to `exclude_keywords`.

**I cloned into Documents/Desktop. Is that a problem?** No. The installer copies everything to `~/Library/Application Support/`, which background jobs are allowed to read. You can delete the clone after installing (keep it if you want `uninstall.sh` and `git pull`).

---

## How it works

Each run:

1. Downloads the ICS feed and expands recurring meetings into individual events within the sync window.
2. Keeps only busy time, applies your filters, and builds each event's title and location.
3. Gives each event a stable ID (derived from the Outlook event ID and start time) and tags it `CATEGORIES:OutlookBusySync`.
4. Compares against the tagged events already in the iCloud calendar. It creates what's missing, updates what changed, and deletes tagged events that no longer exist in Outlook.

Because each run reconciles fully, it's safe to run as often as you like. If the Mac is asleep for a week, the next run brings everything up to date.

Development: `python3 -m venv venv && venv/bin/pip install -r requirements.txt && venv/bin/python -m unittest discover tests`.

## License

[MIT](LICENSE)
