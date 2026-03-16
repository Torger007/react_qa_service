from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentChunk:
    doc_id: str
    chunk_id: str
    text: str
    metadata: dict[str, Any]
