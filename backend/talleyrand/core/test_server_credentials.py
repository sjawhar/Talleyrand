import shlex
import sys

import pytest
from pydantic import ValidationError

from talleyrand.core import server_credentials
from talleyrand.core.config import Settings
from talleyrand.core.server_credentials import (
    IdentityTokenCommandError,
    anthropic_server_credentials,
    run_identity_token_command,
    server_provider_available,
)

FEDERATION = {
    "anthropic_federation_rule_id": "fdrl_test",
    "anthropic_organization_id": "org-test",
    "anthropic_service_account_id": "svac_test",
}


def python_command(code: str) -> str:
    return shlex.join([sys.executable, "-c", code])


def test_command_without_its_federation_rule_is_refused_by_name():
    with pytest.raises(ValidationError, match="ANTHROPIC_FEDERATION_RULE_ID"):
        Settings(
            anthropic_identity_token_command="print-identity-token", anthropic_organization_id="o"
        )


def test_federation_rule_without_a_command_is_refused():
    with pytest.raises(ValidationError, match="ANTHROPIC_IDENTITY_TOKEN_COMMAND is not"):
        Settings(anthropic_federation_rule_id="fdrl_test")


def test_leftover_workspace_without_a_command_is_refused():
    with pytest.raises(ValidationError, match="ANTHROPIC_IDENTITY_TOKEN_COMMAND is not"):
        Settings(anthropic_workspace_id="wrkspc_test")


def test_empty_federation_settings_count_as_unset():
    configured = Settings(
        anthropic_identity_token_command="",
        anthropic_federation_rule_id="",
        anthropic_organization_id="",
        anthropic_service_account_id="",
        anthropic_workspace_id="",
    )
    assert configured.anthropic_identity_token_command is None
    assert configured.anthropic_workspace_id is None


def test_google_redirect_uri_is_required_without_local_mode():
    with pytest.raises(ValidationError, match="GOOGLE_REDIRECT_URI required"):
        Settings(google_redirect_uri=None)


def test_local_mode_starts_without_google():
    configured = Settings(
        google_client_id=None,
        google_client_secret=None,
        google_redirect_uri=None,
        local_user_email="you@example.com",
    )
    assert configured.local_user_email == "you@example.com"
    assert not configured.google_sign_in_configured


def test_empty_google_settings_count_as_unset_in_local_mode():
    # A copied .env.example leaves the Google lines present but empty.
    configured = Settings(
        google_client_id="",
        google_client_secret="",
        google_redirect_uri="",
        local_user_email="you@example.com",
        local_user_name="",
    )
    assert not configured.google_sign_in_configured
    assert configured.local_user_name is None


def test_empty_google_client_id_is_refused_by_name_without_local_mode():
    with pytest.raises(ValidationError, match="GOOGLE_CLIENT_ID required"):
        Settings(google_client_id="")


def test_command_output_is_the_token():
    assert run_identity_token_command(python_command("print('  jwt-abc  ')")) == "jwt-abc"


def test_failing_command_reports_its_stderr():
    failing = python_command("import sys; sys.stderr.write('not logged in'); sys.exit(3)")
    with pytest.raises(IdentityTokenCommandError, match="exited 3: not logged in"):
        run_identity_token_command(failing)


def test_silent_command_is_an_error():
    with pytest.raises(IdentityTokenCommandError, match="printed no token"):
        run_identity_token_command(python_command("pass"))


def test_missing_command_is_an_error():
    with pytest.raises(IdentityTokenCommandError, match="could not run"):
        run_identity_token_command("definitely-not-a-real-command-talleyrand")


def test_only_anthropic_is_available_when_configured(monkeypatch: pytest.MonkeyPatch):
    configured = Settings(
        anthropic_identity_token_command=python_command("print('jwt')"), **FEDERATION
    )
    monkeypatch.setattr(server_credentials, "settings", configured)

    assert server_provider_available("anthropic")
    assert not server_provider_available("openai")


def test_nothing_is_available_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server_credentials, "settings", Settings())

    assert anthropic_server_credentials() is None
    assert not server_provider_available("anthropic")
