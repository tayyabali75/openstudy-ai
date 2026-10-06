#!/usr/bin/env python3
r"""
OpenStudy AI - one-shot project generator.

Usage:
    python setup_openstudy.py [target_dir]

Creates a ready-to-run FastAPI project with:
  - AI tutor (personalized Q&A at a chosen level)
  - Flashcard generation + storage
  - Quiz generation
  - Study plan generator
  - Notes/text summarizer
  - SQLite persistence, tests, Dockerfile, README

Then:
    cd openstudy-ai
    python -m venv .venv && source .venv/bin/activate   (Windows: .venv\Scripts\activate)
    pip install -r requirements.txt
    cp .env.example .env     # add your ANTHROPIC_API_KEY
    uvicorn app.main:app --reload
    # open http://127.0.0.1:8000/docs
"""
import sys
from pathlib import Path

FILES = {}

# --------------------------------------------------------------------------- #
FILES["requirements.txt"] = r"""fastapi>=0.115
uvicorn[standard]>=0.30
sqlalchemy>=2.0
pydantic>=2.7
pydantic-settings>=2.3
anthropic>=0.40
python-multipart>=0.0.9
pypdf>=4.2
pytest>=8.0
httpx>=0.27
"""

FILES[".env.example"] = r"""ANTHROPIC_API_KEY=your-key-here
OPENSTUDY_MODEL=claude-sonnet-5-5
DATABASE_URL=sqlite:///./openstudy.db
"""

FILES[".gitignore"] = r""".venv/
__pycache__/
*.pyc
.env
*.db
.pytest_cache/
"""

# --------------------------------------------------------------------------- #
FILES["app/__init__.py"] = ""
FILES["app/ai/__init__.py"] = ""
FILES["app/routers/__init__.py"] = ""
FILES["tests/__init__.py"] = ""

FILES["app/config.py"] = r"""from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    openstudy_model: str = "claude-sonnet-5-5"
    database_url: str = "sqlite:///./openstudy.db"
    app_name: str = "OpenStudy AI"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
"""

FILES["app/database.py"] = r"""from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
"""

FILES["app/models.py"] = r"""from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Flashcard(Base):
    __tablename__ = "flashcards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    topic: Mapped[str] = mapped_column(String(200), index=True)
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StudyPlan(Base):
    __tablename__ = "study_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    subject: Mapped[str] = mapped_column(String(200))
    plan_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
"""

FILES["app/schemas.py"] = r"""from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Level = Literal["beginner", "intermediate", "advanced"]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class TutorRequest(BaseModel):
    question: str = Field(min_length=1)
    level: Level = "beginner"
    subject: Optional[str] = None
    history: list[ChatTurn] = []


class TutorResponse(BaseModel):
    answer: str


class FlashcardRequest(BaseModel):
    topic: str = Field(min_length=1)
    source_text: Optional[str] = None
    count: int = Field(default=10, ge=1, le=30)
    save: bool = True


class FlashcardOut(BaseModel):
    id: Optional[int] = None
    topic: str
    front: str
    back: str
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class QuizRequest(BaseModel):
    topic: str = Field(min_length=1)
    source_text: Optional[str] = None
    num_questions: int = Field(default=5, ge=1, le=20)
    level: Level = "intermediate"


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    answer_index: int
    explanation: str


class QuizResponse(BaseModel):
    topic: str
    questions: list[QuizQuestion]


class PlanRequest(BaseModel):
    subject: str = Field(min_length=1)
    goals: str = ""
    days: int = Field(default=14, ge=1, le=120)
    hours_per_day: float = Field(default=2, gt=0, le=12)
    level: Level = "beginner"


class SummarizeRequest(BaseModel):
    text: str = Field(min_length=20)
    style: Literal["bullets", "paragraph", "outline"] = "bullets"


class SummarizeResponse(BaseModel):
    summary: str
"""

# --------------------------------------------------------------------------- #
FILES["app/ai/client.py"] = r"""import json
import re

from anthropic import Anthropic

from app.config import settings

_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def ask(system: str, messages: list[dict], max_tokens: int = 1500) -> str:
    resp = get_client().messages.create(
        model=settings.openstudy_model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    return "".join(block.text for block in resp.content if block.type == "text").strip()


def ask_json(system: str, prompt: str, max_tokens: int = 3000):
    system += "\nRespond with valid JSON only. No prose, no markdown fences."
    text = ask(system, [{"role": "user", "content": prompt}], max_tokens=max_tokens)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)
"""

