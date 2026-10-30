# Goal Tracker

A small desktop app for tracking any goal for any person or team. Each goal gets its
own tab and defines what it measures: time (logged in minutes, shown in hours) or a
custom unit such as pages, calls or modules. Every logged session needs an amount and
a short description of the activity. You can export a formatted PDF progress report to
share with a manager or use in an evaluation.

Built with Python and PySide6 (Qt). Works on Windows and Linux.

## Features

- Add, rename (double-click a tab), reorder (drag) and remove goal tabs
- Per-goal targets with a progress bar
- Required activity description on every session, with a date picker for logging past activities
- PDF report with a summary, totals by month and a full activity log,
  filterable by date range and goal
- Data saved to `goals.json` next to `main.py`

## Setup

Requires Python 3.9 or newer.

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## Run

```bash
python main.py
```

The app starts with no goals. Click **+ New goal** to add your first one.

## Your data

Goals and sessions are stored in `goals.json` in the same folder as `main.py`.
This file is listed in `.gitignore` so your personal data is not committed.
Back it up if you want to keep your history.

## Notes

- The PDF report uses US Letter paper. To use A4, change `PAGE_SIZE` near the top of
  `main.py` to `QPageSize.A4`.
- On Linux, the app sets two environment variables at startup to avoid a Qt crash on
  newer GNOME versions and to silence a harmless Wayland warning. They are skipped on
  other platforms.
