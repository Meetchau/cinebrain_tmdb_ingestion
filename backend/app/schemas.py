from typing import Literal
from datetime import date
from pydantic import BaseModel, Field, ConfigDict

class Intent(BaseModel):
    model_config = ConfigDict(extra='ignore')
    desired_feelings: list[str] = Field(default_factory=list)
    desired_aftertaste: list[str] = Field(default_factory=list)
    energy: float | None = Field(None, ge=0, le=1)
    cognitive_capacity: float | None = Field(None, ge=0, le=1)
    desired_pacing: str | None = None
    emotional_intensity: float | None = Field(None, ge=0, le=1)
    complexity_tolerance: float | None = Field(None, ge=0, le=1)
    immersion: float | None = Field(None, ge=0, le=1)
    exploration: float = Field(0, ge=0, le=1)

class Profile(BaseModel):
    feelings: list[str]
    aftertaste: list[str]
    emotional_intensity: float = Field(ge=0, le=1)
    energy: float = Field(ge=0, le=1)
    cognitive_load: float = Field(ge=0, le=1)
    complexity: float = Field(ge=0, le=1)
    immersion: float = Field(ge=0, le=1)
    pacing: str
    personality: list[str]
    themes: list[str]
    cinematic: list[str]
    source: str = 'ollama_inference'

class SessionInput(BaseModel):
    feel: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=1, max_length=2000)
    trust: Literal['understand', 'know', 'challenge', 'surprise']
    max_runtime: int | None = Field(None, ge=1, le=600)
    languages: list[str] = Field(default_factory=list)

class ShuffleInput(BaseModel):
    reason: Literal['too_heavy', 'seen_it', 'not_in_mood', 'not_interested', 'too_long', 'other'] | None = None
    note: str | None = Field(None, max_length=2000)

class FeedbackInput(BaseModel):
    reaction: Literal['Loved it', 'Liked it', 'It was fine', "Didn’t like it", 'Hated it']
    date_watched: date | None = None
    rewatched: bool | None = None
    movie_quality: float | None = Field(None, ge=1, le=10)
    meet_rating: float | None = Field(None, ge=1, le=10)
    tonight_fit: float | None = Field(None, ge=1, le=10)
    emotional_reaction: str | None = Field(None, max_length=4000)
    what_worked: str | None = Field(None, max_length=4000)
    what_failed: str | None = Field(None, max_length=4000)
