#!/usr/bin/env python3
"""Push a daily Apple Screen Time summary (all synced devices) into Capacities.

Data source: Screen Time's synced CloudKit store on this Mac:
  $(getconf DARWIN_USER_DIR)/com.apple.ScreenTimeAgent/Store/RMAdminStore-Cloud.sqlite
With "Share Across Devices" enabled in Screen Time settings, this database
contains usage from the iPhone/iPad too — so one Mac-side job covers everything.
Apple only retains ~30 days of this data, so the Capacities daily notes become
the permanent archive.

Reading the store requires Full Disk Access for the running process:
  - iTerm/Terminal for interactive runs
  - /usr/bin/python3 (or the launchd program) for scheduled runs

Usage:
  screentime_report.py inspect             # dump DB schema + row counts (sanity check)
  screentime_report.py report [--date D]   # print the markdown summary, don't push
  screentime_report.py push [--date D]     # append the summary to the Capacities daily note

--date accepts YYYY-MM-DD, "today" (default) or "yesterday".
"""
import argparse
import datetime as dt
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from capacities_client import CapacitiesClient

CORE_DATA_EPOCH = 978307200  # 2001-01-01 in unix seconds
MARKER = "Screen Time —"
TOP_APPS_PER_DEVICE = 8
MIN_SECONDS = 60

# NOTE: this query follows the commonly documented RMAdminStore schema
# (ZUSAGETIMEDITEM -> ZUSAGECATEGORY -> ZUSAGEBLOCK -> ZUSAGE -> ZCOREDEVICE).
# Run `inspect` first on a new machine/macOS version and adjust if tables moved.
USAGE_QUERY = """
SELECT
  COALESCE(d.ZNAME, 'This Mac')  AS device,
  t.ZBUNDLEIDENTIFIER            AS bundle_id,
  SUM(t.ZTOTALTIMEINSECONDS)     AS seconds
FROM ZUSAGETIMEDITEM t
JOIN ZUSAGECATEGORY c   ON t.ZCATEGORY = c.Z_PK
JOIN ZUSAGEBLOCK b      ON c.ZBLOCK = b.Z_PK
JOIN ZUSAGE u           ON b.ZUSAGE = u.Z_PK
LEFT JOIN ZCOREDEVICE d ON u.ZDEVICE = d.Z_PK
WHERE b.ZSTARTDATE >= :start AND b.ZSTARTDATE < :end
GROUP BY device, bundle_id
HAVING seconds >= :min_seconds
ORDER BY device, seconds DESC
"""

APP_NAMES = {
    "com.apple.Safari": "Safari",
    "com.apple.MobileSMS": "Messages",
    "com.apple.mobilesafari": "Safari",
    "com.apple.mobilemail": "Mail",
    "com.apple.mail": "Mail",
    "com.apple.Music": "Music",
    "com.apple.mobileslideshow": "Photos",
    "com.apple.camera": "Camera",
    "com.apple.Maps": "Maps",
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm",
    "com.burbn.instagram": "Instagram",
    "com.zhiliaoapp.musically": "TikTok",
    "com.google.ios.youtube": "YouTube",
    "com.hammerandchisel.discord": "Discord",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.tinyspeck.chatlyio": "Slack",
    "com.microsoft.VSCode": "VS Code",
    "com.reddit.Reddit": "Reddit",
    "net.whatsapp.WhatsApp": "WhatsApp",
    "com.spotify.client": "Spotify",
}


def find_store():
    darwin_user_dir = subprocess.run(
        ["getconf", "DARWIN_USER_DIR"], capture_output=True, text=True, check=True
    ).stdout.strip()
    store_dir = Path(darwin_user_dir) / "com.apple.ScreenTimeAgent" / "Store"
    for name in ("RMAdminStore-Cloud.sqlite", "RMAdminStore-Local.sqlite"):
        path = store_dir / name
        if path.exists():
            return path
    raise SystemExit(
        f"No Screen Time store readable at {store_dir}.\n"
        "If this says 'Operation not permitted', grant Full Disk Access to the "
        "process running this script (System Settings > Privacy & Security > "
        "Full Disk Access), then restart it."
    )


