"""pw-core reaches pw-foundation only through its public names."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "pw_core"


def _private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _private_foundation_uses(path: Path) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "pw_foundation" and (
                any(_private(p) for p in parts[1:]) or any(_private(a.name) for a in node.names)
            ):
                hits.append(f"{path.name}:{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "pw_foundation" and any(_private(p) for p in parts[1:]):
                    hits.append(f"{path.name}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.Attribute):
            chain, base = [], node
            while isinstance(base, ast.Attribute):
                chain.append(base.attr)
                base = base.value
            if (
                isinstance(base, ast.Name)
                and base.id == "pw_foundation"
                and any(map(_private, chain))
            ):
                hits.append(f"{path.name}:{node.lineno}: pw_foundation.{'.'.join(reversed(chain))}")
    return hits


def test_no_private_pw_foundation_names_are_used():
    hits = [h for p in sorted(SRC.rglob("*.py")) for h in _private_foundation_uses(p)]
    assert hits == []
