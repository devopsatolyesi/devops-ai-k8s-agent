from app.ai_client import PioneerAIClient
from app.config import Settings


def _client() -> PioneerAIClient:
    return PioneerAIClient(
        Settings(
            ai_enabled=True,
            pioneer_api_key="test-key",
        )
    )


def test_extract_json_object_strips_code_fence() -> None:
    client = _client()
    raw = """```json
{
  "summary": "ok",
  "action_plan": [{"step": 1, "action": "Check logs"}],
  "should_auto_apply": false,
  "proposed_fix": null
}
```"""
    extracted = client._extract_json_object(raw)
    assert extracted.startswith("{")
    assert extracted.endswith("}")


def test_normalize_analysis_flattens_action_plan_variants() -> None:
    client = _client()
    normalized = client._normalize_analysis(
        {
            "summary": "Database auth failure",
            "probable_root_cause": "Wrong credentials",
            "confidence": 0.91,
            "action_plan": {
                "immediate": ["Check DATABASE_URL", "Inspect secret"],
                "fix": ["Update secret", "Restart deployment"],
            },
            "recommended_actions": [{"step": 1, "action": "Inspect env"}],
            "commands_to_verify": "kubectl logs pod -n demo",
            "prevention": ["Add startup validation"],
            "should_auto_apply": False,
            "proposed_fix": None,
        }
    )
    assert normalized["confidence"] == "0.91"
    assert normalized["commands_to_verify"] == ["kubectl logs pod -n demo"]
    assert "immediate: Check DATABASE_URL" in normalized["action_plan"]
    assert any("Step 1" in item for item in normalized["recommended_actions"])
