from __future__ import annotations

import re
from typing import Any

from app.models import RuleResult

LOG_HINTS = {
    "connection refused": "Application cannot reach a dependency.",
    "timeout": "A dependency or network path may be slow or unavailable.",
    "permission denied": "The container may be missing filesystem or runtime permissions.",
    "module not found": "The image may miss an application dependency.",
    "cannot connect": "The application cannot connect to a required service.",
    "panic": "The process is crashing inside application code.",
    "segmentation fault": "The process crashed at runtime.",
}


def _parse_memory_mi(mem_str: str) -> int:
    """Parse Kubernetes memory string (e.g. '128Mi', '1Gi', '512M') to MiB integer."""
    if not mem_str:
        return 128
    m = re.match(r'^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$', str(mem_str).strip(), re.IGNORECASE)
    if not m:
        return 128
    value = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit == "ki":
        return max(1, int(value / 1024))
    elif unit in ("mi", "m"):
        return int(value)
    elif unit in ("gi", "g"):
        return int(value * 1024)
    elif unit in ("ti", "t"):
        return int(value * 1024 * 1024)
    elif unit == "k":
        return max(1, int(value / 1024))
    else:  # raw bytes
        return max(1, int(value / (1024 * 1024)))



def _container_statuses(pod: dict[str, Any]) -> list[dict[str, Any]]:
    status = pod.get("status", {})
    return status.get("container_statuses") or status.get("containerStatuses") or []


def _pod_name(pod: dict[str, Any]) -> str:
    return pod.get("metadata", {}).get("name", "")


def _namespace(pod: dict[str, Any]) -> str:
    return pod.get("metadata", {}).get("namespace", "default")


def _deployment_name_from_pod_name(pod_name: str) -> str:
    parts = pod_name.split("-")
    return "-".join(parts[:-2]) if len(parts) >= 3 else parts[0]


def _waiting_reason(container_status: dict[str, Any]) -> str:
    state = container_status.get("state") or {}
    return (state.get("waiting") or {}).get("reason", "")


def _last_termination_reason(container_status: dict[str, Any]) -> str:
    state = container_status.get("last_state") or container_status.get("lastState") or {}
    return (state.get("terminated") or {}).get("reason", "")


def _restart_count(container_status: dict[str, Any]) -> int:
    return int(container_status.get("restart_count", container_status.get("restartCount", 0)) or 0)


def _waiting_reasons(pod: dict[str, Any]) -> set[str]:
    return {reason for status in _container_statuses(pod) if (reason := _waiting_reason(status))}


def _commands(namespace: str, pod: str, container: str | None = None) -> list[str]:
    base = [
        f"kubectl describe pod {pod} -n {namespace}",
        f"kubectl get events -n {namespace} --sort-by=.lastTimestamp",
    ]
    if container:
        base.append(f"kubectl logs {pod} -n {namespace} -c {container} --tail=200")
    else:
        base.append(f"kubectl logs {pod} -n {namespace} --tail=200")
    return base


def _demo_deployment_fix(
    *,
    namespace: str,
    deployment_name: str,
    container_name: str,
    problem_type: str,
    current_memory_str: str | None = None,
) -> dict[str, Any] | None:
    return None


