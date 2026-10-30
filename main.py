"""Goal tracker built with PySide6 / Qt (no tkinter).

Install:  pip install PySide6
Run:      python goal_tracker_qt.py

Features
  - Any number of goal tabs: add, rename (double-click a tab), reorder (drag), remove
  - Each goal defines what it tracks: time (logged in minutes, shown in hours)
    or a custom unit such as pages, calls or modules
  - Every logged session needs an amount and an activity description
  - Export a formatted PDF progress report (summary, totals by month, activity log)
  - Data is stored in goals.json next to this script. Files from earlier
    versions are migrated automatically.
"""

import html
import json
import os
import sys
import uuid
from collections import OrderedDict
from datetime import date, datetime

# Linux-only workarounds. On newer GNOME, Qt's GTK theme integration crashes
# reading a settings key that no longer exists, and Wayland prints a harmless
# text-input warning. These must be set before Qt loads, and only on Linux.
if sys.platform.startswith("linux"):
    os.environ["QT_QPA_PLATFORMTHEME"] = "generic"
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.wayland.textinput=false"

from PySide6.QtCore import QDate, QMarginsF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPageLayout, QPageSize, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goals.json")

# Paper size for the PDF report. Change to QPageSize.A4 if you prefer A4.
PAGE_SIZE = QPageSize.Letter

