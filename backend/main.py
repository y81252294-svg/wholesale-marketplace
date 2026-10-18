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
        original_error = error.orig
        sqlstate = getattr(original_error, "sqlstate", None)
        error_type = type(original_error).__name__
        logger.error(
            "Database readiness check failed (driver_error=%s, sqlstate=%s)",
            error_type,
            sqlstate or "unavailable",
        )
        if sqlstate == "28P01":
            detail = (
                "Database authentication failed. Check the Supabase Session Pooler username "
                "and current database password in Vercel's DATABASE_URL."
            )
        elif sqlstate == "3D000":
            detail = (
                "The database named in DATABASE_URL does not exist. Check the database name "
                "in the Supabase Session Pooler URL."
            )
        elif sqlstate and sqlstate.startswith("08"):
            detail = (
                "The database connection could not be established. Check the Supabase pooler "
                "host, port, project status, and network availability."
            )
        elif error_type == "OperationalError":
            detail = (
                "The database connection failed. Check that DATABASE_URL is the Supabase "
                "Session Pooler URL, with the correct host, port, and current password."
            )
        else:
            detail = f"Database connection failed ({error_type}). Check the Vercel function logs."
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
