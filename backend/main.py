from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from backend.api import router
from backend.database import Base, engine
from backend import models  # noqa: F401


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
        raise HTTPException(
            status_code=503,
            detail="Database unavailable. Verify DATABASE_URL and hosted database connectivity.",
        ) from error

    missing_tables = sorted(table.name for table in Base.metadata.sorted_tables if table.name not in existing_tables)
    if missing_tables:
        raise HTTPException(
            status_code=503,
            detail="Database schema is not initialized. Run `alembic upgrade head`.",
        )
    return {"status": "ok"}
