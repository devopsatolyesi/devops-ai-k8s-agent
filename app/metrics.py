from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Metrics:
    findings_total: int = 0
    ai_requests_total: int = 0
    ai_errors_total: int = 0
    last_scan_timestamp: float = 0.0
    scan_duration_seconds: float = 0.0

    def mark_scan(self, duration: float, findings_total: int) -> None:
        self.last_scan_timestamp = time.time()
        self.scan_duration_seconds = duration
        self.findings_total = findings_total

    def prometheus(self) -> str:
        return "\n".join(
            [
                "# HELP kube_ai_agent_findings_total Current number of open findings.",
                "# TYPE kube_ai_agent_findings_total gauge",
                f"kube_ai_agent_findings_total {self.findings_total}",
                "# HELP kube_ai_agent_ai_requests_total Total AI requests sent.",
                "# TYPE kube_ai_agent_ai_requests_total counter",
                f"kube_ai_agent_ai_requests_total {self.ai_requests_total}",
                "# HELP kube_ai_agent_scan_duration_seconds Last scan duration in seconds.",
                "# TYPE kube_ai_agent_scan_duration_seconds gauge",
                f"kube_ai_agent_scan_duration_seconds {self.scan_duration_seconds}",
                "# HELP kube_ai_agent_last_scan_timestamp Unix timestamp of last scan.",
                "# TYPE kube_ai_agent_last_scan_timestamp gauge",
                f"kube_ai_agent_last_scan_timestamp {self.last_scan_timestamp}",
                "# HELP kube_ai_agent_ai_errors_total Total AI request errors.",
                "# TYPE kube_ai_agent_ai_errors_total counter",
                f"kube_ai_agent_ai_errors_total {self.ai_errors_total}",
                "",
            ]
        )


metrics = Metrics()

