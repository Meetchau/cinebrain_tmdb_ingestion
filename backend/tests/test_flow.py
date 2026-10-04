import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.app import main, ai
from backend.app.db import Base, Movie, Memory, Recommendation, ViewingSession
from backend.app.recommender import taste_model, rank

PROFILE=dict(feelings=['wonder'],aftertaste=['reflective'],emotional_intensity=.5,energy=.5,cognitive_load=.5,complexity=.5,immersion=.8,pacing='medium',personality=['authorial'],themes=['identity'],cinematic=['expressive'])
@pytest.fixture
def client(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(main,'SessionLocal',factory);monkeypatch.setattr(main,'engine',engine)
    monkeypatch.setattr(ai,'embed',lambda _: [1.,0.])
    def generate(task,payload):
        if 'Interpret' in task: return {'desired_feelings':['wonder'],'desired_aftertaste':['reflective']}
        return {'movie_id':payload['candidates'][0]['movie_id'],'why_it_fits':'You asked for wonder and reflection.','what_it_might_not_fit':'It asks for some attention.'}
    monkeypatch.setattr(ai,'generate',generate)
    with factory.begin() as db:
        for i in range(1,7): db.add(Movie(tmdb_id=i,title=f'Film {i}',metadata_json={'year':'2000','runtime':100,'original_language':'en','vote_average':8,'vote_count':100},profile=dict(PROFILE),embedding=[1.,0.]))
        db.add(Memory(movie_id=1,reaction={'source':'confirmed_seed','rating':None}))
    with TestClient(main.app) as c: yield c,factory

PAYLOAD={'feel':'Wonder','state':'A quiet night','trust':'understand'}
def test_full_loop_learns_and_remembers(client):
    c,factory=client
    first=c.post('/api/sessions',json=PAYLOAD);assert first.status_code==200
    data=first.json();sid=data['session_id'];first_movie=data['recommendation']['movie_id'];assert first_movie!=1
    assert c.get('/api/taste').json()==[]
    other=c.post(f'/api/sessions/{sid}/shuffle',json={});assert other.status_code==200
    selected=other.json()['recommendation']['movie_id'];assert selected not in [1,first_movie]
    assert c.get('/api/taste').json()==[]
    assert c.post(f'/api/sessions/{sid}/select',json={}).status_code==200
    assert all(m['movie_id']!=selected for m in c.get('/api/memory').json())
    assert c.post(f'/api/sessions/{sid}/shuffle',json={}).status_code==409
    feedback={'reaction':'Loved it','movie_quality':9,'meet_rating':8,'tonight_fit':3,'emotional_reaction':'Beautiful, but wrong for tonight.'}
    assert c.post(f'/api/sessions/{sid}/feedback',json=feedback).status_code==200
    assert c.post(f'/api/sessions/{sid}/feedback',json=feedback).status_code==409
    traits=c.get('/api/taste').json();assert traits and traits[0]['confidence']==.25
    memory=next(m for m in c.get('/api/memory').json() if m['movie_id']==selected)
    assert memory['reaction']['movie_quality']==9 and memory['reaction']['tonight_fit']==3
    assert memory['reaction']['date_watched'] is None
    with factory() as db:
        s=ViewingSession(id='check',answers={},intent={},constraints={})
        ranked=rank(db,s,[1,0]);assert all(m.id!=selected for m,_ in ranked)
        assert any(scores['taste']>.5 for _,scores in ranked)
    second=c.post('/api/sessions',json=PAYLOAD);assert second.status_code==200
    assert second.json()['recommendation']['movie_id'] not in [1,selected]

def test_constraints_and_validation(client):
    c,_=client
    assert c.post('/api/sessions',json={**PAYLOAD,'max_runtime':90}).status_code==409
    assert c.post('/api/sessions',json={**PAYLOAD,'languages':['fr']}).status_code==409
    assert c.post('/api/sessions',json={**PAYLOAD,'trust':'invalid'}).status_code==422
    assert c.post('/api/sessions',json={**PAYLOAD,'feel':''}).status_code==422

def test_ai_cannot_override_candidates(client,monkeypatch):
    c,_=client
    original=ai.generate
    monkeypatch.setattr(ai,'generate',lambda task,payload:original(task,payload) if 'Interpret' in task else {'movie_id':1,'why_it_fits':'Invented','what_it_might_not_fit':''})
    assert c.post('/api/sessions',json=PAYLOAD).status_code==503

def test_seen_it_is_confirmation_not_dislike(client):
    c,_=client
    session=c.post('/api/sessions',json=PAYLOAD).json();sid=session['session_id'];movie=session['recommendation']['movie_id']
    assert c.post(f'/api/sessions/{sid}/shuffle',json={'reason':'seen_it'}).status_code==200
    assert any(m['movie_id']==movie for m in c.get('/api/memory').json())
    assert c.get('/api/taste').json()==[]

def test_failed_shuffle_rolls_back_session(client,monkeypatch):
    c,factory=client
    session=c.post('/api/sessions',json=PAYLOAD).json();sid=session['session_id']
    monkeypatch.setattr(ai,'generate',lambda *args: (_ for _ in ()).throw(ai.AIUnavailable('Model offline')))
    assert c.post(f'/api/sessions/{sid}/shuffle',json={}).status_code==503
    after=c.get('/api/sessions/'+sid).json()
    assert after['shuffle_count']==0
    assert after['recommendation']['movie_id']==session['recommendation']['movie_id']
    with factory() as db:
        assert db.scalar(select(Recommendation).where(Recommendation.session_id==sid)).event=='presented'

def test_confirmed_seed_memory_has_no_invented_reactions(client):
    c,_=client
    memory=c.get('/api/memory').json()
    pyaasa=next(m for m in memory if m['title']=='Pyaasa')
    assert pyaasa['watched'] is True and pyaasa['movie_id'] is None
    assert pyaasa['reaction']['rating'] is None
    assert pyaasa['reaction']['date_watched'] is None
    assert not any(m['title'] in ['Memories of Murder','Zodiac','The Apartment'] for m in memory)
