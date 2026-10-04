# CineBrain

A local cinematic companion for Meet. Three questions, exactly one film, a deliberate “think again” button, and personal memory that learns from explicit feedback.

The revised one-film specification takes precedence over the original 3–5-choice flow. No ratings, reactions, likes, or watch dates are invented for the seven confirmed watched films. They appear in memory immediately; TMDB resolution adds identifiers later.

## Start with Docker (recommended)

Install Docker Desktop. Create `.env` from `.env.example` if it doesn't already exist, and set your TMDB API **Read Access Token** privately. Never commit or paste the token.

```bash
docker compose up --build -d
docker compose exec ollama ollama pull llama3.2
docker compose exec ollama ollama pull nomic-embed-text
```

Keep the first TMDB ingestion at **10 pages**:

```bash
source venv/bin/activate
pip install -r requirements.txt
python scripts/tmdb_ingest.py
docker compose exec backend python -m backend.app.catalogue import
docker compose exec backend python -m backend.app.catalogue seed
docker compose exec backend python -m backend.app.catalogue enrich --limit 200
```

Open **http://localhost:3000**. API documentation: http://localhost:8000/docs.

Enrichment uses your local Ollama model and can take time. Both catalogue import and enrichment are resumable. Films without enriched profiles stay out of recommendations. Seed resolution intentionally skips ambiguous TMDB title matches rather than guessing a watched film's identity.

## Run without Docker

Requires Python 3.12+, Node.js 22+, and Ollama. SQLite is the default database for local development; Docker uses PostgreSQL with pgvector.

```bash
source venv/bin/activate
pip install -r backend/requirements.txt
alembic -c backend/alembic.ini upgrade head
ollama serve
```

In another terminal:

```bash
ollama pull llama3.2
ollama pull nomic-embed-text
python scripts/tmdb_ingest.py
python -m backend.app.catalogue import
python -m backend.app.catalogue seed
python -m backend.app.catalogue enrich --limit 200
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

`DATABASE_URL` selects PostgreSQL instead of SQLite. `OLLAMA_URL`, `OLLAMA_MODEL`, and `FRONTEND_ORIGIN` can be configured in `.env`. The browser API URL defaults to `http://localhost:8000`; change `NEXT_PUBLIC_API_URL` before a frontend build when necessary.

## What V1 implements

- Exactly three free-form/state/exploration questions, interpreted by Ollama. Unknown state values stay null.
- One current recommendation and persisted session recovery after refresh.
- Watched, already presented, adult, runtime, language, and contextual burden filters enforced before LLM judgement.
- Separate feel/aftertaste, experience, personality, theme, and cinematic descriptors inferred by Ollama.
- Semantic embeddings using local `nomic-embed-text`; pgvector storage/search on PostgreSQL, cosine search on SQLite.
- Auditable taste, feel, state, semantic, quality, and exploration score components.
- Re-evaluation on shuffle with increasing exploration; previously presented films cannot repeat within a session.
- Optional rejection reasons. “Too heavy” and “too long” constrain the current session. “Seen it” is explicit watched confirmation. Shuffle alone never creates a dislike.
- Selecting a film starts a feedback state; it does **not** mark it watched.
- Feedback keeps movie quality, personal rating, and tonight fit separate. Reactions update an evidence-based taste hypothesis with confidence, evidence count, source films, and update time.
- Memory, taste, semantic-search, and basic session/fit analytics APIs.
- Docker Compose, FastAPI, Next.js/React/TypeScript/Tailwind, SQLAlchemy, Alembic, PostgreSQL/pgvector, and local Ollama.

The LLM cannot override hard filters or supply arbitrary movie IDs. An unavailable model produces a retryable error rather than fabricated AI output. Feedback and session transitions are transactional. PostgreSQL locks a session during state changes.

## Validation

```bash
venv/bin/python -m pytest backend/tests -q
cd frontend
npm run typecheck
npm run build
```

Tests use isolated synthetic catalogue entries and stubbed Ollama responses; they never add fictitious films to your real memory. The integration test proves the second session has new taste evidence and adjusted scores after feedback.

## Current limits

This is a single-user local app with no account/login layer. Ports in Compose bind to localhost. Keep it local. General movie profiles are model inferences, and explanations can still be mistaken. Embeddings use Ollama instead of sentence-transformers to avoid a second large model runtime; PostgreSQL vectors are 768-dimensional. Advanced taste evolution, blind-spot charts, feedback-text interpretation, learned scoring weights, and contextual bandits remain future work. Rating-based trait evidence is currently the learning signal; free-form feedback is preserved and supplied to contextual judgement.

The original ingestion scripts remain available under `scripts/`. `scripts/import_meet_history.py` is a legacy importer; use the new `catalogue seed` command for ambiguity-safe resolution. Neither command should be used to invent watch-history records.
