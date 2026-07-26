from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentrail_core.settings import CoreSettings, DatabaseSettings, Environment, QueueSettings


class TestCoreSettings:
    def test_reads_prefixed_environment_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTRAIL_ENVIRONMENT", "ci")
        monkeypatch.setenv("AGENTRAIL_SERVICE_NAME", "api")
        monkeypatch.setenv("AGENTRAIL_LOG_LEVEL", "debug")

        settings = CoreSettings(_env_file=None)

        assert settings.environment is Environment.CI
        assert settings.service_name == "api"
        assert settings.log_level == "DEBUG"

    def test_unprefixed_variables_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERVICE_NAME", "hijacked")

        assert CoreSettings(_env_file=None).service_name == "agentrail"

    def test_rejects_an_unknown_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTRAIL_LOG_LEVEL", "chatty")

        with pytest.raises(ValidationError):
            CoreSettings(_env_file=None)

    def test_rejects_an_unknown_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTRAIL_ENVIRONMENT", "prodlike")

        with pytest.raises(ValidationError):
            CoreSettings(_env_file=None)

    def test_settings_are_immutable(self) -> None:
        settings = CoreSettings(_env_file=None)

        with pytest.raises(ValidationError):
            settings.service_name = "mutated"  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("environment", "deployed"),
        [
            (Environment.LOCAL, False),
            (Environment.TEST, False),
            (Environment.CI, False),
            (Environment.STAGING, True),
            (Environment.PRODUCTION, True),
        ],
    )
    def test_is_deployed_classification(self, environment: Environment, deployed: bool) -> None:
        assert environment.is_deployed is deployed

    def test_shutdown_grace_must_be_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTRAIL_SHUTDOWN_GRACE_SECONDS", "0")

        with pytest.raises(ValidationError):
            CoreSettings(_env_file=None)


class TestDatabaseSettings:
    def test_local_default_points_at_localhost(self) -> None:
        settings = DatabaseSettings(_env_file=None)

        assert "localhost" in str(settings.database_url)

    def test_rejects_a_non_postgres_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTRAIL_DATABASE_URL", "mysql://user:pw@localhost/agentrail")

        with pytest.raises(ValidationError):
            DatabaseSettings(_env_file=None)

    def test_pool_size_bounds_are_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTRAIL_DATABASE_POOL_SIZE", "0")

        with pytest.raises(ValidationError):
            DatabaseSettings(_env_file=None)


class TestQueueSettings:
    def test_default_queue_key_is_namespaced(self) -> None:
        assert QueueSettings(_env_file=None).job_queue_key.startswith("agentrail:")

    def test_rejects_a_non_redis_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTRAIL_REDIS_URL", "http://localhost:6379")

        with pytest.raises(ValidationError):
            QueueSettings(_env_file=None)

    def test_rejects_an_empty_queue_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTRAIL_JOB_QUEUE_KEY", "")

        with pytest.raises(ValidationError):
            QueueSettings(_env_file=None)
