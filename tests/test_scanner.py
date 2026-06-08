from pathlib import Path

from app.config import Settings
from app.models import Finding, build_fingerprint
from app.scanner import Scanner
from app.storage import Storage


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ai_enabled=False,
        storage_path=tmp_path / "scanner.sqlite3",
    )


def _crashloop_pod(name: str = "demo-api-7c9d9f6f5d-abcde") -> dict:
    return {
        "metadata": {"name": name, "namespace": "demo"},
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "app",
                    "restart_count": 4,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "last_state": {},
                }
            ],
        },
    }


def _healthy_pod(name: str = "demo-api-7c9d9f6f5d-abcde") -> dict:
    return {
        "metadata": {"name": name, "namespace": "demo"},
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "app",
                    "ready": True,
                    "restart_count": 0,
                    "state": {"running": {"started_at": "2026-05-24T00:00:00Z"}},
                    "last_state": {},
                }
            ],
        },
    }


class FakeKubernetesClient:
    def __init__(self, pods: list[dict], available: bool = True) -> None:
        self._pods = pods
        self.available = available

    def list_pods(self) -> list[dict]:
        return self._pods

    def get_pod_workload_owner(self, pod_name: str, namespace: str) -> tuple[str, str]:
        return "Pod", pod_name

    def list_services(self) -> list[dict]:
        return []

    def list_endpoints(self) -> list[dict]:
        return []

    def list_ingresses(self) -> list[dict]:
        return []

    def logs_for_pod(self, namespace: str, pod_name: str, container: str | None = None) -> str:
        return ""

    def events_for_pod(self, namespace: str, pod_name: str) -> list[str]:
        return ["Back-off restarting failed container"]


class FakeAIClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def analyze(self, evidence: dict) -> tuple[dict | None, str | None, object]:
        self.calls.append(evidence)
        from app.models import AIAuditEntry
        return (
            {
                "summary": "AI analyzed the crash",
                "probable_root_cause": "Database authentication failed",
                "confidence": "medium",
                "action_plan": ["Check DATABASE_URL", "Rotate the secret"],
                "manual_fix_summary": "Update the referenced secret or env var.",
                "should_auto_apply": False,
                "proposed_fix": None,
            },
            None,
            AIAuditEntry(model="test-model", outcome="success", summary="AI analyzed the crash"),
        )