def analyze_pod(pod: dict[str, Any], logs: str = "", events: list[str] | None = None) -> list[RuleResult]:
    events = events or []
    findings: list[RuleResult] = []
    namespace = _namespace(pod)
    pod_name = _pod_name(pod)
    phase = pod.get("status", {}).get("phase", "")
    event_text = "\n".join(events).lower()
    log_text = (logs or "").lower()
    waiting_reasons = _waiting_reasons(pod)
    container_statuses = _container_statuses(pod)
    pod_currently_unhealthy = (
        phase != "Running"
        or not container_statuses
        or any(container.get("ready") is not True for container in container_statuses)
    )

    # Build spec lookup by container name for resource/envFrom access
    spec_containers = {
        c.get("name", ""): c
        for c in (pod.get("spec", {}).get("containers") or [])
    }

    for container in container_statuses:
        container_name = container.get("name", "")
        container_spec = spec_containers.get(container_name, {})
        waiting = _waiting_reason(container)
        last_reason = _last_termination_reason(container)
        restarts = _restart_count(container)
        evidence = [
            f"pod={pod_name}",
            f"container={container_name}",
            f"phase={phase}",
            f"waiting_reason={waiting or 'none'}",
            f"last_termination_reason={last_reason or 'none'}",
            f"restart_count={restarts}",
        ]
        evidence.extend(events[:8])

        # ── CrashLoopBackOff ──────────────────────────────────────────────
        if waiting == "CrashLoopBackOff":
            matched_hints = [
                f"{phrase}: {meaning}" for phrase, meaning in LOG_HINTS.items() if phrase in log_text
            ]
            deployment_name = _deployment_name_from_pod_name(pod_name)
            crashloop_fix = _demo_deployment_fix(
                namespace=namespace,
                deployment_name=deployment_name,
                container_name=container_name,
                problem_type="CrashLoopBackOff",
            )
            findings.append(
                RuleResult(
                    rule_id="pod.crashloopbackoff",
                    problem_type="CrashLoopBackOff",
                    severity="High" if restarts >= 3 else "Medium",
                    reason="Container repeatedly crashes and Kubernetes is backing off restarts.",
                    evidence=evidence + matched_hints,
                    recommended_actions=[
                        "Read previous container logs to understand why it crashed.",
                        "Check env vars, ConfigMaps, Secrets, and startup command.",
                        "Verify the app can reach its dependencies (DB, cache, APIs).",
                        "Review liveness/readiness probes and startup timing.",
                        "Fix the root cause in code/config, then redeploy with the corrected image.",
                    ],
                    commands_to_verify=_commands(namespace, pod_name, container_name)
                    + [f"kubectl logs {pod_name} -n {namespace} -c {container_name} --previous --tail=200"],
                    confidence=0.86,
                    proposed_fix=crashloop_fix,
                    safe_auto_fix=bool(crashloop_fix),
                    needs_ai_analysis=crashloop_fix is None,
                    ai_can_auto_apply=False,
                )
            )

        # ── ImagePullBackOff / ErrImagePull ───────────────────────────────
        if waiting in {"ImagePullBackOff", "ErrImagePull"}:
            imagepull_fix = _demo_deployment_fix(
                namespace=namespace,
                deployment_name=_deployment_name_from_pod_name(pod_name),
                container_name=container_name,
                problem_type=waiting,
            )
            findings.append(
                RuleResult(
                    rule_id="pod.imagepull",
                    problem_type=waiting,
                    severity="High",
                    reason="Kubernetes cannot pull the configured container image.",
                    evidence=evidence,
                    recommended_actions=[
                        "Verify the image name and tag are exactly correct (case-sensitive).",
                        "Check if the container registry is accessible from the cluster.",
                        "Ensure imagePullSecrets are configured on the pod or ServiceAccount.",
                        "Test manually: docker pull <image> from a cluster node.",
                        "Update the Deployment with the correct image tag and redeploy.",
                    ],
                    commands_to_verify=_commands(namespace, pod_name, container_name) + [
                        f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.spec.containers[0].image}}'",
                        f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.spec.imagePullSecrets}}'",
                        f"kubectl get secret -n {namespace}",
                    ],
                    confidence=0.9,
                    proposed_fix=imagepull_fix,
                    safe_auto_fix=bool(imagepull_fix),
                    needs_ai_analysis=imagepull_fix is None,
                    ai_can_auto_apply=False,
                )
            )

        # ── OOMKilled ─────────────────────────────────────────────────────
        if pod_currently_unhealthy and last_reason == "OOMKilled":
            deployment_name = _deployment_name_from_pod_name(pod_name)

            # Dynamic fix: read current limit and double it (minimum 256Mi)
            current_limits = container_spec.get("resources", {}).get("limits", {})
            current_memory_str = current_limits.get("memory", "128Mi")
            current_mi = _parse_memory_mi(current_memory_str)
            new_mi = max(current_mi * 2, 256)
            new_memory = f"{new_mi}Mi"

            oom_fix = None

            findings.append(
                RuleResult(
                    rule_id="pod.oomkilled",
                    problem_type="OOMKilled",
                    severity="Critical",
                    reason="The container was terminated because it exceeded its memory limit.",
                    evidence=evidence,
                    recommended_actions=[
                        f"Current memory limit: {current_memory_str}. Proposed new limit: {new_memory}.",
                        "Profile the application for memory leaks (heap dumps, memory profiler).",
                        "Check if the workload is genuinely needing more memory or leaking.",
                        "Consider HorizontalPodAutoscaler if load is the cause.",
                    ],
                    commands_to_verify=_commands(namespace, pod_name, container_name)
                    + [f"kubectl top pod {pod_name} -n {namespace} --containers"],
                    confidence=0.93,
                    proposed_fix=oom_fix,
                    safe_auto_fix=bool(oom_fix),
                    needs_ai_analysis=oom_fix is None,
                    ai_can_auto_apply=False,
                )
            )

        # ── CreateContainerConfigError ────────────────────────────────────
        if waiting in {"CreateContainerConfigError", "CreateContainerError", "RunContainerError"}:
            deployment_name = _deployment_name_from_pod_name(pod_name)

            # Try to identify the missing ConfigMap/Secret from the pod spec
            env_from_raw = container_spec.get("env_from") or container_spec.get("envFrom") or []
            # Guard: entries can be dicts or (rarely) serialized strings — skip non-dicts
            env_from = [ef for ef in env_from_raw if isinstance(ef, dict)]
            missing_configmaps = [
                (ef.get("config_map_ref") or ef.get("configMapRef") or {}).get("name", "")
                for ef in env_from
                if isinstance((ef.get("config_map_ref") or ef.get("configMapRef")), dict)
                and (ef.get("config_map_ref") or ef.get("configMapRef") or {}).get("name")
            ]
            missing_secrets = [
                (ef.get("secret_ref") or ef.get("secretRef") or {}).get("name", "")
                for ef in env_from
                if isinstance((ef.get("secret_ref") or ef.get("secretRef")), dict)
                and (ef.get("secret_ref") or ef.get("secretRef") or {}).get("name")
            ]

            config_fix = None

            findings.append(
                RuleResult(
                    rule_id="pod.container_create_error",
                    problem_type=waiting,
                    severity="High",
                    reason="Container creation failed — a referenced ConfigMap, Secret, or volume is missing.",
                    evidence=evidence,
                    recommended_actions=[
                        f"Missing ConfigMaps: {missing_configmaps}"
                        if missing_configmaps
                        else "Check pod YAML for envFrom/volumeMount references.",
                        f"Missing Secrets: {missing_secrets}"
                        if missing_secrets
                        else "Verify all Secrets referenced in the pod exist.",
                        "Create the missing resources, then the pod will restart automatically.",
                        "After the fix, update placeholder values with real configuration.",
                    ],
                    commands_to_verify=_commands(namespace, pod_name, container_name) + [
                        f"kubectl get pod {pod_name} -n {namespace} -o yaml",
                        f"kubectl get configmap -n {namespace}",
                        f"kubectl get secret -n {namespace}",
                    ],
                    confidence=0.84,
                    proposed_fix=config_fix,
                    safe_auto_fix=bool(config_fix),
                    needs_ai_analysis=config_fix is None,
                    ai_can_auto_apply=False,
                )
            )


    explicit_waiting_problem = bool(
        waiting_reasons
        & {
            "CrashLoopBackOff",
            "ImagePullBackOff",
            "ErrImagePull",
            "CreateContainerConfigError",
            "CreateContainerError",
            "RunContainerError",
        }
    )
    has_failed_scheduling = "failedscheduling" in event_text
    scheduling_problem_is_current = phase == "Pending"
    if has_failed_scheduling and scheduling_problem_is_current and not explicit_waiting_problem:
        findings.append(
            RuleResult(
                rule_id="pod.pending_failed_scheduling",
                problem_type="Pending",
                severity="High",
                reason="Pod is pending, commonly because scheduling requirements cannot be satisfied.",
                evidence=[f"pod={pod_name}", f"phase={phase}"] + events[:10],
                recommended_actions=[
                    "Check node capacity, taints/tolerations, nodeSelector, affinity, and PVC binding.",
                    "Review resource requests against allocatable node resources.",
                ],
                commands_to_verify=[
                    f"kubectl describe pod {pod_name} -n {namespace}",
                    "kubectl get nodes -o wide",
                    f"kubectl get pvc -n {namespace}",
                ],
                confidence=0.78,
                needs_ai_analysis=True,
            )
        )

    if pod_currently_unhealthy and ("readiness probe failed" in event_text or "liveness probe failed" in event_text):
        findings.append(
            RuleResult(
                rule_id="pod.probe_failed",
                problem_type="ProbeFailed",
                severity="Medium",
                reason="Kubernetes probe checks are failing.",
                evidence=[f"pod={pod_name}", f"phase={phase}"] + events[:10],
                recommended_actions=[
                    "Verify probe path, port, scheme, and initialDelaySeconds.",
                    "Confirm the application listens on the expected interface and port.",
                ],
                commands_to_verify=_commands(namespace, pod_name),
                confidence=0.77,
                needs_ai_analysis=True,
            )
        )

    return findings


