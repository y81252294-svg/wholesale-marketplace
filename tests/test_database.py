from backend.database import normalize_database_url


def test_postgres_urls_use_psycopg_driver():
    assert normalize_database_url("postgres://user:pass@host:5432/db") == (
        "postgresql+psycopg://user:pass@host:5432/db"
    )
    assert normalize_database_url("postgresql://user:pass@host:5432/db") == (
        "postgresql+psycopg://user:pass@host:5432/db"
    )


def test_vercel_postgres_url_enables_ssl(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert normalize_database_url("postgresql://user:pass@host:5432/db") == (
        "postgresql+psycopg://user:pass@host:5432/db?sslmode=require"
    )


def test_vercel_preserves_explicit_ssl_mode(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert normalize_database_url(
        "postgresql://user:pass@host:5432/db?sslmode=verify-full"
    ) == "postgresql+psycopg://user:pass@host:5432/db?sslmode=verify-full"
