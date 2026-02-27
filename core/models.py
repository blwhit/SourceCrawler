from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import json
import uuid


class SearchMode(str, Enum):
    STRING = "string"
    REGEX = "regex"


class ScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class SourceResult:
    """Normalized result from any scanner."""
    provider_name: str
    target_url: str
    code_snippet: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)
    result_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class ScanRequest:
    """A user's scan request."""
    query: str
    mode: SearchMode
    scan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: ScanStatus = ScanStatus.PENDING
    results: list[SourceResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
