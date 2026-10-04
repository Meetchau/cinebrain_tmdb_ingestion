"""Resumable, era/language-balanced TMDB catalogue expansion."""
import argparse
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import Base, engine, Movie, SessionLocal
from .catalogue import tmdb

LANGUAGES = ['hi','bn','ta','ml','ja','ko','zh','fr','it','de','sv','es','fa','ru','pl','pt','ar']

def discovery_routes(today=None):
    today=today or datetime.now(ZoneInfo('Asia/Kolkata')).date()
    # A separate back catalogue: the original popular feed already covers new releases.
    latest=f'{today.year-3}-12-31'
    base={'include_adult':'false','include_video':'false','language':'en-US','with_runtime.gte':60,'primary_release_date.lte':latest}
    routes=[]
    for decade in range(1920,today.year-2,10):
        end=min(decade+9,today.year-3)
        routes.append((f'era:{decade}',{**base,'primary_release_date.gte':f'{decade}-01-01','primary_release_date.lte':f'{end}-12-31','vote_count.gte':30 if decade<1960 else 200,'sort_by':'vote_average.desc'}))
    for language in LANGUAGES:
        routes.append((f'language:{language}',{**base,'with_original_language':language,'vote_count.gte':60,'sort_by':'vote_average.desc'}))
    return routes

def save_movie(movie_id, route):
    with SessionLocal() as db:
        existing=db.scalar(select(Movie).where(Movie.tmdb_id==movie_id))
        if existing:
            data=dict(existing.metadata_json)
            routes=list(data.get('discovery_routes',[]))
            if route in routes: return False
            routes.append(route);data.update(discovery_routes=routes,collection='deep')
            existing.metadata_json=data;db.commit();return False
    details=tmdb(f'/movie/{movie_id}',{'append_to_response':'credits,keywords','language':'en-US'})
    crew=details.pop('credits',{})
    keywords=details.pop('keywords',{})
    data={**details,'tmdb_id':details['id'],'year':details.get('release_date','')[:4],
          'cast':crew.get('cast',[])[:20],'directors':[p for p in crew.get('crew',[]) if p.get('job')=='Director'],
          'writers':[p for p in crew.get('crew',[]) if p.get('department')=='Writing'],
          'keywords':keywords.get('keywords',[]),'collection':'deep','discovery_routes':[route]}
    with SessionLocal.begin() as db:
        db.add(Movie(tmdb_id=movie_id,title=details['title'],metadata_json=data))
    return True

def expand(per_route=6):
    added=0
    for route,params in discovery_routes():
        ids=[]
        # The first and third pages give both established films and less obvious territory.
        for page in (1,3):
            results=tmdb('/discover/movie',{**params,'page':page}).get('results',[])
            take=(per_route+1)//2 if page==1 else per_route//2
            for movie in results:
                movie_id=movie.get('id')
                if movie_id and movie_id not in ids:
                    ids.append(movie_id)
                    if len(ids)>=(take if page==1 else per_route): break
        for movie_id in ids:
            added+=int(save_movie(movie_id,route))
        print(f'{route}: {len(ids)} films considered; {added} new films so far',flush=True)
    report()

def report():
    with SessionLocal() as db:
        movies=list(db.scalars(select(Movie)))
        for label,subset in [('deep catalogue',[m for m in movies if m.metadata_json.get('collection')=='deep']),('enriched',[m for m in movies if m.profile and m.embedding is not None])]:
            decades=Counter(f'{int(m.metadata_json["year"])//10*10}s' for m in subset if str(m.metadata_json.get('year','')).isdigit())
            languages=Counter(m.metadata_json.get('original_language') for m in subset)
            print(f'{label}: {len(subset)} films; decades={dict(sorted(decades.items()))}; languages={dict(sorted(languages.items()))}',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--per-route',type=int,default=6);parser.add_argument('--report',action='store_true')
    args=parser.parse_args()
    if not 2<=args.per_route<=20: parser.error('--per-route must be 2..20')
    Base.metadata.create_all(engine)
    report() if args.report else expand(args.per_route)
