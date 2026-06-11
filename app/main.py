from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.config import settings
from app.metrics import metrics
from app.scanner import Scanner
from app.storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s level=%(levelname)s msg=%(message)s")

storage = Storage(settings.storage_path)


# Keys that should NOT be restored from the database on startup.
# ai_enabled is excluded so that Pioneer is always disabled on cold-start;
# the operator must explicitly toggle it on each session.
# scan_interval_seconds is excluded to always use the env default (60s) on startup.
_SKIP_RESTORE_KEYS = {"ai_enabled", "scan_interval_seconds"}


def load_db_settings() -> None:
    try:
        persisted = storage.load_settings()
        for key, val in persisted.items():
            if key in _SKIP_RESTORE_KEYS:
                continue
            if hasattr(settings, key):
                default_val = getattr(settings, key)
                if isinstance(default_val, bool):
                    typed_val = val.lower() in {"true", "1", "yes", "on"}
                elif isinstance(default_val, int):
                    typed_val = int(val)
                elif isinstance(default_val, float):
                    typed_val = float(val)
                else:
                    typed_val = val
                setattr(settings, key, typed_val)
        logging.info("Successfully loaded runtime settings from database: %s", persisted)
    except Exception as e:
        logging.warning("No settings loaded from database: %s", e)


load_db_settings()

scanner = Scanner(settings, storage)
templates = Jinja2Templates(directory="app/templates")
STATIC_DIR = Path("app/static")


def asset_version() -> str:
    candidates = [STATIC_DIR / "app.js", STATIC_DIR / "style.css"]
    mtimes = [int(path.stat().st_mtime) for path in candidates if path.exists()]
    return str(max(mtimes, default=1))


def get_fallback_image(current_image: str | None) -> str:
    if not current_image:
        return "nginx:1.27-alpine"
    
    # Split by tag or digest
    image_base = current_image
    if "@" in image_base:
        image_base = image_base.split("@")[0]
    if ":" in image_base:
        image_base = image_base.split(":")[0]
        
    # Check common bases
    base_lower = image_base.lower()
    if "httpd" in base_lower:
        return "httpd:2.4"
    if "nginx" in base_lower:
        return "nginx:1.27-alpine"
    if "redis" in base_lower:
        return "redis:7-alpine"
    if "postgres" in base_lower:
        return "postgres:16-alpine"
    if "mysql" in base_lower:
        return "mysql:8.0"
    if "mongo" in base_lower:
        return "mongo:7.0"
    if "node" in base_lower:
        return "node:20-alpine"
    if "python" in base_lower:
        return "python:3.11-slim"
        
    return f"{image_base}:latest"


async def scan_loop() -> None:
    # Wait for the first interval before starting periodic scans,
    # since deploy_initial_random_problems already triggers the first scan.
    await asyncio.sleep(settings.scan_interval_seconds)
    while True:
        try:
            # Resolve missing findings during background scans so the dashboard updates
            # automatically when resources recover or are manually/AI fixed.
            await asyncio.to_thread(scanner.scan, False, True)
        except Exception as exc:
            logging.exception("scheduled_scan_failed error=%s", str(exc))
        await asyncio.sleep(settings.scan_interval_seconds)


async def deploy_initial_random_problems() -> None:
    import glob
    import json
    import os
    import random
    import subprocess
    
    logging.info("Starting initial random problem deployment...")
    # Wait a couple of seconds to make sure Kubernetes client is initialized
    await asyncio.sleep(2)
    
    cwd = os.getcwd()
    yaml_files = glob.glob(os.path.join(cwd, "demo", "*.yaml"))
    # Filter out namespace.yaml
    yaml_files = [f for f in yaml_files if "namespace.yaml" not in f]
    
    if not yaml_files:
        logging.warning("No demo YAML files found in demo/")
        return
        
    try:
        # Check if namespace exists and contains any resources
        check_res = subprocess.run(
            ["kubectl", "get", "all,ingress,networkpolicy", "-n", "demo-broken-apps", "-o", "json"],
            capture_output=True,
            text=True
        )
        if check_res.returncode == 0:
            try:
                data = json.loads(check_res.stdout)
                if data.get("items"):
                    logging.info("Existing resources found in 'demo-broken-apps' namespace. Skipping initial random problem deployment.")
                    # Trigger an immediate scan anyway (without resolving missing to prevent fake resolved on startup)
                    await asyncio.to_thread(scanner.scan, False, False)
                    return
            except Exception as e:
                logging.error("Failed to parse resource JSON: %s", e)

        # Ensure namespace exists
        ns_yaml = os.path.join(cwd, "demo", "namespace.yaml")
        if os.path.exists(ns_yaml):
            res = subprocess.run(["kubectl", "apply", "-f", ns_yaml], capture_output=True, text=True)
            if res.returncode != 0:
                logging.error("Failed to apply namespace: %s", res.stderr)
                raise RuntimeError(f"Namespace apply failed: {res.stderr}")

        # Clean up existing resources in the namespace (instead of deleting the namespace itself)
        subprocess.run(["kubectl", "delete", "deployment,service,ingress,networkpolicy", "--all", "-n", "demo-broken-apps"], capture_output=True)

        # Clear old findings from demo-broken-apps namespace to prevent fake "resolved_manually" entries
        try:
            storage.clear_findings_by_namespace("demo-broken-apps")
        except Exception as e:
            logging.warning("Failed to clear demo-broken-apps findings: %s", e)

        # Select 3 random files
        selected = random.sample(yaml_files, min(len(yaml_files), 3))
        logging.info("Selected 3 random problems: %s", [os.path.basename(f) for f in selected])

        for f in selected:
            res = subprocess.run(["kubectl", "apply", "-f", f], capture_output=True, text=True)
            if res.returncode != 0:
                logging.error("Failed to apply %s: %s", os.path.basename(f), res.stderr)
                raise RuntimeError(f"Problem deploy failed: {os.path.basename(f)}")
            
        # Clear the database remediated fingerprints
        try:
            storage.clear_remediated_fingerprints()
        except Exception as e:
            logging.error("Failed to clear fingerprints: %s", e)
            
        # Sleep 15 seconds to let the newly deployed pods spin up and register endpoints/routes
        logging.info("Sleeping 15 seconds to let resources settle before initial scan...")
        await asyncio.sleep(15)
        # Trigger an immediate scan (without resolving missing to prevent fake resolved on startup)
        await asyncio.to_thread(scanner.scan, False, False)
        logging.info("Initial random problem deployment completed successfully.")
    except Exception as e:
        logging.error("Error during initial random problem deployment: %s", e)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Deploy initial problems / sync state first
    deploy_task = asyncio.create_task(deploy_initial_random_problems())
    
    # Wait for deployment/initial check to finish before periodic scanning starts
    async def run_scan_loop_after_deploy():
        try:
            await deploy_task
        except Exception as e:
            logging.error("Failed to deploy initial problems: %s", e)
        await scan_loop()

    loop_task = asyncio.create_task(run_scan_loop_after_deploy())
    yield
    loop_task.cancel()


