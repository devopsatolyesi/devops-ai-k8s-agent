from __future__ import annotations

import logging
import time
import uuid
from threading import Lock
from typing import Any

from app.ai_client import PioneerAIClient
from app.config import Settings
from app.k8s_client import KubernetesClient
from app.masking import mask_data
from app.metrics import metrics
from app.models import Finding, build_fingerprint, utc_now
from app.rule_engine import analyze_ingress, analyze_pod, analyze_service
from app.storage import Storage

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
POD_PROBLEM_ORDER = {
    "OOMKilled": 0,
    "CreateContainerConfigError": 1,
    "CreateContainerError": 2,
    "RunContainerError": 3,
    "ImagePullBackOff": 4,
    "ErrImagePull": 5,
    "CrashLoopBackOff": 6,
    "Pending": 7,
    "ProbeFailed": 8,
}

# Map volatile, flapping problem types to a stable category so a single unhealthy
# container keeps ONE fingerprint across scans. Kubernetes flaps the container
# waiting/termination reason for the SAME underlying issue (e.g.
# ErrImagePull <-> ImagePullBackOff, OOMKilled <-> CrashLoopBackOff). Including
# the raw reason in the fingerprint made each flap look like a new problem and
# falsely marked the previous one "resolved_manually" without any user action.
_POD_PROBLEM_CATEGORY = {
    "ErrImagePull": "ImagePull",
    "ImagePullBackOff": "ImagePull",
    "CreateContainerConfigError": "ContainerCreate",
    "CreateContainerError": "ContainerCreate",
    "RunContainerError": "ContainerCreate",
    "CrashLoopBackOff": "ContainerCrash",
    "OOMKilled": "ContainerCrash",
}


def _pod_problem_category(problem_type: str) -> str:
    """Stable fingerprint category for a pod problem type (collapses flapping states)."""
    return _POD_PROBLEM_CATEGORY.get(problem_type, problem_type)

# Re-run AI analysis when restart_count has grown by at least this factor since last analysis
_AI_REANALYSIS_RESTART_THRESHOLD = 1.5
_AI_REANALYSIS_MIN_NEW_RESTARTS = 5

# A container with a crash history must be observed running this long (seconds)
# before a missing finding is treated as recovered. Must cover a scan cycle so a
# crashlooper caught briefly running between restarts is not falsely "resolved".
_STABLE_RUNNING_SECONDS = 60


