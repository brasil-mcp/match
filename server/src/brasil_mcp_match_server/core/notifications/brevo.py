"""Brevo (Sendinblue) transactional email client.

Used to deliver the newly minted API key to paid signups via email. The
email goes out from the webhook handler as a *backup* — the polling endpoint
remains the primary delivery channel — so failures here MUST NOT bubble up
to fail the webhook (Asaas would retry indefinitely and we'd double-issue
keys). The caller wraps this in try/except and logs failures.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"
_DEFAULT_FROM_EMAIL = "noreply@brasil-mcp.dev"
_DEFAULT_FROM_NAME = "Brasil MCP"


def _api_key() -> str:
    """Read BREVO_API_KEY; raises RuntimeError if absent."""
    key = os.environ.get("BREVO_API_KEY")
    if not key:
        raise RuntimeError("BREVO_API_KEY not set")
    return key


def _from_email() -> str:
    return os.environ.get("BREVO_FROM_EMAIL", _DEFAULT_FROM_EMAIL)


def _from_name() -> str:
    return os.environ.get("BREVO_FROM_NAME", _DEFAULT_FROM_NAME)


def _render_text(api_key: str, plan: str) -> str:
    return (
        "Your Brasil MCP Match API key is ready.\n\n"
        "SAVE THIS KEY NOW. The server has discarded its plaintext copy — this\n"
        "email is your only backup. If you lose this email AND don't save the\n"
        "key elsewhere, you'll need to sign up again with a new payment.\n\n"
        f"API key: {api_key}\n"
        f"Plan: {plan}\n\n"
        f"Add it as BRASIL_MCP_MATCH_KEY in your MCP client config.\n"
        f"Keep it secret. Treat it like a password.\n"
    )


def _render_html(api_key: str, plan: str) -> str:
    return (
        "<p>Your Brasil MCP Match API key is ready.</p>"
        "<p><strong>SAVE THIS KEY NOW.</strong> The server has discarded its "
        "plaintext copy — this email is your only backup. If you lose this "
        "email AND don't save the key elsewhere, you'll need to sign up again "
        "with a new payment.</p>"
        f"<p><strong>API key:</strong> <code>{api_key}</code><br>"
        f"<strong>Plan:</strong> <code>{plan}</code></p>"
        "<p>Add it as <code>BRASIL_MCP_MATCH_KEY</code> in your MCP client config. "
        "Keep it secret. Treat it like a password.</p>"
    )


async def send_api_key_email(
    to: str,
    api_key: str,
    plan: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """POST /v3/smtp/email — deliver the API key to the signup email.

    Raises ``RuntimeError`` if ``BREVO_API_KEY`` is unset and
    ``httpx.HTTPStatusError`` on non-2xx responses. Callers should wrap this
    in try/except — see module docstring.
    """
    own = client is None
    cli = client or httpx.AsyncClient(timeout=15.0)
    try:
        body: dict[str, Any] = {
            "sender": {"name": _from_name(), "email": _from_email()},
            "to": [{"email": to}],
            "subject": "Your Brasil MCP Match API key",
            "textContent": _render_text(api_key, plan),
            "htmlContent": _render_html(api_key, plan),
        }
        r = await cli.post(
            BREVO_ENDPOINT,
            json=body,
            headers={
                "api-key": _api_key(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        r.raise_for_status()
    finally:
        if own:
            await cli.aclose()
