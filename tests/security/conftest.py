"""Security tests reuse the adapter test fixtures (FakeRepo, fake_audit, etc.).

We don't duplicate them — just import to re-register at this scope.
"""

from __future__ import annotations

from tests.adapters.conftest import (  # noqa: F401
    FakeAuditStore,
    FakeOptOutStore,
    FakeRepo,
    api_key,
    api_key_hash,
    fake_audit,
    fake_opt_out,
    fake_repo,
    revoked_key_plaintext,
    revoked_service_context,
    service_context,
)
