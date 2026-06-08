from __future__ import annotations

import json
import re
from typing import Any

SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|pwd|token|api[_-]?key|secret|authorization|credential|database_url|aws_secret_access_key)",
    re.IGNORECASE,
)
CONNECTION_RE = re.compile(
    r"(?P<scheme>(?:postgres|postgresql|mysql|mongodb|redis)://[^:\s]+:)(?P<password>[^@\s]+)(?P<rest>@[^\s]+)",
    re.IGNORECASE,
)
AUTH_HEADER_RE = re.compile(r"(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
ASSIGNMENT_RE = re.compile(
    r"(?P<key>(?:password|passwd|pwd|token|api[_-]?key|secret|authorization|AWS_SECRET_ACCESS_KEY|DATABASE_URL)\s*=\s*)(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)


def mask_string(value: str) -> str:
    masked = AUTH_HEADER_RE.sub(r"\1******", value)
    masked = CONNECTION_RE.sub(r"\g<scheme>****\g<rest>", masked)

    def _mask_assignment(match: re.Match[str]) -> str:
        key = match.group("key")
        raw_value = match.group("value")
        if "DATABASE_URL" in key.upper() or "://" in raw_value:
            return key + CONNECTION_RE.sub(r"\g<scheme>****\g<rest>", raw_value)
        return key + "******"

    return ASSIGNMENT_RE.sub(_mask_assignment, masked)


def mask_data(data: Any) -> Any:
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for key, value in data.items():
            if SENSITIVE_KEY_RE.search(str(key)):
                result[str(key)] = "******"
            else:
                result[str(key)] = mask_data(value)
        return result
    if isinstance(data, list):
        return [mask_data(item) for item in data]
    if isinstance(data, tuple):
        return tuple(mask_data(item) for item in data)
    if isinstance(data, str):
        masked = mask_string(data)
        # Try to parse and mask embedded JSON strings
        try:
            if masked.strip().startswith(("{", "[")):
                parsed = json.loads(masked)
                return json.dumps(mask_data(parsed), ensure_ascii=False, default=str)
        except (json.JSONDecodeError, ValueError):
            pass
        return masked
    return data


def mask_json_text(data: Any) -> str:
    return json.dumps(mask_data(data), ensure_ascii=False, indent=2, default=str)

