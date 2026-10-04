import logging
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from .db import Base, engine, SessionLocal, settings, Movie, Memory, ViewingSession, Recommendation, Feedback, now
from .schemas import Intent, SessionInput, ShuffleInput, FeedbackInput
from . import ai
from .recommender import decide, taste_model, cosine
from .constants import CONFIRMED_WATCHED

logging.basicConfig(level=logging.INFO)
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    yield
app=FastAPI(title='CineBrain', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin],allow_methods=['GET','POST'],allow_headers=['Content-Type'])

@app.exception_handler(ai.AIUnavailable)
async def ai_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503,content={'detail':str(exc)})

def get_session(db, sid):
    session=db.scalar(select(ViewingSession).where(ViewingSession.id==sid).with_for_update())
    if not session: raise HTTPException(404,'Session not found')
    return session

def current(db, session):
    rec=db.scalar(select(Recommendation).where(Recommendation.session_id==session.id).order_by(Recommendation.id.desc()))
    movie=db.get(Movie,rec.movie_id) if rec else None
    return {'session_id':session.id,'status':session.status,'shuffle_count':session.shuffle_count,'selected_movie_id':session.selected_movie_id,'recommendation': {'id':rec.id,'movie_id':movie.id,'title':movie.title,'year':movie.metadata_json.get('year'),'runtime':movie.metadata_json.get('runtime'),'poster_path':movie.metadata_json.get('poster_path'),'overview':movie.metadata_json.get('overview'),'why_it_fits':rec.explanation,'what_it_might_not_fit':rec.caveat} if rec else None}

@app.get('/health')
def health():
    with engine.connect() as conn: conn.execute(text('SELECT 1'))
    return {'status':'ok','database':'ok'}

@app.post('/api/sessions')
def create_session(body: SessionInput):
    raw=ai.generate('Interpret exactly these three answers. Return desired_feelings (list), desired_aftertaste (list), energy, cognitive_capacity, desired_pacing, emotional_intensity, complexity_tolerance, immersion. Numeric values 0..1; unspecified fields null. Do not infer long-term taste.', body.model_dump())
    try: intent=Intent.model_validate(raw).model_dump()
    except ValueError as exc: raise ai.AIUnavailable('Invalid session interpretation. Please retry.') from exc
    intent['exploration']={'understand':.05,'know':.2,'challenge':.6,'surprise':1}[body.trust]
    with SessionLocal.begin() as db:
        session=ViewingSession(id=str(uuid4()),answers={'feel':body.feel,'state':body.state,'trust':body.trust},intent=intent,constraints={'max_runtime':body.max_runtime,'languages':body.languages})
        db.add(session); db.flush()
        if not decide(db,session): raise HTTPException(409,'No eligible enriched movies. Import and enrich a catalogue first, or loosen constraints.')
        return current(db,session)

@app.get('/api/sessions/{sid}')
def session_detail(sid:str):
    with SessionLocal() as db: return current(db,get_session(db,sid))

@app.post('/api/sessions/{sid}/shuffle')
def shuffle(sid:str, body:ShuffleInput):
    with SessionLocal.begin() as db:
        session=get_session(db,sid)
        if session.status!='recommended': raise HTTPException(409,'This session cannot be shuffled')
        rec=db.scalar(select(Recommendation).where(Recommendation.session_id==sid).order_by(Recommendation.id.desc()))
        rec.event='rejected' if body.reason else 'shuffled_away'
        rec.rejection_reason=body.reason or body.note
        constraints=dict(session.constraints)
        movie=db.get(Movie,rec.movie_id)
        if body.reason=='seen_it' and not db.get(Memory,movie.id): db.add(Memory(movie_id=movie.id,reaction={'source':'explicit_seen_it','rating':None,'liked':None,'date_watched':None}))
        if body.reason=='too_heavy': constraints['max_intensity']=max(0,movie.profile['emotional_intensity']-.1)
        if body.reason=='too_long': constraints['max_runtime']=max(1,(movie.metadata_json.get('runtime') or 120)-1)
        session.constraints=constraints; session.shuffle_count+=1; db.flush()
        if not decide(db,session): raise HTTPException(409,'No more eligible movies. Try a new session with broader constraints.')
        return current(db,session)

@app.post('/api/sessions/{sid}/select')
def choose(sid:str):
    with SessionLocal.begin() as db:
        session=get_session(db,sid)
        if session.status!='recommended': raise HTTPException(409,'Session is already resolved')
        rec=db.scalar(select(Recommendation).where(Recommendation.session_id==sid).order_by(Recommendation.id.desc()))
        session.selected_movie_id=rec.movie_id; session.selected_at=now(); session.status='selected'; rec.event='selected'
        return current(db,session)

@app.post('/api/sessions/{sid}/feedback')
def feedback(sid:str, body:FeedbackInput):
    with SessionLocal.begin() as db:
        session=get_session(db,sid)
        if session.status!='selected': raise HTTPException(409,'Select a film before recording feedback; completed feedback cannot be duplicated')
        response=body.model_dump(mode='json')
        db.add(Feedback(session_id=sid,movie_id=session.selected_movie_id,response=response))
        memory=db.get(Memory,session.selected_movie_id)
        if not memory: memory=Memory(movie_id=session.selected_movie_id); db.add(memory)
        memory.watched=True; memory.reaction={**response,'source':'explicit_feedback','date_watched':response.get('date_watched')}; memory.updated_at=now()
        session.status='complete'; db.flush()
        return {'status':'remembered','taste':taste_model(db)}

@app.get('/api/memory')
def memory():
    with SessionLocal() as db:
        records=[{'movie_id':m.movie_id,'title':film.title,'watched':m.watched,'reaction':m.reaction} for m,film in db.execute(select(Memory,Movie).join(Movie,Memory.movie_id==Movie.id))]
        resolved={r['title'].casefold() for r in records}
        return records + [{'movie_id':None,'title':title,'watched':True,'reaction':{'source':'confirmed_seed','rating':None,'liked':None,'date_watched':None,'notes':None}} for title in CONFIRMED_WATCHED if title.casefold() not in resolved]

@app.get('/api/taste')
def taste():
    with SessionLocal() as db: return taste_model(db)

@app.get('/api/analytics')
def analytics():
    with SessionLocal() as db:
        sessions=list(db.scalars(select(ViewingSession))); feedback=list(db.scalars(select(Feedback)))
        fits=[f.response['tonight_fit'] for f in feedback if f.response.get('tonight_fit') is not None]
        return {'sessions':len(sessions),'completed':len(feedback),'average_tonight_fit':sum(fits)/len(fits) if fits else None,'shuffle_counts':{str(n):sum(s.shuffle_count==n for s in sessions) for n in sorted({s.shuffle_count for s in sessions})}}

@app.get('/api/search')
def search(q:str):
    if not q.strip() or len(q)>2000: raise HTTPException(422,'Provide a search phrase under 2000 characters')
    vector=ai.embed(q)
    with SessionLocal() as db:
        if db.bind.dialect.name == 'postgresql':
            from sqlalchemy import cast
            from pgvector.sqlalchemy import Vector
            movies=list(db.scalars(select(Movie).where(Movie.embedding.is_not(None)).order_by(cast(Movie.embedding,Vector(768)).cosine_distance(vector)).limit(20)))
        else:
            movies=sorted([m for m in db.scalars(select(Movie)) if m.embedding],key=lambda m:cosine(vector,m.embedding),reverse=True)
        return [{'id':m.id,'title':m.title,'year':m.metadata_json.get('year'),'overview':m.metadata_json.get('overview')} for m in movies[:20]]
