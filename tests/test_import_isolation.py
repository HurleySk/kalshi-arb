"""Verify that src/core/ and src/strategies/ never import from src/exchanges/."""
import ast
import os


def test_core_does_not_import_exchanges():
    core_dir = os.path.join("src", "core")
    violations = []

    for filename in os.listdir(core_dir):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(core_dir, filename)
        with open(filepath) as f:
            tree = ast.parse(f.read(), filepath)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src.exchanges"):
                        violations.append(f"{filepath}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("src.exchanges"):
                    violations.append(f"{filepath}: from {node.module} import ...")

    assert not violations, f"Core imports exchanges:\n" + "\n".join(violations)


def test_strategies_does_not_import_exchanges():
    strat_dir = os.path.join("src", "strategies")
    violations = []

    for filename in os.listdir(strat_dir):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(strat_dir, filename)
        with open(filepath) as f:
            tree = ast.parse(f.read(), filepath)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src.exchanges"):
                        violations.append(f"{filepath}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("src.exchanges"):
                    violations.append(f"{filepath}: from {node.module} import ...")

    assert not violations, f"Strategies imports exchanges:\n" + "\n".join(violations)
