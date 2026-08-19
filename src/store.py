"""Scaffold compatibility layer: storage and persistence integration."""
from src.storage.postgres import PostgresStorage, PostgresJobRepository
from src.storage.memory import InMemoryStorage
