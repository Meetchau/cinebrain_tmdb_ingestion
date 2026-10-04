from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, ForeignKey, JSON, String, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from pydantic_settings import BaseSettings, SettingsConfigDict
from pgvector.sqlalchemy import Vector

ROOT = Path(__file__).resolve().parents[2]
class Settings(BaseSettings):
    database_url: str = f"sqlite:///{ROOT}/data/cinebrain.db"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    tmdb_api_token: str = ""
    frontend_origin: str = "http://localhost:3000"
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')

settings = Settings()
engine = create_engine(settings.database_url, connect_args={'check_same_thread': False} if settings.database_url.startswith('sqlite') else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass

def now(): return datetime.now(timezone.utc)

class Movie(Base):
    __tablename__ = 'movies'
    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(unique=True)
    title: Mapped[str]
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON().with_variant(Vector(768), "postgresql"), nullable=True)

class Memory(Base):
    __tablename__ = 'meet_movie_memory'
    movie_id: Mapped[int] = mapped_column(ForeignKey('movies.id'), primary_key=True)
    watched: Mapped[bool] = mapped_column(default=True)
    reaction: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ViewingSession(Base):
    __tablename__ = 'recommendation_sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(default='thinking')
    answers: Mapped[dict] = mapped_column(JSON)
    intent: Mapped[dict] = mapped_column(JSON)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    shuffle_count: Mapped[int] = mapped_column(default=0)
    selected_movie_id: Mapped[int | None] = mapped_column(ForeignKey('movies.id'), nullable=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Recommendation(Base):
    __tablename__ = 'recommendations'
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('recommendation_sessions.id'))
    movie_id: Mapped[int] = mapped_column(ForeignKey('movies.id'))
    scores: Mapped[dict] = mapped_column(JSON)
    explanation: Mapped[str]
    caveat: Mapped[str]
    event: Mapped[str] = mapped_column(default='presented')
    rejection_reason: Mapped[str | None] = mapped_column(nullable=True)
    engine_version: Mapped[str] = mapped_column(default='1.0')

class Feedback(Base):
    __tablename__ = 'feedback'
    session_id: Mapped[str] = mapped_column(ForeignKey('recommendation_sessions.id'), primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey('movies.id'))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
