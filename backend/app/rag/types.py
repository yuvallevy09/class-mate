from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RagHit:
    text: str
    metadata: dict[str, Any]
    score: float | None = None


