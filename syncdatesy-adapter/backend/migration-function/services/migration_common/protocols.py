"""Typing protocols for migration extract/load pipelines."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SourceExtractor(Protocol):
    def extract_chunk(self) -> Any:
        """Return the next chunk of source rows, or a sentinel when exhausted."""

    def close(self) -> None:
        """Release source-side resources."""


@runtime_checkable
class DestinationLoader(Protocol):
    def load_chunk(self, chunk: Any) -> None:
        """Load a chunk into the destination staging or target."""

    def merge(self) -> None:
        """Apply merge/upsert from staging into the final destination."""

    def close(self) -> None:
        """Release destination-side resources."""