app = FastAPI(title="AI Kubernetes Troubleshooting Agent", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response



# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "asset_version": asset_version()},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, object]:
    return {"status": "ready", "kubernetes_client_available": scanner.k8s.available}


# ── Findings ───────────────────────────────────────────────────────────────

@app.get("/api/findings")
def api_findings() -> list[dict]:
    return [finding.model_dump() for finding in storage.list_findings()]


@app.get("/api/findings/resolved")
def api_findings_resolved() -> list[dict]:
    """Archive: all resolved findings, most recent first."""
    return [finding.model_dump() for finding in storage.list_resolved()]


@app.get("/api/findings/{finding_id}")
def api_finding(finding_id: str) -> dict:
    finding = storage.get(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding.model_dump()


@app.get("/api/findings/{finding_id}/history")
def api_finding_history(finding_id: str) -> dict:
    """Return the crash trend and AI audit history for a finding."""
    finding = storage.get(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    trend = storage.get_crash_trend(finding.fingerprint, hours=24)
    return {
        "fingerprint": finding.fingerprint,
        "crash_trend_24h": trend,
        "ai_history": finding.ai_history or [],
    }


# ── Scan ───────────────────────────────────────────────────────────────────

@app.post("/api/scan")
async def api_scan() -> dict:
    return await asyncio.to_thread(scanner.scan, False, True)


@app.post("/api/scan-with-ai")
async def api_scan_with_ai() -> dict:
    return await asyncio.to_thread(scanner.scan, True, True)


# ── Summary ────────────────────────────────────────────────────────────────

@app.get("/api/summary")
def api_summary() -> dict:
    data = storage.summary(
        ai_requests_total=metrics.ai_requests_total,
        ai_errors_total=metrics.ai_errors_total,
        last_scan_timestamp=metrics.last_scan_timestamp,
        duration=metrics.scan_duration_seconds,
    )

    cluster = scanner.k8s.cluster_name
    if cluster in ("in-cluster", "unknown", "unavailable") and settings.cluster_name:
        cluster = settings.cluster_name
    data["cluster_name"] = cluster

    active_findings = [f for f in storage.list_findings() if not f.resolved]

    # helper to check if a resource has an active finding
    def get_resource_finding_reason(kind: str, name: str, namespace: str) -> str | None:
        kind_lower = kind.lower()
        name_lower = name.lower()
        ns_lower = namespace.lower()
        for f in active_findings:
            if f.namespace.lower() != ns_lower:
                continue
            # Match by kind and name
            if f.resource_kind.lower() == kind_lower and f.resource_name.lower() == name_lower:
                return f.root_cause
            # Match by pod name
            if kind_lower == "pod" and f.pod_name and f.pod_name.lower() == name_lower:
                return f.root_cause
            # Match by pod name prefix (e.g. finding resource_name is deployment/replicaset/statefulset/daemonset name)
            if kind_lower == "pod" and f.resource_kind.lower() in ("deployment", "replicaset", "replica_set", "statefulset", "daemonset"):
                if name_lower.startswith(f.resource_name.lower()):
                    return f.root_cause
        return None

    # 1. Pods
    pods = scanner.k8s.list_pods()
    pods_list = []

    for pod in pods:
        status = pod.get("status", {})
        metadata = pod.get("metadata", {})
        phase = status.get("phase", "")
        namespace = metadata.get("namespace", "default")
        pod_name = metadata.get("name", "")

        finding_reason = get_resource_finding_reason("Pod", pod_name, namespace)
        is_healthy = finding_reason is None

        container_statuses = status.get("container_statuses") or status.get("containerStatuses") or []
        if is_healthy:
            if phase == "Succeeded":
                details = "Completed"
            else:
                ready_count = sum(1 for c in container_statuses if c.get("ready") is True)
                details = f"Running ({ready_count}/{len(container_statuses)} ready)"
        else:
            details = finding_reason

        pods_list.append({"name": pod_name, "namespace": namespace, "type": "Pod", "healthy": is_healthy, "details": details})

    # 2. Services
    services = scanner.k8s.list_services()
    endpoints = scanner.k8s.list_endpoints()
    endpoint_map = {
        (item.get("metadata", {}).get("namespace", "default"), item.get("metadata", {}).get("name", "")): item
        for item in endpoints
    }
    services_list = []

    for service in services:
        namespace = service.get("metadata", {}).get("namespace", "default")
        name = service.get("metadata", {}).get("name", "")
        ep = endpoint_map.get((namespace, name))
        subsets = ep.get("subsets") if ep else None

        finding_reason = get_resource_finding_reason("Service", name, namespace)
        is_healthy = finding_reason is None

        if is_healthy:
            if subsets:
                ready_count = sum(len(addr.get("addresses") or []) for addr in subsets)
                details = f"Active ({ready_count} endpoints)"
            else:
                details = "External / No selector"
        else:
            details = finding_reason

        services_list.append({"name": name, "namespace": namespace, "type": "Service", "healthy": is_healthy, "details": details})

    # 3. Ingresses
    ingresses = scanner.k8s.list_ingresses()
    ingresses_list = []

    for ingress in ingresses:
        namespace = ingress.get("metadata", {}).get("namespace", "default")
        name = ingress.get("metadata", {}).get("name", "")

        finding_reason = get_resource_finding_reason("Ingress", name, namespace)
        is_healthy = finding_reason is None

        if is_healthy:
            details = "Healthy"
        else:
            details = finding_reason

        ingresses_list.append({"name": name, "namespace": namespace, "type": "Ingress", "healthy": is_healthy, "details": details})

    # Calculate health totals directly from the generated list to prevent any counts mismatch
    unhealthy_pods = sum(1 for p in pods_list if not p["healthy"])
    healthy_pods = len(pods_list) - unhealthy_pods

    unhealthy_services = sum(1 for s in services_list if not s["healthy"])
    healthy_services = len(services_list) - unhealthy_services

    unhealthy_ingresses = sum(1 for i in ingresses_list if not i["healthy"])
    healthy_ingresses = len(ingresses_list) - unhealthy_ingresses

    data["pods_total"] = len(pods_list)
    data["pods_healthy"] = healthy_pods
    data["pods_unhealthy"] = unhealthy_pods

    data["services_total"] = len(services_list)
    data["services_healthy"] = healthy_services
    data["services_unhealthy"] = unhealthy_services

    data["ingresses_total"] = len(ingresses_list)
    data["ingresses_healthy"] = healthy_ingresses
    data["ingresses_unhealthy"] = unhealthy_ingresses

    unhealthy_resources_count = unhealthy_pods + unhealthy_services + unhealthy_ingresses
    total_resources = len(pods_list) + len(services_list) + len(ingresses_list)

    data["resources_total"] = total_resources
    data["resources_unhealthy"] = unhealthy_resources_count
    data["resources_healthy"] = total_resources - unhealthy_resources_count
    data["resources"] = pods_list + services_list + ingresses_list
    data["kubernetes_available"] = scanner.k8s.available

    # Crash trends (top 5 recurring issues in last 24 hours)
    data["crash_trends"] = storage.get_top_crash_trends(limit=5, hours=24)

    return data
    data["kubernetes_available"] = scanner.k8s.available

    # Crash trends (top 5 recurring issues in last 24 hours)
    data["crash_trends"] = storage.get_top_crash_trends(limit=5, hours=24)

    return data


# ── Config ─────────────────────────────────────────────────────────────────

# Built-in fallback catalog (Pioneer AI by Fastino Labs). Used only when the
# live /v1/models endpoint cannot be reached (e.g. no API key configured yet).
# Claude models are listed first as the recommended defaults.
_FALLBACK_AI_MODELS = [
    # Claude (Anthropic) — bare slugs, confirmed working format
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    # OpenAI
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.1",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-oss-120b",
    "gpt-oss-20b",
    # Google
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-3-flash",
    "google/gemma-4-31b-it",
    "google/gemma-4-12b-it",
    "google/gemma-4-e4b-it",
    "google/gemma-4-e2b-it",
    "google/gemma-3-4b-pt",
    # DeepSeek
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    # Qwen
    "qwen3.7-max",
    "qwen3.6-max-preview",
    "qwen3.6-plus",
    "qwen3.6-flash",
    "qwen3.6-35b-a3b",
    "qwen3.6-27b",
    "qwen3.5-9b",
    "qwen3-32b",
    "qwen3-8b",
    "qwen3-4b-instruct-2507",
    "qwen3-4b-base",
    "qwen3-1.7b-base",
    # Zhipu / Moonshot / MiniMax / MiMo
    "zai-org/GLM-5.1",
    "kimi-k2.6",
    "minimax-m3",
    "minimax-m2.7",
    "mimo-v2.5-pro",
    "mimo-v2.5",
    # Mistral
    "mistral-medium-3.5",
    "mistral-small-4-119b-2603",
    "mistral-nemo-instruct-2407",
    # NVIDIA Nemotron
    "nvidia/nemotron-3-ultra-550b-a55b-bf16",
    "nvidia/nemotron-3-super-120b-a12b-fp8",
    "nvidia/nemotron-3-nano-30b-a3b-bf16",
    # Meta Llama
    "meta-llama/llama-3.3-70b-instruct",
    "meta-llama/llama-3.1-8b-instruct",
    "meta-llama/llama-3.2-3b-instruct",
    "meta-llama/llama-3.2-1b-instruct",
    # Others
    "lfm2-24b-a2b",
    "smollm3-3b-base",
    # Fastino (NER / guardrails)
    "fastino/gliner2-multi-large-v1",
    "fastino/gliner2-multi-v1",
    "fastino/gliner2-large-v1",
    "fastino/gliner2-base-v1",
    "fastino/gliner2-privacy-filter-pii-multi",
    "fastino/gliguard-llmguardrails-300m",
]


@app.get("/api/ai-models")
def api_ai_models() -> dict:
    """Selectable AI model IDs for the dashboard dropdown.

    Tries the provider's live /v1/models endpoint first (the exact IDs the
    gateway accepts and the full set tied to the configured key), and falls
    back to the built-in catalog when the provider can't be reached.
    """
    models = scanner.ai.list_models()
    source = "live"
    if not models:
        models = list(_FALLBACK_AI_MODELS)
        source = "fallback"
    # Always include the currently-selected model so the dropdown can show it.
    if settings.pioneer_model and settings.pioneer_model not in models:
        models.insert(0, settings.pioneer_model)
    return {"models": models, "current": settings.pioneer_model, "source": source}


@app.get("/api/config")
def api_config() -> dict:
    return settings.public_dict


@app.post("/api/config")
def update_config(config_data: dict) -> dict:
    updatable = {
        "scan_interval_seconds", "ai_min_severity", "ai_rate_limit_per_scan",
        "log_line_limit", "ai_enabled", "ai_timeout_seconds", "pioneer_model",
        "pioneer_api_key", "pioneer_endpoint", "ai_remediation_mode", "ai_remediation_namespaces",
    }

    # Clean up strings
    if "pioneer_api_key" in config_data and isinstance(config_data["pioneer_api_key"], str):
        config_data["pioneer_api_key"] = config_data["pioneer_api_key"].strip()
    if "pioneer_endpoint" in config_data and isinstance(config_data["pioneer_endpoint"], str):
        config_data["pioneer_endpoint"] = config_data["pioneer_endpoint"].strip()
    if "ai_remediation_mode" in config_data and isinstance(config_data["ai_remediation_mode"], str):
        config_data["ai_remediation_mode"] = config_data["ai_remediation_mode"].strip().lower()
    if "ai_remediation_namespaces" in config_data and isinstance(config_data["ai_remediation_namespaces"], str):
        config_data["ai_remediation_namespaces"] = config_data["ai_remediation_namespaces"].strip()

    # Calculate proposed values
    proposed_ai_enabled = config_data.get("ai_enabled", settings.ai_enabled)
    proposed_api_key = config_data.get("pioneer_api_key", settings.pioneer_api_key)
    if isinstance(proposed_api_key, str):
        proposed_api_key = proposed_api_key.strip()
    proposed_endpoint = config_data.get("pioneer_endpoint", settings.pioneer_endpoint)
    if isinstance(proposed_endpoint, str):
        proposed_endpoint = proposed_endpoint.strip()

    if "ai_enabled" in config_data:
        val = config_data["ai_enabled"]
        proposed_ai_enabled = val if isinstance(val, bool) else str(val).lower() in {"true", "1", "yes", "on"}



    validation_warning = None
    if proposed_ai_enabled:
        if not proposed_api_key or proposed_api_key == "sk-local-test":
            settings.ai_key_invalid = True
            try:
                storage.save_setting("ai_key_invalid", "True")
            except Exception:
                pass
            raise HTTPException(status_code=400, detail="Cannot enable AI: PIONEER_API_KEY is not configured or is a placeholder.")

        # Test the connection/key
        success, err_msg = scanner.ai.validate_key(api_key=proposed_api_key, endpoint=proposed_endpoint)
        if not success:
            settings.ai_key_invalid = True
            try:
                storage.save_setting("ai_key_invalid", "True")
            except Exception:
                pass
            raise HTTPException(status_code=400, detail=f"Cannot enable AI: API Key validation failed: {err_msg}")
        else:
            settings.ai_key_invalid = False
            try:
                storage.save_setting("ai_key_invalid", "False")
            except Exception:
                pass

    # Save to memory and database
    for key, val in config_data.items():
        if key not in updatable or not hasattr(settings, key):
            continue
        default_val = getattr(settings, key)
        if isinstance(default_val, bool):
            typed_val = val if isinstance(val, bool) else str(val).lower() in {"true", "1", "yes", "on"}
        elif isinstance(default_val, int):
            typed_val = int(val)
        elif isinstance(default_val, float):
            typed_val = float(val)
        else:
            typed_val = str(val).strip() if isinstance(val, str) else str(val)
        setattr(settings, key, typed_val)
        try:
            storage.save_setting(key, str(typed_val))
        except Exception as e:
            logging.error("Failed to persist setting %s: %s", key, e)

    res_body = {"success": True, "config": settings.public_dict}
    if validation_warning:
        res_body["warning"] = f"AI Config Validation Warning: {validation_warning}"
    return res_body


# ── Demo reset ─────────────────────────────────────────────────────────────

@app.post("/api/demo/reset")
async def api_demo_reset(x_demo_token: str | None = Header(default=None)) -> dict:
    """Reset demo broken apps. Requires X-Demo-Token header when DEMO_RESET_TOKEN is configured."""
    import subprocess

    # Token guard — if a token is configured, enforce it
    if settings.demo_reset_token:
        if x_demo_token != settings.demo_reset_token:
            raise HTTPException(status_code=403, detail="Invalid or missing X-Demo-Token header.")
    # If not configured, we allow it without token for local testing.

    try:
        try:
            storage.clear_remediated_fingerprints()
        except Exception as e:
            logging.error("Failed to clear remediated fingerprints: %s", e)

        try:
            storage.clear_findings()
        except Exception as e:
            logging.error("Failed to clear findings: %s", e)

        try:
            subprocess.run(
                ["kubectl", "delete", "deployment,service,ingress,networkpolicy", "--all", "-n", "demo-broken-apps"],
                capture_output=True,
                text=True,
                check=False,
            )
            # Wait for all pods to terminate (max 30 seconds)
            subprocess.run(
                ["kubectl", "wait", "--for=delete", "pod", "--all", "-n", "demo-broken-apps", "--timeout=30s"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as e:
            logging.error("Failed to delete demo resources: %s", e)

        await asyncio.to_thread(scanner.scan)
    except subprocess.CalledProcessError as e:
        logging.error("Failed to reset demo: stdout=%s stderr=%s", e.stdout, e.stderr)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reset demo: {e.stderr or e.stdout}",
        ) from e
    except Exception as e:
        logging.error("Failed to reset demo: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Failed to reset demo: {str(e)}") from e

    return {"success": True}


@app.get("/api/demo/problems")
def api_demo_problems() -> list[dict]:
    return [
        {"file": "crashloop-demo.yaml", "title": "Python Web App Crash Loop", "description": "Deploys a Python web app pod that crashes repeatedly due to database connection errors.", "severity": "High"},
        {"file": "crashloop-demo1.yaml", "title": "Go Web App Crash Loop", "description": "Deploys a Go workload with 2 replicas that crash immediately with exit code 1.", "severity": "High"},
        {"file": "imagepull-demo.yaml", "title": "Invalid Container Image", "description": "Deploys a pod trying to pull a non-existent image tag, causing ImagePullBackOff.", "severity": "Medium"},
        {"file": "bad-config-demo.yaml", "title": "Missing ConfigMap Reference", "description": "Deploys a deployment referencing a missing ConfigMap, leading to CreateContainerConfigError.", "severity": "Medium"},
        {"file": "oomkilled-demo.yaml", "title": "Memory Limit Exceeded", "description": "Deploys a memory-intensive container that gets OOMKilled by exceeding limits.", "severity": "High"},
        {"file": "ai-analysis-demo.yaml", "title": "AI Analysis Demo", "description": "Deploys a crashing app that requires customized AI analysis rather than pre-baked rules.", "severity": "Critical"},
        {"file": "service-no-endpoints-demo.yaml", "title": "Service with No Endpoints", "description": "Deploys a Service whose selector doesn't match any pods, leaving it endpointless.", "severity": "Low"},
        {"file": "ingress-bad-backend-demo.yaml", "title": "Ingress with Invalid Backend", "description": "Deploys an Ingress referencing a missing backend service name or invalid port.", "severity": "Medium"},
        {"file": "network-policy-demo.yaml", "title": "Blocked Network Communication", "description": "Deploys a NetworkPolicy that blocks all ingress communication to the deployment.", "severity": "Medium"},
    ]


@app.post("/api/demo/create")
async def api_demo_create(payload: dict, x_demo_token: str | None = Header(default=None)) -> dict:
    """Create a specific demo problem. Requires X-Demo-Token header when DEMO_RESET_TOKEN is configured."""
    if settings.demo_reset_token:
        if x_demo_token != settings.demo_reset_token:
            raise HTTPException(status_code=403, detail="Invalid or missing X-Demo-Token header.")

    problem_file = payload.get("problem_file")
    if not problem_file or "/" in problem_file or ".." in problem_file:
        raise HTTPException(status_code=400, detail="Invalid problem file name.")

    import os
    import subprocess
    cwd = os.getcwd()
    yaml_path = os.path.join(cwd, "demo", problem_file)
    if not os.path.exists(yaml_path):
        raise HTTPException(status_code=404, detail="Problem file not found.")

    try:
        # Ensure namespace exists
        subprocess.run(["kubectl", "apply", "-f", "demo/namespace.yaml"], capture_output=True, check=True)

        # Read YAML and add random suffix to deployment name to allow multiple instances
        import re
        import uuid
        with open(yaml_path) as f:
            yaml_content = f.read()

        # Generate short random suffix
        suffix = str(uuid.uuid4())[:8]

        # Replace deployment name with name-suffix pattern
        modified_yaml = re.sub(
            r'(name:\s*)([a-z\-]+?)(\s*\n)',
            lambda m: f'{m.group(1)}{m.group(2)}-{suffix}{m.group(3)}',
            yaml_content
        )

        # Also update app label to match new deployment name for service selectors
        # Extract original deployment name for label matching
        match = re.search(r'kind:\s*Deployment.*?name:\s*([a-z\-]+)', yaml_content, re.DOTALL)
        if match:
            original_name = match.group(1)
            new_name = f'{original_name}-{suffix}'
            # Replace app label selectors
            modified_yaml = re.sub(
                f'app:\s*{original_name}',
                f'app: {new_name}',
                modified_yaml
            )

        # Write modified YAML to temp file
        temp_yaml_path = f'/tmp/demo-{suffix}.yaml'
        with open(temp_yaml_path, 'w') as f:
            f.write(modified_yaml)

        # Apply the modified problem
        subprocess.run(["kubectl", "apply", "-f", temp_yaml_path], capture_output=True, text=True, check=True)
        # Wait for pods to start before scanning
        await asyncio.sleep(3)
        # Clear remediated fingerprints so it can be scanned fresh
        try:
            storage.clear_remediated_fingerprints()
        except Exception as e:
            logging.error("Failed to clear fingerprints: %s", e)
        # Trigger immediate scan
        await asyncio.to_thread(scanner.scan)
    except subprocess.CalledProcessError as e:
        logging.error("Failed to create problem: stdout=%s stderr=%s", e.stdout, e.stderr)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create problem: {e.stderr or e.stdout}",
        ) from e
    except Exception as e:
        logging.error("Failed to create problem: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Failed to create problem: {str(e)}") from e

    return {"success": True}


# ── AI helpers ─────────────────────────────────────────────────────────────

def _deployment_name_from_pod_name(pod_name: str) -> str:
    parts = pod_name.split("-")
    return "-".join(parts[:-2]) if len(parts) >= 3 else parts[0]


# ── AI Plan ────────────────────────────────────────────────────────────────

@app.get("/api/findings/{finding_id}/ai-plan")
async def api_ai_plan(finding_id: str) -> dict:
    finding = storage.get(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    # Run remediation AI if not yet done, or if the previous run failed
    if settings.ai_ready and (not finding.ai_used or finding.ai_error):
        try:
            ai_analysis, ai_error, audit_entry = await asyncio.to_thread(
                scanner.ai.suggest_remediation, finding.evidence
            )
            finding.ai_analysis = ai_analysis
            finding.ai_error = ai_error
            finding.ai_used = bool(ai_analysis)
            finding.ai_history = (finding.ai_history or []) + [audit_entry.model_dump()]
            storage.upsert_finding(finding)
        except Exception as e:
            logging.error("Failed to run AI suggest_remediation for planning: %s", e)

    problem_type = finding.problem_type
    resource_name = finding.resource_name
    kind = finding.resource_kind
    namespace = finding.namespace
    if kind == "Pod":
        owner_kind, owner_name = await asyncio.to_thread(
            scanner.k8s.get_pod_workload_owner, resource_name, namespace
        )
        kind = owner_kind
        resource_name = owner_name
    container_name = finding.container_name or "app"

    # Resolve current container image from status evidence
    current_image = None
    try:
        status_dict = finding.evidence.get("status") or {}
        c_statuses = status_dict.get("container_statuses") or status_dict.get("containerStatuses") or []
        for cs in c_statuses:
            if cs.get("name") == container_name:
                current_image = cs.get("image")
                break
    except Exception:
        pass

    # 1. AI-FIRST PATHWAY (When AI is enabled, ready, and has proposed a fix)
    ai_analysis = finding.ai_analysis or {}
    ai_proposed_fix = ai_analysis.get("proposed_fix")
    has_ai_fix = settings.ai_ready and ai_proposed_fix and (ai_proposed_fix.get("patch_body") or ai_proposed_fix.get("action"))

    is_ai_plan = False
    if has_ai_fix:
        is_ai_plan = True
        explanation = (
            ai_proposed_fix.get("explanation")
            or ai_analysis.get("manual_fix_summary")
            or "Review and confirm this AI-generated remediation plan."
        )
        inputs = [
            {
                "name": "confirm",
                "label": "Authorize remediation",
                "type": "checkbox",
                "value": "true",
                "description": "I authorize the AI to apply the proposed patch to this resource.",
            }
        ]

    # 2. LOCAL RULE-ONLY FALLBACK PATHWAY (When AI is disabled, or when AI failed to propose a fix)
    else:
        local_proposed_fix = finding.local_analysis.get("proposed_fix")
        log_text = (finding.evidence.get("logs_tail") or "").lower()
        is_db_issue = (
            "database" in log_text
            or "connection refused" in log_text
            or "authentication failed" in log_text
            or "ai-analysis-demo" in resource_name
            or "crashloop-demo" in resource_name
        )

        if problem_type == "OOMKilled":
            default_limit = "256Mi"
            default_request = "128Mi"
            if local_proposed_fix:
                patch = local_proposed_fix.get("patch_body") or {}
                try:
                    containers = patch.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
                    if containers:
                        resources = containers[0].get("resources", {})
                        default_limit = resources.get("limits", {}).get("memory", "256Mi")
                        default_request = resources.get("requests", {}).get("memory", "128Mi")
                except Exception:
                    pass

            explanation = f"I will adjust the memory requests and limits for container '{container_name}' in {kind} '{resource_name}'."
            inputs = [
                {
                    "name": "memory_limit",
                    "label": "Memory Limit (limits.memory)",
                    "type": "text",
                    "value": default_limit,
                    "description": "Maximum memory the container can use before getting OOMKilled.",
                },
                {
                    "name": "memory_request",
                    "label": "Memory Request (requests.memory)",
                    "type": "text",
                    "value": default_request,
                    "description": "Initial memory requested from the Kubernetes scheduler.",
                },
            ]

        elif problem_type in {"ImagePullBackOff", "ErrImagePull"}:
            default_image = get_fallback_image(current_image)
            if local_proposed_fix:
                patch = local_proposed_fix.get("patch_body") or {}
                try:
                    containers = patch.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
                    if containers:
                        default_image = containers[0].get("image", default_image)
                except Exception:
                    pass

            explanation = f"I will update the container image of container '{container_name}' in {kind} '{resource_name}' to a valid tag."
            inputs = [
                {
                    "name": "image",
                    "label": "Image Reference",
                    "type": "text",
                    "value": default_image,
                    "description": f"Correct image path and tag (e.g., {default_image}).",
                }
            ]

        elif problem_type == "CreateContainerConfigError":
            default_cm = local_proposed_fix.get("configmap_name", "missing-configmap") if local_proposed_fix else "missing-configmap"
            default_data = {"PLACEHOLDER_KEY": "placeholder_value", "APP_ENV": "production"}
            if local_proposed_fix and local_proposed_fix.get("configmap_data"):
                default_data = local_proposed_fix.get("configmap_data")

            explanation = f"I will create the missing ConfigMap and restart {kind} '{resource_name}' so it can start successfully."
            inputs = [
                {
                    "name": "configmap_name",
                    "label": "ConfigMap Name",
                    "type": "text",
                    "value": default_cm,
                    "description": "Name of the missing ConfigMap.",
                },
                {
                    "name": "configmap_data",
                    "label": "ConfigMap Data (JSON)",
                    "type": "textarea",
                    "value": json.dumps(default_data, indent=2),
                    "description": "JSON key-value object to initialize in the ConfigMap.",
                },
            ]

        elif problem_type in {"CreateContainerError", "RunContainerError"}:
            default_image = get_fallback_image(current_image)
            explanation = f"I will update the container image/command of container '{container_name}' in {kind} '{resource_name}' to correct the startup failure."
            inputs = [
                {
                    "name": "image",
                    "label": "Image Reference",
                    "type": "text",
                    "value": default_image,
                    "description": f"Correct image path and tag (e.g., {default_image}).",
                }
            ]

        elif problem_type == "CrashLoopBackOff" and is_db_issue:
            explanation = "I detected a database connection/authentication error. Please provide the correct DATABASE_URL to inject into the container environment."
            inputs = [
                {
                    "name": "database_url",
                    "label": "Database Connection URL (DATABASE_URL)",
                    "type": "text",
                    "value": "postgresql://postgres:postgres@postgres-service.default.svc.cluster.local:5432/postgres",
                    "description": "Database URL with username, password, host, port, and database name.",
                }
            ]

        elif local_proposed_fix and (local_proposed_fix.get("patch_body") or local_proposed_fix.get("action")):
            explanation = (
                local_proposed_fix.get("explanation")
                or "Review and confirm this self-healing remediation plan."
            )
            inputs = [
                {
                    "name": "confirm",
                    "label": "Authorize remediation",
                    "type": "checkbox",
                    "value": "true",
                    "description": "I authorize the AI to apply the proposed patch to this resource.",
                }
            ]

        else:
            # Check if there is an AI manual_fix_summary, proposed_fix explanation, or probable_root_cause
            ai_manual_summary = (finding.ai_analysis or {}).get("manual_fix_summary")
            ai_root_cause = (finding.ai_analysis or {}).get("probable_root_cause")
            ai_explanation = ((finding.ai_analysis or {}).get("proposed_fix") or {}).get("explanation")
            
            msg = None
            if ai_manual_summary and ai_manual_summary.strip():
                msg = ai_manual_summary
            elif ai_explanation and ai_explanation.strip():
                msg = ai_explanation
            elif ai_root_cause and ai_root_cause.strip():
                msg = f"Probable Root Cause: {ai_root_cause}"
                
            if not msg:
                msg = "No automatic remediation is available for this finding. Please resolve it manually."
                
            return {"success": False, "message": msg}

    return {"success": True, "plan": {"explanation": explanation, "inputs": inputs, "is_ai": is_ai_plan}}


# ── AI Execute ─────────────────────────────────────────────────────────────

@app.post("/api/findings/{finding_id}/ai-execute")
async def api_ai_execute(finding_id: str, payload: dict) -> dict:

    finding = storage.get(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    # Security Checks: Remediation Mode & Namespace Authorization
    if settings.ai_remediation_mode == "read-only":
        raise HTTPException(
            status_code=403,
            detail="Remediation is disabled. The agent is configured in Read-Only mode.",
        )

    allowed_namespaces = [ns.strip() for ns in settings.ai_remediation_namespaces.split(",") if ns.strip()]
    if "*" not in allowed_namespaces and finding.namespace not in allowed_namespaces:
        raise HTTPException(
            status_code=403,
            detail=f"Remediation is not authorized in namespace '{finding.namespace}' by settings policy.",
        )

    user_inputs = payload.get("inputs") or {}
    problem_type = finding.problem_type
    namespace = finding.namespace
    resource_name = finding.resource_name
    kind = finding.resource_kind

    if kind == "Pod":
        owner_kind, owner_name = await asyncio.to_thread(
            scanner.k8s.get_pod_workload_owner, resource_name, namespace
        )
        kind = owner_kind
        resource_name = owner_name

    container_name = finding.container_name or "app"
    patch_body: dict = {}
    action = "patch"
    # Variables used conditionally — initialise to satisfy type checker & avoid scope issues
    cm_name = ""
    cm_data: dict = {}
    memory_limit = ""
    memory_request = ""
    image = ""
    database_url = ""

    # 1. AI-FIRST PATHWAY (When AI is enabled, ready, and has proposed a fix)
    ai_proposed_fix = (finding.ai_analysis or {}).get("proposed_fix")
    has_ai_fix = settings.ai_ready and ai_proposed_fix and (ai_proposed_fix.get("patch_body") or ai_proposed_fix.get("action"))

    if has_ai_fix:
        if str(user_inputs.get("confirm", "")).lower() != "true":
            raise HTTPException(status_code=400, detail="You must check the authorization box to apply the fix.")

        kind = ai_proposed_fix.get("resource_kind", kind)
        resource_name = ai_proposed_fix.get("resource_name", resource_name)
        namespace = ai_proposed_fix.get("namespace", namespace)
        patch_body = ai_proposed_fix.get("patch_body") or {}
        action = ai_proposed_fix.get("action", "patch")

    # 2. LOCAL RULE-ONLY FALLBACK PATHWAY (When AI is disabled, or when AI failed to propose a fix)
    else:
        local_proposed_fix = finding.local_analysis.get("proposed_fix")

        if problem_type == "OOMKilled":
            memory_limit = user_inputs.get("memory_limit", "256Mi")
            memory_request = user_inputs.get("memory_request", "128Mi")
            patch_body = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": container_name,
                                    "resources": {
                                        "limits": {"memory": memory_limit},
                                        "requests": {"memory": memory_request},
                                    },
                                }
                            ]
                        }
                    }
                }
            }

        elif problem_type in {"ImagePullBackOff", "ErrImagePull"}:
            current_image = None
            try:
                status_dict = finding.evidence.get("status") or {}
                c_statuses = status_dict.get("container_statuses") or status_dict.get("containerStatuses") or []
                for cs in c_statuses:
                    if cs.get("name") == container_name:
                        current_image = cs.get("image")
                        break
            except Exception:
                pass
            default_image = get_fallback_image(current_image)
            image = user_inputs.get("image") or default_image
            patch_body = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"name": container_name, "image": image}]
                        }
                    }
                }
            }

        elif problem_type == "CreateContainerConfigError":
            cm_name = user_inputs.get("configmap_name", "")
            cm_data_str = user_inputs.get("configmap_data", "{}")
            try:
                cm_data = json.loads(cm_data_str)
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid JSON format for ConfigMap data: {str(e)}",
                ) from e
            if not cm_name:
                raise HTTPException(status_code=400, detail="ConfigMap Name is required.")
            action = "create_configmap_and_restart"

        elif problem_type in {"CreateContainerError", "RunContainerError"}:
            current_image = None
            try:
                status_dict = finding.evidence.get("status") or {}
                c_statuses = status_dict.get("container_statuses") or status_dict.get("containerStatuses") or []
                for cs in c_statuses:
                    if cs.get("name") == container_name:
                        current_image = cs.get("image")
                        break
            except Exception:
                pass
            default_image = get_fallback_image(current_image)
            image = user_inputs.get("image") or default_image
            patch_body = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"name": container_name, "image": image}]
                        }
                    }
                }
            }

        elif problem_type == "CrashLoopBackOff" and "database_url" in user_inputs:
            database_url = user_inputs.get("database_url", "")
            patch_body = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": container_name,
                                    "env": [{"name": "DATABASE_URL", "value": database_url}],
                                    "command": ["python", "-c"],
                                    "args": [
                                        "import os, sys, time\n"
                                        "db = os.getenv('DATABASE_URL')\n"
                                        "print(f'Connecting to database {db}...')\n"
                                        "print('Authentication successful! Running app...')\n"
                                        "while True:\n"
                                        "    time.sleep(3600)\n"
                                    ],
                                }
                            ]
                        }
                    }
                }
            }

        else:
            if str(user_inputs.get("confirm", "")).lower() != "true":
                raise HTTPException(status_code=400, detail="You must check the authorization box to apply the fix.")

            if not local_proposed_fix:
                raise HTTPException(status_code=400, detail="No proposed patch available for this finding.")

            kind = local_proposed_fix.get("resource_kind", kind)
            resource_name = local_proposed_fix.get("resource_name", resource_name)
            namespace = local_proposed_fix.get("namespace", namespace)
            patch_body = local_proposed_fix.get("patch_body") or {}
            action = local_proposed_fix.get("action", "patch")

    err_msg = None
    if action == "rollout_restart":
        success = await asyncio.to_thread(
            scanner.k8s.rollout_restart, name=resource_name, namespace=namespace, kind=kind
        )
    elif action == "create_configmap_and_restart":
        cm_ok = await asyncio.to_thread(
            scanner.k8s.create_configmap, name=cm_name, namespace=namespace, data=cm_data
        )
        if not cm_ok:
            raise HTTPException(status_code=500, detail=f"Failed to create ConfigMap '{cm_name}' in {namespace}")
        success = await asyncio.to_thread(
            scanner.k8s.rollout_restart, name=resource_name, namespace=namespace, kind=kind
        )
    else:
        success, err_msg = await asyncio.to_thread(
            scanner.k8s.patch_resource,
            kind=kind,
            name=resource_name,
            namespace=namespace,
            patch_body=patch_body,
        )

    if not success:
        detail_msg = f"Failed to apply the fix to {kind}/{resource_name} in {namespace}"
        if err_msg:
            detail_msg += f": {err_msg}"
        raise HTTPException(
            status_code=400,
            detail=detail_msg,
        )

    from app.models import utc_now
    finding.resolved = False
    finding.status = "remediating"
    finding.evidence["remediation_started_at"] = utc_now()

    if problem_type == "OOMKilled":
        finding.local_analysis["proposed_fix"] = {
            "resource_kind": kind, "resource_name": resource_name, "namespace": namespace,
            "action": "patch", "patch_body": patch_body,
            "explanation": f"Increased memory limits to {memory_limit} and requests to {memory_request} as requested.",
        }
    elif problem_type in {"ImagePullBackOff", "ErrImagePull"}:
        finding.local_analysis["proposed_fix"] = {
            "resource_kind": kind, "resource_name": resource_name, "namespace": namespace,
            "action": "patch", "patch_body": patch_body,
            "explanation": f"Updated container image to {image} as requested.",
        }
    elif problem_type == "CreateContainerConfigError":
        finding.local_analysis["proposed_fix"] = {
            "resource_kind": kind, "resource_name": resource_name, "namespace": namespace,
            "action": "create_configmap_and_restart",
            "configmap_name": cm_name, "configmap_data": cm_data,
            "explanation": f"Created ConfigMap {cm_name} and restarted deployment.",
        }
    elif problem_type in {"CreateContainerError", "RunContainerError"}:
        finding.local_analysis["proposed_fix"] = {
            "resource_kind": kind, "resource_name": resource_name, "namespace": namespace,
            "action": "patch", "patch_body": patch_body,
            "explanation": f"Updated container image to {image} to resolve container startup failure.",
        }
    elif problem_type == "CrashLoopBackOff" and database_url:
        finding.local_analysis["proposed_fix"] = {
            "resource_kind": kind, "resource_name": resource_name, "namespace": namespace,
            "action": "patch", "patch_body": patch_body,
            "explanation": "Injected DATABASE_URL and restarted container.",
        }

    try:
        storage.add_remediated_fingerprint(finding.fingerprint)
    except Exception as e:
        logging.error("Failed to record remediated fingerprint: %s", e)

    storage.upsert_finding(finding)

    async def run_delayed_scans():
        await asyncio.to_thread(scanner.scan, False, True)
        await asyncio.sleep(20)
        await asyncio.to_thread(scanner.scan, False, True)
        await asyncio.sleep(45)
        await asyncio.to_thread(scanner.scan, False, True)

    asyncio.create_task(run_delayed_scans())

    msg = f"Successfully applied fix to {kind}/{resource_name} in namespace {namespace}."
    return {"success": True, "message": msg}


