#!/usr/bin/env python3
"""
Procare Media Downloader

Downloads all photos and videos from Procare daily activities for one or more months.
Works with the parent portal at schools.*.procareconnect.com.

Usage:
    python3 procare_downloader.py

You'll be prompted for your email, password, subdomain, and target month(s).

Supports:
    - Single month:  March 2026
    - Comma list:    March 2026, April 2026
    - Range:         January 2026 - April 2026
"""

import os
import re
import sys
import json
import getpass
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse
from pathlib import Path

BASE_DOMAIN = "procareconnect.com"


def login(session, subdomain, email, password):
    """Authenticate and return the auth token."""
    api_host = f"api-school.{subdomain}.{BASE_DOMAIN}"
    url = f"https://{api_host}/api/web/auth/"
    resp = session.post(
        url,
        json={"email": email, "password": password},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    token = data.get("user", {}).get("auth_token") or data.get("auth_token")
    if not token:
        for key in data:
            if isinstance(data[key], dict) and "auth_token" in data[key]:
                token = data[key]["auth_token"]
                break

    if not token:
        print("Login response (keys):", list(data.keys()))
        raise SystemExit("Could not extract auth_token from login response. Check credentials.")

    return token


def api_get(session, api_host, token, path, params=None):
    """Make an authenticated GET request to the Procare API."""
    url = f"https://{api_host}{path}"
    resp = session.get(
        url,
        params=params,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    resp.raise_for_status()
    return resp.json()


def get_children(session, api_host, token):
    """Return list of children."""
    data = api_get(session, api_host, token, "/api/web/parent/kids/")
    return data.get("kids", data if isinstance(data, list) else [])


def get_activities(session, api_host, token, kid_id, date_from, date_to):
    """Fetch all daily activities for a child in the date range (paginated)."""
    activities = []
    page = 1
    while True:
        params = {
            "kid_id": kid_id,
            "filters[daily_activity][date_from]": date_from,
            "filters[daily_activity][date_to]": date_to,
            "page": page,
        }
        data = api_get(session, api_host, token, "/api/web/parent/daily_activities/", params)
        batch = data.get("daily_activities", [])
        if not batch:
            break
        activities.extend(batch)
        print(f"  Fetched page {page} ({len(batch)} activities)")
        page += 1
    return activities


def extract_media_urls(activity):
    """Extract all downloadable media URLs from any activity type."""
    urls = []
    act = activity.get("activiable") or activity

    # Direct photo/video fields
    for key in ("main_url", "photo_url", "original_url", "media_url", "image_url"):
        val = act.get(key)
        if val and isinstance(val, str) and val.startswith("http"):
            urls.append(val)

    if act.get("is_video") and act.get("video_file_url"):
        urls.append(act["video_file_url"])

    # Media embedded in nested structures
    for key in ("photos", "videos", "media", "attachments", "images"):
        items = act.get(key, [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str) and item.startswith("http"):
                    urls.append(item)
                elif isinstance(item, dict):
                    for sub_key in ("url", "main_url", "original_url", "photo_url", "video_file_url", "media_url"):
                        v = item.get(sub_key)
                        if v and isinstance(v, str) and v.startswith("http"):
                            urls.append(v)

    return list(dict.fromkeys(urls))  # dedupe preserving order


def safe_filename(url, activity, index):
    """Generate a filename from activity metadata and URL."""
    created = activity.get("created_at", "") or activity.get("activity_date", "")
    date_part = re.sub(r"[^\d\-T]", "", created[:19]).replace("T", "_") if created else "unknown"
    act_type = activity.get("activity_type", "media").replace("_activity", "")
    act_id = activity.get("id", "")

    # Get extension from URL
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if not ext or len(ext) > 6:
        ext = ".jpg"  # default

    return f"{date_part}_{act_type}_{act_id}_{index}{ext}"


def download_file(session, url, dest_path):
    """Download a file to disk."""
    try:
        resp = session.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"  ✗ Failed to download {url}: {e}")
        return False


def parse_single_month(month_str):
    """Parse 'March 2026' or '2026-03' into a datetime (first of that month)."""
    for fmt in ("%B %Y", "%b %Y", "%Y-%m"):
        try:
            return datetime.strptime(month_str.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Could not parse '{month_str}'. Use format like 'March 2026' or '2026-03'.")


def month_date_range(dt):
    """Return (date_from, date_to) strings for a given month datetime."""
    year, month = dt.year, dt.month
    date_from = f"{year}-{month:02d}-01"
    if month == 12:
        date_to = f"{year}-12-31"
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)
        date_to = last_day.strftime("%Y-%m-%d")
    return date_from, date_to


def expand_month_range(start_dt, end_dt):
    """Generate a list of month datetimes from start to end (inclusive)."""
    months = []
    current = start_dt.replace(day=1)
    end = end_dt.replace(day=1)
    while current <= end:
        months.append(current)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def parse_months_input(months_str):
    """Parse month input supporting single, comma-separated, and range formats.

    Returns a list of (date_from, date_to) tuples.

    Examples:
        'March 2026'                       -> [('2026-03-01', '2026-03-31')]
        'March 2026, May 2026'             -> [('2026-03-01', '2026-03-31'), ('2026-05-01', '2026-05-31')]
        'January 2026 - April 2026'        -> [(...), (...), (...), (...)]
    """
    months_str = months_str.strip()

    # Check for 'to' keyword range: 'Jan 2026 to Apr 2026'
    to_match = re.split(r'\s+to\s+', months_str, maxsplit=1, flags=re.IGNORECASE)
    if len(to_match) == 2:
        start_dt = parse_single_month(to_match[0])
        end_dt = parse_single_month(to_match[1])
        if end_dt < start_dt:
            raise ValueError("End month must be after start month.")
        return [month_date_range(dt) for dt in expand_month_range(start_dt, end_dt)]

    # Check for dash/en-dash range, but avoid splitting YYYY-MM on the internal hyphen.
    # We require at least one space around the separator to distinguish from YYYY-MM.
    dash_match = re.split(r'\s+[-–]\s+', months_str, maxsplit=1)
    if len(dash_match) == 2:
        start_dt = parse_single_month(dash_match[0])
        end_dt = parse_single_month(dash_match[1])
        if end_dt < start_dt:
            raise ValueError("End month must be after start month.")
        return [month_date_range(dt) for dt in expand_month_range(start_dt, end_dt)]

    # Check for comma-separated list
    parts = [p.strip() for p in months_str.split(",") if p.strip()]
    return [month_date_range(parse_single_month(p)) for p in parts]


def main():
    print("=== Procare Media Downloader ===\n")

    subdomain = input("Subdomain (e.g. for schools.yourschool.procareconnect.com enter 'yourschool'): ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    print("Month(s) to download. Supported formats:")
    print("  Single:  March 2026  or  2026-03")
    print("  Range:   Jan 2026 - Apr 2026  or  2026-01 - 2026-03  or  Jan 2026 to Apr 2026")
    print("  List:    March 2026, May 2026  or  2026-03, 2026-05")
    month_str = input("Enter month(s): ").strip()

    month_ranges = parse_months_input(month_str)
    print(f"\n{len(month_ranges)} month(s) to download\n")

    session = requests.Session()
    api_host = f"api-school.{subdomain}.{BASE_DOMAIN}"

    # Login
    print("Logging in...")
    token = login(session, subdomain, email, password)
    print("✓ Logged in\n")

    # Get children
    children = get_children(session, api_host, token)
    print(f"Found {len(children)} child(ren)\n")

    grand_total = 0
    for date_from, date_to in month_ranges:
        folder_name = datetime.strptime(date_from, "%Y-%m-%d").strftime("%Y-%m_%B")
        output_dir = Path("procare_downloads") / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"=== {folder_name} ({date_from} to {date_to}) → {output_dir}/ ===\n")

        for child in children:
            kid_id = child.get("id")
            kid_name = child.get("first_name", "child")
            print(f"--- {kid_name} ---")

            activities = get_activities(session, api_host, token, kid_id, date_from, date_to)
            print(f"  Total activities: {len(activities)}")

            media_count = 0
            for activity in activities:
                urls = extract_media_urls(activity)
                for i, url in enumerate(urls):
                    filename = safe_filename(url, activity, i)
                    dest = output_dir / filename
                    if dest.exists():
                        print(f"  ⊘ Already exists: {filename}")
                        media_count += 1
                        continue
                    print(f"  ↓ {filename}")
                    if download_file(session, url, dest):
                        media_count += 1

            print(f"  Downloaded {media_count} files for {kid_name}\n")
            grand_total += media_count

    print(f"\n=== Done! {grand_total} total files downloaded ===")


if __name__ == "__main__":
    main()
