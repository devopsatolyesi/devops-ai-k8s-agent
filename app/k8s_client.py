from __future__ import annotations

import datetime
import logging
from typing import Any

from app.masking import mask_data

logger = logging.getLogger(__name__)

# Workload kinds supported for patching and rollout restart
_PATCHABLE_KINDS = {"deployment", "statefulset", "daemonset", "service", "ingress", "configmap", "pod"}


class KubernetesClient:
    def __init__(self, kube_context: str | None = None, log_line_limit: int = 150) -> None:
        self.kube_context = kube_context
        self.log_line_limit = log_line_limit
        self.core = None
        self.networking = None
        self.apps = None
        self.available = False
        self._load()

    def _load(self) -> None:
        self.cluster_name = "unknown"
        try:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
                self.cluster_name = "in-cluster"
            except config.ConfigException:
                config.load_kube_config(context=self.kube_context)
                try:
                    _contexts, active_context = config.list_kube_config_contexts()
                    self.cluster_name = active_context.get("name", "unknown")
                except Exception:
                    self.cluster_name = self.kube_context or "unknown"

            self.core = client.CoreV1Api()
            self.networking = client.NetworkingV1Api()
            self.apps = client.AppsV1Api()
            self.available = True
        except Exception as exc:
            logger.warning("kubernetes_client_unavailable error=%s", str(exc))
            self.available = False
            self.cluster_name = "unavailable"

    # ── Static helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _to_dict(obj: Any) -> dict[str, Any]:
        if obj is None:
            return {}
        if hasattr(obj, "to_dict"):
            return mask_data(obj.to_dict())
        if isinstance(obj, dict):
            return mask_data(obj)
        return {}

    # ── Read operations ─────────────────────────────────────────────────────

    def list_pods(self) -> list[dict[str, Any]]:
        if not self.available or self.core is None:
            return []
        return [self._to_dict(item) for item in self.core.list_pod_for_all_namespaces().items]

    def list_services(self) -> list[dict[str, Any]]:
        if not self.available or self.core is None:
            return []
        return [self._to_dict(item) for item in self.core.list_service_for_all_namespaces().items]

    def list_endpoints(self) -> list[dict[str, Any]]:
        if not self.available or self.core is None:
            return []
        return [self._to_dict(item) for item in self.core.list_endpoints_for_all_namespaces().items]

    def list_ingresses(self) -> list[dict[str, Any]]:
        if not self.available or self.networking is None:
            return []
        return [self._to_dict(item) for item in self.networking.list_ingress_for_all_namespaces().items]

    def list_statefulsets(self) -> list[dict[str, Any]]:
        if not self.available or self.apps is None:
            return []
        return [self._to_dict(item) for item in self.apps.list_stateful_set_for_all_namespaces().items]

    def list_daemonsets(self) -> list[dict[str, Any]]:
        if not self.available or self.apps is None:
            return []
        return [self._to_dict(item) for item in self.apps.list_daemon_set_for_all_namespaces().items]

    def events_for_pod(self, namespace: str, pod_name: str) -> list[str]:
        if not self.available or self.core is None:
            return []
        try:
            events = self.core.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name}",
            ).items
            return [
                f"{event.reason}: {event.message}"
                for event in sorted(events, key=lambda item: str(item.last_timestamp or item.event_time or ""))
            ][-20:]
        except Exception as exc:
            logger.warning("pod_events_failed namespace=%s pod=%s error=%s", namespace, pod_name, str(exc))
            return []

    def logs_for_pod(self, namespace: str, pod_name: str, container: str | None = None) -> str:
        if not self.available or self.core is None:
            return ""
        try:
            return self.core.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=self.log_line_limit,
                timestamps=True,
            )
        except Exception as exc:
            logger.info("pod_logs_unavailable namespace=%s pod=%s error=%s", namespace, pod_name, str(exc))
            return ""

    def get_pod_workload_owner(self, pod_name: str, namespace: str) -> tuple[str, str]:
        """Resolve the workload owner (Kind, Name) of a pod.

        Returns ("Pod", pod_name) if it is a standalone pod.
        """
        if not self.available or self.core is None:
            return "Pod", pod_name

        try:
            pod = self.core.read_namespaced_pod(name=pod_name, namespace=namespace)
            owners = pod.metadata.owner_references
            if not owners:
                return "Pod", pod_name

            for owner in owners:
                if not owner.controller:
                    continue
                owner_kind = owner.kind
                owner_name = owner.name

                if owner_kind == "ReplicaSet":
                    try:
                        rs = self.apps.read_namespaced_replica_set(name=owner_name, namespace=namespace)
                        rs_owners = rs.metadata.owner_references
                        if rs_owners:
                            for rs_owner in rs_owners:
                                if rs_owner.controller:
                                    return rs_owner.kind, rs_owner.name
                        return "ReplicaSet", owner_name
                    except Exception:
                        parts = owner_name.split("-")
                        if len(parts) >= 2:
                            return "Deployment", "-".join(parts[:-1])
                        return "ReplicaSet", owner_name

                return owner_kind, owner_name

            return "Pod", pod_name
        except Exception as exc:
            logger.error("failed_to_resolve_pod_owner pod=%s namespace=%s error=%s", pod_name, namespace, str(exc))
            parts = pod_name.split("-")
            if len(parts) >= 3:
                return "Deployment", "-".join(parts[:-2])
            return "Pod", pod_name

    # ── Write operations ────────────────────────────────────────────────────

    def patch_resource(self, kind: str, name: str, namespace: str, patch_body: dict[str, Any]) -> tuple[bool, str | None]:
        """Apply a strategic-merge patch to a Kubernetes resource."""
        if not self.available:
            return False, "Kubernetes client is not available"
        try:
            kind_lower = kind.lower()
            if kind_lower not in _PATCHABLE_KINDS:
                logger.warning("unsupported_remediation_resource kind=%s", kind)
                return False, f"Unsupported resource kind: {kind}"

            if kind_lower == "deployment":
                self.apps.patch_namespaced_deployment(name=name, namespace=namespace, body=patch_body)
            elif kind_lower == "statefulset":
                self.apps.patch_namespaced_stateful_set(name=name, namespace=namespace, body=patch_body)
            elif kind_lower == "daemonset":
                self.apps.patch_namespaced_daemon_set(name=name, namespace=namespace, body=patch_body)
            elif kind_lower == "service":
                self.core.patch_namespaced_service(name=name, namespace=namespace, body=patch_body)
            elif kind_lower == "ingress":
                self.networking.patch_namespaced_ingress(name=name, namespace=namespace, body=patch_body)
            elif kind_lower == "configmap":
                self.core.patch_namespaced_config_map(name=name, namespace=namespace, body=patch_body)
            elif kind_lower == "pod":
                # Standalone Pod recreation since spec modifications are mostly immutable
                pod = self.core.read_namespaced_pod(name=name, namespace=namespace)
                import copy
                new_pod_body = copy.deepcopy(pod)
                new_pod_body.metadata.resource_version = None
                new_pod_body.metadata.uid = None
                new_pod_body.metadata.creation_timestamp = None
                new_pod_body.status = None

                # Extract patch specification
                containers_patch = []
                if "spec" in patch_body and "template" in patch_body["spec"] and "spec" in patch_body["spec"]["template"]:
                    containers_patch = patch_body["spec"]["template"]["spec"].get("containers", [])
                elif "spec" in patch_body:
                    containers_patch = patch_body["spec"].get("containers", [])

                # Update container details in spec
                for c_patch in containers_patch:
                    c_name = c_patch.get("name")
                    for container in new_pod_body.spec.containers:
                        if container.name == c_name:
                            if "image" in c_patch:
                                container.image = c_patch["image"]
                            if "resources" in c_patch:
                                if not container.resources:
                                    container.resources = {}
                                r_patch = c_patch["resources"]
                                if "limits" in r_patch:
                                    if not container.resources.limits:
                                        container.resources.limits = {}
                                    container.resources.limits.update(r_patch["limits"])
                                if "requests" in r_patch:
                                    if not container.resources.requests:
                                        container.resources.requests = {}
                                    container.resources.requests.update(r_patch["requests"])
                            if "env" in c_patch:
                                container.env = c_patch["env"]
                            if "command" in c_patch:
                                container.command = c_patch["command"]
                            if "args" in c_patch:
                                container.args = c_patch["args"]

                # Delete the old pod
                self.core.delete_namespaced_pod(name=name, namespace=namespace, grace_period_seconds=0)
                # Wait for deletion
                import time
                for _ in range(10):
                    try:
                        self.core.read_namespaced_pod(name=name, namespace=namespace)
                        time.sleep(0.5)
                    except Exception:
                        break
                # Create the new pod
                self.core.create_namespaced_pod(namespace=namespace, body=new_pod_body)
                logger.info("recreated_pod_remediation name=%s namespace=%s", name, namespace)
                return True, None

            logger.info("patch_resource_ok kind=%s name=%s namespace=%s", kind, name, namespace)
            return True, None
        except Exception as exc:
            err_msg = str(exc)
            logger.error(
                "patch_resource_failed kind=%s name=%s namespace=%s error=%s",
                kind, name, namespace, err_msg,
            )
            return False, err_msg

    def create_configmap(self, name: str, namespace: str, data: dict[str, str]) -> bool:
        """Create a ConfigMap, or patch it if it already exists."""
        if not self.available or self.core is None:
            return False
        try:
            from kubernetes import client as k8s_client

            cm = k8s_client.V1ConfigMap(
                metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
                data={str(k): str(v) for k, v in (data or {}).items()},
            )
            try:
                self.core.create_namespaced_config_map(namespace=namespace, body=cm)
                logger.info("configmap_created name=%s namespace=%s", name, namespace)
            except Exception:
                self.core.patch_namespaced_config_map(name=name, namespace=namespace, body={"data": cm.data})
                logger.info("configmap_patched name=%s namespace=%s", name, namespace)
            return True
        except Exception as exc:
            logger.error("create_configmap_failed name=%s namespace=%s error=%s", name, namespace, str(exc))
            return False

    def rollout_restart(self, name: str, namespace: str, kind: str = "Deployment") -> bool:
        """Trigger a rolling restart by patching the pod template annotation.

        Supports Deployment, StatefulSet, and DaemonSet.
        """
        if not self.available or self.apps is None:
            return False
        try:
            restart_annotation = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": datetime.datetime.utcnow().isoformat()
                            }
                        }
                    }
                }
            }
            kind_lower = kind.lower()
            if kind_lower == "statefulset":
                self.apps.patch_namespaced_stateful_set(name=name, namespace=namespace, body=restart_annotation)
            elif kind_lower == "daemonset":
                self.apps.patch_namespaced_daemon_set(name=name, namespace=namespace, body=restart_annotation)
            else:
                # Default to Deployment
                self.apps.patch_namespaced_deployment(name=name, namespace=namespace, body=restart_annotation)

            logger.info("rollout_restart_triggered kind=%s name=%s namespace=%s", kind, name, namespace)
            return True
        except Exception as exc:
            logger.error(
                "rollout_restart_failed kind=%s name=%s namespace=%s error=%s",
                kind, name, namespace, str(exc),
            )
            return False
