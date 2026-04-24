"""Day-level models for NQ news intelligence."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List


def session_bucket_from_timestamp(timestamp: str) -> str:
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        hour = dt.hour
    except ValueError:
        return "unknown"

    if hour < 9:
        return "overnight"
    if hour < 14:
        return "premarket"
    if hour < 21:
        return "regular"
    return "afterhours"


def known_before_open(timestamp: str) -> bool:
    return session_bucket_from_timestamp(timestamp) in {"overnight", "premarket"}


@dataclass
class DayEvent:
    event_id: str
    date: str
    timestamp: str
    source_family: str
    provider: str
    title: str
    summary: str = ""
    raw_text: str = ""
    url: str = ""
    author: str = ""
    event_type: str = "NEWS"
    sentiment: float = 0.0
    importance: float = 0.0
    session_bucket: str = "unknown"
    known_before_open: bool = False
    entities: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def to_db_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "date": self.date,
            "timestamp": self.timestamp,
            "source_family": self.source_family,
            "provider": self.provider,
            "title": self.title,
            "summary": self.summary,
            "raw_text": self.raw_text,
            "url": self.url,
            "author": self.author,
            "event_type": self.event_type,
            "sentiment": self.sentiment,
            "importance": self.importance,
            "session_bucket": self.session_bucket,
            "known_before_open": 1 if self.known_before_open else 0,
            "entities_json": json.dumps(self.entities),
            "tags_json": json.dumps(self.tags),
            "raw_payload_json": json.dumps(self.raw_payload),
        }


@dataclass
class MacroEvent:
    event_id: str
    date: str
    timestamp: str
    provider: str
    event_name: str
    category: str
    scheduled_time: str = ""
    actual_value: str = ""
    prior_value: str = ""
    revised_value: str = ""
    importance: float = 0.0
    known_before_open: bool = False
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def to_db_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "date": self.date,
            "timestamp": self.timestamp,
            "provider": self.provider,
            "event_name": self.event_name,
            "category": self.category,
            "scheduled_time": self.scheduled_time,
            "actual_value": self.actual_value,
            "prior_value": self.prior_value,
            "revised_value": self.revised_value,
            "importance": self.importance,
            "known_before_open": 1 if self.known_before_open else 0,
            "raw_payload_json": json.dumps(self.raw_payload),
        }
