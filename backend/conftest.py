"""
Test environment for the backend suite.

`talleyrand.core.config` builds its Settings the moment it is imported, so
every test module that touches the package needs a complete configuration
before pytest can even collect it. Setting one here lets the suite run from a
fresh clone with no .env file and no Google Cloud project, and pins the tests
to a known configuration instead of whatever the developer happens to have
locally.

Environment variables win over the .env file, so anything a test asserts on
must be set here — otherwise the same test passes on the host and fails in
Docker, which sets a different value. These values are placeholders. Nothing in
the suite talks to Google or signs a token anyone outside the test process will
see.
"""

import os

os.environ["JWT_SECRET_KEY"] = "test-only-signing-key-never-use-this-anywhere-real"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost:8000/auth/google/callback"

# docker compose turns this off so sign-in works over plain HTTP locally. The
# oauth_state cookie is then no longer Secure, the test client starts replaying
# it, and the tests that must send no cookie quietly send one.
os.environ["COOKIE_SECURE"] = "true"

# The instance settings a developer's own environment or .env may carry, pinned to their
# defaults: otherwise the suite runs in local mode, sends background features to Claude,
# and makes real token exchanges through the configured identity token command. An empty
# value counts as unset and, unlike a removed variable, also overrides a .env file.
os.environ["BACKGROUND_PROVIDER"] = "openai"
os.environ["DICTATION_ENABLED"] = "true"
for _name in (
    "ANTHROPIC_IDENTITY_TOKEN_COMMAND",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_SERVICE_ACCOUNT_ID",
    "ANTHROPIC_WORKSPACE_ID",
    "LOCAL_USER_EMAIL",
    "LOCAL_USER_NAME",
):
    os.environ[_name] = ""
