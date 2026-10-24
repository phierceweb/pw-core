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


RULE_SOURCES = [
    *sorted((ROOT / ".ai" / "rules").glob("*.md")),
    *sorted((ROOT / "src" / "pw_core" / "rules").glob("*.md")),
    ROOT / ".env.example",
    ROOT / ".pre-commit-config.yaml",
]
DOC_TOKEN = re.compile(r"(?<!pw_foundation/)\bdocs/(?!pw-foundation/)(?:pw-core/)?([\w-]+\.md)\b")


def test_rules_and_config_cite_docs_that_ship() -> None:
    dead = []
    for path in RULE_SOURCES:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in DOC_TOKEN.finditer(line):
                if not (DOCS / m.group(1)).is_file():
                    dead.append(f"{path.relative_to(ROOT)}:{lineno}: docs/{m.group(1)}")
    assert dead == [], "\n".join(dead)
