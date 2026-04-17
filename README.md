# Procare Media Downloader

Downloads all photos and videos from your child's [Procare](https://www.procareconnect.com/) daily activities for a given month.

Works with the parent portal at `schools.*.procareconnect.com`.

## Requirements

- Python 3.7+
- [requests](https://pypi.org/project/requests/)

## Setup

```bash
pip install requests
```

Or using the included requirements file:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python3 procare_downloader.py
```

You'll be prompted for:

| Prompt | Example | Description |
|--------|---------|-------------|
| Subdomain | `yourschool` | From your portal URL `schools.yourschool.procareconnect.com` |
| Email | `parent@example.com` | Your Procare parent login email |
| Password | *(hidden)* | Your Procare password |
| Month | `March 2026` | Target month (also accepts `2026-03`) |

## Output

Files are saved to `procare_downloads/<YYYY-MM_MonthName>/` with filenames based on activity date, type, and ID.

```
procare_downloads/
  2026-03_March/
    2026-03-05_photo_12345_0.jpg
    2026-03-05_video_12346_0.mp4
    ...
```

- Existing files are skipped (safe to re-run).
- All children on the account are downloaded automatically.

## How It Works

1. Authenticates with the Procare parent API
2. Retrieves all children linked to your account
3. Fetches daily activities for the specified month (paginated)
4. Extracts media URLs from photos, videos, and attachments
5. Downloads each file to the output folder