def _is_newly_created(metadata: dict[str, Any], grace_period_seconds: float = 60.0) -> bool:
    creation_ts = metadata.get("creationTimestamp")
    if not creation_ts:
        return False
    try:
        from datetime import UTC, datetime
        ts_str = creation_ts.rstrip('Z')
        if 'T' in ts_str:
            dt = datetime.fromisoformat(ts_str).replace(tzinfo=UTC)
        else:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return (now - dt).total_seconds() < grace_period_seconds
    except Exception:
        return False


def analyze_service(service: dict[str, Any], endpoints: dict[str, Any] | None) -> list[RuleResult]:
    metadata = service.get("metadata", {})
    if _is_newly_created(metadata, 60.0):
        return []
    namespace = metadata.get("namespace", "default")
    name = metadata.get("name", "")
    subsets = (endpoints or {}).get("subsets") or []
    if subsets:
        return []
    selector = service.get("spec", {}).get("selector") or {}
    if not selector:
        return []
    # Proposed fix: remove the broken selector so the Service stops looking for missing pods.
    # The operator should then update the selector to match real pod labels.
    service_fix = None
    return [
        RuleResult(
            rule_id="service.no_endpoints",
            problem_type="ServiceNoEndpoints",
            severity="High",
            reason="Service has no ready endpoints, so traffic will not reach pods.",
            evidence=[f"service={name}", f"namespace={namespace}", f"selector={selector}"],
            recommended_actions=[
                "Compare Service selector with Pod labels.",
                "Check whether matching Pods are Ready.",
                "Verify targetPort matches the container port.",
            ],
            commands_to_verify=[
                f"kubectl describe service {name} -n {namespace}",
                f"kubectl get endpoints {name} -n {namespace} -o yaml",
                f"kubectl get pods -n {namespace} --show-labels",
            ],
            confidence=0.88,
            proposed_fix=service_fix,
            safe_auto_fix=bool(service_fix),
            needs_ai_analysis=True,
            ai_can_auto_apply=False,
        )
    ]


