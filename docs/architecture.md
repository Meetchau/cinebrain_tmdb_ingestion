# Architecture and data truth

The second attached specification overrides the first: the client receives one current recommendation, never a list of ranked alternatives. Candidate rankings remain internal and their component scores are persisted for review. Engine 1.1 builds a diversified shortlist among candidates within 0.18 of the top score, penalizing repeated decades, languages, and directors during shortlist selection. The original component scores remain unchanged. Age or recency does not receive a quality bonus.

`backend/app/db.py` owns SQLAlchemy persistence and environment configuration. `schemas.py` validates user input and model-produced profiles. `ai.py` calls local Ollama using structured JSON. `recommender.py` owns hard filtering, semantic comparison, weighted scoring, taste evidence, and validated final model judgement. `discovery.py` expands a separate historical/world-cinema catalogue with stored route provenance; `catalogue.py` imports TMDB JSONL, resolves confirmed seeds conservatively, and enriches profiles/embeddings. `main.py` exposes transactional session, feedback, memory, search, and analytics APIs. The frontend persists only the active session identifier in browser storage; cinematic history resides in the local database.

## State transitions

A session is created only when interpretation and recommendation succeed. It starts `thinking`, then becomes `recommended`. Shuffle/rejection re-evaluates an eligible pool and leaves it `recommended`; selection changes it to `selected`; explicit feedback changes it to `complete`. Invalid transitions return HTTP 409. API/AI failures roll back the transaction, preserving the last usable recommendation. SQLite writes are postponed until model calls finish; a concurrency test verifies a separate catalogue writer can commit during contextual judgement. Enrichment also performs inference outside its write transaction and retries transient SQLite locks. PostgreSQL locks the session row to prevent concurrent shuffle/select/feedback updates.

Selection is intent to watch, not a watched fact. Shuffle is a presentation event, not dislike. “Seen it” is explicit confirmation. Feedback establishes watched status; the watch date remains unknown unless supplied. Feedback timestamps record submission time separately.

The seven confirmed watched titles are an authoritative source constant. Before TMDB resolution they are returned as unresolved memory with null movie identifiers. Matching titles are excluded conservatively from recommendations; remakes sharing those titles may also be excluded until a richer identity model is added. TMDB seed search refuses non-unique exact-title matches.

## Learning

Only explicit post-viewing reactions support taste hypotheses. Each profile trait receives signed reaction evidence, a mean strength, evidence count, source titles, and confidence `n/(n+3)`. A single reaction has confidence 0.25. Unknown reactions contribute no evidence. Both likes and dislikes affect future taste scores. Genre is excluded from this representation.

The session's feel/state remains distinct from long-term taste. Shuffle gradually raises the exploration target. Rejection burden/runtime constraints apply to the session alone. Quality uses TMDB votes with a conservative prior and receives only 5% of the starting score. Exploration compares known exposure across languages, directors, countries, and decades. Emotional matches normalize common word forms. The six starting weights match the original specification. They are kept in `WEIGHTS` in `recommender.py` for explicit tuning.

Feedback quality, personal rating, and tonight fit are separate nullable values. Their free-form reaction/worked/failed notes remain authoritative user text and are passed to final contextual judgement. General profile descriptors are labelled `ollama_inference`, never substituted for personal reactions.

## Deployment

Docker runs PostgreSQL 16 with pgvector, Ollama, the API, and Next.js. Named volumes preserve database and model state; catalogue files are mounted from `data/`. PostgreSQL stores native 768-dimensional vectors and executes cosine-distance queries. SQLite stores vector arrays in JSON and computes cosine scores in Python for a lightweight development setup.

The app is intended for a single user on localhost. There is no public deployment or authentication flow. Ollama models and catalogue content are prerequisites for real recommendations. Synthetic fixtures appear only in isolated test databases.

## Verification boundaries

Backend tests stub Ollama to verify database rules, ranking changes, feedback persistence, state transitions, unknown values, and candidate membership. A passing test does not prove model reasoning quality or live TMDB credentials. Frontend type checks and production builds verify compilation; real browser usability and PostgreSQL runtime require their services. Docker is not installed in the initial workspace, so Compose runtime remains unverified there.
