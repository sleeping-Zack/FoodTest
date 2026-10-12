from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

from ..common import now


class Outcome(StrEnum):
    SUCCESS = "success"
    DUPLICATE = "duplicate"
    QUARANTINED = "quarantined"
    SOURCE_MISSING = "source_missing"
    IDENTITY_CONFLICT = "identity_conflict"
    UNRECOGNIZED = "unrecognized"
    FAILED = "failed"


@dataclass
class JobReport:
    stage: str
    input_fingerprint: str
    config_hash: str
    job_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: str = field(default_factory=now)
    status: str = "running"
    processor_version: str = "legacy-1"
    schema_version: str = "001"
    knowledge_release: str | None = None
    actor: str = "food-kb/0.1.0"
    items: list = field(default_factory=list)

    def finish(self):
        counts = {x.value: 0 for x in Outcome}
        for row in self.items:
            counts[row["status"]] += 1
        self.status = "completed" if sum(counts[x] for x in ("failed", "identity_conflict", "source_missing", "quarantined", "unrecognized")) == 0 else "completed_with_issues"
        return self.__dict__ | {"finished_at": now(), "total": len(self.items), "counts": counts}
