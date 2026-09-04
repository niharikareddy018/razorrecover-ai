"""
DB engine + session setup.

SQLite file lives at backend/razorrecover.db. To move to Postgres for
production, change SQLALCHEMY_DATABASE_URL to something like:
  postgresql://user:password@localhost:5432/razorrecover
and add `psycopg2-binary` to requirements.txt. Nothing else changes.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

SQLALCHEMY_DATABASE_URL = "sqlite:///./razorrecover.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
