"""Static AST validation for user transform scripts."""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional, Tuple

MAX_MAPPING_JSON_MB = 10
MAX_SCRIPT_KB =1024
MAX_MAPPING_JSON_BYTES = MAX_MAPPING_JSON_MB * 1024 * 1024
MAX_SCRIPT_BYTES = MAX_SCRIPT_KB * 1024 
MAX_SCRIPT_LINES = 20000

ALLOWED_IMPORTS = frozenset(
    {"pandas", "numpy", "datetime", "re", "decimal", "math", "pd", "np"}
)
BANNED_IMPORTS = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "http",
        "ftplib",
        "shutil",
        "pathlib",
        "importlib",
        "pickle",
        "ctypes",
    }
)
BANNED_NAMES = frozenset(
    {"open", "eval", "exec", "compile", "__import__", "input", "breakpoint"}
)


class ScriptValidationError(Exception):
    pass


def check_script_size(script_text: str) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    size = len(script_text.encode("utf-8"))
    if size > MAX_SCRIPT_BYTES:
        errors.append(
            {
                "line": 0,
                "message": f"Script exceeds maximum size of {MAX_SCRIPT_BYTES // 1024} KB ({size} bytes).",
            }
        )
    line_count = script_text.count("\n") + (1 if script_text else 0)
    if line_count > MAX_SCRIPT_LINES:
        errors.append(
            {
                "line": 0,
                "message": f"Script exceeds maximum of {MAX_SCRIPT_LINES} lines ({line_count} lines).",
            }
        )
    return errors


def detect_entry_points(script_text: str) -> List[str]:
    """Find module-level functions with a single parameter (df-like)."""
    try:
        tree = ast.parse(script_text)
    except SyntaxError:
        return []

    candidates = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        args = node.args
        positional = len(getattr(args, "posonlyargs", [])) + len(args.args)
        if positional == 1 and not args.vararg and not args.kwarg:
            candidates.append(node.name)
    return candidates


def validate_script_ast(script_text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Returns (checklist_items, entry_points).
    checklist items: {id, label, passed, line?, message?}
    """
    checklist: List[Dict[str, Any]] = []
    entry_points: List[str] = []

    size_errors = check_script_size(script_text)
    if size_errors:
        for err in size_errors:
            checklist.append(
                {
                    "id": "size",
                    "label": "Size limits",
                    "passed": False,
                    "line": err.get("line", 0),
                    "message": err["message"],
                }
            )
        return checklist, entry_points

    try:
        tree = ast.parse(script_text)
        checklist.append({"id": "parse", "label": "Python syntax valid", "passed": True})
    except SyntaxError as exc:
        checklist.append(
            {
                "id": "parse",
                "label": "Python syntax valid",
                "passed": False,
                "line": exc.lineno or 0,
                "message": str(exc.msg),
            }
        )
        return checklist, entry_points

    import_errors: List[Dict[str, Any]] = []
    banned_errors: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in BANNED_IMPORTS:
                    import_errors.append(
                        {
                            "line": node.lineno,
                            "message": f"Import '{alias.name}' is not allowed",
                        }
                    )
                elif mod not in ALLOWED_IMPORTS:
                    import_errors.append(
                        {
                            "line": node.lineno,
                            "message": f"Import '{alias.name}' is not in the allowlist",
                        }
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in BANNED_IMPORTS:
                import_errors.append(
                    {
                        "line": node.lineno,
                        "message": f"Import from '{node.module}' is not allowed",
                    }
                )
            elif mod and mod not in ALLOWED_IMPORTS:
                import_errors.append(
                    {
                        "line": node.lineno,
                        "message": f"Import from '{node.module}' is not in the allowlist",
                    }
                )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in BANNED_NAMES:
                banned_errors.append(
                    {
                        "line": node.lineno,
                        "message": f"Call to '{node.func.id}' is not allowed",
                    }
                )

    if import_errors:
        first = import_errors[0]
        checklist.append(
            {
                "id": "imports",
                "label": "Imports restricted to allowlist",
                "passed": False,
                "line": first["line"],
                "message": first["message"],
                "all_errors": import_errors,
            }
        )
    else:
        checklist.append(
            {"id": "imports", "label": "Imports restricted to allowlist", "passed": True}
        )

    if banned_errors:
        first = banned_errors[0]
        checklist.append(
            {
                "id": "banned",
                "label": "No banned builtins (open, eval, exec, …)",
                "passed": False,
                "line": first["line"],
                "message": first["message"],
                "all_errors": banned_errors,
            }
        )
    else:
        checklist.append(
            {
                "id": "banned",
                "label": "No banned builtins (open, eval, exec, …)",
                "passed": True,
            }
        )

    entry_points = detect_entry_points(script_text)
    if entry_points:
        checklist.append(
            {
                "id": "entry_point",
                "label": f"Entry point candidate(s): {', '.join(entry_points[:5])}",
                "passed": True,
            }
        )

    return checklist, entry_points


def script_has_filter_pattern(script_text: str, field: str) -> bool:
    """Heuristic: detect row-filter logic duplicated in script."""
    patterns = [
        rf'["\']{field}["\']',
        rf"\b{field}\b",
    ]
    filter_hints = ["isin", "filter", "IN", "=="]
    lower = script_text.lower()
    if field.lower() not in lower:
        return False
    return any(h in lower or h in script_text for h in filter_hints)
