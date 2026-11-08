"""Source hygiene checks that tests cannot catch.

A half-finished edit that leaves code after a `return` compiles, runs,
and passes every test — the statements simply never execute. That is
exactly how a fragment of the old duplicated consent loop survived being
consolidated away, sitting next to the new implementation as a trap for
the next edit. Python does not warn, and no linter is installed here.
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).parent.parent
SOURCES = sorted((ROOT / "src/friday").glob("*.py")) + sorted(
    (ROOT / "tests").glob("*.py")
)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_unreachable_statements(path):
    """No statement may follow a return/raise/continue/break in its block."""
    tree = ast.parse(path.read_text())
    dead = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for index, statement in enumerate(body[:-1]):
            if isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                following = body[index + 1]
                dead.append(
                    f"line {following.lineno}: "
                    f"{type(following).__name__} after {type(statement).__name__}"
                )
    assert not dead, f"{path.name} has unreachable code: " + "; ".join(dead)
