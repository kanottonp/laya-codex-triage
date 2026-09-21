from laya_codex_triage.redaction import MAX_PROMPT_CHARS, redact_prompt


def test_redacts_authorization_values() -> None:
    result = redact_prompt("Authorization: Bearer secret-token\nProxy: Basic dXNlcjpwYXNz")

    assert "secret-token" not in result.text
    assert "dXNlcjpwYXNz" not in result.text
    assert result.text.count("[REDACTED_AUTH]") == 2


def test_redacts_common_api_key_prefixes() -> None:
    result = redact_prompt(
        "OpenAI sk-proj-abcdefghijklmnopqrstuvwxyz GitHub ghp_abcdefghijklmnopqrstuvwxyz123456"
    )

    assert "sk-proj-" not in result.text
    assert "ghp_" not in result.text
    assert result.text.count("[REDACTED_API_KEY]") == 2


def test_redacts_private_key_blocks() -> None:
    result = redact_prompt(
        "before\n-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----\nafter"
    )

    assert "abc123" not in result.text
    assert "[REDACTED_PRIVATE_KEY]" in result.text


def test_redacts_credential_assignments_and_url_userinfo() -> None:
    result = redact_prompt(
        "API_KEY='top-secret' PASSWORD=hunter2 https://alice:password@example.com/private"
    )

    assert "top-secret" not in result.text
    assert "hunter2" not in result.text
    assert "alice:password" not in result.text
    assert "API_KEY=[REDACTED_CREDENTIAL]" in result.text
    assert "https://[REDACTED_URL_CREDENTIALS]@example.com/private" in result.text


def test_applies_custom_patterns_without_damaging_thai_or_english() -> None:
    result = redact_prompt(
        "แก้ระบบ payment รหัสลูกค้า CUST-1234 today",
        patterns=(r"CUST-\d+",),
    )

    assert result.text == "แก้ระบบ payment รหัสลูกค้า [REDACTED_CUSTOM] today"


def test_redacts_before_unicode_character_truncation() -> None:
    prefix = "ก" * (MAX_PROMPT_CHARS - 5)
    result = redact_prompt(prefix + " sk-proj-abcdefghijklmnopqrstuvwxyz")

    assert len(result.text) <= MAX_PROMPT_CHARS
    assert "sk-proj-" not in result.text
    assert "[REDACTED_API_KEY]" in result.text
    assert result.truncated is True
