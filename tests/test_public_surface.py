"""The stable surface is what the docs show: every public module sits under a documented path,
and every name a module exports (its ``__all__``, else its ``_LAZY`` re-exports, else its public
top-level defs and constants) is named on an indexed page that also shows that module."""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "pw_core"
DOCS = SRC / "docs"
# Shipped but outside the stable API: a plug-in pw-foundation loads by name, and the
# admin dashboard's routes and data layer.
INTERNAL = {
    "pw_core.guards_rules",
    "pw_core.web.llm_admin.api",
    "pw_core.web.llm_admin.pages",
    "pw_core.web.llm_admin.queries",
}


def _public_modules() -> dict[str, Path]:
    out = {}
    for p in sorted(SRC.rglob("*.py")):
        parts = list(p.relative_to(SRC.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if any(x.startswith("_") for x in parts[1:]) or "templates" in parts[:-1]:
            continue
        out[".".join(parts)] = p
    return out


def _named(text: str, name: str, *, whole_path: bool = False) -> bool:
    # whole_path: ``pw_core.x`` inside ``pw_core.x.y`` does not name ``pw_core.x``.
    after = r"(?!\.?\w)" if whole_path else r"(?![\w])"
    return re.search(rf"(?<![\w]){re.escape(name)}{after}", text) is not None


def _indexed_pages() -> list[str]:
    index = (DOCS / "modules.md").read_text(encoding="utf-8")
    pages = set(re.findall(r"\]\(([a-z0-9/_-]+\.md)\)", index))
    # Link targets and URLs carry words (``blob/main``) that are not mentions.
    strip = re.compile(r"\]\([^)]*\)|https?://\S+")
    return [
        strip.sub("]", (DOCS / f).read_text(encoding="utf-8"))
        for f in sorted(pages)
        if (DOCS / f).is_file()
    ]


def _reaches(text: str, mod: str) -> bool:
    parts = mod.split(".")
    ancestors = [".".join(parts[:i]) for i in range(len(parts) - 1, 1, -1)]
    return _named(text, mod) or any(_named(text, a, whole_path=True) for a in ancestors)


def _exported(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    declared = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        else:
            target = getattr(node, "target", None)
        if getattr(target, "id", None) != "__all__" or getattr(node, "value", None) is None:
            continue
        declared = True
        names += [
            e.value
            for e in ast.walk(node.value)
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    if declared:
        return names
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        value = getattr(node, "value", None)
        if getattr(target, "id", None) == "_LAZY" and isinstance(value, ast.Dict):
            return [k.value for k in value.keys if isinstance(k, ast.Constant)]
    defs = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not n.name.startswith("_")
    ]
    constants = [
        t.id
        for n in tree.body
        if isinstance(n, (ast.Assign, ast.AnnAssign))
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        if isinstance(t, ast.Name)
        and t.id.isupper()
        and not t.id.startswith("_")
        and not (isinstance(n.value, ast.Call) and getattr(n.value.func, "id", None) == "TypeVar")
    ]
    return defs + constants


def test_every_public_module_sits_under_a_documented_path() -> None:
    text = "\n".join(_indexed_pages())
    orphans = [mod for mod in _public_modules() if mod not in INTERNAL and not _reaches(text, mod)]
    assert not orphans, (
        "public modules no indexed doc reaches (document them, or prefix with _):\n"
        + "\n".join(orphans)
    )


def test_every_exported_name_is_documented() -> None:
    pages = _indexed_pages()
    missing = [
        f"{mod}.{name}"
        for mod, path in _public_modules().items()
        if mod not in INTERNAL
        for name in _exported(path)
        if not name.startswith("pytest_")
        and not any(_reaches(page, mod) and _named(page, name) for page in pages)
    ]
    assert not missing, (
        "exported names not on an indexed page that shows their module:\n" + "\n".join(missing)
    )
