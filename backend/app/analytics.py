"""Process-level analytics helpers."""

from __future__ import annotations

import sqlite3
from typing import Optional

_DEPLOYED_STATUSES = ("deployed", "linked")


def _duration_to_hours(
    duration_value: Optional[int], duration_unit: Optional[str]
) -> float:
    if duration_value is None:
        return 0.0
    unit = duration_unit or "minutes"
    if unit == "hours":
        return float(duration_value)
    if unit == "days":
        return float(duration_value) * 8.0
    return float(duration_value) / 60.0


def calculate_leverage_multiplier(process_id: str, conn: sqlite3.Connection) -> float:
    """Human Leverage Multiplier for a process.

    Sums labor-hours for nodes in deployed/linked agentic groups, then applies
    ``1.0 + (total_hours / 10) * 1.5``, rounded to one decimal place.
    """
    rows = conn.execute(
        'SELECT m.duration_value, m.duration_unit '
        'FROM node n '
        'JOIN "group" g ON n.group_id = g.id '
        'LEFT JOIN metadata m ON m.owner_type = ? AND m.owner_id = n.id '
        'WHERE n.process_id = ? AND g.deployment_status IN (?, ?)',
        ("node", process_id, *_DEPLOYED_STATUSES),
    ).fetchall()

    total_hours = sum(
        _duration_to_hours(row["duration_value"], row["duration_unit"]) for row in rows
    )
    multiplier = 1.0 + (total_hours / 10.0) * 1.5
    return round(multiplier, 1)
