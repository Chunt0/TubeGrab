"""The package map in tubegrab/__init__.py holds: a module imports (at the top)
only from modules listed above it, so there are no import cycles."""
import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "tubegrab"


def layers() -> list[str]:
    doc = ast.get_docstring(ast.parse((PKG / "__init__.py").read_text()))
    return [m.group(1).rstrip("/") for m in re.finditer(r"^    (\w+/?)\s{2,}", doc, re.M)]


def module_of(path: Path) -> str:
    rel = path.relative_to(PKG).with_suffix("").parts
    return "api" if rel[0] == "api" else rel[0]


def test_every_module_is_on_the_map():
    on_map = set(layers())
    for p in PKG.rglob("*.py"):
        if p.name != "__init__.py":
            assert module_of(p) in on_map, f"{p.relative_to(PKG)} is missing from the map in tubegrab/__init__.py"


def test_top_level_imports_only_go_down():
    order = layers()
    for p in PKG.rglob("*.py"):
        if p.name == "__init__.py":
            continue
        me = module_of(p)
        for node in ast.parse(p.read_text()).body:   # top level only: imports inside functions are deferred
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            base = (node.module or "").split(".")[0]
            targets = [base] if base else [a.name for a in node.names]
            if node.level == 1 and me == "api" and base:
                targets = ["api"]                     # api/x.py importing a sibling
            for t in targets:
                if t == me:
                    continue
                assert order.index(t) < order.index(me), \
                    f"{p.relative_to(PKG)} imports {t}, which is not above it on the map"
