"""
Model-provider credentials the server holds itself, used when a request brings no key.

Anthropic: workload identity federation. A configured command prints an identity token
from an identity provider the organization's federation rule trusts, and the Anthropic
SDK exchanges it for a short-lived access token, caching and refreshing it. There is no
server-held OpenAI credential.
"""

import shlex
import subprocess

from anthropic import AnthropicError
from anthropic.lib.credentials import WorkloadIdentityCredentials

from talleyrand.core.config import settings
from talleyrand.core.model_settings import Provider

IDENTITY_TOKEN_TIMEOUT_SECONDS = 30


class IdentityTokenCommandError(AnthropicError):
    """
    The configured identity token command failed or printed nothing.

    An AnthropicError so the SDK passes it to the caller as it is: any other exception
    raised while authenticating is wrapped as a bare "Connection error." and retried,
    re-running a command that has already failed.
    """


def run_identity_token_command(command: str) -> str:
    """Run the identity token command and return the token it prints."""
    try:
        completed = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=IDENTITY_TOKEN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IdentityTokenCommandError(
            f"Identity token command {command!r} could not run: {exc}"
        ) from exc
    if completed.returncode != 0:
        raise IdentityTokenCommandError(
            f"Identity token command {command!r} exited {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    token = completed.stdout.strip()
    if not token:
        raise IdentityTokenCommandError(f"Identity token command {command!r} printed no token")
    return token


def anthropic_server_credentials() -> WorkloadIdentityCredentials | None:
    """
    Anthropic credentials from the settings, or None when none are configured.

    Built once, for the one client every keyless call shares (llm.server_anthropic_client):
    the SDK caches the exchanged access token per client, not per credentials object.
    """
    command = settings.anthropic_identity_token_command
    if command is None:
        return None
    # The settings validator guarantees these are set whenever the command is.
    assert settings.anthropic_federation_rule_id is not None
    assert settings.anthropic_organization_id is not None
    return WorkloadIdentityCredentials(
        identity_token_provider=lambda: run_identity_token_command(command),
        federation_rule_id=settings.anthropic_federation_rule_id,
        organization_id=settings.anthropic_organization_id,
        service_account_id=settings.anthropic_service_account_id,
        workspace_id=settings.anthropic_workspace_id,
    )


def server_provider_available(provider: Provider) -> bool:
    """Whether the server holds its own credentials for this provider."""
    return provider == "anthropic" and settings.anthropic_identity_token_command is not None
