"""The demo's made-up world: one clinic, one day of appointments, three people in line.

All of it is invented for the demo. Dates are set relative to today so that
"tomorrow" in the cancellation message means something.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .line_sitter import CalendarEvent, Person

CLINIC = "Corner Clinic"
TZ = ZoneInfo("America/Los_Angeles")

PRESETS = [
    {"id": "maria-3pm", "label": "Maria cancels 3:00 PM", "text": "hi this is Maria I can't make my 3pm tomorrow"},
    {"id": "tom-930", "label": "Tom cancels 9:30 AM", "text": "This is Tom. Something came up, please cancel my 9:30 tomorrow morning."},
]


def today() -> date:
    return datetime.now(TZ).date()


def _at(day: date, hhmm: str) -> str:
    return f"{day.isoformat()}T{hhmm}"


def schedule(day0: date) -> list[dict]:
    d = day0 + timedelta(days=1)
    rows = [("09:30", "Tom B."), ("11:00", "Priya S."), ("13:30", "Luis G."), ("15:00", "Maria R."), ("16:00", "Wen L.")]
    return [
        {"slot_id": f"{d.isoformat()}-{hhmm.replace(':', '')}", "start": _at(d, hhmm), "minutes": 30,
         "with": "Dr. Okafor", "patient": who, "status": "booked"}
        for hhmm, who in rows
    ]


def people(day0: date) -> list[Person]:
    d = day0 + timedelta(days=1)
    return [
        Person(
            id="dev", name="Dev", visit_reason="knee follow-up",
            constraints=["Never move a meeting I am presenting at.", "No mornings before 10 unless it is urgent."],
            calendar=[CalendarEvent("Quarterly review (presenting)", _at(d, "14:30"), _at(d, "16:00"))],
            current_appointment=_at(day0 + timedelta(days=12), "10:30"),
        ),
        Person(
            id="ana", name="Ana", visit_reason="annual checkup",
            constraints=["No mornings before 10 unless it is urgent.", "I need 40 minutes to get there."],
            calendar=[CalendarEvent("Team lunch", _at(d, "12:30"), _at(d, "13:30")),
                      CalendarEvent("School pickup", _at(d, "17:00"), _at(d, "17:30"))],
            current_appointment=_at(day0 + timedelta(days=9), "14:00"),
        ),
        Person(
            id="sam", name="Sam", visit_reason="blood pressure check",
            constraints=["Any time of day works.", "I need 20 minutes to get there."],
            calendar=[CalendarEvent("Gym", _at(d, "07:00"), _at(d, "08:00"))],
            current_appointment=_at(day0 + timedelta(days=15), "09:00"),
        ),
    ]
