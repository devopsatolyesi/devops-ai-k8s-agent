from app.rule_engine import analyze_pod, analyze_service


def pod_with_waiting(reason: str, restarts: int = 4) -> dict:
    return {
        "metadata": {"name": "demo", "namespace": "demo-broken-apps"},
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "app",
                    "restart_count": restarts,
                    "state": {"waiting": {"reason": reason}},
                    "last_state": {},
                }
            ],
        },
    }


def deployment_pod(name: str, reason: str, restarts: int = 4) -> dict:
    pod = pod_with_waiting(reason, restarts=restarts)
    pod["metadata"]["name"] = name
    pod["spec"] = {"containers": [{"name": "app", "resources": {"limits": {"memory": "32Mi"}}}]}
    return pod


def test_crashloopbackoff_rule() -> None:
    results = analyze_pod(
        pod_with_waiting("CrashLoopBackOff"),
        logs="cannot connect to database: connection refused",
        events=["Back-off restarting failed container"],
    )
    result = results[0]
    assert result.problem_type == "CrashLoopBackOff"
    assert result.severity == "High"
    assert any("connection refused" in item for item in result.evidence)


def test_imagepull_rule() -> None:
    results = analyze_pod(pod_with_waiting("ImagePullBackOff"))
    assert results[0].problem_type == "ImagePullBackOff"
    assert results[0].severity == "High"


def test_oomkilled_rule() -> None:
    pod = pod_with_waiting("")
    pod["status"]["container_statuses"][0]["last_state"] = {"terminated": {"reason": "OOMKilled"}}
    results = analyze_pod(pod)
    assert results[0].problem_type == "OOMKilled"
    assert results[0].severity == "Critical"


def test_running_pod_with_old_failed_scheduling_event_is_not_pending() -> None:
    pod = pod_with_waiting("")
    pod["status"]["container_statuses"][0]["state"] = {"running": {"started_at": "2026-05-23T00:00:00Z"}}
    results = analyze_pod(
        pod,
        events=[
            "FailedScheduling: 0/1 nodes are available: node was not ready",
            "Scheduled: Successfully assigned demo/demo to node",
            "Started: Started container app",
        ],
    )
    assert [result.problem_type for result in results] == []


def test_create_container_config_error_does_not_emit_generic_pending() -> None:
    pod = pod_with_waiting("CreateContainerConfigError", restarts=0)
    pod["status"]["phase"] = "Pending"
    results = analyze_pod(pod, events=['Failed: Error: configmap "missing-configmap" not found'])
    assert [result.problem_type for result in results] == ["CreateContainerConfigError"]


def test_running_ready_pod_with_old_probe_failure_event_does_not_emit_probe_failed() -> None:
    pod = pod_with_waiting("", restarts=0)
    pod["status"]["container_statuses"][0]["ready"] = True
    pod["status"]["container_statuses"][0]["state"] = {"running": {"started_at": "2026-05-24T00:00:00Z"}}
    results = analyze_pod(
        pod,
        events=[
            "Unhealthy: Readiness probe failed: Get http://10.0.0.1:8080/healthz: timeout",
            "Started: Started container app",
        ],
    )
    assert [result.problem_type for result in results] == []


def test_service_without_selector_does_not_emit_no_endpoints() -> None:
    service = {
        "metadata": {"name": "service-no-endpoints-demo", "namespace": "demo-broken-apps"},
        "spec": {
            "selector": None,
            "ports": [{"port": 80, "targetPort": 8080}],
        },
    }
    assert analyze_service(service, endpoints=None) == []


def test_demo_crashloop_gets_permanent_patch_fix() -> None:
    results = analyze_pod(
        deployment_pod("crashloop-demo-6ccff6785c-vs595", "CrashLoopBackOff"),
        logs="cannot connect to database: connection refused",
        events=["Back-off restarting failed container"],
    )
    assert results[0].proposed_fix is None
    assert results[0].safe_auto_fix is False
    assert results[0].needs_ai_analysis is True


def test_non_demo_crashloop_needs_ai_analysis_and_has_no_auto_fix() -> None:
    results = analyze_pod(
        deployment_pod("ai-analysis-demo-6ccff6785c-ab123", "CrashLoopBackOff"),
        logs="database authentication failed for user 'app'",
        events=["Back-off restarting failed container"],
    )
    result = results[0]
    assert result.proposed_fix is None
    assert result.safe_auto_fix is False
    assert result.needs_ai_analysis is True


def test_demo_imagepull_gets_permanent_image_patch() -> None:
    results = analyze_pod(deployment_pod("imagepull-demo-7fc976cc9f-d9zrj", "ImagePullBackOff", restarts=0))
    assert results[0].proposed_fix is None
    assert results[0].safe_auto_fix is False
    assert results[0].needs_ai_analysis is True


def test_demo_oomkilled_gets_permanent_patch_fix() -> None:
    pod = deployment_pod("oomkilled-demo-858b549467-j288h", "", restarts=2)
    pod["status"]["container_statuses"][0]["last_state"] = {"terminated": {"reason": "OOMKilled"}}
    results = analyze_pod(pod)
    assert results[0].proposed_fix is None
    assert results[0].safe_auto_fix is False
    assert results[0].needs_ai_analysis is True
