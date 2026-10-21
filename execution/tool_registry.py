
"""Registry for tools available to the PHANES execution engine."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any
from dataclasses import dataclass


class ToolRegistryError(Exception):
    """Base exception for tool registry errors."""


class DuplicateToolError(ToolRegistryError):
    """Raised when a tool name is already registered."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a requested tool does not exist."""


class ToolSafetyLevel(str, Enum):
    """Trust classification assigned by the tool registrant."""

    READ_ONLY = "read_only"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ToolMetadata:
    """Safety metadata associated with a registered tool."""

    safety_level: ToolSafetyLevel


class ToolRegistry:
    """Register tools with explicit safety metadata."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        self._metadata: dict[str, ToolMetadata] = {}

    def register(
        self,
        name: str,
        tool: Callable[..., Any],
        *,
        safety_level: ToolSafetyLevel = ToolSafetyLevel.APPROVAL_REQUIRED,
    ) -> None:
        """Register a callable under a unique name.

        Unclassified tools default to requiring approval.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name must be a non-empty string.")

        if not callable(tool):
            raise TypeError("Tool must be callable.")

        if not isinstance(safety_level, ToolSafetyLevel):
            raise TypeError("safety_level must be a ToolSafetyLevel value.")

        normalized_name = name.strip()

        if normalized_name in self._tools:
            raise DuplicateToolError(
                f"Tool '{normalized_name}' is already registered."
            )

        self._tools[normalized_name] = tool
        self._metadata[normalized_name] = ToolMetadata(
            safety_level=safety_level
        )

    def get(self, name: str) -> Callable[..., Any]:
        """Return a registered tool or raise ToolNotFoundError."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name must be a non-empty string.")

        normalized_name = name.strip()

        try:
            return self._tools[normalized_name]
        except KeyError:
            raise ToolNotFoundError(
                f"Tool '{normalized_name}' is not registered."
            ) from None

    def get_metadata(self, name: str) -> ToolMetadata:
        """Return safety metadata for a registered tool."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name must be a non-empty string.")

        normalized_name = name.strip()

        try:
            return self._metadata[normalized_name]
        except KeyError:
            raise ToolNotFoundError(
                f"Tool '{normalized_name}' is not registered."
            ) from None

    def list_tools(self) -> list[str]:
        """Return registered tool names in registration order."""
        return list(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        """Allow checks such as: 'filesystem.read_file' in registry."""
        return name in self._tools

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)
