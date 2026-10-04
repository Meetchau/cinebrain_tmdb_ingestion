from collections import defaultdict
from math import sqrt
from sqlalchemy import select, cast
from pgvector.sqlalchemy import Vector
from .db import Movie, Memory, Recommendation, now
from . import ai
from .constants import CONFIRMED_WATCHED

WEIGHTS = dict(taste=.30, feel=.25, state=.20, semantic=.10, quality=.05, exploration=.10)

def traits(profile):
    return set((profile or {}).get('personality', []) + (profile or {}).get('themes', []) + (profile or {}).get('cinematic', []))

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

def overlap(a,b):
    return len(set(a)&set(b))/max(1,len(set(a))) if a else .5

def rank(db, session, vector):
    taste = taste_model(db)
    memories = list(db.scalars(select(Memory)))
    watched = {m.movie_id for m in memories if m.watched}
    presented = set(db.scalars(select(Recommendation.movie_id).where(Recommendation.session_id == session.id)))
    history = list(db.scalars(select(Movie).where(Movie.id.in_(watched))))
    known_languages = {m.metadata_json.get('original_language') for m in history}
    known_directors = {p.get('id') for m in history for p in m.metadata_json.get('directors', [])}
    candidates=[]
    intent=session.intent
    query = select(Movie)
    if db.bind.dialect.name == 'postgresql':
        query = query.order_by(cast(Movie.embedding, Vector(768)).cosine_distance(vector))
    for movie in db.scalars(query):
        data, profile=movie.metadata_json, movie.profile
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
        novelty=(float(data.get('original_language') not in known_languages)+float(not any(p.get('id') in known_directors for p in data.get('directors', []))))/2
        exploration=min(1,intent.get('exploration',0)+(session.shuffle_count or 0)*.12)
        votes=data.get('vote_count',0) or 0
        quality=((data.get('vote_average',0) or 0)*votes+6*100)/(votes+100)/10
        scores=dict(taste=taste_score,feel=feel,state=sum(state_values)/len(state_values) if state_values else .5,semantic=(cosine(vector,movie.embedding)+1)/2,quality=quality,exploration=1-abs(novelty-exploration))
        scores['final']=sum(scores[k]*w for k,w in WEIGHTS.items())
        candidates.append((movie,scores))
    return sorted(candidates,key=lambda item:(-item[1]['final'],item[0].id))

def decide(db, session):
    vector=ai.embed(' '.join(str(v) for v in session.answers.values()))
    candidates=rank(db,session,vector)[:12]
    if not candidates: return None
    history=[{'movie_id':m.movie_id,'title':movie.title,'reaction':m.reaction} for m,movie in db.execute(select(Memory,Movie).join(Movie,Memory.movie_id==Movie.id))]
    decision=ai.generate('Choose exactly one supplied movie_id for tonight. You may reject high-scoring candidates. Return {movie_id, why_it_fits, what_it_might_not_fit}. Base personal claims only on explicit reactions or evidence in taste. A watched film alone proves no liking. Do not invent facts. Explain concisely as a friend.', {'session':session.intent,'answers':session.answers,'shuffle_count':session.shuffle_count,'taste':taste_model(db),'memory':history,'previous':[{'movie_id':r.movie_id,'reason':r.rejection_reason} for r in db.scalars(select(Recommendation).where(Recommendation.session_id==session.id))],'candidates':[{'movie_id':m.id,'title':m.title,'metadata':m.metadata_json,'profile':m.profile,'scores':s} for m,s in candidates]})
    chosen=next(((m,s) for m,s in candidates if m.id==decision.get('movie_id')),None)
    if not chosen or not isinstance(decision.get('why_it_fits'),str) or not isinstance(decision.get('what_it_might_not_fit'),str):
        raise ai.AIUnavailable('Ollama returned an invalid recommendation. Please retry.')
    movie,scores=chosen
    record=Recommendation(session_id=session.id,movie_id=movie.id,scores=scores,explanation=decision['why_it_fits'],caveat=decision['what_it_might_not_fit'])
    db.add(record)
    session.status='recommended'
    db.flush()
    return record
