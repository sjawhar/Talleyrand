"""
Configuration settings for the Talleyrand backend.
Should be split in the future, so that each app registers its own settings.
"""

from typing import Literal, Self

from pydantic import AnyUrl, EmailStr, Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Settings configuration for the Talleyrand backend.

    Configuration is loaded from environment variables and .env file.
    Environment variables take precedence over .env file values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # JWT Settings
    jwt_secret_key: str = Field(
        ...,
        description=(
            "Secret key used for signing JWT tokens. "
            "Must be a unique random value per deployment, generated with `openssl rand -hex 32`."
        ),
        min_length=32,
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="Algorithm used for JWT encoding/decoding",
    )
    jwt_access_token_expire_minutes: int = Field(
        default=1440,
        description="Expiration time for JWT access tokens in minutes",
    )
    jwt_refresh_token_expire_days: int = Field(
        default=7,
        description="Expiration time for JWT refresh tokens in days",
    )

    # Google OAuth Settings. All three are required unless single-user local mode
    # (LOCAL_USER_EMAIL) is on; in local mode Google sign-in works only if all three are set.
    # An empty value counts as unset, here and for the Anthropic and local-mode settings below.
    google_client_id: str | None = Field(
        default=None, description="Google OAuth client ID", min_length=1
    )
    google_client_secret: str | None = Field(
        default=None, description="Google OAuth client secret", min_length=1
    )
    google_redirect_uri: HttpUrl | None = Field(
        default=None,
        description="Redirect URI for Google OAuth callbacks",
    )
    google_token_url: HttpUrl = Field(
        default=HttpUrl("https://oauth2.googleapis.com/token"),
        description="URL to obtain Google OAuth tokens",
    )
    google_userinfo_url: HttpUrl = Field(
        default=HttpUrl("https://www.googleapis.com/oauth2/v2/userinfo"),
        description="URL to fetch user info from Google",
    )
    auth_failure_redirect: HttpUrl = Field(
        default=HttpUrl("http://localhost:3000/auth/failure"),
        description="Frontend URL to redirect after successful authentication",
    )

    # CORS Settings
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
        ],
        description="List of allowed CORS origins",
    )

    email_whitelist_enabled: bool = Field(
        default=False,
        description="Whether to enable email whitelist checking",
    )

    email_whitelist: list[EmailStr] = Field(
        default_factory=lambda: [
            "example@example.com",
        ],
        description="List of allowed emails",
    )

    # Cookie Settings (used for oauth_state cookie only)
    cookie_secure: bool = Field(
        default=True,
        description="Whether cookies should be marked as secure",
    )

    mongodb_url: AnyUrl = Field(
        description="MongoDB connection URL",
        default_factory=lambda: AnyUrl("mongodb://localhost"),
    )
    mongodb_database: str = Field(
        default="talleyrand",
        description="Name of the MongoDB database",
    )
    logging_level: str = Field(
        default="INFO",
        description="Logging level for the application",
    )

    query_chunk_size: int = Field(
        default=100,
        description="Chunk size for buffering the LLM responses before frontend delivery",
    )
    stream_idle_timeout_seconds: float = Field(
        default=1000.0,
        description=(
            "Max seconds a generation may wait for the next streamed token before the job is "
            "treated as stalled and failed. Bounds the idle gap (reset on every token), not the "
            "total duration, so long reasoning/web-search pauses are tolerated but a hung "
            "provider stream cannot freeze the job forever."
        ),
    )

    log_llm_requests: bool = Field(
        default=False,
        description="Log full LLM responses to console",
    )

    # Server-held Anthropic credentials: Anthropic workload identity federation, exchanging
    # the identity token a command prints. Used only when a request brings no Anthropic key.
    anthropic_identity_token_command: str | None = Field(
        default=None,
        description=(
            "Command that prints an identity token for Anthropic workload identity federation. "
            "Setting it turns on server-held Anthropic credentials."
        ),
    )
    anthropic_federation_rule_id: str | None = Field(
        default=None, description="Anthropic federation rule to exchange the token under"
    )
    anthropic_organization_id: str | None = Field(
        default=None, description="Anthropic organization the federation rule belongs to"
    )
    anthropic_service_account_id: str | None = Field(
        default=None, description="Service account the federation rule mints tokens for"
    )
    anthropic_workspace_id: str | None = Field(
        default=None,
        description="Workspace to mint for, when the federation rule covers more than one",
    )

    background_provider: Literal["openai", "anthropic"] = Field(
        default="openai",
        description=(
            "Provider for the background features: kickstart, suggestions, big-picture "
            "questions, thread recaps, reports and case titles"
        ),
    )

    dictation_enabled: bool = Field(
        default=True,
        description="Whether the speech-to-text endpoint is available",
    )

    # Single-user local mode: sign-in issues tokens for this person without Google.
    local_user_email: EmailStr | None = Field(
        default=None,
        description=(
            "Turns on single-user local mode for this address. Only for an instance nobody "
            "else can reach."
        ),
    )
    local_user_name: str | None = Field(
        default=None, description="Display name for the single local user"
    )

    @property
    def google_sign_in_configured(self) -> bool:
        """Whether every setting Google sign-in needs is present."""
        return (
            self.google_client_id is not None
            and self.google_client_secret is not None
            and self.google_redirect_uri is not None
        )

    @field_validator(
        "google_client_id",
        "google_client_secret",
        "google_redirect_uri",
        "anthropic_identity_token_command",
        "anthropic_federation_rule_id",
        "anthropic_organization_id",
        "anthropic_service_account_id",
        "anthropic_workspace_id",
        "local_user_email",
        "local_user_name",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # A .env copied from .env.example keeps these lines with nothing after the `=`.
        return None if value == "" else value

    @model_validator(mode="after")
    def _check_sign_in_and_federation(self) -> Self:
        if self.local_user_email is None and not self.google_sign_in_configured:
            missing = [
                name
                for name, value in (
                    ("GOOGLE_CLIENT_ID", self.google_client_id),
                    ("GOOGLE_CLIENT_SECRET", self.google_client_secret),
                    ("GOOGLE_REDIRECT_URI", self.google_redirect_uri),
                )
                if value is None
            ]
            raise ValueError(", ".join(missing) + " required unless LOCAL_USER_EMAIL is set")
        federation = {
            "ANTHROPIC_FEDERATION_RULE_ID": self.anthropic_federation_rule_id,
            "ANTHROPIC_ORGANIZATION_ID": self.anthropic_organization_id,
        }
        if self.anthropic_identity_token_command is not None:
            missing = [name for name, value in federation.items() if not value]
            if missing:
                raise ValueError(
                    "ANTHROPIC_IDENTITY_TOKEN_COMMAND is set but " + ", ".join(missing) + " is not"
                )
        elif (
            any(federation.values())
            or self.anthropic_service_account_id is not None
            or self.anthropic_workspace_id is not None
        ):
            raise ValueError(
                "Anthropic federation settings are set but ANTHROPIC_IDENTITY_TOKEN_COMMAND is not"
            )
        return self


settings = Settings()
