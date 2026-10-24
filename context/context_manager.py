"""Small in-memory context buffer for UI/session-local notes."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass
class ContextItem:
    content: str
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class ContextManager:
    def __init__(self): self._context: list[ContextItem] = []
    def add(self, content: str, source: str) -> None:
        if content and content.strip(): self._context.append(ContextItem(content=content.strip(), source=source))
    def get_context(self) -> list[ContextItem]: return list(self._context)
    def clear(self) -> None: self._context.clear()
