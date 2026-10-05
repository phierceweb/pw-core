"""Keyset-paged reads of ``llm_runs`` for :mod:`pf_core.llm.tracking.otel`."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Select, select

from pf_core.db.repository import Repository
from pf_core.llm.tracking import schema as s

Page = list[tuple[dict[str, Any], frozenset[str], dict[str, Any] | None]]


class RunReader(Repository):
    """Keyset-paged reads of runs with their model, agent type, tags and payloads."""

    def pages(
        self,
        *,
        size: int,
        since: dt.datetime | None,
        until: dt.datetime | None,
        agent_type: str | None,
        job_id: int | None,
        bodies_for: frozenset[str],
    ) -> Iterator[Page]:
        after = 0
        while True:
            stmt = _runs_query(since=since, until=until, agent_type=agent_type, job_id=job_id)
            with self._tx() as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        stmt.where(s.llm_runs.c.id > after).order_by(s.llm_runs.c.id).limit(size)
                    ).mappings()
                ]
                if not rows:
                    return
                ids = [r["id"] for r in rows]
                tags: dict[int, set[str]] = {}
                for run_id, tag in conn.execute(
                    select(s.llm_run_tags.c.llm_run_id, s.llm_run_tags.c.tag).where(
                        s.llm_run_tags.c.llm_run_id.in_(ids)
                    )
                ):
                    tags.setdefault(run_id, set()).add(tag.casefold())
                wanted = [r["id"] for r in rows if r["agent_type"] in bodies_for]
                payloads = {
                    p["llm_run_id"]: dict(p)
                    for p in conn.execute(
                        select(s.llm_run_payloads).where(
                            s.llm_run_payloads.c.llm_run_id.in_(wanted)
                        )
                    ).mappings()
                }
            yield [(r, frozenset(tags.get(r["id"], ())), payloads.get(r["id"])) for r in rows]
            if len(rows) < size:
                return
            after = rows[-1]["id"]


def _runs_query(
    *,
    since: dt.datetime | None,
    until: dt.datetime | None,
    agent_type: str | None,
    job_id: int | None,
) -> Select[Any]:
    runs = s.llm_runs
    stmt = select(
        runs,
        s.llm_models.c.name.label("model"),
        s.llm_agent_types.c.slug.label("agent_type"),
    ).select_from(
        runs.join(s.llm_models, s.llm_models.c.id == runs.c.model_id).join(
            s.llm_agent_types, s.llm_agent_types.c.id == runs.c.agent_type_id
        )
    )
    if since is not None:
        stmt = stmt.where(runs.c.created_at >= since)
    if until is not None:
        stmt = stmt.where(runs.c.created_at < until)
    if agent_type is not None:
        stmt = stmt.where(s.llm_agent_types.c.slug == agent_type)
    if job_id is not None:
        stmt = stmt.where(runs.c.job_id == job_id)
    return stmt
