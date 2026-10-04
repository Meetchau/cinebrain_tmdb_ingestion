from collections import defaultdict
from typing import Literal
from pydantic import create_model, Field
from math import sqrt
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select, cast
from pgvector.sqlalchemy import Vector
from .db import Movie, Memory, Recommendation, now
from . import ai
from .constants import CONFIRMED_WATCHED

WEIGHTS = dict(taste=.30, feel=.25, state=.20, semantic=.10, quality=.05, exploration=.10)

def normalize(value):
    return re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()

def traits(profile):
    return {normalize(value) for key in ['personality','themes','cinematic'] for value in (profile or {}).get(key,[])}

def taste_model(db):
    evidence = defaultdict(list)
    for memory, movie in db.execute(select(Memory, Movie).join(Movie, Memory.movie_id == Movie.id)):
        reaction = memory.reaction
        value = {'Loved it': 1, 'Liked it': .6, 'It was fine': 0, 'Didn’t like it': -.6, 'Hated it': -1}.get(reaction.get('reaction'))
        if value is None: continue
        for trait in traits(movie.profile): evidence[trait].append((value, movie.title, memory.updated_at.isoformat()))
    return [{'trait': trait, 'strength': sum(v[0] for v in values)/len(values), 'confidence': len(values)/(len(values)+3), 'evidence_count': len(values), 'evidence': [v[1] for v in values], 'last_updated': max(v[2] for v in values), 'truth': 'inferred'} for trait, values in sorted(evidence.items())]

def cosine(a, b):
    if a is None or b is None or len(a) == 0 or len(b) == 0 or len(a) != len(b): return 0
    denom = sqrt(sum(x*x for x in a)*sum(x*x for x in b))
    return sum(x*y for x,y in zip(a,b))/denom if denom else 0

EMOTION_ALIASES = {
    'hopeful':'hope','inspired':'inspiration','inspiring':'inspiration',
    'reflective':'reflection','contemplative':'reflection','melancholic':'melancholy',
    'nostalgic':'nostalgia','warm':'warmth','joyful':'joy','happy':'joy',
    'uneasy':'unease','unsettled':'unease','tense':'tension','peaceful':'peace',
    'comforting':'comfort','romantic':'romance','exhilarating':'exhilaration',
    'exhilarated':'exhilaration','awe inspiring':'awe',
}

def overlap(a,b):
    left={EMOTION_ALIASES.get(normalize(v),normalize(v)) for v in a}
    right={EMOTION_ALIASES.get(normalize(v),normalize(v)) for v in b}
    return len(left & right)/len(left) if left else .5

