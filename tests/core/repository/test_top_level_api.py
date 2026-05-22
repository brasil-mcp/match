"""Test public top-level lookup_cnpj() API surface."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import brasil_mcp_match


def test_lookup_cnpj_is_callable():
    assert callable(brasil_mcp_match.lookup_cnpj)


def test_lookup_cnpj_delegates_to_postgres_repo():
    fake_record = MagicMock(cnpj_basico="33000167")
    with (
        patch("brasil_mcp_match.core.repository.connection.connect") as mock_connect,
        patch("brasil_mcp_match.core.repository.postgres_repo.PostgresCnpjRepo") as mock_repo_cls,
    ):
        mock_connect.return_value.__enter__.return_value = "fake_conn"
        mock_repo = mock_repo_cls.return_value
        mock_repo.find_by_cnpj.return_value = fake_record

        result = brasil_mcp_match.lookup_cnpj("33000167000101")

    mock_repo_cls.assert_called_once_with("fake_conn")
    mock_repo.find_by_cnpj.assert_called_once_with("33000167000101")
    assert result is fake_record


def test_lookup_cnpj_returns_none_when_not_found():
    with (
        patch("brasil_mcp_match.core.repository.connection.connect") as mock_connect,
        patch("brasil_mcp_match.core.repository.postgres_repo.PostgresCnpjRepo") as mock_repo_cls,
    ):
        mock_connect.return_value.__enter__.return_value = "fake_conn"
        mock_repo_cls.return_value.find_by_cnpj.return_value = None
        result = brasil_mcp_match.lookup_cnpj("99999999000199")
    assert result is None
