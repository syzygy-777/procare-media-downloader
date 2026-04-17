#!/usr/bin/env python3
"""
Procare Media Downloader

Downloads all photos and videos from Procare daily activities for a given month.
Works with the parent portal at schools.*.procareconnect.com.

Usage:
    python3 procare_downloader.py

You'll be prompted for your email, password, subdomain, and target month.
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


def parse_month_input(month_str):
    """Parse 'March 2026' or '2026-03' into (date_from, date_to) strings."""
    for fmt in ("%B %Y", "%b %Y", "%Y-%m"):
        try:
            dt = datetime.strptime(month_str.strip(), fmt)
            year, month = dt.year, dt.month
            date_from = f"{year}-{month:02d}-01"

            if month == 12:
                date_to = f"{year}-12-31"
            else:
                next_month = datetime(year, month + 1, 1)
                last_day = next_month - timedelta(days=1)
                date_to = last_day.strftime("%Y-%m-%d")

            return date_from, date_to
        except ValueError:
            continue

    raise ValueError(f"Could not parse '{month_str}'. Use format like 'March 2026' or '2026-03'.")


def main():
    print("=== Procare Media Downloader ===\n")

    subdomain = input("Subdomain (e.g. for schools.yourschool.procareconnect.com enter 'yourschool'): ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    month_str = input("Month to download (e.g. 'March 2026'): ").strip()

    date_from, date_to = parse_month_input(month_str)
    print(f"\nDate range: {date_from} to {date_to}")

    # Output folder
    folder_name = datetime.strptime(date_from, "%Y-%m-%d").strftime("%Y-%m_%B")
    output_dir = Path("procare_downloads") / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saving to: {output_dir}/\n")

    session = requests.Session()
    api_host = f"api-school.{subdomain}.{BASE_DOMAIN}"

    # Login
    print("Logging in...")
    token = login(session, subdomain, email, password)
    print("✓ Logged in\n")

    # Get children
    children = get_children(session, api_host, token)
    print(f"Found {len(children)} child(ren)\n")

    total_downloaded = 0
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
        total_downloaded += media_count

    print(f"\n=== Done! {total_downloaded} total files saved to {output_dir}/ ===")


if __name__ == "__main__":
    main()
