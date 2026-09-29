import pytest

from data_analysis_agent.config.settings import ConfigurationError, load_settings


def test_auth_settings_parse_ttl_secure_cookie_and_admin_emails():
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "AUTH_SESSION_TTL_SECONDS": "900",
            "AUTH_SESSION_COOKIE_NAME": "test_session",
            "AUTH_SESSION_COOKIE_SECURE": "true",
            "AUTH_ADMIN_EMAILS": " Admin@Example.com,root@example.com ",
        },
    )

    assert settings.session_ttl_seconds == 900
    assert settings.session_cookie_name == "test_session"
    assert settings.session_cookie_secure is True
    assert settings.auth_admin_emails == ("admin@example.com", "root@example.com")


def test_production_cannot_disable_secure_session_cookie():
    with pytest.raises(ConfigurationError, match="AUTH_SESSION_COOKIE_SECURE"):
        load_settings(
            app_env="production",
            environ={
                "APP_ENV": "production",
                "OPENAI_API_KEY": "key",
                "OPENAI_BASE_URL": "https://llm.example",
                "OPENAI_MODEL": "model",
                "DATABASE_URL": "mysql+pymysql://user:pass@db/app",
                "STORAGE_ENDPOINT": "https://s3.example",
                "STORAGE_BUCKET": "bucket",
                "EXECUTION_IMAGE": "analysis:latest",
                "AUTH_SESSION_COOKIE_SECURE": "false",
            },
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AUTH_SESSION_TTL_SECONDS", "0"),
        ("AUTH_SESSION_TTL_SECONDS", "not-an-int"),
        ("AUTH_SESSION_COOKIE_NAME", " "),
        ("AUTH_SESSION_COOKIE_SECURE", "yes"),
    ],
)
def test_invalid_auth_settings_name_their_configuration_key(key, value):
    with pytest.raises(ConfigurationError, match=key):
        load_settings(app_env="test", environ={key: value})


def test_auth_settings_defaults_are_safe_for_local_test_environment():
    settings = load_settings(app_env="test", environ={})

    assert settings.session_ttl_seconds == 86400
    assert settings.session_cookie_name == "daa_session"
    assert settings.session_cookie_secure is False
    assert settings.auth_admin_emails == ()