def analyze_ingress(ingress: dict[str, Any], services_by_namespace: dict[str, set[str]]) -> list[RuleResult]:
    metadata = ingress.get("metadata", {})
    namespace = metadata.get("namespace", "default")
    name = metadata.get("name", "")
    missing: list[str] = []
    bad_paths: list[dict] = []  # track which paths have bad backends
    for rule in ingress.get("spec", {}).get("rules") or []:
        http = rule.get("http") or {}
        for path in http.get("paths") or []:
            service_name = (
                ((path.get("backend") or {}).get("service") or {}).get("name")
                or ((path.get("backend") or {}).get("serviceName"))
            )
            if service_name and service_name not in services_by_namespace.get(namespace, set()):
                missing.append(service_name)
                bad_paths.append(path)

    # Skip grace period if Ingress has invalid backends (this is always a bug, not a race condition)
    if not missing and _is_newly_created(metadata, 60.0):
        return []

    if not missing:
        return []

    # Build a proposed fix: patch every path that has a bad backend to use the first available service
    ingress_fix = None

    return [
        RuleResult(
            rule_id="ingress.bad_backend",
            problem_type="IngressBadBackend",
            severity="High",
            reason="Ingress references a backend Service that does not exist.",
            evidence=[f"ingress={name}", f"namespace={namespace}", f"missing_services={sorted(set(missing))}"],
            recommended_actions=[
                "Fix the Ingress backend service name.",
                "Verify the backend Service port and Ingress controller status.",
            ],
            commands_to_verify=[
                f"kubectl describe ingress {name} -n {namespace}",
                f"kubectl get svc -n {namespace}",
                "kubectl get ingressclass",
            ],
            confidence=0.9,
            proposed_fix=ingress_fix,
            safe_auto_fix=bool(ingress_fix),
            needs_ai_analysis=True,
            ai_can_auto_apply=False,
        )
    ]
