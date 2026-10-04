from datetime import date
from types import SimpleNamespace
from backend.app.discovery import discovery_routes, LANGUAGES
from backend.app.recommender import diverse_shortlist, overlap

def candidate(movie_id, year, language, score):
    return (SimpleNamespace(id=movie_id,metadata_json={'year':str(year),'original_language':language,'directors':[{'id':movie_id}]}),{'final':score})

def test_discovery_spans_decades_languages_and_older_secondary_pages():
    routes=dict(discovery_routes(date(2026,10,4)))
    assert 'era:1920' in routes and 'era:1960' in routes and 'era:2020' in routes
    assert len(LANGUAGES)>=15
    assert routes['language:ja']['with_original_language']=='ja'
    assert routes['language:hi']['primary_release_date.lte']=='2023-12-31'
    assert routes['era:1960']['primary_release_date.gte']=='1960-01-01'
    assert all(params['include_adult']=='false' and params['with_runtime.gte']==60 for params in routes.values())

def test_shortlist_surfaces_equally_good_older_world_cinema():
    recent=[candidate(i,2026,'en',.9) for i in range(1,21)]
    historical=[candidate(21,1959,'fr',.88),candidate(22,1972,'ja',.87),candidate(23,1991,'hi',.86)]
    shortlist=diverse_shortlist(recent+historical,limit=6)
    assert {21,22,23} <= {m.id for m,_ in shortlist}
    assert shortlist[0][0].id==1
    assert all(s['final'] in [.9,.88,.87,.86] for _,s in shortlist)

def test_shortlist_does_not_promote_poor_fit_for_age_alone():
    shortlist=diverse_shortlist([candidate(1,2026,'en',.95),candidate(2,1930,'de',.3)])
    assert [m.id for m,_ in shortlist]==[1]

def test_common_emotional_forms_match_without_genre_shortcuts():
    assert overlap(['hopeful','reflective'],['hope','reflection'])==1
    assert overlap(['melancholic'],['melancholy'])==1
    assert overlap(['wonder'],['fear'])==0
