from dataclasses import dataclass, field


@dataclass
class Record:
    source: str
    pointer: str
    key: str
    kind: str
    raw: dict
    context: dict = field(default_factory=dict)
    unknown_fields: list = field(default_factory=list)

