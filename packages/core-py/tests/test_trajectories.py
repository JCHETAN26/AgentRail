"""Trajectory redaction tests."""

from __future__ import annotations

from agentrail_core.trajectories import redact_payload


def test_redacts_sensitive_keys_and_email_addresses() -> None:
    redacted, summary = redact_payload(
        {
            "authorization": "Bearer secret",
            "nested": {"api_key": "abc123", "owner": "alice@example.com"},
            "items": [{"token": "value"}, {"message": "safe"}],
        }
    )

    assert redacted == {
        "authorization": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]", "owner": "a****@example.com"},
        "items": [{"token": "[REDACTED]"}, {"message": "safe"}],
    }
    assert summary == {"keys": 3, "emails": 1}
