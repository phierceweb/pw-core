"""The three rules pw-core ships for pw-setup are the repo's own, and never shadow pw-foundation's."""

from __future__ import annotations

from pathlib import Path

from pw_foundation.wiring import installed_rules_dir

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "src" / "pw_core" / "rules"


def test_shipped_rules_match_the_repo_rules():
    names = sorted(p.name for p in SHIPPED.glob("*.md"))
    assert names == ["anti-patterns.md", "layering.md", "project-structure.md"]
    for name in names:
        assert (SHIPPED / name).read_text() == (ROOT / ".ai" / "rules" / name).read_text(), name


def test_shipped_rule_names_do_not_collide_with_pw_foundation():
    foundation = {p.name for p in installed_rules_dir().glob("*.md")}
    assert not foundation & {p.name for p in SHIPPED.glob("*.md")}


def test_pw_setup_sees_them():
    assert installed_rules_dir("pw_core") == SHIPPED.resolve()


def test_shipped_rules_carry_cursor_frontmatter_like_pw_foundations():
    for p in SHIPPED.glob("*.md"):
        head = p.read_text().split("\n", 4)[:4]
        assert head[0] == "---" and head[1].startswith("description: "), p.name
        assert head[2] == "alwaysApply: true" and head[3] == "---", p.name


def test_shipped_rules_cite_docs_by_their_consumer_paths():
    """In a consumer the docs live under docs/pw-core/ and docs/pw-foundation/, never docs/x.md."""
    import re

    bad = []
    for p in SHIPPED.glob("*.md"):
        for m in re.finditer(r"docs/([\w./-]+\.md)", p.read_text()):
            ref = m.group(1)
            if not ref.startswith(("pw-core/", "pw-foundation/")):
                bad.append(f"{p.name}: docs/{ref}")
            elif (
                ref.startswith("pw-core/")
                and not (ROOT / "src" / "pw_core" / "docs" / ref.removeprefix("pw-core/")).is_file()
            ):
                bad.append(f"{p.name}: docs/{ref} does not ship")
    assert bad == []
