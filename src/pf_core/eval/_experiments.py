"""Paired baseline-vs-candidate comparison over scored replay runs.

Backs :meth:`pf_core.eval.EvalRunner.compare_experiments`. See
``docs/eval-harness.md``.
"""

from __future__ import annotations

from pf_core.db.repository import Repository
from pf_core.exceptions import InvalidInputError, PreconditionError


def _exact_tag_rows(rows: list, tag: str) -> list:
    """Drop rows whose stored tag is not *tag* byte-for-byte.

    MySQL's default collation makes SQL ``=`` case-insensitive; tag matching is
    exact on every dialect.
    """
    return [r for r in rows if r["tag"] == tag]


def _fetch_tag_rows(repo: Repository, tag: str, agent_type: str) -> list:
    """Every replay row the server matches for *tag*, with its eval_score or None.

    Ordered by child run id ascending, so a later replay of a golden overwrites
    an earlier one. Callers must filter through :func:`_exact_tag_rows`.
    """
    from sqlalchemy import and_, select

    import pf_core.llm.tracking.schema as s

    stmt = (
        select(
            s.llm_run_links.c.parent_run_id.label("golden_id"),
            s.llm_run_outcomes.c.score,
            s.llm_run_tags.c.tag,
        )
        .join(
            s.llm_run_tags,
            s.llm_run_tags.c.llm_run_id == s.llm_run_links.c.child_run_id,
        )
        .outerjoin(
            s.llm_run_outcomes,
            and_(
                s.llm_run_outcomes.c.llm_run_id == s.llm_run_links.c.child_run_id,
                s.llm_run_outcomes.c.outcome_kind == "eval_score",
            ),
        )
        .join(
            s.llm_runs,
            s.llm_runs.c.id == s.llm_run_links.c.child_run_id,
        )
        .join(
            s.llm_agent_types,
            s.llm_agent_types.c.id == s.llm_runs.c.agent_type_id,
        )
        .where(s.llm_run_tags.c.tag == tag)
        .where(s.llm_run_links.c.relation == "replay")
        .where(s.llm_agent_types.c.slug == agent_type)
        .order_by(s.llm_run_links.c.child_run_id)
    )
    with repo._tx() as conn:
        return list(conn.execute(stmt).mappings().fetchall())


def _tag_replays(
    repo: Repository, tag: str, agent_type: str
) -> tuple[dict[int, float], set[int], int]:
    """(golden_id -> score, goldens replayed, scored row count) for one tag.

    A golden's latest replay decides it: one that errored, or whose score is NULL,
    leaves the golden unscored even when an earlier replay under the tag scored.
    """
    rows = _exact_tag_rows(_fetch_tag_rows(repo, tag, agent_type), tag)
    latest = {int(r["golden_id"]): r["score"] for r in rows}
    scores = {gid: float(score) for gid, score in latest.items() if score is not None}
    return scores, set(latest), sum(1 for r in rows if r["score"] is not None)


def compare_experiments(*, baseline: str, candidate: str, agent_type: str) -> dict:
    """Pair baseline and candidate replay runs by shared golden parent.

    Args:
        baseline: Experiment tag for the baseline runs.
        candidate: Experiment tag for the candidate runs.
        agent_type: Slug to filter both sets by.

    Raises:
        InvalidInputError: If both tags are the same string.
        PreconditionError: If the baseline has no scored replay runs, the
            candidate has no replay runs at all, or the two share no golden
            parent.

    Returns:
        ``{"pairs", "baseline_runs", "candidate_runs", "baseline_only",
        "candidate_only"}`` — ``pairs`` holds ``golden_id``, ``baseline_score``,
        ``candidate_score`` and ``delta`` per shared golden, sorted by
        ``golden_id``; the counts are matched scored-replay rows per tag; the
        ``*_only`` lists are golden ids scored under that tag alone. A golden's
        latest replay under a tag decides whether it is scored there. Shape:
        ``docs/eval-harness.md``.
    """
    if baseline == candidate:
        raise InvalidInputError(
            f"baseline and candidate are the same tag ({baseline!r}); "
            "a self-comparison reports every delta as 0.0"
        )

    repo = Repository()
    base_scores, _, base_rows = _tag_replays(repo, baseline, agent_type)
    cand_scores, cand_replayed, cand_rows = _tag_replays(repo, candidate, agent_type)

    empty_tags = [
        tag for tag, found in ((baseline, base_scores), (candidate, cand_replayed)) if not found
    ]
    if empty_tags:
        names = ", ".join(repr(t) for t in empty_tags)
        raise PreconditionError(
            f"No scored replay runs found for tag(s) {names} with "
            f"agent_type={agent_type!r}. Check the tag spelling and that the "
            "replays recorded eval_score outcomes."
        )

    shared_golden_ids = sorted(set(base_scores) & set(cand_scores))
    if not set(base_scores) & cand_replayed:
        raise PreconditionError(
            f"Tags {baseline!r} ({base_rows} scored runs) and {candidate!r} "
            f"({cand_rows} scored runs) share no golden parent, so there is "
            "nothing to compare. The two experiments replayed different "
            "golden sets."
        )

    return {
        "pairs": [
            {
                "golden_id": gid,
                "baseline_score": base_scores[gid],
                "candidate_score": cand_scores[gid],
                "delta": cand_scores[gid] - base_scores[gid],
            }
            for gid in shared_golden_ids
        ],
        "baseline_runs": base_rows,
        "candidate_runs": cand_rows,
        "baseline_only": sorted(set(base_scores) - set(cand_scores)),
        "candidate_only": sorted(set(cand_scores) - set(base_scores)),
    }
