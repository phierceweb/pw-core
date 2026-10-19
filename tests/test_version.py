"""Guard against ``pw_core.__version__`` drifting from the package metadata.

``__version__`` derives from the installed distribution metadata — the single
source of truth. Fails if a hand-maintained literal is reintroduced.
"""

from __future__ import annotations

from importlib.metadata import version

import pw_core


def test_version_matches_installed_distribution_metadata():
    assert pw_core.__version__ == version("pw-core")


def test_version_is_not_the_unknown_fallback():
    # In the test environment pw-core is always installed, so the
    # PackageNotFoundError fallback must not be what we resolved to.
    assert pw_core.__version__ != "0.0.0+unknown"
