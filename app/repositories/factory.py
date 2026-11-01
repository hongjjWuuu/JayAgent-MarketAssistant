from functools import lru_cache

from app.core.config import settings
from app.repositories.contracts import MarketRepository
from app.repositories.memory import store
from app.repositories.postgres import PostgresRepository


@lru_cache
def get_repository() -> MarketRepository:
    if settings.persistence_backend == "postgres":
        return PostgresRepository()
    return store