FILES["app/ai/prompts.py"] = r"""TUTOR_SYSTEM = (
    "You are OpenStudy AI, a patient, encouraging study tutor. "
    "Explain clearly at the learner's level ({level}){subject_clause}. "
    "Use short examples and analogies, check understanding with one brief follow-up question, "
    "and guide the student toward the answer rather than only giving it when they are working on homework. "
    "If you are unsure about a fact, say so."
)

FLASHCARD_SYSTEM = (
    "You create high-quality study flashcards. Each card tests one atomic idea. "
    "Return a JSON array of objects with keys 'front' and 'back'."
)

QUIZ_SYSTEM = (
    "You write accurate multiple-choice quizzes with plausible distractors. "
    "Return a JSON array of objects with keys: 'question', 'options' (exactly 4 strings), "
    "'answer_index' (0-3), 'explanation'."
)

PLAN_SYSTEM = (
    "You are a study-planning coach. Build realistic, spaced, goal-driven plans. "
    "Return a JSON object: {'overview': str, 'days': [{'day': int, 'focus': str, "
    "'tasks': [str], 'minutes': int}], 'tips': [str]}."
)

SUMMARY_SYSTEM = (
    "You summarize study material faithfully and concisely, keeping key terms, "
    "definitions, and formulas. Do not add facts that are not in the text."
)
"""

FILES["app/ai/services.py"] = r"""import json

from app.ai import prompts
from app.ai.client import ask, ask_json
from app.schemas import (
    FlashcardRequest,
    PlanRequest,
    QuizRequest,
    SummarizeRequest,
    TutorRequest,
)


def tutor_answer(req: TutorRequest) -> str:
    subject_clause = f" in {req.subject}" if req.subject else ""
    system = prompts.TUTOR_SYSTEM.format(level=req.level, subject_clause=subject_clause)
    messages = [t.model_dump() for t in req.history]
    messages.append({"role": "user", "content": req.question})
    return ask(system, messages)


def generate_flashcards(req: FlashcardRequest) -> list[dict]:
    prompt = f"Create {req.count} flashcards on: {req.topic}."
    if req.source_text:
        prompt += f"\nBase them only on this material:\n{req.source_text[:12000]}"
    return ask_json(prompts.FLASHCARD_SYSTEM, prompt)


def generate_quiz(req: QuizRequest) -> list[dict]:
    prompt = f"Write {req.num_questions} {req.level}-level questions on: {req.topic}."
    if req.source_text:
        prompt += f"\nBase them only on this material:\n{req.source_text[:12000]}"
    return ask_json(prompts.QUIZ_SYSTEM, prompt)


def generate_plan(req: PlanRequest) -> dict:
    prompt = (
        f"Subject: {req.subject}\nLevel: {req.level}\nGoals: {req.goals or 'general mastery'}\n"
        f"Days available: {req.days}\nHours per day: {req.hours_per_day}"
    )
    return ask_json(prompts.PLAN_SYSTEM, prompt, max_tokens=4000)


def summarize(req: SummarizeRequest) -> str:
    prompt = f"Summarize as {req.style}:\n\n{req.text[:20000]}"
    return ask(prompts.SUMMARY_SYSTEM, [{"role": "user", "content": prompt}])


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
"""

# --------------------------------------------------------------------------- #
FILES["app/routers/tutor.py"] = r"""from fastapi import APIRouter, HTTPException

from app.ai import services
from app.schemas import SummarizeRequest, SummarizeResponse, TutorRequest, TutorResponse

router = APIRouter(prefix="/api", tags=["tutor"])


@router.post("/tutor/ask", response_model=TutorResponse)
def ask_tutor(req: TutorRequest):
    try:
        return TutorResponse(answer=services.tutor_answer(req))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/summarize", response_model=SummarizeResponse)
def summarize(req: SummarizeRequest):
    try:
        return SummarizeResponse(summary=services.summarize(req))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
"""

FILES["app/routers/flashcards.py"] = r"""from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai import services
from app.database import get_db
from app.models import Flashcard
from app.schemas import FlashcardOut, FlashcardRequest

router = APIRouter(prefix="/api/flashcards", tags=["flashcards"])


@router.post("/generate", response_model=list[FlashcardOut])
def generate(req: FlashcardRequest, db: Session = Depends(get_db)):
    try:
        cards = services.generate_flashcards(req)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError:
        raise HTTPException(status_code=502, detail="AI returned an unreadable response. Try again.")

    rows = [Flashcard(topic=req.topic, front=c["front"], back=c["back"]) for c in cards]
    if req.save:
        db.add_all(rows)
        db.commit()
        for r in rows:
            db.refresh(r)
    return [FlashcardOut.model_validate(r) if req.save else FlashcardOut(topic=req.topic, front=r.front, back=r.back) for r in rows]


@router.get("", response_model=list[FlashcardOut])
def list_cards(topic: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Flashcard)
    if topic:
        q = q.filter(Flashcard.topic == topic)
    return q.order_by(Flashcard.id.desc()).all()


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = db.get(Flashcard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    db.delete(card)
    db.commit()
"""

