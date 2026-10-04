"""Run from the repository root: venv/bin/python -m backend.app.catalogue import|enrich|seed"""
import argparse
import json
import time
from pathlib import Path
import requests
from sqlalchemy import select
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

def enrich(limit):
    with SessionLocal() as db:
        ids=list(db.scalars(select(Movie.id).where(Movie.profile.is_(None) | Movie.embedding.is_(None)).limit(limit)))
    for movie_id in ids:
        with SessionLocal.begin() as db:
            movie=db.get(Movie,movie_id)
            if not movie.profile:
                raw=ai.generate('Create an inferred cinematic experience profile from this film metadata. Return feelings and aftertaste arrays; emotional_intensity, energy, cognitive_load, complexity, immersion as numbers 0..1; pacing string; personality, themes, cinematic as arrays of semantic descriptors. Differentiate during-film feelings from aftertaste. Do not invent user reactions.',movie.metadata_json)
                profile=Profile.model_validate(raw).model_dump(); profile['source']='ollama_inference'; movie.profile=profile
            movie.embedding=ai.embed(json.dumps({'title':movie.title,'overview':movie.metadata_json.get('overview'),'profile':movie.profile}))
            print(f'Enriched: {movie.title}')

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
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['import','enrich','seed']); parser.add_argument('--file',default='data/raw/tmdb_movies.jsonl'); parser.add_argument('--limit',type=int,default=200)
    args=parser.parse_args(); Base.metadata.create_all(engine)
    if args.action=='import': import_records(args.file)
    elif args.action=='enrich': enrich(args.limit)
    else: seed()
if __name__=='__main__': main()
