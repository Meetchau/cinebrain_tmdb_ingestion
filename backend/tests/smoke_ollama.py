"""Live local-model smoke test using a disposable catalogue copy, never real memory."""
import argparse
import sqlite3
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from backend.app import main
from backend.app.db import ROOT, ViewingSession, Movie

def run(latest=False):
    with tempfile.TemporaryDirectory(prefix='cinebrain-smoke-') as tmp:
        copied=Path(tmp)/'test.db'
        with sqlite3.connect(ROOT/'data/cinebrain.db') as source, sqlite3.connect(copied) as target:
            source.backup(target)
        isolated=create_engine(f'sqlite:///{copied}',connect_args={'check_same_thread':False})
        main.engine=isolated
        main.SessionLocal=sessionmaker(isolated,expire_on_commit=False)
        with TestClient(main.app) as client:
            if latest:
                with main.SessionLocal() as db:
                    session=db.scalar(select(ViewingSession).where(ViewingSession.status=='recommended').order_by(ViewingSession.started_at.desc()))
                    if session is None: raise RuntimeError('No active recommendation session to verify')
                    sid=session.id
                result=client.post('/api/sessions/'+sid+'/shuffle',json={})
                assert result.status_code==200,result.json()
                film=result.json()['recommendation']
                with main.SessionLocal() as db:
                    language=db.get(Movie,film['movie_id']).metadata_json.get('original_language')
                print('Your latest session, temporary-copy shuffle:',film['title'],film['year'],language,flush=True)
                print('Your real session and memory were not changed.',flush=True)
                isolated.dispose()
                return
            response=client.post('/api/sessions',json={'feel':'I want wonder and something hopeful afterward','state':'I am a little tired; give me something absorbing without too much mental work','trust':'understand'})
            assert response.status_code==200, response.json()
            data=response.json();film=data['recommendation']
            print('Real Ollama recommendation succeeded:',film['title'],film['year'],flush=True)
            shuffled=client.post('/api/sessions/'+data['session_id']+'/shuffle',json={})
            assert shuffled.status_code==200, shuffled.json()
            other=shuffled.json()['recommendation']
            assert other['movie_id']!=film['movie_id']
            print('Session-aware shuffle succeeded:',other['title'],other['year'],flush=True)
            print('Test sessions used a temporary database; real memory unchanged.',flush=True)
        isolated.dispose()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--latest',action='store_true')
    run(parser.parse_args().latest)
