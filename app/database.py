from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = (
    create_async_engine(settings.DATABASE_URL, echo=False)
    if settings.DATABASE_URL
    else None
)
SessionLocal = (
    async_sessionmaker(bind=engine, expire_on_commit=False)
    if engine is not None
    else None
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if SessionLocal is None:
        raise RuntimeError(
            "Database is not configured. Add DATABASE_URL to your .env file."
        )

    async with SessionLocal() as session:
        yield session


async def get_db_optional() -> AsyncGenerator[AsyncSession | None, None]:
    if SessionLocal is None:
        yield None
        return

    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    if engine is None:
        return

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    except Exception:
        # The app should keep running even if PostgreSQL is not available yet.
        return