def rank(db, session, vector):
    taste = taste_model(db)
    memories = list(db.scalars(select(Memory)))
    watched = {m.movie_id for m in memories if m.watched}
    presented = set(db.scalars(select(Recommendation.movie_id).where(Recommendation.session_id == session.id)))
    history = list(db.scalars(select(Movie).where(Movie.id.in_(watched))))
    known_languages = {m.metadata_json.get('original_language') for m in history}
    known_decades={int(m.metadata_json['year'])//10 for m in history if str(m.metadata_json.get('year','')).isdigit()}
    known_countries={c for m in history for c in m.metadata_json.get('origin_country',[])}
    known_directors = {p.get('id') for m in history for p in m.metadata_json.get('directors', [])}
    candidates=[]
    intent=session.intent
    today=datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat()
    query = select(Movie)
    if db.bind.dialect.name == 'postgresql':
        query = query.order_by(cast(Movie.embedding, Vector(768)).cosine_distance(vector))
    for movie in db.scalars(query):
        data, profile=movie.metadata_json, movie.profile
        release_date=data.get('release_date')
        if not release_date or release_date > today: continue
        if movie.title.casefold() in {t.casefold() for t in CONFIRMED_WATCHED}: continue
        if movie.id in watched|presented or not profile or movie.embedding is None or data.get('adult'): continue
        limit=session.constraints.get('max_runtime')
        if limit and (not data.get('runtime') or data['runtime'] > limit): continue
        languages=session.constraints.get('languages')
        if languages and data.get('original_language') not in languages: continue
        burden=session.constraints.get('max_intensity')
        if burden is not None and profile['emotional_intensity'] > burden: continue
        evidence=[t for t in taste if t['trait'] in traits(profile)]
        taste_score=.5 + sum(t['strength']*t['confidence'] for t in evidence)/(2*max(1,len(evidence)))
        feel=(overlap(intent.get('desired_feelings', []),profile['feelings']) + overlap(intent.get('desired_aftertaste', []), profile['aftertaste']))/2
        state_values=[]
        for key, field in [('energy','energy'),('cognitive_capacity','cognitive_load'),('complexity_tolerance','complexity'),('immersion','immersion')]:
            if intent.get(key) is not None: state_values.append(1-abs(intent[key]-profile[field]))
        if intent.get('desired_pacing'): state_values.append(float(intent['desired_pacing']==profile['pacing']))
        intensity=intent.get('emotional_intensity')
        if intensity is not None: feel=(feel+1-abs(intensity-profile['emotional_intensity']))/2
        new_decade=str(data.get('year','')).isdigit() and int(data['year'])//10 not in known_decades
        new_country=bool(data.get('origin_country')) and not set(data['origin_country']) & known_countries
        novelty=(float(data.get('original_language') not in known_languages)+float(not any(p.get('id') in known_directors for p in data.get('directors', [])))+float(new_decade)+float(new_country))/4
        exploration=min(1,intent.get('exploration',0)+(session.shuffle_count or 0)*.12)
        votes=data.get('vote_count',0) or 0
        quality=((data.get('vote_average',0) or 0)*votes+6*100)/(votes+100)/10
        scores=dict(taste=taste_score,feel=feel,state=sum(state_values)/len(state_values) if state_values else .5,semantic=(cosine(vector,movie.embedding)+1)/2,quality=quality,exploration=1-abs(novelty-exploration))
        scores['final']=sum(scores[k]*w for k,w in WEIGHTS.items())
        candidates.append((movie,scores))
    return sorted(candidates,key=lambda item:(-item[1]['final'],item[0].id))

def diverse_shortlist(ranked, limit=12):
    """Expand beyond one crowded era without sacrificing a strong contextual fit."""
    if not ranked: return []
    pool=[item for item in ranked if item[1]['final'] >= ranked[0][1]['final']-.18]
    chosen=[]
    decades=defaultdict(int);languages=defaultdict(int);directors=defaultdict(int)
    while pool and len(chosen)<limit:
        def adjusted(item):
            movie,scores=item;data=movie.metadata_json
            year=str(data.get('year',''))
            decade=int(year)//10*10 if year.isdigit() else None
            language=data.get('original_language')
            people=[p.get('id') for p in data.get('directors',[]) if p.get('id') is not None]
            penalty=.055*decades[decade]+.025*languages[language]+.045*max((directors[p] for p in people),default=0)
            return scores['final']-penalty
        item=max(pool,key=adjusted);pool.remove(item);chosen.append(item)
        data=item[0].metadata_json;year=str(data.get('year',''))
        decades[int(year)//10*10 if year.isdigit() else None]+=1
        languages[data.get('original_language')]+=1
        for p in data.get('directors',[]):
            if p.get('id') is not None: directors[p['id']]+=1
    return chosen


def decide(db, session):
    vector=ai.embed(' '.join(str(v) for v in session.answers.values()))
    candidates=diverse_shortlist(rank(db,session,vector))
    if not candidates: return None
    history=[{'movie_id':m.movie_id,'title':movie.title,'reaction':m.reaction} for m,movie in db.execute(select(Memory,Movie).join(Movie,Memory.movie_id==Movie.id))]
    Choice=create_model('FilmChoice', movie_id=(Literal[tuple(m.id for m,_ in candidates)], ...), why_it_fits=(str,Field(min_length=1)), what_it_might_not_fit=(str,...))
    decision=ai.structured('Choose exactly one supplied movie_id for tonight. Consider the full history of world cinema, including older, independent, and non-English films. Release recency, hype, and familiarity are not reasons to prefer a film. The shortlist intentionally covers different eras and cinematic traditions; choose for emotional experience, authorial language, current state, and actual personal evidence. Older films are not automatically difficult; subtitled films are not automatically challenging. You may reject high-scoring candidates. Return {movie_id, why_it_fits, what_it_might_not_fit}. Base personal claims only on explicit reactions or evidence in taste. A watched film alone proves no liking. Do not invent facts. Explain concisely as a friend.', {'session':session.intent,'answers':session.answers,'shuffle_count':session.shuffle_count,'taste':taste_model(db),'memory':history,'previous':[{'movie_id':r.movie_id,'reason':r.rejection_reason} for r in db.scalars(select(Recommendation).where(Recommendation.session_id==session.id))],'candidates':[{'movie_id':m.id,'title':m.title,'metadata':{key:m.metadata_json.get(key) for key in ['year','runtime','overview','original_language']},'profile':m.profile,'scores':s} for m,s in candidates]}, Choice).model_dump()
    chosen=next(((m,s) for m,s in candidates if m.id==decision.get('movie_id')),None)
    if not chosen or not isinstance(decision.get('why_it_fits'),str) or not isinstance(decision.get('what_it_might_not_fit'),str):
        raise ai.AIUnavailable('Ollama returned an invalid recommendation. Please retry.')
    movie,scores=chosen
    record=Recommendation(engine_version='1.1',session_id=session.id,movie_id=movie.id,scores=scores,explanation=decision['why_it_fits'],caveat=decision['what_it_might_not_fit'])
    # Flush the parent/session transitions only after model calls finish.
    db.flush()
    db.add(record)
    session.status='recommended'
    db.flush()
    return record
