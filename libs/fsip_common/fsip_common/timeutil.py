from __future__ import annotations

from datetime import UTC, datetime


def parse_sim_time(value: str | datetime) -> datetime:
    """Simulator sends naive ISO strings ("2026-01-01T00:15:00"); the PDF shows "+00:00". Accept both -> aware UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def ticks_per_day(tick_minutes: int) -> int:
    return (24 * 60) // tick_minutes


def ticks_to_hours(ticks: float, tick_minutes: int) -> float:
    return ticks * tick_minutes / 60.0


def hours_to_ticks(hours: float, tick_minutes: int) -> int:
    return int(round(hours * 60.0 / tick_minutes))


def expected_arrival_tick(created_tick: int, transit_ticks: int) -> int:
    """Calculate the expected arrival tick for an allocation.

    Semantics (02_SIMULATOR_FACTS.md §3, verified in Task 1.9):
    - When an allocation is created at `created_tick` on a route with `transit_ticks`:
      - Departure occurs at `departure_tick = created_tick`.
      - Expected arrival tick is `created_tick + transit_ticks`.
      - Arrival occurs during the step processing tick `expected_arrival_tick`,
        and station inventory increases at instance tick `expected_arrival_tick + 1`.
    """
    if not isinstance(created_tick, int) or isinstance(created_tick, bool):
        raise TypeError(f"created_tick must be an int, got {type(created_tick).__name__}")
    if not isinstance(transit_ticks, int) or isinstance(transit_ticks, bool):
        raise TypeError(f"transit_ticks must be an int, got {type(transit_ticks).__name__}")
    if created_tick < 0:
        raise ValueError(f"created_tick must be >= 0, got {created_tick}")
    if transit_ticks < 0:
        raise ValueError(f"transit_ticks must be >= 0, got {transit_ticks}")
    return created_tick + transit_ticks




def format_sim_clock(tick: int, tick_minutes: int) -> str:
    """'Day 2 11:00 · t=140' style label used across the UI and logs."""
    minutes = tick * tick_minutes
    day, rem = divmod(minutes, 24 * 60)
    return f"Day {day + 1} {rem // 60:02d}:{rem % 60:02d} · t={tick}"
