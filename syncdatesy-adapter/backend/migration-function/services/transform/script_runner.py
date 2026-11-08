"""Run user transform scripts in a restricted subprocess."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd

from services.transform.script_validator import (
    ALLOWED_IMPORTS,
    BANNED_IMPORTS,
    validate_script_ast,
)


class ScriptRuntimeError(Exception):
    def __init__(self, message: str, line: Optional[int] = None, traceback_text: str = ""):
        self.line = line
        self.traceback_text = traceback_text
        super().__init__(message)


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if root in BANNED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed in transform sandbox")
    if root not in ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not in the allowlist")
    return __import__(name, globals, locals, fromlist, level)


def load_transform_module(script_text: str, module_name: str = "user_transform") -> ModuleType:
    checklist, _ = validate_script_ast(script_text)
    failed = [c for c in checklist if not c.get("passed")]
    if failed:
        first = failed[0]
        raise ScriptRuntimeError(first.get("message") or "Script validation failed", first.get("line"))

    spec = importlib.util.spec_from_loader(module_name, loader=None)
    module = importlib.util.module_from_spec(spec)

    import builtins as _builtins_mod

    raw_builtins = getattr(_builtins_mod, "__dict__", _builtins_mod)
    safe_builtins = {
        k: v
        for k, v in raw_builtins.items()
        if k
        not in {
            "open",
            "eval",
            "exec",
            "compile",
            "__import__",
            "input",
            "breakpoint",
            "help",
            "exit",
            "quit",
        }
    }
    safe_builtins["__import__"] = _restricted_import

    module.__dict__["__builtins__"] = safe_builtins
    exec(compile(script_text, "<transform_script>", "exec"), module.__dict__)
    return module


def resolve_entry_point(module: ModuleType, entry_point: Optional[str]) -> Callable:
    if entry_point:
        fn = getattr(module, entry_point, None)
        if fn is None or not callable(fn):
            raise ScriptRuntimeError(f"Entry point '{entry_point}' not found or not callable")
        return fn

    transform_fn = getattr(module, "transform", None)
    if callable(transform_fn):
        return transform_fn

    import inspect

    funcs: list[str] = []
    for name in dir(module):
        # Allow module-level names like __TRANSFORM_*; skip single-underscore helpers.
        if name.startswith("_") and not name.startswith("__"):
            continue
        obj = getattr(module, name)
        if not callable(obj):
            continue
        try:
            sig = inspect.signature(obj)
            if len(sig.parameters) == 1:
                funcs.append(name)
        except (TypeError, ValueError):
            continue

    if not funcs:
        raise ScriptRuntimeError("No suitable entry point function found (expected one df parameter)")

    return getattr(module, funcs[0])


_SHELL_ONLY_STATEMENTS = {
    "__target_df = __source_df.copy()",
    "df = __target_df",
    "df = df.copy()",
    "return __target_df",
    "return df",
}


def effective_script_text(script_text: Optional[str]) -> Optional[str]:
    """Return script text only when the function body does real work.

    An empty string, whitespace, or the locked shell with no user body is
    treated as "no script" so migration skips the Python step.
    """
    if not script_text or not str(script_text).strip():
        return None
    body_lines: list[str] = []
    in_def = False
    for raw in str(script_text).replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        if not in_def:
            if stripped.startswith("def "):
                in_def = True
            continue
        if stripped and not raw.startswith((" ", "\t")):
            break
        if not stripped or stripped.startswith("#"):
            continue
        body_lines.append(stripped)
    if not body_lines:
        return None
    if all(line in _SHELL_ONLY_STATEMENTS for line in body_lines):
        return None
    return str(script_text)


def run_transform(
    script_text: str,
    df: pd.DataFrame,
    entry_point: Optional[str] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Execute transform and return (result_df, metadata)."""
    module = load_transform_module(script_text)
    fn = resolve_entry_point(module, entry_point)

    try:
        result = fn(df)
    except Exception as exc:
        tb = traceback.format_exc()
        line = None
        if exc.__traceback__:
            line = exc.__traceback__.tb_lineno
        raise ScriptRuntimeError(str(exc), line, tb) from exc

    if not isinstance(result, pd.DataFrame):
        raise ScriptRuntimeError(
            f"Transform must return a pandas DataFrame, got {type(result).__name__}"
        )

    return result, {"entry_point": getattr(fn, "__name__", entry_point)}
