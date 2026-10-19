"""pw-core: the application framework on top of pw-foundation.

The base install (``pip install pw-core``) brings pw-foundation — structured logging, the
exception hierarchy, the config base and env resolvers, pure utils, ``Service``, the
``pw-guards`` gate — and adds pw-core's own dependency-light modules: the ``AppConfig``
subclass with the framework settings, json recovery, similarity, vocab, parsers, budget.
Consumers import the foundation as ``pw_foundation.*``; nothing is re-exported here.

Everything else ships as opt-in, orthogonally-composable extras: anti-slop output guards
(``[validate]``), LLM clients (``[llm]`` ⊇ ``[validate]``), HTTP utils (``[http]``), CLI
scaffolding (``[cli]``), and the FastAPI + SQLAlchemy app framework (``[db]``, ``[web]``,
``[jobs]``, ``[tracking]``, ``[eval]``, ``[admin]``). See ``docs/INSTALLATION.md``.
"""


def __getattr__(name: str) -> str:
    """PEP 562: ``__version__`` reads package metadata on first access, not at import."""
    if name != "__version__":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib.metadata import PackageNotFoundError, version

    try:
        value = version("pw-core")
    except PackageNotFoundError:  # running from a source tree with no install
        value = "0.0.0+unknown"
    globals()["__version__"] = value
    return value
