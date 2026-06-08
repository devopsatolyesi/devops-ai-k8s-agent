from __future__ import annotations

import json
import logging
import time
from collections import deque
from threading import Lock
from typing import Any

import httpx

from app.config import Settings
from app.masking import mask_data, mask_json_text
from app.models import AIAuditEntry

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Kubernetes SRE. Analyze the evidence and identify the root cause.
Verify the scanner's categorization against logs, events, and specs.
Use ONLY the provided evidence—do not invent facts.
Return valid JSON with: probable_root_cause, action_plan (flat string array), recommended_actions, prevention.
Keep each item to one short sentence (max 4 items per list)."""

REMEDIATION_SYSTEM_PROMPT = """You are a Kubernetes SRE. Propose the safest fix from the evidence.
Verify the scanner's categorization against logs/events/specs.
Always propose an automated fix (should_auto_apply=true, proposed_fix JSON).
If real pod/service names are in evidence (matching_pods, available_services_in_namespace), use them—never use placeholders.
Return valid JSON only. Do not include secrets.
Common fixes:
- OOMKilled: patch memory limits in Deployment spec.template.spec.containers[].resources.limits.memory
- ImagePullBackOff: patch container image tag
- ServiceNoEndpoints: use real matching pod labels from 'matching_pods' to fix selector
- IngressBadBackend: use real service name from 'available_services_in_namespace'
Keep response compact. action_plan is a flat string array (max 4 items).
"""

# ── Sliding-window global rate limiter ──────────────────────────────────────

class _SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter.

    Tracks the timestamps of recent calls and blocks new ones when the limit
    for the given *window_seconds* has been exceeded.
    """

    def __init__(self, max_calls: int, window_seconds: float = 60.0) -> None:
        self._max_calls = max_calls
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = Lock()

    def allow(self) -> bool:
        """Return True if a new call is permitted, recording it atomically."""
        now = time.monotonic()
        with self._lock:
            # Evict entries outside the window
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) >= self._max_calls:
                return False

            self._timestamps.append(now)
            return True

    @property
    def current_count(self) -> int:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return len(self._timestamps)


# Module-level singleton limiter; reset on import (intentional — lifetime is the process).
_global_limiter: _SlidingWindowRateLimiter | None = None


def _get_limiter(max_calls: int) -> _SlidingWindowRateLimiter:
    global _global_limiter
    if _global_limiter is None or _global_limiter._max_calls != max_calls:
        _global_limiter = _SlidingWindowRateLimiter(max_calls=max_calls, window_seconds=60.0)
    return _global_limiter


# ── AI Client ───────────────────────────────────────────────────────────────

class PioneerAIClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ── Prompt builders ────────────────────────────────────────────────────

    def build_user_prompt(self, evidence: dict[str, Any]) -> str:
        safe_evidence = mask_data(evidence)
        return (
            "Analyze this Kubernetes evidence. Return only valid JSON with keys: "
            "summary, probable_root_cause, severity, confidence, recommended_actions, "
            "commands_to_verify, prevention, junior_friendly_explanation, action_plan, "
            "manual_fix_summary, should_auto_apply, proposed_fix.\n\n"
            "Keep the JSON compact. Use short strings. Limit recommended_actions, commands_to_verify, "
            "prevention, and action_plan to at most 4 items each. action_plan must be a flat array of strings.\n\n"
            "If a simple patch to a Deployment, StatefulSet, DaemonSet, Service, ConfigMap, or Ingress can fix the issue, "
            "provide it in the 'proposed_fix' key with fields: "
            "'resource_kind' (string, e.g. 'Deployment'), "
            "'resource_name' (string), "
            "'namespace' (string), "
            "'patch_body' (object), "
            "and 'explanation' (string). If no simple patch can fix it safely, set 'proposed_fix' to null "
            "and 'should_auto_apply' to false.\n\n"
            "Questions to answer:\n"
            "- What happened?\n"
            "- Why did it happen?\n"
            "- How can we verify?\n"
            "- What is the safest action plan?\n"
            "- How can we prevent it?\n\n"
            f"Evidence:\n{mask_json_text(safe_evidence)}"
        )

    def build_remediation_prompt(self, evidence: dict[str, Any]) -> str:
        safe_evidence = mask_data(evidence)
        return (
            "Propose the safest Kubernetes remediation for this finding. Return only valid JSON with keys: "
            "summary, probable_root_cause, severity, confidence, recommended_actions, "
            "commands_to_verify, prevention, junior_friendly_explanation, action_plan, "
            "manual_fix_summary, should_auto_apply, proposed_fix.\n\n"
            "Use short strings. Limit recommended_actions, commands_to_verify, prevention, and action_plan "
            "to at most 4 items each. action_plan must be a flat array of strings.\n\n"
            "Be proactive: whenever the issue can be resolved by updating a Kubernetes resource (such as updating an image tag, "
            "increasing memory limits, creating a ConfigMap, adding environment variables, or running a rollout restart), "
            "always set should_auto_apply=true and populate proposed_fix. Use typical default/stable values or placeholders (e.g., 'change-me' "
            "for passwords/secrets) if specific values are not in the logs, so that the user gets a working template they can apply. "
            "proposed_fix must contain: resource_kind (Deployment|StatefulSet|DaemonSet|Service|ConfigMap|Ingress), "
            "resource_name, namespace, action (patch, rollout_restart, create_configmap_and_restart), "
            "patch_body (the exact K8s patch payload) when relevant, and explanation. "
            "Set should_auto_apply=false and proposed_fix=null only if the issue is a physical cluster error "
            "that cannot be resolved by resource modification.\n\n"
            f"Evidence:\n{mask_json_text(safe_evidence)}"
        )

    def build_request_body(self, evidence: dict[str, Any], remediation: bool = False) -> dict[str, Any]:
        user_content = (
            self.build_remediation_prompt(evidence)
            if remediation
            else self.build_user_prompt(evidence)
        )
        return {
            "model": self.settings.pioneer_model,
            "messages": [
                {"role": "system", "content": REMEDIATION_SYSTEM_PROMPT if remediation else SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "max_tokens": min(self.settings.pioneer_max_tokens, 2000),
            "temperature": self.settings.pioneer_temperature,
        }

    # ── JSON extraction helpers ────────────────────────────────────────────

    def _extract_json_object(self, content: str) -> str:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        first_brace = text.find("{")
        if first_brace == -1:
            return text

        in_string = False
        escape = False
        depth = 0
        start = None
        for idx, ch in enumerate(text[first_brace:], start=first_brace):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                if start is None:
                    start = idx
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start : idx + 1]
        return text[first_brace:]

    def _coerce_string_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            result: list[str] = []
            for item in value:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, dict):
                    parts = []
                    step = item.get("step")
                    action = item.get("action")
                    command = item.get("command")
                    purpose = item.get("purpose")
                    if step is not None:
                        parts.append(f"Step {step}")
                    if action:
                        parts.append(str(action))
                    if command:
                        parts.append(f"Command: {command}")
                    if purpose:
                        parts.append(f"Why: {purpose}")
                    if parts:
                        result.append(" | ".join(parts))
                    else:
                        result.append(json.dumps(item, ensure_ascii=True))
                else:
                    result.append(str(item))
            return result
        if isinstance(value, dict):
            result = []
            for key, items in value.items():
                if isinstance(items, list):
                    for item in items:
                        result.append(f"{key}: {item}")
                else:
                    result.append(f"{key}: {items}")
            return result
        return [str(value)]

    def _normalize_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["recommended_actions"] = self._coerce_string_list(payload.get("recommended_actions"))
        normalized["commands_to_verify"] = self._coerce_string_list(payload.get("commands_to_verify"))
        normalized["prevention"] = self._coerce_string_list(payload.get("prevention"))
        normalized["action_plan"] = self._coerce_string_list(payload.get("action_plan"))
        normalized["manual_fix_summary"] = str(payload.get("manual_fix_summary") or "")
        normalized["summary"] = str(payload.get("summary") or "")
        normalized["probable_root_cause"] = str(payload.get("probable_root_cause") or "")
        normalized["severity"] = str(payload.get("severity") or "")
        normalized["confidence"] = str(payload.get("confidence") or "")
        normalized["junior_friendly_explanation"] = str(payload.get("junior_friendly_explanation") or "")
        normalized["should_auto_apply"] = bool(payload.get("should_auto_apply"))
        if normalized.get("proposed_fix") is not None and not isinstance(normalized["proposed_fix"], dict):
            normalized["proposed_fix"] = None
        return normalized

    # ── HTTP request with retry ────────────────────────────────────────────

    _RETRYABLE_STATUS = {429, 500, 502, 503, 504}
    _MAX_RETRIES = 2

    def _friendly_error(self, exc: Exception, status_code: int | None = None) -> str:
        if status_code == 401:
            return "Invalid or missing API key (HTTP 401). Check PIONEER_API_KEY."
        if status_code == 429:
            return "AI provider rate limited (HTTP 429). Reduce AI_RATE_LIMIT_PER_SCAN or increase scan interval."
        if status_code == 503:
            return "AI provider temporarily unavailable (HTTP 503). Will retry automatically."
        if isinstance(exc, httpx.ConnectError):
            return f"Cannot connect to AI endpoint: {self.settings.pioneer_endpoint}"
        if isinstance(exc, httpx.TimeoutException):
            return f"AI request timed out after {self.settings.ai_timeout_seconds}s. Consider increasing AI_TIMEOUT_SECONDS."
        return str(exc)

    def _request(
        self, evidence: dict[str, Any], remediation: bool = False
    ) -> tuple[dict[str, Any] | None, str | None, AIAuditEntry]:
        audit = AIAuditEntry(
            model=self.settings.pioneer_model,
            remediation_type=remediation,
        )

        if not self.settings.ai_ready:
            audit.outcome = "skipped"
            if not self.settings.ai_enabled:
                err = "AI disabled"
            elif not self.settings.pioneer_api_key:
                err = "PIONEER_API_KEY is not configured"
            elif not self.settings.pioneer_endpoint:
                err = "PIONEER_ENDPOINT is not configured"
            elif self.settings.ai_key_invalid:
                err = "PIONEER_API_KEY is invalid"
            else:
                err = "AI is not ready"
            audit.error = err
            return None, err, audit

        # Global sliding-window rate check
        limiter = _get_limiter(self.settings.ai_rate_limit_per_scan * 2)
        if not limiter.allow():
            msg = (
                f"Global AI rate limit reached ({limiter._max_calls} calls/60s). "
                "Scan will retry on next cycle."
            )
            audit.outcome = "skipped"
            audit.error = msg
            return None, msg, audit

        body = self.build_request_body(evidence, remediation=remediation)
        headers = {
            "Authorization": f"Bearer {self.settings.pioneer_api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        status_code: int | None = None

        for attempt in range(self._MAX_RETRIES + 1):
            if attempt > 0:
                backoff = 2 ** attempt
                logger.info("ai_retry attempt=%d backoff=%ds", attempt, backoff)
                time.sleep(backoff)
            try:
                with httpx.Client(timeout=self.settings.ai_timeout_seconds) as client:
                    response = client.post(self.settings.pioneer_endpoint, headers=headers, json=body)
                    status_code = response.status_code

                    if status_code in self._RETRYABLE_STATUS and attempt < self._MAX_RETRIES:
                        logger.warning(
                            "ai_retryable_error status=%d attempt=%d", status_code, attempt
                        )
                        last_exc = httpx.HTTPStatusError(
                            f"HTTP {status_code}", request=response.request, response=response
                        )
                        continue

                    response.raise_for_status()
                    payload = response.json()

                content = payload.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                if isinstance(content, dict):
                    result = mask_data(self._normalize_analysis(content))
                else:
                    content_cleaned = self._extract_json_object(content)
                    result = mask_data(self._normalize_analysis(json.loads(content_cleaned)))

                audit.outcome = "success"
                audit.summary = str(result.get("summary", ""))[:200]

                # Clear invalid flag if it was set
                if self.settings.ai_key_invalid:
                    self.settings.ai_key_invalid = False
                    try:
                        from app.main import storage
                        storage.save_setting("ai_key_invalid", "False")
                    except Exception:
                        pass

                return result, None, audit

            except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError) as exc:
                last_exc = exc
                logger.warning(
                    "ai_analysis_failed error=%s raw_content=%r",
                    str(exc),
                    locals().get("content"),
                )

        error_msg = self._friendly_error(last_exc, status_code) if last_exc else "Unknown AI error"
        audit.outcome = "error"
        audit.error = error_msg

        # If we got a 401 or 403, flag key as invalid
        if status_code in {401, 403}:
            self.settings.ai_key_invalid = True
            try:
                from app.main import storage
                storage.save_setting("ai_key_invalid", "True")
            except Exception:
                pass

        return None, error_msg, audit

    # ── Public API ─────────────────────────────────────────────────────────

    def analyze(
        self, evidence: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None, AIAuditEntry]:
        return self._request(evidence, remediation=False)

    def suggest_remediation(
        self, evidence: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None, AIAuditEntry]:
        return self._request(evidence, remediation=True)

    def validate_key(self, api_key: str | None = None, endpoint: str | None = None) -> tuple[bool, str]:
        """Test connection to verify if the API key and endpoint are working.

        Returns (success, error_message).
        """
        key = api_key if api_key is not None else self.settings.pioneer_api_key
        url = endpoint if endpoint is not None else self.settings.pioneer_endpoint

        if not key:
            return False, "API key is missing."
        if not url:
            return False, "Endpoint is missing."

        try:
            import httpx
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": self.settings.pioneer_model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1
            }
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, headers=headers, json=body)
                if response.status_code in {401, 403}:
                    return False, f"Invalid API Key (HTTP {response.status_code})."
        except Exception:
            # Let connection/timeout issues pass as we don't want to lock settings out if the endpoint is temporarily unreachable.
            pass

        return True, ""