def test_scan_marks_missing_finding_resolved(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    scanner.k8s = FakeKubernetesClient([_crashloop_pod()])
    scanner.scan()

    first_scan = storage.list_findings()
    assert len(first_scan) == 1
    assert first_scan[0].resolved is False
    assert first_scan[0].status == "open"

    scanner.k8s = FakeKubernetesClient([_healthy_pod()])
    scanner.scan(resolve_missing=True)

    second_scan = storage.list_findings()
    assert second_scan == []

    resolved = storage.list_resolved()
    assert len(resolved) == 1
    assert resolved[0].resolved is True
    assert resolved[0].status == "resolved_manually"
    assert resolved[0].resolved_at is not None


def test_scan_finishes_remediation_when_finding_disappears(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    fingerprint = build_fingerprint(
        namespace="demo",
        pod_name="demo-api-7c9d9f6f5d-abcde",
        container_name="app",
        problem_type="CrashLoopBackOff",
        reason="Container repeatedly crashes and Kubernetes is backing off restarts.",
    )
    storage.upsert_finding(
        Finding(
            id="finding-1",
            cluster_name="local-cluster",
            namespace="demo",
            resource_kind="Pod",
            resource_name="demo-api-7c9d9f6f5d-abcde",
            pod_name="demo-api-7c9d9f6f5d-abcde",
            container_name="app",
            problem_type="CrashLoopBackOff",
            severity="High",
            status="remediating",
            local_analysis={"reason": "Container repeatedly crashes and Kubernetes is backing off restarts."},
            recommended_actions=[],
            commands_to_verify=[],
            fingerprint=fingerprint,
            resolved=False,
        )
    )

    scanner.k8s = FakeKubernetesClient([_healthy_pod()])
    scanner.scan(resolve_missing=True)

    assert storage.list_findings() == []
    finding = storage.list_resolved()[0]
    assert finding.resolved is True
    assert finding.status == "resolved"


def test_scan_reopens_resolved_finding_if_problem_still_exists(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    fingerprint = build_fingerprint(
        namespace="demo",
        pod_name="demo-api-7c9d9f6f5d-abcde",
        container_name="app",
        problem_type="CrashLoopBackOff",
        reason="Container repeatedly crashes and Kubernetes is backing off restarts.",
    )
    storage.upsert_finding(
        Finding(
            id="finding-3",
            cluster_name="local-cluster",
            namespace="demo",
            resource_kind="Pod",
            resource_name="demo-api-7c9d9f6f5d-abcde",
            pod_name="demo-api-7c9d9f6f5d-abcde",
            container_name="app",
            problem_type="CrashLoopBackOff",
            severity="High",
            status="remediating",
            local_analysis={"reason": "Container repeatedly crashes and Kubernetes is backing off restarts."},
            recommended_actions=[],
            commands_to_verify=[],
            fingerprint=fingerprint,
            resolved=True,
        )
    )

    scanner.k8s = FakeKubernetesClient([_crashloop_pod()])
    scanner.scan()

    finding = storage.list_findings()[0]
    assert finding.resolved is False
    assert finding.status == "open"
    assert finding.resolved_at is None


def test_scan_does_not_auto_resolve_when_kubernetes_is_unavailable(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    storage.upsert_finding(
        Finding(
            id="finding-2",
            cluster_name="local-cluster",
            namespace="demo",
            resource_kind="Pod",
            resource_name="demo-api-7c9d9f6f5d-abcde",
            pod_name="demo-api-7c9d9f6f5d-abcde",
            container_name="app",
            problem_type="CrashLoopBackOff",
            severity="High",
            local_analysis={"reason": "Container repeatedly crashes and Kubernetes is backing off restarts."},
            recommended_actions=[],
            commands_to_verify=[],
            fingerprint=build_fingerprint(
                namespace="demo",
                pod_name="demo-api-7c9d9f6f5d-abcde",
                container_name="app",
                problem_type="CrashLoopBackOff",
                reason="Container repeatedly crashes and Kubernetes is backing off restarts.",
            ),
        )
    )

    scanner.k8s = FakeKubernetesClient([], available=False)
    scanner.scan()

    finding = storage.list_findings()[0]
    assert finding.resolved is False
    assert finding.status == "open"


def test_scan_keeps_single_primary_finding_per_unhealthy_pod(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    pod = _crashloop_pod()
    pod["status"]["container_statuses"][0]["last_state"] = {"terminated": {"reason": "OOMKilled"}}

    scanner.k8s = FakeKubernetesClient([pod])
    scanner.scan()

    findings = storage.list_findings()
    assert len(findings) == 1
    assert findings[0].problem_type == "OOMKilled"


def test_scan_calls_ai_only_for_findings_that_need_ai_analysis(tmp_path: Path) -> None:
    settings = Settings(
        ai_enabled=True,
        pioneer_api_key="sk-test",
        pioneer_endpoint="http://localhost:8080/api",
        ai_min_severity="Medium",
        storage_path=tmp_path / "scanner.sqlite3",
    )
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)
    scanner.k8s = FakeKubernetesClient([_crashloop_pod(name="ai-analysis-demo-7c9d9f6f5d-abcde")])
    scanner.ai = FakeAIClient()

    scanner.scan(use_ai=True)

    findings = storage.list_findings()
    assert len(findings) == 1
    assert findings[0].needs_ai_analysis is True
    assert findings[0].ai_used is True
    assert findings[0].ai_analysis is not None
    assert len(scanner.ai.calls) == 1


def test_resolve_service_when_endpoints_available(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    fingerprint = build_fingerprint("demo", "my-svc", None, "ServiceNoEndpoints", "Service has no ready endpoints")
    storage.upsert_finding(
        Finding(
            id="f-svc-1",
            cluster_name="local-cluster",
            namespace="demo",
            resource_kind="Service",
            resource_name="my-svc",
            problem_type="ServiceNoEndpoints",
            severity="High",
            status="open",
            fingerprint=fingerprint,
            recommended_actions=[],
            commands_to_verify=[],
        )
    )

    class ServiceFakeClient(FakeKubernetesClient):
        def list_services(self) -> list[dict]:
            return [{"metadata": {"name": "my-svc", "namespace": "demo"}}]
        def list_endpoints(self) -> list[dict]:
            return [{"metadata": {"name": "my-svc", "namespace": "demo"}, "subsets": [{"addresses": [{"ip": "1.2.3.4"}]}]}]

    scanner.k8s = ServiceFakeClient([])
    scanner.scan(resolve_missing=True)

    findings = storage.list_findings()
    assert len(findings) == 0
    resolved = storage.list_resolved()
    assert len(resolved) == 1
    assert resolved[0].resolved is True
    assert resolved[0].status == "resolved_manually"


def test_resolve_ingress_when_backend_service_exists(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    fingerprint = build_fingerprint("demo", "my-ing", None, "IngressBadBackend", "Ingress references a backend Service that does not exist")
    storage.upsert_finding(
        Finding(
            id="f-ing-1",
            cluster_name="local-cluster",
            namespace="demo",
            resource_kind="Ingress",
            resource_name="my-ing",
            problem_type="IngressBadBackend",
            severity="High",
            status="open",
            fingerprint=fingerprint,
            recommended_actions=[],
            commands_to_verify=[],
        )
    )

    class IngressFakeClient(FakeKubernetesClient):
        def list_ingresses(self) -> list[dict]:
            return [{
                "metadata": {"name": "my-ing", "namespace": "demo"},
                "spec": {
                    "rules": [{
                        "http": {
                            "paths": [{
                                "backend": {"service": {"name": "my-backend-svc"}}
                            }]
                        }
                    }]
                }
            }]
        def list_services(self) -> list[dict]:
            return [{"metadata": {"name": "my-backend-svc", "namespace": "demo"}}]

    scanner.k8s = IngressFakeClient([])
    scanner.scan(resolve_missing=True)

    findings = storage.list_findings()
    assert len(findings) == 0
    resolved = storage.list_resolved()
    assert len(resolved) == 1
    assert resolved[0].resolved is True


def test_resolve_old_finding_when_new_finding_exists_for_same_container(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    fingerprint_old = build_fingerprint("demo", "demo-api", "app", "ErrImagePull", "ErrImagePull reason")
    storage.upsert_finding(
        Finding(
            id="f-old",
            cluster_name="local-cluster",
            namespace="demo",
            resource_kind="Pod",
            resource_name="demo-api-7c9d9f6f5d-abcde",
            pod_name="demo-api-7c9d9f6f5d-abcde",
            container_name="app",
            problem_type="ErrImagePull",
            severity="High",
            status="open",
            fingerprint=fingerprint_old,
            recommended_actions=[],
            commands_to_verify=[],
        )
    )

    pod = {
        "metadata": {"name": "demo-api-7c9d9f6f5d-abcde", "namespace": "demo"},
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restart_count": 0,
                    "state": {"waiting": {"reason": "ImagePullBackOff"}},
                    "last_state": {},
                }
            ],
        },
    }
    scanner.k8s = FakeKubernetesClient([pod])
    scanner.scan(resolve_missing=True)

    findings = storage.list_findings()
    assert len(findings) == 1
    assert findings[0].problem_type == "ImagePullBackOff"

    resolved = storage.list_resolved()
    assert len(resolved) == 1
    assert resolved[0].problem_type == "ErrImagePull"
    assert resolved[0].resolved is True


def test_scan_does_not_resolve_recently_restarted_pod(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    scanner.k8s = FakeKubernetesClient([_crashloop_pod()])
    scanner.scan()
    assert len(storage.list_findings()) == 1

    from datetime import UTC, datetime
    recent_start = datetime.now(UTC).isoformat()
    pod = {
        "metadata": {"name": "demo-api-7c9d9f6f5d-abcde", "namespace": "demo"},
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "app",
                    "ready": True,
                    "restart_count": 5,
                    "state": {"running": {"startedAt": recent_start}},
                    "last_state": {"terminated": {"reason": "Error"}},
                }
            ],
        },
    }
    scanner.k8s = FakeKubernetesClient([pod])
    scanner.scan(resolve_missing=True)

    # Finding should NOT be resolved because it has been running for < 10 seconds
    assert len(storage.list_findings()) == 1


def test_scan_resolves_stably_running_restarted_pod(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage = Storage(settings.storage_path)
    scanner = Scanner(settings, storage)

    scanner.k8s = FakeKubernetesClient([_crashloop_pod()])
    scanner.scan()
    assert len(storage.list_findings()) == 1

    from datetime import UTC, datetime, timedelta
    stable_start = (datetime.now(UTC) - timedelta(seconds=15)).isoformat()
    pod = {
        "metadata": {"name": "demo-api-7c9d9f6f5d-abcde", "namespace": "demo"},
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "app",
                    "ready": True,
                    "restart_count": 5,
                    "state": {"running": {"startedAt": stable_start}},
                    "last_state": {"terminated": {"reason": "Error"}},
                }
            ],
        },
    }
    scanner.k8s = FakeKubernetesClient([pod])
    scanner.scan(resolve_missing=True)

    # Finding should be resolved because it has been running stably for >= 10 seconds
    assert len(storage.list_findings()) == 0