FILES["app/routers/quizzes.py"] = r"""from fastapi import APIRouter, HTTPException

from app.ai import services
from app.schemas import QuizQuestion, QuizRequest, QuizResponse

router = APIRouter(prefix="/api/quizzes", tags=["quizzes"])


@router.post("/generate", response_model=QuizResponse)
def generate(req: QuizRequest):
    try:
        raw = services.generate_quiz(req)
        questions = [QuizQuestion(**q) for q in raw]
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=502, detail="AI returned an unreadable response. Try again.")
    return QuizResponse(topic=req.topic, questions=questions)
"""

FILES["app/routers/plans.py"] = r"""import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai import services
from app.database import get_db
from app.models import StudyPlan
from app.schemas import PlanRequest

router = APIRouter(prefix="/api/plans", tags=["study plans"])


@router.post("/generate")
def generate(req: PlanRequest, db: Session = Depends(get_db)):
    try:
        plan = services.generate_plan(req)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError:
        raise HTTPException(status_code=502, detail="AI returned an unreadable response. Try again.")
    row = StudyPlan(subject=req.subject, plan_json=services.dumps(plan))
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "subject": row.subject, "plan": plan}


@router.get("/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    row = db.get(StudyPlan, plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"id": row.id, "subject": row.subject, "plan": json.loads(row.plan_json)}
"""

FILES["app/main.py"] = r"""from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers import flashcards, plans, quizzes, tutor


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    description="AI-powered study platform: tutor, flashcards, quizzes, study plans, summaries.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tutor.router)
app.include_router(flashcards.router)
app.include_router(quizzes.router)
app.include_router(plans.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "app": settings.app_name}
"""

FILES["tests/test_api.py"] = r"""from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_tutor_ask_mocked():
    with patch("app.ai.services.ask", return_value="Photosynthesis makes sugar from light."):
        r = client.post("/api/tutor/ask", json={"question": "What is photosynthesis?", "level": "beginner"})
    assert r.status_code == 200
    assert "Photosynthesis" in r.json()["answer"]


def test_quiz_mocked():
    fake = [{"question": "2+2?", "options": ["3", "4", "5", "6"], "answer_index": 1, "explanation": "Basic sum."}]
    with patch("app.ai.services.ask_json", return_value=fake):
        r = client.post("/api/quizzes/generate", json={"topic": "math", "num_questions": 1})
    assert r.status_code == 200
    assert r.json()["questions"][0]["answer_index"] == 1
"""

# --------------------------------------------------------------------------- #
FILES["Dockerfile"] = r"""FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

FILES["README.md"] = r"""# OpenStudy AI

An AI-powered study platform that helps students learn smarter with personalized assistance,
study resources, and intelligent learning tools.

## Features
- **AI Tutor** - level-aware explanations with conversation history (`POST /api/tutor/ask`)
- **Flashcards** - generate from a topic or your own notes, saved to SQLite (`/api/flashcards`)
- **Quizzes** - multiple-choice with explanations (`POST /api/quizzes/generate`)
- **Study Plans** - day-by-day schedules from your goals and time (`POST /api/plans/generate`)
- **Summarizer** - bullets, paragraph, or outline (`POST /api/summarize`)

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```
Interactive docs: http://127.0.0.1:8000/docs

## Test
```bash
pytest
```

## Docker
```bash
docker build -t openstudy-ai .
docker run -p 8000:8000 --env-file .env openstudy-ai
```

## Roadmap
User accounts and auth, spaced-repetition scheduling for flashcards, PDF/notes upload
(`pypdf` is already in requirements), progress analytics, and a web front end.
"""


def main():
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "openstudy-ai")
    for rel, content in FILES.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"created {path}")
    print(f"\nOpenStudy AI scaffolded in ./{target}")
    print("Next: cd", target, "&& pip install -r requirements.txt && cp .env.example .env")
    print("Then: uvicorn app.main:app --reload   ->  http://127.0.0.1:8000/docs")


if __name__ == "__main__":
    main()
