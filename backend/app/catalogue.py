"""Run from the repository root: venv/bin/python -m backend.app.catalogue import|enrich|seed"""
import argparse
import json
import time
from pathlib import Path
import requests
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from .db import Base, engine, SessionLocal, settings, Movie, Memory
from .schemas import Profile
from . import ai

SEEDS=['Pyaasa','Kaagaz Ke Phool','Gangs of Wasseypur','The Wolf of Wall Street','The Handmaiden','The Silence of the Lambs','Pulp Fiction']

def import_records(path):
    count=0
    with SessionLocal.begin() as db:
        with Path(path).open() as file:
            for line in file:
                record=json.loads(line)
                if not isinstance(record.get('tmdb_id'),int) or not record.get('title'): raise ValueError('Each movie requires tmdb_id and title')
                movie=db.scalar(select(Movie).where(Movie.tmdb_id==record['tmdb_id']))
                if not movie: movie=Movie(tmdb_id=record['tmdb_id'],title=record['title']); db.add(movie)
                movie.metadata_json=record; movie.title=record['title']; count+=1
    print(f'Imported {count} records (upsert by TMDB ID).')

def enrich(limit, collection=None):
    with SessionLocal() as db:
        movies=list(db.scalars(select(Movie)))
        pending=[m for m in movies if m.profile is None or m.embedding is None]
        if collection: pending=[m for m in pending if m.metadata_json.get('collection')==collection]
        # Preserve the broader back catalogue when the default job is resumed.
        pending.sort(key=lambda m:(m.metadata_json.get('collection')!='deep',str(m.metadata_json.get('year','')),m.id))
        ids=[m.id for m in pending[:limit]]
    failures=[]
    for movie_id in ids:
        # Model calls happen outside the database write transaction.
        with SessionLocal() as db:
            movie=db.get(Movie,movie_id)
            data=dict(movie.metadata_json); profile=movie.profile; title=movie.title
        try:
            if not profile:
                facts={key:data.get(key) for key in ['title','original_title','year','runtime','overview','original_language','genres','keywords']}
                facts['directors']=[person.get('name') for person in data.get('directors',[])]
                facts['cast']=[person.get('name') for person in data.get('cast',[])[:8]]
                profile=ai.structured('Create an inferred cinematic experience profile from this film metadata. Return feelings and aftertaste arrays; emotional_intensity, energy, cognitive_load, complexity, immersion as decimal fractions between 0.0 and 1.0 ONLY (example: 0.65); pacing string; personality, themes, cinematic as arrays of semantic descriptors. Describe the specific cinematic experience, authorial style, character work, rhythm, atmosphere, and emotional residue; avoid generic genre descriptions. Differentiate during-film feelings from aftertaste. Do not invent user reactions.',facts,Profile).model_dump()
                profile['source']='ollama_inference'
            vector=ai.embed(json.dumps({'title':title,'overview':data.get('overview'),'profile':profile}))
            for attempt in range(3):
                try:
                    with SessionLocal.begin() as db:
                        movie=db.get(Movie,movie_id);movie.profile=profile;movie.embedding=vector
                    break
                except OperationalError as exc:
                    if 'locked' not in str(exc).lower() or attempt==2: raise
                    time.sleep(2**attempt)
            print(f'Enriched: {title}',flush=True)
        except (ai.AIUnavailable, ValueError) as exc:
            failures.append(title)
            print(f'Enrichment deferred: {title}: {exc}',flush=True)
    if failures:
        raise RuntimeError(f'{len(failures)} films need a retry: '+', '.join(failures))

def tmdb(endpoint, params=None):
    token=settings.tmdb_api_token
    if not token or token.startswith('your_'): raise ValueError('Configure a real TMDB_API_TOKEN in .env first')
    for attempt in range(4):
        response=requests.get('https://api.themoviedb.org/3'+endpoint,headers={'Authorization':f'Bearer {token}'},params=params,timeout=30)
        if response.status_code==429 or response.status_code>=500:
            time.sleep(min(60,int(response.headers.get('Retry-After',2**attempt)))); continue
        response.raise_for_status(); time.sleep(.15); return response.json()
    raise RuntimeError('TMDB retry limit reached')

def seed():
    for title in SEEDS:
        results=tmdb('/search/movie',{'query':title,'include_adult':'false'})['results']
        exact=[r for r in results if r.get('title','').casefold()==title.casefold() or r.get('original_title','').casefold()==title.casefold()]
        if len(exact)!=1:
            print(f'Needs manual resolution: {title} ({len(exact)} exact matches). No history added.'); continue
        record=tmdb(f"/movie/{exact[0]['id']}",{'append_to_response':'credits,keywords','language':'en-US'})
        movie_data={**record,'tmdb_id':record['id'],'year':record.get('release_date','')[:4],'cast':record.get('credits',{}).get('cast',[])[:20],'directors':[p for p in record.get('credits',{}).get('crew',[]) if p.get('job')=='Director']}
        with SessionLocal.begin() as db:
            movie=db.scalar(select(Movie).where(Movie.tmdb_id==record['id']))
            if not movie: movie=Movie(tmdb_id=record['id'],title=record['title'],metadata_json=movie_data); db.add(movie); db.flush()
            if not db.get(Memory,movie.id): db.add(Memory(movie_id=movie.id,reaction={'source':'confirmed_seed','rating':None,'liked':None,'date_watched':None,'notes':None}))
        print(f'Confirmed watched: {title}')

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['import','enrich','seed']); parser.add_argument('--file',default='data/raw/tmdb_movies.jsonl'); parser.add_argument('--limit',type=int,default=200); parser.add_argument('--collection',choices=['deep'])
    args=parser.parse_args(); Base.metadata.create_all(engine)
    if args.action=='import': import_records(args.file)
    elif args.action=='enrich': enrich(args.limit,args.collection)
    else: seed()
if __name__=='__main__': main()
