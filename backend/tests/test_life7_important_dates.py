from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

import pytest

from app import db, important_dates


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def clean_dates():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM important_date_events")
        conn.execute("DELETE FROM important_date_sources")
        conn.execute("DELETE FROM important_dates")
        conn.commit()
    finally:
        conn.close()


def _candidate(**overrides):
    values = dict(
        label="纪念日", recurrence="yearly_solar", date_year=None, date_month=7, date_day=26,
        timezone_id="Asia/Shanghai", confidence=0.9, source_kind="user_statement",
        source_id="message-1", source_revision="1", source_hash=_hash("message-1"),
        celebration_policy="natural",
    )
    values.update(overrides)
    return important_dates.create_candidate(**values)


def test_schema_69_adds_sourced_solar_date_tables():
    conn = db.connect()
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        recurrence_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='important_dates'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert version == "75"
    assert "yearly_solar" in recurrence_sql and "lunar" not in recurrence_sql


def test_ambiguous_date_remains_candidate_and_cannot_proactively_trigger():
    item = _candidate(date_month=None, date_day=None, confidence=0.4)
    assert item["status"] == "candidate"
    assert important_dates.next_occurrence(item, today=date(2026, 7, 26)) is None
    assert important_dates.proactive_allowed(item, today=date(2026, 7, 26)) is False


def test_confirmation_activates_and_program_computes_next_occurrence():
    item = _candidate(date_month=None, date_day=None)
    active = important_dates.confirm(
        item["id"], expected_revision=1, date_year=None, date_month=7, date_day=26,
    )
    assert active["status"] == "active" and active["revision"] == 2
    assert important_dates.next_occurrence(active, today=date(2026, 7, 25)) == date(2026, 7, 26)
    assert important_dates.next_occurrence(active, today=date(2026, 7, 27)) == date(2027, 7, 26)


def test_leap_day_skips_non_leap_years_and_crosses_year_correctly():
    item = _candidate(date_month=2, date_day=29)
    active = important_dates.confirm(item["id"], expected_revision=1, date_year=None, date_month=2, date_day=29)
    assert important_dates.next_occurrence(active, today=date(2025, 3, 1)) == date(2028, 2, 29)


def test_preparation_day_followup_and_boundary_policy():
    item = _candidate()
    active = important_dates.confirm(item["id"], expected_revision=1, date_year=None, date_month=7, date_day=26)
    assert important_dates.phase(active, today=date(2026, 7, 20)) == "preparation"
    assert important_dates.phase(active, today=date(2026, 7, 26)) == "day"
    assert important_dates.phase(active, today=date(2026, 7, 28)) == "follow_up"
    blocked = _candidate(source_id="message-2", celebration_policy="none")
    blocked = important_dates.confirm(blocked["id"], expected_revision=1, date_year=None, date_month=7, date_day=26)
    assert important_dates.proactive_allowed(blocked, today=date(2026, 7, 26)) is False


def test_source_deletion_revokes_without_manual_source_and_preserves_manual_item():
    item = _candidate()
    important_dates.confirm(item["id"], expected_revision=1, date_year=None, date_month=7, date_day=26)
    assert important_dates.remove_source(source_kind="user_statement", source_id="message-1") == 1
    assert important_dates.get(item["id"])["status"] == "revoked"
    manual = _candidate(source_kind="manual", source_id="manual-1")
    important_dates.confirm(manual["id"], expected_revision=1, date_year=None, date_month=7, date_day=26)
    assert important_dates.remove_source(source_kind="user_statement", source_id="missing") == 0
    assert important_dates.get(manual["id"])["status"] == "active"


def test_crossings_emit_only_confirmed_dates_in_interval_with_revision():
    item = _candidate()
    active = important_dates.confirm(item["id"], expected_revision=1, date_year=None, date_month=7, date_day=26)
    start = datetime(2026, 7, 25, tzinfo=timezone.utc).timestamp()
    end = datetime(2026, 7, 27, tzinfo=timezone.utc).timestamp()
    crossings = important_dates.crossings(interval_start=start, interval_end=end)
    assert len(crossings) == 1
    assert crossings[0].id == active["id"] and crossings[0].revision == "2"
