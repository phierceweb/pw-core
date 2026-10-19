"""TLS-verification policy for outbound HTTP.

Shared by every outbound path — the ``[http]`` inspection helpers (``urls``,
``url_liveness``) and the base-install ``pw_core.fetch.Fetcher``. Disabling
verification removes MITM protection, and since fetched bodies flow to
downstream LLMs and to disk, a MITM could inject content — so the opt-out
exists only for deliberately probing hosts with known-broken certs.
"""

from __future__ import annotations

from pw_core._env_compat import env_name
from pw_foundation.utils.env import resolve_bool

_VERIFY_TLS_ENV_VAR = "PW_VERIFY_TLS"
_LEGACY_ENV_VAR = "URL_CHECK_VERIFY_TLS"


def verify_tls(override: bool | None = None) -> bool:
    """Resolve TLS verification: *override* > ``PW_VERIFY_TLS`` > ``URL_CHECK_VERIFY_TLS`` > True.

    The legacy name is honored because it governs the base-install fetch path
    too, so dropping it would silently re-enable verification for an operator
    deliberately probing a broken-cert host.
    """
    legacy = resolve_bool(None, _LEGACY_ENV_VAR, default=True)
    return resolve_bool(override, env_name(_VERIFY_TLS_ENV_VAR), default=legacy)
