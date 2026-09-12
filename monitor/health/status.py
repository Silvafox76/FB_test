"""What `monitor status` reports.

The morning question is "did the pipeline see anything, and did it throw the right
things away". This answers it from the database alone: source health, the filter's
own arithmetic per source, and what is waiting.

Read-only by construction. Nothing here writes, so it is safe to run against a
live pipeline and it is what the ops-analyst agent reads.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class SourceStatus:
    source_id: str
    enabled: bool
    state: str
    last_success_at: object
    consecutive_failures: int
    notices: int
    filtered_in: int
    dropped: int
    needs_translation: int
    unfiltered: int

    @property
    def considered(self) -> int:
        """Notices the filter has decided on. The denominator of the drop rate."""
        return self.filtered_in + self.dropped

    @property
    def drop_rate(self) -> float:
        return self.dropped / self.considered if self.considered else 0.0


def collect(conn: psycopg.Connection) -> list[SourceStatus]:
    rows = conn.execute(
        """
        select s.id,
               s.enabled,
               coalesce(h.state, 'unknown'),
               h.last_success_at,
               coalesce(h.consecutive_failures, 0),
               count(n.id),
               count(n.id) filter (where n.status = 'filtered_in'),
               count(n.id) filter (where n.status = 'filtered_out'),
               count(n.id) filter (where n.filter_result = 'needs translation'),
               count(n.id) filter (where n.status = 'detected'
                                     and coalesce(n.filter_result, '') = '')
        from sources s
        left join source_health h on h.source_id = s.id
        left join notices n on n.source_id = s.id
        group by s.id, s.enabled, h.state, h.last_success_at, h.consecutive_failures
        order by s.id
        """
    ).fetchall()
    return [SourceStatus(*row) for row in rows]


def render(statuses: list[SourceStatus]) -> str:
    """One line per source, then the totals. Plain text; this is read in a terminal."""
    lines = [
        f"{'source':<12} {'state':<9} {'notices':>7} {'passed':>7} {'dropped':>8} "
        f"{'drop rate':>10} {'needs tr.':>10} {'unfiltered':>11}"
    ]
    for status in statuses:
        flag = "" if status.enabled else "  (not enabled)"
        rate = f"{status.drop_rate:.0%}" if status.considered else "-"
        lines.append(
            f"{status.source_id:<12} {status.state:<9} {status.notices:>7} {status.filtered_in:>7} "
            f"{status.dropped:>8} {rate:>10} {status.needs_translation:>10} {status.unfiltered:>11}{flag}"
        )

    considered = sum(s.considered for s in statuses)
    dropped = sum(s.dropped for s in statuses)
    passed = sum(s.filtered_in for s in statuses)
    overall = f"{dropped / considered:.1%}" if considered else "-"
    lines.append("")
    lines.append(f"considered {considered}, passed {passed}, dropped {dropped}, drop rate {overall}")
    return "\n".join(lines)


# The same "newest day that has happened" rule monitor/fx/store.py uses, so this
# line and the stager agree on which day is current.
NEWEST_RATE = """
select rate_date, count(*) from fx_rates
where source = %s and rate_date <= %s
group by rate_date
order by rate_date desc
limit 1
"""


def render_rates(conn: psycopg.Connection) -> str:
    """One line: the newest rate day held, how old it is, and whether staging will accept it.

    Staging refuses a rate table older than `max_rate_age_days` (monitor/fx/store.py),
    so a person reading this at 09:30 wants to know before `make run` fires, not
    from its traceback. Reads as monitor_pipeline, which holds select on fx_rates.
    """
    from datetime import UTC, datetime

    from monitor.fx.config import load

    config = load()
    today = datetime.now(UTC).date()
    row = conn.execute(NEWEST_RATE, (config.publisher.id, today)).fetchone()
    if row is None:
        return f"rates: none held from {config.publisher.id}; staging will refuse until `make fx` runs"
    newest, currencies = row
    age = (today - newest).days
    verdict = "ok" if age <= config.max_rate_age_days else f"STALE, past the {config.max_rate_age_days}-day tolerance"
    return f"rates: {config.publisher.id} {newest.isoformat()}, {currencies} currencies, {age} days old, {verdict}"