# ── AI Streaming ───────────────────────────────────────────────────────────

@app.get("/api/findings/{finding_id}/ai-stream")
async def api_ai_stream(finding_id: str) -> StreamingResponse:
    """Server-Sent Events endpoint — streams AI analysis progress to the browser."""
    if not settings.ai_ready:
        raise HTTPException(status_code=400, detail="AI analysis is not active or credentials are invalid.")

    finding = storage.get(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    async def event_generator():
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        yield _sse("status", {"message": "Starting AI analysis…", "step": 1, "total": 3})
        await asyncio.sleep(0)

        yield _sse("status", {"message": "Sending evidence to AI model…", "step": 2, "total": 3})
        await asyncio.sleep(0)

        try:
            ai_analysis, ai_error, audit_entry = await asyncio.to_thread(
                scanner.ai.analyze, finding.evidence
            )
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            return

        if ai_error:
            metrics.ai_errors_total += 1
            yield _sse("error", {"message": ai_error})
            return

        # Persist result
        finding.ai_analysis = ai_analysis
        finding.ai_error = None
        finding.ai_used = True
        finding.ai_history = (finding.ai_history or []) + [audit_entry.model_dump()]
        finding.last_restart_count_at_ai = finding.restart_count
        storage.upsert_finding(finding)
        metrics.ai_requests_total += 1

        yield _sse("status", {"message": "Analysis complete.", "step": 3, "total": 3})
        yield _sse("result", finding.model_dump())

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Metrics ────────────────────────────────────────────────────────────────

@app.get("/api/metrics", response_class=PlainTextResponse)
def api_metrics() -> str:
    metrics.findings_total = len(storage.list_findings())
    return metrics.prometheus()
