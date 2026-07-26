from __future__ import annotations

import pytest

from agentrail_core.identity.secrets import (
    API_KEY_PREFIX,
    KEY_ID_LENGTH,
    generate_api_key,
    generate_oauth_state,
    generate_session_token,
    hash_session_token,
    parse_api_key,
    verify_secret,
)


class TestApiKeyGeneration:
    def test_token_has_the_documented_shape(self) -> None:
        generated = generate_api_key()

        prefix, key_id, secret = generated.token.split("_", 2)
        assert prefix == API_KEY_PREFIX
        assert key_id == generated.key_id
        assert len(key_id) == KEY_ID_LENGTH
        assert secret

    def test_the_secret_is_never_stored_in_plaintext(self) -> None:
        generated = generate_api_key()
        secret = generated.token.split("_", 2)[2]

        assert secret not in generated.secret_hash
        assert len(generated.secret_hash) == 64

    def test_keys_are_unique(self) -> None:
        keys = {generate_api_key().token for _ in range(500)}

        assert len(keys) == 500

    def test_a_generated_key_verifies_against_its_own_hash(self) -> None:
        generated = generate_api_key()
        parsed = parse_api_key(generated.token)

        assert parsed is not None
        assert verify_secret(parsed[1], generated.secret_hash) is True

    def test_a_different_secret_does_not_verify(self) -> None:
        first, second = generate_api_key(), generate_api_key()
        parsed = parse_api_key(second.token)

        assert parsed is not None
        assert verify_secret(parsed[1], first.secret_hash) is False


class TestApiKeyParsing:
    @pytest.mark.parametrize(
        "token",
        [
            "",
            "nonsense",
            "ar_short_secret",
            "wrong_0123456789abcdef_secret",
            "ar__secret",
            "ar_0123456789abcdef_",
        ],
    )
    def test_malformed_tokens_are_rejected(self, token: str) -> None:
        assert parse_api_key(token) is None

    def test_a_secret_containing_underscores_survives_parsing(self) -> None:
        """The secret is base64url, which includes '_'; only the first two splits matter."""
        parsed = parse_api_key("ar_0123456789abcdef_aaa_bbb_ccc")

        assert parsed == ("0123456789abcdef", "aaa_bbb_ccc")


class TestSessionTokens:
    def test_token_and_hash_correspond(self) -> None:
        token, token_hash = generate_session_token()

        assert hash_session_token(token) == token_hash
        assert token not in token_hash

    def test_tokens_are_unique(self) -> None:
        assert len({generate_session_token()[0] for _ in range(500)}) == 500

    def test_hashing_is_deterministic(self) -> None:
        token, _ = generate_session_token()

        assert hash_session_token(token) == hash_session_token(token)


class TestOAuthState:
    def test_state_values_are_unique_and_long(self) -> None:
        states = {generate_oauth_state() for _ in range(200)}

        assert len(states) == 200
        assert all(len(state) >= 32 for state in states)