class Scanner:
    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self.k8s = KubernetesClient(settings.kube_context, settings.log_line_limit)
        self.ai = PioneerAIClient(settings)
        self._scan_lock = Lock()

    def scan(self, use_ai: bool = False, resolve_missing: bool = False) -> dict[str, Any]:
        self._scan_lock.acquire()
        try:
            return self._scan_locked(use_ai=use_ai, resolve_missing=resolve_missing)
        finally:
            self._scan_lock.release()

    def _scan_locked(self, use_ai: bool = False, resolve_missing: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        ai_calls_this_scan = 0
        findings: list[Finding] = []
        seen_fingerprints: set[str] = set()
        ai_enabled_for_scan = self.settings.ai_enabled and use_ai

        pods = self.k8s.list_pods()
        services = self.k8s.list_services()
        endpoints = self.k8s.list_endpoints()
        ingresses = self.k8s.list_ingresses()

        # ── Pods ──────────────────────────────────────────────────────────
        for pod in pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            namespace = metadata.get("namespace", "default")
            pod_name = metadata.get("name", "")
            container_statuses = status.get("container_statuses") or status.get("containerStatuses") or []

            # Analyse all containers, not just the first one
            analysed_containers: set[str] = set()
            for cs in container_statuses:
                container_name = cs.get("name") or None

                # Skip duplicate container names (shouldn't happen but guard anyway)
                if container_name in analysed_containers:
                    continue
                analysed_containers.add(container_name or "")

                logs = self.k8s.logs_for_pod(namespace, pod_name, container_name)
                events = self.k8s.events_for_pod(namespace, pod_name)

                # Analyse only the primary (most severe) rule per container
                pod_rules = self._select_primary_pod_rules(
                    analyze_pod(pod, logs=logs, events=events)
                )

                # Resolve workload owner from pod's ownerReferences for stable fingerprint
                owner_name = pod_name
                owner_refs = pod.get("metadata", {}).get("ownerReferences", [])
                if owner_refs:
                    for ref in owner_refs:
                        if ref.get("controller") is True and ref.get("kind") in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"):
                            owner_name = ref.get("name", pod_name)
                            break

                for rule in pod_rules:
                    # Use a stable category (not the flapping problem_type/reason) so the
                    # same unhealthy container keeps one fingerprint across scans.
                    fingerprint = build_fingerprint(
                        namespace, owner_name, container_name,
                        _pod_problem_category(rule.problem_type), "",
                    )
                    restart_count = max(
                        [int(c.get("restart_count", c.get("restartCount", 0)) or 0) for c in container_statuses]
                        or [0]
                    )

                    evidence = mask_data(
                        {
                            "namespace": namespace,
                            "pod": pod_name,
                            "container": container_name,
                            "spec": pod.get("spec", {}),
                            "status": status,
                            "events": events,
                            "logs_tail": logs,
                            "local_rule_result": rule.model_dump(),
                        }
                    )

                    # Record crash event for trend analysis
                    if rule.problem_type in {
                        "CrashLoopBackOff", "OOMKilled", "ImagePullBackOff",
                        "ErrImagePull", "CreateContainerConfigError",
                        "CreateContainerError", "RunContainerError",
                    }:
                        self.storage.record_crash_event(
                            fingerprint=fingerprint,
                            namespace=namespace,
                            pod_name=pod_name,
                            problem_type=rule.problem_type,
                            restart_count=restart_count,
                        )

                    # Enrich evidence with crash trend
                    trend = self.storage.get_crash_trend(fingerprint, hours=24)
                    evidence["crash_trend_24h"] = trend

                    finding = Finding(
                        id=str(uuid.uuid4()),
                        cluster_name=self.settings.cluster_name,
                        namespace=namespace,
                        resource_kind="Pod",
                        resource_name=pod_name,
                        pod_name=pod_name,
                        container_name=container_name,
                        problem_type=rule.problem_type,
                        severity=rule.severity,
                        restart_count=restart_count,
                        local_analysis=rule.model_dump(),
                        evidence=evidence,
                        recommended_actions=rule.recommended_actions,
                        commands_to_verify=rule.commands_to_verify,
                        confidence=rule.confidence,
                        rule_id=rule.rule_id,
                        rule_detected=True,
                        rule_confidence=rule.confidence,
                        rule_fix_available=bool(rule.proposed_fix) and rule.safe_auto_fix,
                        needs_ai_analysis=rule.needs_ai_analysis,
                        ai_can_auto_apply=rule.ai_can_auto_apply,
                        fingerprint=fingerprint,
                    )
                    stored = self._store_and_maybe_ai(finding, ai_calls_this_scan, use_ai=ai_enabled_for_scan)
                    findings.append(stored)
                    seen_fingerprints.add(fingerprint)
                    if stored.ai_used or stored.ai_error:
                        ai_calls_this_scan += 1

        # ── Services ──────────────────────────────────────────────────────
        endpoint_map = {
            (
                item.get("metadata", {}).get("namespace", "default"),
                item.get("metadata", {}).get("name", ""),
            ): item
            for item in endpoints
        }
        for service in services:
            namespace = service.get("metadata", {}).get("namespace", "default")
            name = service.get("metadata", {}).get("name", "")
            for rule in analyze_service(service, endpoint_map.get((namespace, name))):
                fingerprint = build_fingerprint(namespace, name, None, rule.problem_type, rule.reason)

                # Get service selector
                service_selector = service.get("spec", {}).get("selector", {})

                # Find matching pods
                pods_in_namespace = []
                matching_pods = []
                for p in pods:
                    if p.get("metadata", {}).get("namespace", "default") == namespace:
                        pod_info = {
                            "name": p.get("metadata", {}).get("name"),
                            "labels": p.get("metadata", {}).get("labels", {}),
                            "status": p.get("status", {}).get("phase")
                        }
                        pods_in_namespace.append(pod_info)

                        # Check if pod matches service selector
                        pod_labels = p.get("metadata", {}).get("labels", {})
                        if all(pod_labels.get(k) == v for k, v in service_selector.items()):
                            matching_pods.append(pod_info)

                finding = Finding(
                    id=str(uuid.uuid4()),
                    cluster_name=self.settings.cluster_name,
                    namespace=namespace,
                    resource_kind="Service",
                    resource_name=name,
                    problem_type=rule.problem_type,
                    severity=rule.severity,
                    local_analysis=rule.model_dump(),
                    evidence=mask_data(
                        {
                            "service": service,
                            "endpoints": endpoint_map.get((namespace, name)),
                            "local_rule_result": rule.model_dump(),
                            "pods_in_namespace": pods_in_namespace,
                            "matching_pods": matching_pods,
                            "selector": service_selector,
                        }
                    ),
                    recommended_actions=rule.recommended_actions,
                    commands_to_verify=rule.commands_to_verify,
                    confidence=rule.confidence,
                    rule_id=rule.rule_id,
                    rule_detected=True,
                    rule_confidence=rule.confidence,
                    rule_fix_available=bool(rule.proposed_fix) and rule.safe_auto_fix,
                    needs_ai_analysis=rule.needs_ai_analysis,
                    ai_can_auto_apply=rule.ai_can_auto_apply,
                    fingerprint=fingerprint,
                )
                stored = self._store_and_maybe_ai(finding, ai_calls_this_scan, use_ai=ai_enabled_for_scan)
                findings.append(stored)
                seen_fingerprints.add(fingerprint)
                if stored.ai_used or stored.ai_error:
                    ai_calls_this_scan += 1

        # ── Ingresses ─────────────────────────────────────────────────────
        services_by_namespace: dict[str, set[str]] = {}
        for svc in services:
            services_by_namespace.setdefault(
                svc.get("metadata", {}).get("namespace", "default"), set()
            ).add(svc.get("metadata", {}).get("name", ""))

        for ingress in ingresses:
            namespace = ingress.get("metadata", {}).get("namespace", "default")
            name = ingress.get("metadata", {}).get("name", "")
            for rule in analyze_ingress(ingress, services_by_namespace):
                fingerprint = build_fingerprint(namespace, name, None, rule.problem_type, rule.reason)
                finding = Finding(
                    id=str(uuid.uuid4()),
                    cluster_name=self.settings.cluster_name,
                    namespace=namespace,
                    resource_kind="Ingress",
                    resource_name=name,
                    problem_type=rule.problem_type,
                    severity=rule.severity,
                    local_analysis=rule.model_dump(),
                    evidence=mask_data({
                        "ingress": ingress,
                        "local_rule_result": rule.model_dump(),
                        "available_services_in_namespace": list(services_by_namespace.get(namespace, set()))
                    }),
                    recommended_actions=rule.recommended_actions,
                    commands_to_verify=rule.commands_to_verify,
                    confidence=rule.confidence,
                    rule_id=rule.rule_id,
                    rule_detected=True,
                    rule_confidence=rule.confidence,
                    rule_fix_available=bool(rule.proposed_fix) and rule.safe_auto_fix,
                    needs_ai_analysis=rule.needs_ai_analysis,
                    ai_can_auto_apply=rule.ai_can_auto_apply,
                    fingerprint=fingerprint,
                )
                stored = self._store_and_maybe_ai(finding, ai_calls_this_scan, use_ai=ai_enabled_for_scan)
                findings.append(stored)
                seen_fingerprints.add(fingerprint)
                if stored.ai_used or stored.ai_error:
                    ai_calls_this_scan += 1

        if resolve_missing:
            self._resolve_missing_findings(seen_fingerprints, findings)

        duration = time.monotonic() - started
        total = len(self.storage.list_findings())
        metrics.mark_scan(duration, total)
        logger.info("scan_completed duration=%.3f findings=%d", duration, total)
        return {
            "scanned_at": utc_now(),
            "new_or_updated_findings": len(findings),
            "total_findings": total,
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    def _resolve_missing_findings(self, seen_fingerprints: set[str], current_findings: list[Finding]) -> None:
        if not self.k8s.available:
            return
        
        # Get all current resources to check if they still exist/are healthy
        pods = {p.get("metadata", {}).get("name", ""): p for p in self.k8s.list_pods()}
        services = {
            (s.get("metadata", {}).get("namespace", "default"), s.get("metadata", {}).get("name", "")): s
            for s in self.k8s.list_services()
        }
        endpoints = {
            (ep.get("metadata", {}).get("namespace", "default"), ep.get("metadata", {}).get("name", "")): ep
            for ep in self.k8s.list_endpoints()
        }
        ingresses = {
            (i.get("metadata", {}).get("namespace", "default"), i.get("metadata", {}).get("name", "")): i
            for i in self.k8s.list_ingresses()
        }
        remediated = self.storage.get_remediated_fingerprints()
        
        for finding in self.storage.list_findings():
            if finding.fingerprint in seen_fingerprints:
                continue
            
            # If the finding is related to a Pod, check if the pod still exists and is not fully healthy yet
            if finding.resource_kind == "Pod":
                # Bypass checking container health if there is a newer, different active finding for this container in the current scan
                has_active_finding_for_container = any(
                    cf.namespace == finding.namespace
                    and cf.resource_name == finding.resource_name
                    and cf.container_name == finding.container_name
                    for cf in current_findings
                )
                if not has_active_finding_for_container:
                    pod = pods.get(finding.resource_name)
                    if pod:
                        status = pod.get("status", {})
                        container_statuses = status.get("container_statuses") or status.get("containerStatuses") or []
                        container_ok = False
                        for cs in container_statuses:
                            if cs.get("name") == finding.container_name:
                                state = cs.get("state", {})
                                waiting_reason = (state.get("waiting") or {}).get("reason", "")
                                terminated_reason = (state.get("terminated") or {}).get("reason", "")
                                last_state = cs.get("last_state") or cs.get("lastState") or {}
                                last_terminated_reason = (last_state.get("terminated") or {}).get("reason", "")

                                running_long_enough = True
                                if last_terminated_reason:
                                    running_state = state.get("running") or {}
                                    started_at = running_state.get("startedAt") or running_state.get("started_at")
                                    if started_at:
                                        try:
                                            from datetime import UTC, datetime
                                            ts_str = str(started_at).rstrip('Z')
                                            if 'T' in ts_str:
                                                dt = datetime.fromisoformat(ts_str).replace(tzinfo=UTC)
                                            else:
                                                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                                            # A container with a crash history must run stably for a
                                            # full scan cycle before we trust it as recovered. 10s let
                                            # crashloopers caught between restarts look "healthy".
                                            if (datetime.now(UTC) - dt).total_seconds() < _STABLE_RUNNING_SECONDS:
                                                running_long_enough = False
                                        except Exception:
                                            pass

                                # Only mark container as ok if:
                                # - it is ready, AND
                                # - has no waiting/terminated failure states, AND
                                # - has been running stably if it has a history of restarts
                                if (cs.get("ready") is True and
                                    not waiting_reason and
                                    not terminated_reason and
                                    running_long_enough):
                                    container_ok = True
                                break
                        if not container_ok:
                            # Do not resolve finding since the pod still exists and is not fully healthy/ready yet
                            continue

            elif finding.resource_kind == "Service":
                svc = services.get((finding.namespace, finding.resource_name))
                if svc:
                    # If the service exists, check if it has endpoints
                    ep = endpoints.get((finding.namespace, finding.resource_name))
                    subsets = ep.get("subsets") if ep else None
                    if not subsets:
                        # Still has no endpoints; do not resolve
                        continue

            elif finding.resource_kind == "Ingress":
                ing = ingresses.get((finding.namespace, finding.resource_name))
                if ing:
                    # Check if all referenced backend services exist in the namespace
                    rules = ing.get("spec", {}).get("rules", [])
                    backends_ok = True
                    referenced_services = []
                    
                    default_backend = ing.get("spec", {}).get("defaultBackend") or ing.get("spec", {}).get("default_backend")
                    if default_backend:
                        svc_name = default_backend.get("service", {}).get("name") or default_backend.get("serviceName")
                        if svc_name:
                            referenced_services.append(svc_name)
                    for r in rules:
                        paths = r.get("http", {}).get("paths", [])
                        for p in paths:
                            backend = p.get("backend", {})
                            svc_name = backend.get("service", {}).get("name") or backend.get("serviceName")
                            if svc_name:
                                referenced_services.append(svc_name)
                    
                    for svc_name in referenced_services:
                        if (finding.namespace, svc_name) not in services:
                            backends_ok = False
                            break
                    if not backends_ok:
                        # Referenced backend service still missing; do not resolve
                        continue
            
            finding.resolved = True
            import json
            if finding.status == "remediating" or finding.fingerprint in remediated:
                finding.status = "resolved_by_ai" if finding.ai_analysis else "resolved"
                if finding.fingerprint in remediated:
                    try:
                        remediated.discard(finding.fingerprint)
                        self.storage.save_setting("remediated_fingerprints", json.dumps(list(remediated)))
                    except Exception as e:
                        logger.error("Failed to remove remediated fingerprint: %s", e)
            else:
                finding.status = "resolved_manually"
            self.storage.upsert_finding(finding)

    def _should_rerun_ai(self, existing: Finding, current_restart_count: int) -> bool:
        """Return True if the finding warrants a fresh AI analysis."""
        if not existing.ai_used:
            return False  # was never analysed; normal path will handle it
        last_count = existing.last_restart_count_at_ai or 0
        new_restarts = current_restart_count - last_count
        if new_restarts < _AI_REANALYSIS_MIN_NEW_RESTARTS:
            return False
        if last_count == 0:
            return new_restarts >= _AI_REANALYSIS_MIN_NEW_RESTARTS
        growth = current_restart_count / last_count
        return growth >= _AI_REANALYSIS_RESTART_THRESHOLD

    def _store_and_maybe_ai(self, finding: Finding, ai_calls_this_scan: int, use_ai: bool = False) -> Finding:
        existing = self.storage.get_by_fingerprint(finding.fingerprint)
        rerun_ai = existing is not None and self._should_rerun_ai(existing, finding.restart_count)

        if existing and existing.ai_used and not rerun_ai:
            # AI already done and no trigger for re-analysis — just update metadata
            return self.storage.upsert_finding(finding)

        finding = self.storage.upsert_finding(finding)

        should_run_ai = (
            self.settings.ai_ready
            and finding.needs_ai_analysis
            and self.settings.severity_allows_ai(finding.severity)
            and ai_calls_this_scan < self.settings.ai_rate_limit_per_scan
            and (use_ai or rerun_ai)
        )

        if should_run_ai:
            metrics.ai_requests_total += 1
            ai_analysis, ai_error, audit_entry = self.ai.analyze(finding.evidence)
            if ai_error:
                metrics.ai_errors_total += 1

            # Append to audit history
            history_entry = audit_entry.model_dump()
            finding.ai_history = (finding.ai_history or []) + [history_entry]
            finding.ai_analysis = ai_analysis
            finding.ai_error = ai_error
            finding.ai_used = bool(ai_analysis)
            finding.last_restart_count_at_ai = finding.restart_count
            self.storage.upsert_finding(finding)

        return finding

    def _select_primary_pod_rules(self, rules: list[Any]) -> list[Any]:
        if len(rules) <= 1:
            return rules

        def sort_key(rule: Any) -> tuple[int, int, str]:
            return (
                SEVERITY_ORDER.get(rule.severity, 99),
                POD_PROBLEM_ORDER.get(rule.problem_type, 99),
                rule.problem_type,
            )

        return [sorted(rules, key=sort_key)[0]]
