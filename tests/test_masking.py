from app.masking import mask_data, mask_string


def test_mask_string_hides_common_secret_patterns() -> None:
    raw = (
        "password=supersecret token=abc123 Authorization: Bearer xyz "
        "DATABASE_URL=postgres://user:pass@db/prod AWS_SECRET_ACCESS_KEY=secret"
    )
    masked = mask_string(raw)
    assert "supersecret" not in masked
    assert "abc123" not in masked
    assert "Bearer xyz" not in masked
    assert "user:pass" not in masked
    assert "AWS_SECRET_ACCESS_KEY=******" in masked


def test_mask_data_masks_sensitive_keys_recursively() -> None:
    data = {"env": {"PIONEER_API_KEY": "real", "normal": "ok"}, "logs": ["token=abc"]}
    masked = mask_data(data)
    assert masked["env"]["PIONEER_API_KEY"] == "******"
    assert masked["env"]["normal"] == "ok"
    assert masked["logs"][0] == "token=******"

