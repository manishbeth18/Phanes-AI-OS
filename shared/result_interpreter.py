
"""Deterministic interpretation of successful PHANES tool results."""

from __future__ import annotations

import ast
import re
from typing import Protocol


class ResultInterpreter(Protocol):
    def interpret(self, goal: str, tool_name: str, output: str) -> str:
        """Turn actual tool output into a concise user-facing response."""
        ...


class DeterministicResultInterpreter:
    """Summarize supported results without inventing information."""

    def interpret(self, goal: str, tool_name: str, output: str) -> str:
        content = output.strip()
        if not content:
            return "The tool completed but returned no content to summarize."

        if tool_name == "filesystem.read_file":
            return self._summarize_text(content)

        if tool_name == "filesystem.list_directory":
            return self._summarize_listing(content)

        return content

    @staticmethod
    def _summarize_text(content: str) -> str:
        # Extract the first Markdown heading as the document title.
        heading = re.search(r"(?m)^\s*#\s+(.+?)\s*$", content)
        title = heading.group(1).strip() if heading else None

        # Remove Markdown heading markers and collapse whitespace.
        cleaned = re.sub(r"(?m)^\s*#{1,6}\s+", "", content)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        if title:
            title_text = f"Document: {title}."
            remainder = re.sub(
                rf"^\s*{re.escape(title)}\s*",
                "",
                cleaned,
                count=1,
            ).strip()
            if remainder:
                return f"{title_text}\n\nSummary: {remainder}"
            return title_text

        return f"Summary: {cleaned}"

    @staticmethod
    def _summarize_listing(content: str) -> str:
        # The filesystem tool currently returns a Python-style list of entries.
        try:
            entries = ast.literal_eval(content)
        except (ValueError, SyntaxError):
            return f"Directory listing returned:\n{content}"

        if not isinstance(entries, list):
            return f"Directory listing returned:\n{content}"

        files = [
            item for item in entries
            if isinstance(item, dict) and item.get("type") == "file"
        ]
        directories = [
            item for item in entries
            if isinstance(item, dict) and item.get("type") == "directory"
        ]

        return (
            f"Workspace contains {len(files)} file(s) and "
            f"{len(directories)} directorie(s)."
        )
