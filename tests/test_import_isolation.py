"""Verify that src/core/ and src/strategies/ never import from src/exchanges/ at module level."""
import ast
import os


def _find_module_level_exchange_imports(directory: str) -> list[str]:
    violations = []
    for filename in os.listdir(directory):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(directory, filename)
        with open(filepath) as f:
            tree = ast.parse(f.read(), filepath)

        # Only check top-level statements, not imports inside functions
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src.exchanges"):
                        violations.append(f"{filepath}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("src.exchanges"):
                    violations.append(f"{filepath}: from {node.module} import ...")
    return violations


def test_core_does_not_import_exchanges():
    violations = _find_module_level_exchange_imports(os.path.join("src", "core"))
    assert not violations, f"Core imports exchanges:\n" + "\n".join(violations)


def test_strategies_does_not_import_exchanges():
    violations = _find_module_level_exchange_imports(os.path.join("src", "strategies"))
    assert not violations, f"Strategies imports exchanges:\n" + "\n".join(violations)
