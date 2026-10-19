"""
Pytest plugin providing pw-core's base testing fixtures.

Auto-discovered by pytest via the ``pytest11`` entry point in pyproject.toml.
Consumer projects get these fixtures by installing pw-core.

This module has no DB dependencies — it's safe for consumers who don't
install the ``[db]`` extra. For DB fixtures, opt in via::

    # In your conftest.py:
    pytest_plugins = ["pw_core.testing.db_fixtures"]

Fixtures
--------
pw_app_client
    httpx ``AsyncClient`` bound to the consumer project's FastAPI app.
    Requires the consumer project to define a ``pw_app`` fixture returning
    their ``FastAPI`` instance.

pw_budget_disabled
    Neutralizes the cost-budget guard for a test (env kill switch).
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def pw_budget_disabled(monkeypatch):
    """Neutralize the cost-budget guard pair for this test.

    Sets ``BUDGET_ENFORCEMENT_DISABLED=1`` so ``check_budget()`` no-ops and
    ``project_cost()`` returns ``0.0`` without touching the DB. The switch
    is read per call, so services' ``from pw_core.budget import …`` bindings
    honor it too. To silence the guard suite-wide, autouse-wrap it in your
    conftest::

        @pytest.fixture(autouse=True)
        def _no_budget(pw_budget_disabled):
            yield
    """
    monkeypatch.setenv("BUDGET_ENFORCEMENT_DISABLED", "1")


@pytest.fixture()
async def pw_app_client(request):
    """httpx AsyncClient bound to the consumer's FastAPI app.

    Requires consumer conftest.py to define::

        @pytest.fixture
        def pw_app():
            from app.api import app
            return app

    Then in tests::

        async def test_api(pw_app_client):
            resp = await pw_app_client.get("/api/health")
            assert resp.status_code == 200
    """
    import httpx

    app = request.getfixturevalue("pw_app")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
