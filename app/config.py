from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SEVERITY_ORDER = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    cluster_name: str = os.getenv("CLUSTER_NAME", "local-cluster")
    pioneer_api_key: str = os.getenv("PIONEER_API_KEY", "")
    pioneer_model: str = os.getenv("PIONEER_MODEL", "claude-haiku-4-5")
    pioneer_endpoint: str = os.getenv(
        "PIONEER_ENDPOINT", "https://api.pioneer.ai/v1/chat/completions"
    )
    pioneer_max_tokens: int = _int_env("PIONEER_MAX_TOKENS", 1500)
    pioneer_temperature: float = _float_env("PIONEER_TEMPERATURE", 0.2)
    ai_enabled: bool = _bool_env("AI_ENABLED", False)
    ai_min_severity: str = os.getenv("AI_MIN_SEVERITY", "High")
    scan_interval_seconds: int = _int_env("SCAN_INTERVAL_SECONDS", 60)
    log_line_limit: int = _int_env("LOG_LINE_LIMIT", 150)
    ai_timeout_seconds: float = _float_env("AI_TIMEOUT_SECONDS", 30.0)
    ai_rate_limit_per_scan: int = _int_env("AI_RATE_LIMIT_PER_SCAN", 5)
    # Default to /data (K8s PV mount point). Falls back gracefully to /tmp locally.
    storage_path: Path = Path(os.getenv("STORAGE_PATH", "/data/ai-kube-agent.sqlite3"))
    kube_context: str | None = os.getenv("KUBE_CONTEXT") or None
    # Demo reset token — leave empty to disable the endpoint
    demo_reset_token: str = os.getenv("DEMO_RESET_TOKEN", "")
    ai_key_invalid: bool = False
    ai_remediation_mode: str = os.getenv("AI_REMEDIATION_MODE", "read-write")
    ai_remediation_namespaces: str = os.getenv("AI_REMEDIATION_NAMESPACES", "*")

    def __post_init__(self) -> None:
        # If pioneer_api_key is empty or set to the placeholder 'sk-local-test', disable AI by default
        if not self.pioneer_api_key or self.pioneer_api_key == "sk-local-test":
            self.ai_enabled = False

    @property
    def ai_ready(self) -> bool:
        """True only when AI is enabled AND has a non-empty API key AND an endpoint AND the key is valid."""
        return self.ai_enabled and bool(self.pioneer_api_key) and bool(self.pioneer_endpoint) and not self.ai_key_invalid

    @property
    def ai_status(self) -> str:
        """Human-readable AI status for the dashboard."""
        if not self.ai_enabled:
            return "disabled"
        if not self.pioneer_api_key:
            return "no_key"
        if not self.pioneer_endpoint:
            return "no_endpoint"
        if self.ai_key_invalid:
            return "invalid_key"
        return "active"

    @property
    def public_dict(self) -> dict[str, object]:
        return {
            "cluster_name": self.cluster_name,
            "pioneer_model": self.pioneer_model,
            "pioneer_endpoint": self.pioneer_endpoint,
            "pioneer_endpoint_configured": bool(self.pioneer_endpoint),
            "ai_enabled": self.ai_enabled,
            "ai_ready": self.ai_ready,
            "ai_status": self.ai_status,
            "ai_key_invalid": self.ai_key_invalid,
            "ai_min_severity": self.ai_min_severity,
            "scan_interval_seconds": self.scan_interval_seconds,
            "log_line_limit": self.log_line_limit,
            "ai_timeout_seconds": self.ai_timeout_seconds,
            "ai_rate_limit_per_scan": self.ai_rate_limit_per_scan,
            "storage_path": str(self.storage_path),
            "pioneer_api_key_configured": bool(self.pioneer_api_key),
            "demo_reset_enabled": bool(self.demo_reset_token),
            "kube_context": self.kube_context or "default",
            "ai_remediation_mode": self.ai_remediation_mode,
            "ai_remediation_namespaces": self.ai_remediation_namespaces,
        }

    def severity_allows_ai(self, severity: str) -> bool:
        return SEVERITY_ORDER.get(severity, 0) >= SEVERITY_ORDER.get(self.ai_min_severity, 2)


settings = Settings()
