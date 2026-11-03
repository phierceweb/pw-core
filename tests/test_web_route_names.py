"""The admin routers name every route under their own prefix, so both mount in one app — beside a
consumer's own routes — without ``url_path_for`` resolving a shared name to the wrong router."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from pw_core.web.jobs_admin import make_jobs_router  # noqa: E402
from pw_core.web.llm_admin import make_admin_router  # noqa: E402

ROUTERS = {
    "llm_admin_": lambda: make_admin_router(auth_dep=lambda: None),
    "jobs_admin_": lambda: make_jobs_router(auth_dep=lambda: None),
}


@pytest.mark.parametrize("prefix", sorted(ROUTERS))
def test_every_route_name_carries_its_router_prefix(prefix):
    names = [r.name for r in ROUTERS[prefix]().routes if isinstance(r, APIRoute)]
    assert names
    assert [n for n in names if not n.startswith(prefix)] == []


def test_both_routers_resolve_their_own_paths_in_one_app():
    app = FastAPI()
    app.include_router(ROUTERS["llm_admin_"]())
    app.include_router(ROUTERS["jobs_admin_"]())

    assert app.url_path_for("llm_admin_jobs_list") == "/admin/llm/jobs"
    assert app.url_path_for("llm_admin_job_detail", job_id=7) == "/admin/llm/job/7"
    assert app.url_path_for("llm_admin_cancel_job", job_id=7) == "/admin/llm/api/job/7/cancel"
    assert app.url_path_for("jobs_admin_jobs_list") == "/jobs"
    assert app.url_path_for("jobs_admin_job_detail", job_id=7) == "/jobs/7"
    assert app.url_path_for("jobs_admin_job_bundle", job_id=7) == "/jobs/api/7"
    assert app.url_path_for("jobs_admin_cancel_job", job_id=7) == "/jobs/api/7/cancel"
