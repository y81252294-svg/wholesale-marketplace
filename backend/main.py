from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from backend.api import router
from backend.database import Base, engine
from backend import models  # noqa: F401

logger = logging.getLogger(__name__)


def database_connection_failure_detail(error: SQLAlchemyError) -> tuple[str, str, str]:
    original_error = error.orig
    sqlstate = getattr(original_error, "sqlstate", None)
    error_type = type(original_error).__name__
    message = str(original_error).lower()

    if sqlstate == "28P01" or "password authentication failed" in message:
        return (
            error_type,
            sqlstate or "unavailable",
            "Database authentication failed. Check the Supabase Session Pooler username and "
            "current database password saved in Vercel's DATABASE_URL.",
        )
    if sqlstate == "3D000":
        return (
            error_type,
            sqlstate,
            "The database named in DATABASE_URL does not exist. Check the database name in the "
            "Supabase Session Pooler URL.",
        )
    if sqlstate and sqlstate.startswith("08"):
        return (
            error_type,
            sqlstate,
            "The database connection could not be established. Check the Supabase pooler host, "
            "port, project status, and network availability.",
        )
    if any(marker in message for marker in ("could not translate host", "name or service not known", "getaddrinfo failed")):
        return (
            error_type,
            "unavailable",
            "Supabase hostname could not be resolved. Check that DATABASE_URL uses the exact "
            "Session Pooler host copied from Supabase.",
        )
    if "timeout" in message or "timed out" in message:
        return (
            error_type,
            "unavailable",
            "The connection to Supabase timed out. Check the Session Pooler host and port, and "
            "confirm the Supabase project is active.",
        )
    if "connection refused" in message:
        return (
            error_type,
            "unavailable",
            "Supabase refused the connection. Check the Session Pooler host and port in "
            "DATABASE_URL and confirm the project is active.",
        )
    if "ssl" in message or "tls" in message:
        return (
            error_type,
            "unavailable",
            "The secure connection to Supabase failed. Use the Session Pooler URL with SSL "
            "enabled and remove any conflicting sslmode override.",
        )
    return (
        error_type,
        sqlstate or "unavailable",
        f"Database connection failed ({error_type}). Check the Vercel function logs.",
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not os.getenv("VERCEL"):
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Wholesale-Retail Marketplace API",
    description="Marketplace, recommendation, and demand forecasting API.",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")


@app.get("/api/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            existing_tables = set(inspect(connection).get_table_names())
    except SQLAlchemyError as error:
        error_type, sqlstate, detail = database_connection_failure_detail(error)
        logger.error(
            "Database readiness check failed (driver_error=%s, sqlstate=%s)",
            error_type,
            sqlstate,
        )
        raise HTTPException(
            status_code=503,
            detail=detail,
        ) from error

    missing_tables = sorted(table.name for table in Base.metadata.sorted_tables if table.name not in existing_tables)
    if missing_tables:
        raise HTTPException(
            status_code=503,
            detail="Database schema is not initialized. Run `alembic upgrade head`.",
        )
    return {"status": "ok"}
