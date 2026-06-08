from unittest.mock import MagicMock, patch

from app.ai_client import PioneerAIClient
from app.config import Settings


def test_ai_prompt_masks_secrets() -> None:
    client = PioneerAIClient(Settings(pioneer_api_key="not-used"))
    prompt = client.build_user_prompt(
        {
            "namespace": "default",
            "logs_tail": "password=supersecret Authorization: Bearer tokenvalue",
            "env": {"DATABASE_URL": "postgres://user:pass@db/prod", "PIONEER_API_KEY": "real"},
        }
    )
    assert "supersecret" not in prompt
    assert "tokenvalue" not in prompt
    assert "user:pass" not in prompt
    assert "real" not in prompt
    assert "******" in prompt


def test_ai_client_parses_wrapped_json() -> None:
    client = PioneerAIClient(Settings(pioneer_api_key="test-key", ai_enabled=True))
    
    mock_payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        "```json\n{\n  \"summary\": \"Test summary\",\n"
                        "  \"probable_root_cause\": \"Test cause\"\n}\n```"
                    )
                }
            }
        ]
    }
    
    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_payload
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        result, error, audit = client.analyze({"dummy": "evidence"})
        
        assert error is None
        assert result is not None
        assert result["summary"] == "Test summary"
        assert result["probable_root_cause"] == "Test cause"

