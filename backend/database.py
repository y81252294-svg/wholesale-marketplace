import os
from urllib.parse import parse_qsl, quote, unquote, urlencode

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    if os.getenv("VERCEL") and database_url.startswith("postgresql+psycopg://"):
        scheme, _, remainder = database_url.partition("://")
        userinfo, at_separator, endpoint = remainder.rpartition("@")
        if at_separator:
            host, path_separator, path_and_query = endpoint.partition("/")
            username, separator, password = userinfo.partition(":")
            username = quote(unquote(username), safe="")
            if separator:
                password = quote(unquote(password), safe="")
                userinfo = f"{username}:{password}"
            else:
                userinfo = username
            database_url = f"{scheme}://{userinfo}@{host}{path_separator}{path_and_query}"
        else:
            database_url = f"{scheme}://{endpoint}"

        base_url, separator, query_string = database_url.partition("?")
        query = parse_qsl(query_string, keep_blank_values=True) if separator else []
        if not any(key == "sslmode" for key, _ in query):
            query.append(("sslmode", "require"))
        database_url = f"{base_url}?{urlencode(query)}"

    return database_url


configured_database_url = os.getenv("DATABASE_URL")
if os.getenv("VERCEL") and not configured_database_url:
    raise RuntimeError("DATABASE_URL must be configured for Vercel using a hosted PostgreSQL database.")

DATABASE_URL = normalize_database_url(configured_database_url or "sqlite:///./marketplace.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine_options = {"pool_pre_ping": True}
if os.getenv("VERCEL"):
    engine_options["poolclass"] = NullPool
engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
