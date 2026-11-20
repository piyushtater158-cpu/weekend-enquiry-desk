import pytest

from app.db import MissingDatabaseUrl, get_database_url


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(MissingDatabaseUrl):
        get_database_url()


def test_transaction_pooler_port_rejected(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://postgres:PASSWORD@db.example.supabase.co:6543/postgres"
    )
    with pytest.raises(MissingDatabaseUrl):
        get_database_url()


def test_session_pooler_port_accepted(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://postgres:PASSWORD@db.example.supabase.co:5432/postgres"
    )
    assert get_database_url().endswith(":5432/postgres")