def open_copy(store_path):
    """Copy the DB (plus -wal/-shm) to a temp dir and open read-only, to avoid
    touching the live store while ScreenTimeAgent is writing to it."""
    tmp = Path(tempfile.mkdtemp(prefix="screentime_"))
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(store_path) + suffix)
        if src.exists():
            shutil.copy2(src, tmp / src.name)
    conn = sqlite3.connect(f"file:{tmp / store_path.name}?mode=ro", uri=True)
    return conn, tmp


def parse_date(value):
    today = dt.date.today()
    if value in (None, "today"):
        return today
    if value == "yesterday":
        return today - dt.timedelta(days=1)
    return dt.date.fromisoformat(value)


def day_bounds_core_data(day):
    start = dt.datetime.combine(day, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    return start.timestamp() - CORE_DATA_EPOCH, end.timestamp() - CORE_DATA_EPOCH


def pretty_app_name(bundle_id):
    if not bundle_id:
        return "Unknown"
    if bundle_id in APP_NAMES:
        return APP_NAMES[bundle_id]
    tail = bundle_id.rsplit(".", 1)[-1]
    return tail if tail.isupper() else tail.replace("-", " ").replace("_", " ").title()


def fmt_duration(seconds):
    h, m = int(seconds) // 3600, (int(seconds) % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def query_usage(conn, day):
    start, end = day_bounds_core_data(day)
    rows = conn.execute(
        USAGE_QUERY, {"start": start, "end": end, "min_seconds": MIN_SECONDS}
    ).fetchall()
    per_device = {}
    for device, bundle_id, seconds in rows:
        per_device.setdefault(device, []).append((pretty_app_name(bundle_id), seconds))
    return per_device


def build_markdown(day, per_device):
    lines = [f"## 📱 {MARKER} {day.isoformat()}", ""]
    if not per_device:
        lines.append("*No usage recorded.*")
        return "\n".join(lines)
    for device in sorted(per_device, key=lambda d: -sum(s for _, s in per_device[d])):
        apps = per_device[device]
        total = sum(s for _, s in apps)
        lines.append(f"**{device}** — {fmt_duration(total)}")
        for name, seconds in apps[:TOP_APPS_PER_DEVICE]:
            lines.append(f"- {name} — {fmt_duration(seconds)}")
        rest = sum(s for _, s in apps[TOP_APPS_PER_DEVICE:])
        if rest >= MIN_SECONDS:
            lines.append(f"- Other — {fmt_duration(rest)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def already_pushed(client, day):
    """True if the daily note for `day` already contains this report's heading."""
    date_str = day.isoformat()
    seen = set()
    for result in client.search(date_str, structure_ids=["RootDailyNote"], limit=5):
        if date_str not in result.get("title", "") or result["id"] in seen:
            continue
        seen.add(result["id"])
        obj = client.get_object(result["id"])
        stack = [b for blocks in (obj.get("blocks") or {}).values() for b in blocks]
        while stack:
            block = stack.pop()
            text = "".join(t.get("text", "") for t in block.get("tokens") or [])
            if MARKER in text and date_str in text:
                return True
            stack.extend(block.get("blocks") or [])
    return False


def cmd_inspect():
    store = find_store()
    print(f"store: {store}")
    conn, tmp = open_copy(store)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
            print(f"{count:>8}  {table}")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_report(day, push):
    store = find_store()
    conn, tmp = open_copy(store)
    try:
        per_device = query_usage(conn, day)
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)

    markdown = build_markdown(day, per_device)
    print(markdown)
    if not push:
        return
    client = CapacitiesClient()
    if already_pushed(client, day):
        print(f"\n[skip] daily note for {day} already has a Screen Time section")
        return
    client.append_daily_note(markdown, date=day.isoformat())
    print(f"\n[ok] appended to daily note {day}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "report", "push"])
    parser.add_argument("--date", default="today", help="YYYY-MM-DD, today, or yesterday")
    args = parser.parse_args()

    if args.command == "inspect":
        cmd_inspect()
    else:
        cmd_report(parse_date(args.date), push=args.command == "push")


if __name__ == "__main__":
    sys.exit(main())
