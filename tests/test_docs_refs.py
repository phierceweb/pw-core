"""Every relative .md link in the shipped docs points at a doc that ships."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "src" / "pw_core" / "docs"
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")


def test_every_relative_doc_link_resolves() -> None:
    dead = []
    for path in sorted(DOCS.rglob("*.md")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in LINK.finditer(line):
                target = m.group(1)
                if target.startswith(("http://", "https://")):
                    continue
                if not (path.parent / target).resolve().is_file():
                    dead.append(f"{path.relative_to(ROOT)}:{lineno}: {target}")
    assert dead == [], "\n".join(dead)
