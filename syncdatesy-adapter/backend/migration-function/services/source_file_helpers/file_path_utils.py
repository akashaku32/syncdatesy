"""Utilities for normalizing and matching remote file paths."""

from __future__ import annotations

import os
import re
from typing import Iterable, Optional


def normalize_posix_path(path: Optional[str], *, keep_leading_slash: bool = False) -> str:
    """Normalize a path to POSIX-style separators.

    Args:
        path: Raw path value from the source configuration or file listing.
        keep_leading_slash: Whether to preserve a leading slash for absolute paths.

    Returns:
        Normalized path with duplicate separators removed.
    """
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""

    prefix = "/" if keep_leading_slash and raw.startswith("/") else ""
    parts = [part for part in raw.split("/") if part and part != "."]
    normalized = "/".join(parts)
    return f"{prefix}{normalized}" if normalized else prefix


def join_posix_path(*parts: Optional[str], keep_leading_slash: bool = False) -> str:
    """Join POSIX path parts with a single slash.

    Args:
        *parts: Path fragments to join.
        keep_leading_slash: Whether the joined path should keep a leading slash.

    Returns:
        The normalized path.
    """
    cleaned = [normalize_posix_path(part, keep_leading_slash=False) for part in parts if str(part or "").strip()]
    cleaned = [part for part in cleaned if part]
    joined = "/".join(cleaned)
    if keep_leading_slash and joined and not joined.startswith("/"):
        return f"/{joined}"
    return joined


def normalize_root_path(root: Optional[str], base: Optional[str] = None) -> str:
    """Combine a root path and optional base folder into one normalized path.

    Args:
        root: Root folder configured on the source.
        base: Optional logical folder below the root.

    Returns:
        Combined normalized path.
    """
    root_path = normalize_posix_path(root, keep_leading_slash=True)
    base_path = normalize_posix_path(base, keep_leading_slash=False)
    if not base_path:
        return root_path
    if not root_path:
        return base_path
    if root_path.endswith("/"):
        return f"{root_path}{base_path}"
    return f"{root_path}/{base_path}"


def relative_to_root(path: str, root: str) -> str:
    """Return a path relative to the configured root folder.

    Args:
        path: Absolute or root-relative remote path.
        root: Root folder used as the listing boundary.

    Returns:
        Relative path if the source path starts with the root, otherwise the
        normalized source path.
    """
    normalized_path = normalize_posix_path(path, keep_leading_slash=False)
    normalized_root = normalize_posix_path(root, keep_leading_slash=False)
    if not normalized_root:
        return normalized_path
    if normalized_path == normalized_root:
        return ""
    if normalized_path.startswith(normalized_root + "/"):
        return normalized_path[len(normalized_root) + 1 :]
    return normalized_path


def path_depth(path: str) -> int:
    """Return the number of non-empty path segments."""
    return len([part for part in normalize_posix_path(path).split("/") if part])


def matches_any_pattern(value: str, patterns: Iterable[str]) -> bool:
    """Check whether a string matches any regex pattern.

    Args:
        value: Candidate string.
        patterns: Regex patterns to test.

    Returns:
        True if any pattern matches.
    """
    candidate = str(value or "")
    for pattern in patterns or []:
        raw_pattern = str(pattern or "").strip()
        if not raw_pattern:
            continue
        try:
            if re.search(raw_pattern, candidate):
                return True
        except re.error:
            if raw_pattern in candidate:
                return True
    return False


def safe_path_basename(path: str) -> str:
    """Return the last non-empty path segment."""
    normalized = normalize_posix_path(path)
    return normalized.rsplit("/", 1)[-1] if normalized else ""


def safe_path_stem(path: str) -> str:
    """Return a filename stem without the final extension."""
    basename = safe_path_basename(path)
    stem, _ext = os.path.splitext(basename)
    return stem or basename
