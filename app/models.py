from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def build_fingerprint(
    namespace: str,
    pod_name: str,
    container_name: str | None,
    problem_type: str,
    reason: str,
) -> str:
    raw = "|".join(
        [
            namespace or "",
            pod_name or "",
            container_name or "",
            problem_type or "",
            reason or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class RuleResult(BaseModel):
    rule_id: str
    problem_type: str
    severity: str
    reason: str
    evidence: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    commands_to_verify: list[str] = Field(default_factory=list)
    confidence: float = 0.7
    proposed_fix: dict[str, Any] | None = None
    safe_auto_fix: bool = False
    needs_ai_analysis: bool = False
    ai_can_auto_apply: bool = False


class AIAnalysis(BaseModel):
    summary: str = ""
    probable_root_cause: str = ""
    severity: str = ""
    confidence: str = ""
    recommended_actions: list[str] = Field(default_factory=list)
    commands_to_verify: list[str] = Field(default_factory=list)
    prevention: list[str] = Field(default_factory=list)
    junior_friendly_explanation: str = ""
    action_plan: list[str] = Field(default_factory=list)
    manual_fix_summary: str = ""
    should_auto_apply: bool = False
    proposed_fix: dict[str, Any] | None = None


class AIAuditEntry(BaseModel):
    """A single record of an AI analysis call for a finding."""
    timestamp: str = Field(default_factory=utc_now)
    model: str = ""
    outcome: str = ""          # "success" | "error" | "skipped"
    error: str | None = None
    summary: str = ""          # Short summary from the AI response
    remediation_type: bool = False  # True if this was a remediation call


class Finding(BaseModel):
    id: str
    cluster_name: str
    namespace: str
    resource_kind: str
    resource_name: str
    pod_name: str | None = None
    container_name: str | None = None
    problem_type: str
    severity: str
    status: str = "open"
    first_seen: str = Field(default_factory=utc_now)
    last_seen: str = Field(default_factory=utc_now)
    resolved_at: str | None = None
    finding_number: int | None = None
    restart_count: int = 0
    local_analysis: dict[str, Any] = Field(default_factory=dict)
    ai_analysis: dict[str, Any] | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommended_actions: list[str] = Field(default_factory=list)
    commands_to_verify: list[str] = Field(default_factory=list)
    confidence: float = 0.7
    rule_id: str | None = None
    rule_detected: bool = True
    rule_confidence: float = 0.7
    rule_fix_available: bool = False
    needs_ai_analysis: bool = False
    ai_can_auto_apply: bool = False
    fingerprint: str
    ai_used: bool = False
    ai_error: str | None = None
    resolved: bool = False
    # Audit trail: every AI call made for this finding
    ai_history: list[dict[str, Any]] = Field(default_factory=list)
    # Restart count recorded at the time of the last AI analysis (for re-analysis trigger)
    last_restart_count_at_ai: int = 0

    @property
    def root_cause(self) -> str:
        if self.ai_analysis and self.ai_analysis.get("probable_root_cause"):
            return str(self.ai_analysis["probable_root_cause"])
        return str(self.local_analysis.get("reason", self.problem_type))


class Summary(BaseModel):
    total_findings: int
    by_severity: dict[str, int]
    by_namespace: dict[str, int]
    ai_requests_total: int
    ai_errors_total: int
    last_scan_timestamp: float
    last_scan_duration_seconds: float