PALETTE = ["#8b5cf6", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#ec4899", "#14b8a6", "#6366f1"]

STYLESHEET = """
QWidget { background: #14151a; color: #e6e7eb; font-size: 13px; }
QLabel { background: transparent; }
QTabWidget::pane { border: none; }
QTabBar::tab {
    background: transparent; color: #8b8fa3; padding: 10px 20px;
    font-weight: 600; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid #e6e7eb; }
QTabBar::tab:hover { color: #ffffff; }
QFrame#card, QFrame#row { background: #1e2028; border-radius: 14px; }
QFrame#row { border-radius: 10px; }
QLineEdit, QDateEdit, QComboBox {
    background: #1e2028; border: 1px solid #2c2f3a; border-radius: 8px; padding: 8px 10px;
}
QLineEdit:focus, QDateEdit:focus, QComboBox:focus { border: 1px solid #5a5f78; }
QLineEdit:disabled { color: #5a5f78; }
QComboBox QAbstractItemView {
    background: #1e2028; border: 1px solid #2c2f3a; selection-background-color: #383c4b;
}
QPushButton {
    background: #2c2f3a; border: none; border-radius: 8px; padding: 8px 16px; font-weight: 600;
}
QPushButton:hover { background: #383c4b; }
QPushButton#primary { background: #e6e7eb; color: #14151a; }
QPushButton#primary:hover { background: #ffffff; }
QPushButton#danger { background: transparent; color: #f87171; }
QPushButton#danger:hover { background: #3a2328; }
QPushButton#del { background: transparent; color: #8b8fa3; padding: 4px 8px; }
QPushButton#del:hover { background: #383c4b; color: #ffffff; }
QCheckBox { background: transparent; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid #5a5f78; background: #1e2028;
}
QCheckBox::indicator:checked { background: #8b5cf6; border-color: #8b5cf6; }
QProgressBar { background: #2c2f3a; border: none; border-radius: 6px; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #383c4b; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


# ---------------------------------------------------------------------------
# Goal model
#
# A goal is a dict:
#   id, name, color, target, sessions
#   kind: "time"  -> sessions store {"date", "minutes", "note"}; target is in hours
#   kind: "count" -> sessions store {"date", "amount",  "note"}; target is in `unit`
# The JSON key "note" holds the activity description (kept for compatibility).
# ---------------------------------------------------------------------------

def new_goal(name, kind, unit, target, color):
    return {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "kind": kind,
        "unit": "hours" if kind == "time" else unit,
        "target": float(target),
        "color": color,
        "sessions": [],
    }


def is_time(goal):
    return goal.get("kind", "time") == "time"


def unit_name(goal):
    return "hours" if is_time(goal) else goal["unit"]


def qty(goal, session):
    """A session's raw quantity: minutes for time goals, the amount for others."""
    return session["minutes"] if is_time(goal) else session["amount"]


def total_display(goal, qty_sum):
    """Convert a summed raw quantity to display units (hours, or the custom unit)."""
    return qty_sum / 60 if is_time(goal) else qty_sum


def fmt_duration(minutes):
    hours, mins = divmod(int(round(minutes)), 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def fmt_qty(goal, quantity, escape=False):
    """Format a session quantity or a period total, e.g. '1 h 30 min' or '12 pages'."""
    if is_time(goal):
        return fmt_duration(quantity)
    unit = html.escape(goal["unit"]) if escape else goal["unit"]
    return f"{quantity:g} {unit}"


def fmt_progress(goal, total, target, escape=False):
    frac = total / target if target else 0
    if is_time(goal):
        return f"{total:.1f} / {target:g} h ({frac * 100:.0f}%)"
    unit = html.escape(goal["unit"]) if escape else goal["unit"]
    return f"{total:g} / {target:g} {unit} ({frac * 100:.0f}%)"


# ---------------------------------------------------------------------------
# Data handling
# ---------------------------------------------------------------------------

def pick_color(goals):
    used = {g["color"] for g in goals}
    for color in PALETTE:
        if color not in used:
            return color
    return PALETTE[len(goals) % len(PALETTE)]


def default_data():
    """A fresh install starts with no goals; the user adds their own."""
    return {"version": 3, "settings": {"prepared_by": ""}, "goals": []}


def normalize(raw):
    """Accept the current format or older ones (including {"soft_skills": {...}, ...})."""
    if not isinstance(raw, dict):
        return default_data()

    is_current = isinstance(raw.get("goals"), list)
    if is_current:
        goals_raw = raw["goals"]
        settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    else:
        goals_raw = [
            {"id": key, **value}
            for key, value in raw.items()
            if isinstance(value, dict) and "sessions" in value
        ]
        settings = {}

    goals = []
    for g in goals_raw:
        if not isinstance(g, dict):
            continue
        kind = "count" if g.get("kind") == "count" else "time"
        try:
            target = float(g.get("target", g.get("target_hours", 20)))
        except (TypeError, ValueError):
            target = 20.0
        if target <= 0:
            target = 20.0
        goal = {
            "id": str(g.get("id") or uuid.uuid4().hex[:8]),
            "name": str(g.get("name") or g.get("label") or "Untitled goal"),
            "kind": kind,
            "unit": "hours" if kind == "time" else str(g.get("unit") or "units"),
            "target": target,
            "color": str(g.get("color") or pick_color(goals)),
            "sessions": [],
        }
        value_key = "minutes" if kind == "time" else "amount"
        for s in g.get("sessions", []):
            try:
                value = float(s[value_key])
            except (KeyError, TypeError, ValueError):
                continue
            goal["sessions"].append({
                "date": str(s.get("date") or date.today().isoformat()),
                value_key: value,
                "note": str(s.get("note") or ""),
            })
        goals.append(goal)

    if not is_current and not goals:
        return default_data()
    return {"version": 3, "settings": {"prepared_by": str(settings.get("prepared_by", ""))}, "goals": goals}


def load_data():
    if not os.path.exists(DATA_FILE):
        return default_data()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return normalize(json.load(f))
    except (json.JSONDecodeError, OSError):
        # Keep the unreadable file instead of silently overwriting it later.
        try:
            os.replace(DATA_FILE, DATA_FILE + ".corrupt")
        except OSError:
            pass
        return default_data()


def save_data(data):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)


def parse_positive(text):
    """Return a positive float or None."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def tab_title(name):
    return name.replace("&", "&&")  # a single & would be treated as a shortcut marker


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------

REPORT_FONT = "'Segoe UI', 'Helvetica Neue', Arial, sans-serif"


def esc(text):
    return html.escape(str(text))


def fmt_date(d):
    return f"{d:%b} {d.day}, {d.year}"


def fmt_iso(iso):
    try:
        return fmt_date(datetime.strptime(iso, "%Y-%m-%d").date())
    except ValueError:
        return esc(iso)


def month_label(key):
    try:
        return f"{datetime.strptime(key, '%Y-%m'):%B %Y}"
    except ValueError:
        return esc(key)


def progress_bar_html(frac, color):
    pct = max(0, min(100, round(frac * 100)))
    cell = 'style="font-size:3pt;"'
    if pct <= 0:
        cells = f'<td bgcolor="#e5e7eb" {cell}>&nbsp;</td>'
    elif pct >= 100:
        cells = f'<td bgcolor="{esc(color)}" {cell}>&nbsp;</td>'
    else:
        cells = (f'<td width="{pct}%" bgcolor="{esc(color)}" {cell}>&nbsp;</td>'
                 f'<td bgcolor="#e5e7eb" {cell}>&nbsp;</td>')
    return f'<table width="100%" cellspacing="0" cellpadding="0"><tr>{cells}</tr></table>'


def html_table(headers, rows, widths, right_cols=()):
    out = ['<table width="100%" cellspacing="0" cellpadding="6">', '<tr bgcolor="#f3f4f6">']
    for j, (header, width) in enumerate(zip(headers, widths)):
        align = "right" if j in right_cols else "left"
        out.append(f'<th width="{width}" align="{align}" style="color:#374151;">{header}</th>')
    out.append("</tr>")
    for i, row in enumerate(rows):
        out.append(f'<tr bgcolor="{"#ffffff" if i % 2 == 0 else "#f9fafb"}">')
        for j, cell in enumerate(row):
            align = "right" if j in right_cols else "left"
            out.append(f'<td align="{align}" valign="top">{cell}</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def build_report_html(goals, prepared_by, start, end):
    start_iso, end_iso = start.isoformat(), end.isoformat()

    summary_rows = []
    sections = []
    total_time_minutes = 0
    total_sessions = 0
    any_time_goal = False

    for index, goal in enumerate(goals):
        color = goal["color"]
        in_period = sorted(
            (s for s in goal["sessions"] if start_iso <= s["date"] <= end_iso),
            key=lambda s: s["date"],
        )
        period_qty = sum(qty(goal, s) for s in in_period)
        all_total = total_display(goal, sum(qty(goal, s) for s in goal["sessions"]))
        target = goal["target"]
        frac = all_total / target if target else 0
        progress_text = fmt_progress(goal, all_total, target, escape=True)
        value_header = "Time" if is_time(goal) else "Amount"

        total_sessions += len(in_period)
        if is_time(goal):
            any_time_goal = True
            total_time_minutes += period_qty

        summary_rows.append([
            f'<b style="color:{esc(color)};">{esc(goal["name"])}</b>',
            str(len(in_period)),
            fmt_qty(goal, period_qty, escape=True),
            f"{progress_text}{progress_bar_html(frac, color)}",
        ])

        # --- Section for this goal ---
        page_break = "page-break-before:always; " if index > 0 else ""
        sections.append(
            f'<h2 style="{page_break}color:{esc(color)}; margin-top:18px;">{esc(goal["name"])}</h2>'
            f'<p style="color:#4b5563;">All-time progress: <b>{progress_text}</b><br>'
            f'In this period: <b>{fmt_qty(goal, period_qty, escape=True)}</b> '
            f'across <b>{len(in_period)}</b> session(s)</p>'
            f'{progress_bar_html(frac, color)}'
        )

        if not in_period:
            sections.append('<p style="color:#6b7280;"><i>No sessions logged in this period.</i></p>')
            continue

        months = OrderedDict()
        for s in in_period:
            bucket = months.setdefault(s["date"][:7], [0, 0.0])
            bucket[0] += 1
            bucket[1] += qty(goal, s)
        month_rows = [
            [month_label(k), str(v[0]), fmt_qty(goal, v[1], escape=True)]
            for k, v in months.items()
        ]
        sections.append(f'<h3 style="color:#374151; margin-top:16px;">{value_header} by month</h3>')
        sections.append(html_table(["Month", "Sessions", value_header], month_rows,
                                   ["50%", "20%", "30%"], right_cols={1, 2}))

        log_rows = [
            [
                fmt_iso(s["date"]),
                esc(s["note"]) if s["note"].strip() else "<i>(no description)</i>",
                fmt_qty(goal, qty(goal, s), escape=True),
            ]
            for s in in_period
        ]
        log_rows.append(["", "<b>Total</b>", f"<b>{fmt_qty(goal, period_qty, escape=True)}</b>"])
        sections.append('<h3 style="color:#374151; margin-top:16px;">Activity log</h3>')
        sections.append(html_table(["Date", "Activity", value_header], log_rows,
                                   ["18%", "58%", "24%"], right_cols={2}))

    headline = (f"<b>{total_sessions}</b> session(s) logged across "
                f"<b>{len(goals)}</b> goal(s)")
    if any_time_goal:
        headline += f"; <b>{fmt_duration(total_time_minutes)}</b> on time-based goals"

    meta = []
    if prepared_by:
        meta.append(f"Prepared by <b>{esc(prepared_by)}</b>")
    meta.append(f"Period: {fmt_date(start)} &ndash; {fmt_date(end)}")
    meta.append(f"Generated: {fmt_date(date.today())}")

    head = (
        '<h1 style="color:#111827; margin-bottom:2px;">Goal Progress Report</h1>'
        f'<p style="color:#6b7280;">{" &nbsp;|&nbsp; ".join(meta)}</p>'
        '<h2 style="color:#111827; margin-top:14px;">Summary</h2>'
        f"<p>In this period: {headline}.</p>"
        + html_table(["Goal", "Sessions", "In period", "All-time progress vs. target"],
                     summary_rows, ["26%", "12%", "20%", "42%"], right_cols={1, 2})
    )

    return (f'<html><body style="font-family:{REPORT_FONT}; font-size:10pt; color:#111827;">'
            f'{head}{"".join(sections)}</body></html>')


def write_pdf(path, html_text):
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(PAGE_SIZE))
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)
    writer.setTitle("Goal Progress Report")
    doc = QTextDocument()
    doc.setHtml(html_text)
    doc.print_(writer)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

class NewGoalDialog(QDialog):
    TIME_DEFAULT = "20"
    COUNT_DEFAULT = "100"

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("New goal")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Public speaking, Read 12 books")
        self.kind_box = QComboBox()
        self.kind_box.addItems(["Time (logged in minutes, shown in hours)", "Custom unit"])
        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("e.g. pages, calls, modules")
        self.target_label = QLabel("Target (hours)")
        self.target_edit = QLineEdit(self.TIME_DEFAULT)

        form.addRow("Name", self.name_edit)
        form.addRow("Track", self.kind_box)
        form.addRow("Unit", self.unit_edit)
        form.addRow(self.target_label, self.target_edit)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ef4444;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.kind_box.currentIndexChanged.connect(self.on_kind_changed)
        self.on_kind_changed()

    def on_kind_changed(self):
        custom = self.kind_box.currentIndex() == 1
        self.unit_edit.setEnabled(custom)
        if not custom:
            self.unit_edit.clear()
        self.target_label.setText("Target (units)" if custom else "Target (hours)")
        # Swap the default target only if the user hasn't typed their own.
        if self.target_edit.text() in (self.TIME_DEFAULT, self.COUNT_DEFAULT):
            self.target_edit.setText(self.COUNT_DEFAULT if custom else self.TIME_DEFAULT)

    def name(self):
        return self.name_edit.text().strip()

    def kind(self):
        return "count" if self.kind_box.currentIndex() == 1 else "time"

    def unit(self):
        return self.unit_edit.text().strip() if self.kind() == "count" else "hours"

    def target(self):
        return parse_positive(self.target_edit.text())

    def validate(self):
        if not self.name():
            self.error_label.setText("Give the goal a name.")
        elif self.kind() == "count" and not self.unit():
            self.error_label.setText("Enter a unit, such as pages or calls.")
        elif self.target() is None:
            self.error_label.setText("Target must be a positive number.")
        else:
            self.accept()


class ExportDialog(QDialog):
    def __init__(self, parent, goals, prepared_by):
        super().__init__(parent)
        self.setWindowTitle("Export PDF report")
        self.setMinimumWidth(400)
        self.goals = goals

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        self.name_edit = QLineEdit(prepared_by)
        self.name_edit.setPlaceholderText("Your name (shown on the report)")
        form.addRow("Prepared by", self.name_edit)

        today = QDate.currentDate()
        dates = sorted(s["date"] for g in goals for s in g["sessions"])
        first = QDate.fromString(dates[0], "yyyy-MM-dd") if dates else today
        if not first.isValid():
            first = today
        self.start_edit = QDateEdit(first)
        self.end_edit = QDateEdit(today)
        for edit in (self.start_edit, self.end_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("MMM d, yyyy")
        form.addRow("From", self.start_edit)
        form.addRow("To", self.end_edit)
        layout.addLayout(form)

        layout.addWidget(QLabel("Goals to include"))
        self.checks = []
        for goal in goals:
            box = QCheckBox(goal["name"])
            box.setChecked(True)
            self.checks.append((box, goal))
            layout.addWidget(box)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ef4444;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Export...")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_goals(self):
        return [goal for box, goal in self.checks if box.isChecked()]

    def start_date(self):
        return self.start_edit.date().toPython()

    def end_date(self):
        return self.end_edit.date().toPython()

    def validate(self):
        if self.start_edit.date() > self.end_edit.date():
            self.error_label.setText("The start date must be on or before the end date.")
        elif not self.selected_goals():
            self.error_label.setText("Select at least one goal.")
        else:
            self.accept()


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

class GoalTab(QWidget):
    def __init__(self, goal, on_change, on_remove):
        super().__init__()
        self.goal = goal
        self.on_change = on_change
        self.on_remove = on_remove
        self.accent = goal["color"]

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 16, 4, 4)
        root.setSpacing(12)

        # --- Progress card ---
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(6)

        self.pct_label = QLabel("0%")
        self.pct_label.setStyleSheet(f"font-size: 40px; font-weight: 700; color: {self.accent};")
        self.hours_label = QLabel("")
        self.hours_label.setStyleSheet("color: #8b8fa3; font-size: 14px;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        self.bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {self.accent}; border-radius: 6px; }}"
        )

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel(f"Target ({unit_name(goal)})"))
        self.target_edit = QLineEdit(f"{goal['target']:g}")
        self.target_edit.setFixedWidth(80)
        self.target_edit.returnPressed.connect(self.update_target)
        update_btn = QPushButton("Update")
        update_btn.clicked.connect(self.update_target)
        remove_btn = QPushButton("Remove goal")
        remove_btn.setObjectName("danger")
        remove_btn.clicked.connect(lambda checked=False: self.on_remove(self))
        target_row.addWidget(self.target_edit)
        target_row.addWidget(update_btn)
        target_row.addStretch()
        target_row.addWidget(remove_btn)

        card_layout.addWidget(self.pct_label)
        card_layout.addWidget(self.hours_label)
        card_layout.addSpacing(4)
        card_layout.addWidget(self.bar)
        card_layout.addSpacing(8)
        card_layout.addLayout(target_row)
        root.addWidget(card)

        # --- Log form ---
        form = QHBoxLayout()
        self.amount_edit = QLineEdit()
        self.amount_edit.setPlaceholderText("Minutes" if is_time(goal) else f"Amount ({goal['unit']})")
        self.amount_edit.setFixedWidth(130)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("What did you work on? (required)")
        log_btn = QPushButton("Log")
        log_btn.setStyleSheet(f"QPushButton {{ background: {self.accent}; color: white; }}"
                              f"QPushButton:hover {{ background: {self.accent}; }}")
        for w in (self.amount_edit, self.note_edit):
            w.returnPressed.connect(self.add_session)
        log_btn.clicked.connect(self.add_session)
        form.addWidget(self.amount_edit)
        form.addWidget(self.note_edit, 1)
        form.addWidget(log_btn)
        root.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ef4444;")
        root.addWidget(self.error_label)

        # --- History ---
        title = QLabel("History")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.history_layout = QVBoxLayout(inner)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(6)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        self.refresh()

    # --- Actions ---
    def add_session(self):
        amount = parse_positive(self.amount_edit.text())
        if amount is None:
            what = "minutes" if is_time(self.goal) else "amount"
            self.error_label.setText(f"Enter a positive number of {what}.")
            return
        activity = self.note_edit.text().strip()
        if not activity:
            self.error_label.setText("Describe what you worked on.")
            self.note_edit.setFocus()
            return
        self.error_label.setText("")
        key = "minutes" if is_time(self.goal) else "amount"
        self.goal["sessions"].append({
            "date": date.today().isoformat(),
            key: amount,
            "note": activity,
        })
        self.amount_edit.clear()
        self.note_edit.clear()
        self.on_change()
        self.refresh()

    def delete_session(self, index):
        del self.goal["sessions"][index]
        self.on_change()
        self.refresh()

    def update_target(self):
        value = parse_positive(self.target_edit.text())
        if value is None:
            self.error_label.setText("Target must be a positive number.")
            return
        self.error_label.setText("")
        self.goal["target"] = value
        self.on_change()
        self.refresh()

    def refresh(self):
        goal = self.goal
        total = total_display(goal, sum(qty(goal, s) for s in goal["sessions"]))
        target = goal["target"]
        frac = min(1.0, total / target) if target else 0
        self.bar.setValue(int(frac * 1000))
        self.pct_label.setText(f"{frac * 100:.0f}%")
        if is_time(goal):
            self.hours_label.setText(f"{total:.1f} of {target:g} hours")
        else:
            self.hours_label.setText(f"{total:g} of {target:g} {goal['unit']}")

        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sessions = goal["sessions"]
        if not sessions:
            empty = QLabel("No sessions yet. Log your first one above.")
            empty.setStyleSheet("color: #8b8fa3;")
            empty.setAlignment(Qt.AlignCenter)
            self.history_layout.addWidget(empty)
        else:
            for i in reversed(range(len(sessions))):
                self.history_layout.addWidget(self._make_row(i, sessions[i]))
        self.history_layout.addStretch()

    def _make_row(self, index, session):
        row = QFrame()
        row.setObjectName("row")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 8, 8)

        date_label = QLabel(session["date"])
        date_label.setStyleSheet("color: #8b8fa3;")
        date_label.setFixedWidth(90)
        if is_time(self.goal):
            amount_text = f"{session['minutes']:g} min"
        else:
            amount_text = f"{session['amount']:g} {self.goal['unit']}"
        amount_label = QLabel(amount_text)
        amount_label.setStyleSheet("font-weight: 700;")
        amount_label.setMinimumWidth(80)
        note_label = QLabel(session["note"] or "(no description)")
        note_label.setWordWrap(True)
        if not session["note"]:
            note_label.setStyleSheet("color: #8b8fa3; font-style: italic;")

        del_btn = QPushButton("\u2715")
        del_btn.setObjectName("del")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(lambda checked=False, idx=index: self.delete_session(idx))

        layout.addWidget(date_label)
        layout.addWidget(amount_label)
        layout.addWidget(note_label, 1)
        layout.addWidget(del_btn)
        return row


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Goal Tracker")
        self.resize(640, 760)
        self.data = load_data()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 16)

        header = QHBoxLayout()
        title = QLabel("Goal Tracker")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.export_btn = QPushButton("Export PDF")
        self.export_btn.clicked.connect(self.export_pdf)
        add_btn = QPushButton("+ New goal")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self.add_goal)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.export_btn)
        header.addWidget(add_btn)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setMovable(True)
        self.tabs.tabBar().tabMoved.connect(self.on_tab_moved)
        self.tabs.tabBar().tabBarDoubleClicked.connect(self.rename_goal)
        layout.addWidget(self.tabs, 1)

        self.empty_label = QLabel("No goals yet.\nClick \u201c+ New goal\u201d to start tracking one.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #8b8fa3; font-size: 14px;")
        layout.addWidget(self.empty_label, 1)

        self.setCentralWidget(container)

        for goal in self.data["goals"]:
            self._add_tab(goal)
        self.update_empty_state()

    # --- Tab management ---
    def _add_tab(self, goal):
        tab = GoalTab(goal, self.save, self.remove_goal)
        self.tabs.addTab(tab, tab_title(goal["name"]))
        return tab

    def update_empty_state(self):
        has_goals = bool(self.data["goals"])
        self.tabs.setVisible(has_goals)
        self.empty_label.setVisible(not has_goals)
        self.export_btn.setEnabled(has_goals)

    def add_goal(self):
        dialog = NewGoalDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        goal = new_goal(dialog.name(), dialog.kind(), dialog.unit(), dialog.target(),
                        pick_color(self.data["goals"]))
        self.data["goals"].append(goal)
        self.tabs.setCurrentWidget(self._add_tab(goal))
        self.update_empty_state()
        self.save()

    def remove_goal(self, tab):
        goal = tab.goal
        count = len(goal["sessions"])
        detail = (f"This permanently deletes its {count} logged session(s). "
                  "Export a PDF first if you need a record." if count
                  else "It has no logged sessions.")
        answer = QMessageBox.question(
            self, "Remove goal", f"Remove \u201c{goal['name']}\u201d?\n\n{detail}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.tabs.removeTab(self.tabs.indexOf(tab))
        tab.deleteLater()
        self.data["goals"] = [g for g in self.data["goals"] if g is not goal]
        self.update_empty_state()
        self.save()

    def rename_goal(self, index):
        if index < 0:
            return
        goal = self.tabs.widget(index).goal
        name, ok = QInputDialog.getText(self, "Rename goal", "Goal name:", text=goal["name"])
        name = name.strip()
        if not ok or not name:
            return
        goal["name"] = name
        self.tabs.setTabText(index, tab_title(name))
        self.save()

    def on_tab_moved(self, from_index, to_index):
        goals = self.data["goals"]
        goals.insert(to_index, goals.pop(from_index))
        self.save()

    # --- PDF export ---
    def export_pdf(self):
        dialog = ExportDialog(self, self.data["goals"], self.data["settings"].get("prepared_by", ""))
        if dialog.exec() != QDialog.Accepted:
            return
        prepared_by = dialog.name_edit.text().strip()
        self.data["settings"]["prepared_by"] = prepared_by
        self.save()

        default_path = os.path.join(os.path.expanduser("~"), f"goal_report_{date.today().isoformat()}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF report", default_path, "PDF files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        try:
            report = build_report_html(dialog.selected_goals(), prepared_by,
                                       dialog.start_date(), dialog.end_date())
            write_pdf(path, report)
        except Exception as e:  # report problems shouldn't crash the app
            QMessageBox.critical(self, "Export failed", f"Could not create the report:\n{e}")
            return

        answer = QMessageBox.question(
            self, "Report saved", f"Saved to:\n{path}\n\nOpen it now?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def save(self):
        try:
            save_data(self.data)
        except OSError as e:
            QMessageBox.warning(self, "Save error", f"Could not save data:\n{e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())