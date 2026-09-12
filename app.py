#!/usr/bin/env python3
"""FastAPI application for Sebastian's personal job assistant.

One user, four screens: Jobs, Applications, Profile, Config.  Every route lives
in ``jobscanner.api``; this module only assembles them and serves the static
frontend.  Start it with ``python3 run.py``.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jobscanner import db as jsdb
from jobscanner import documents as documents_mod
from jobscanner.api import ROUTERS

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'static'

@asynccontextmanager
async def lifespan(_app):
    jsdb.init_db()
    documents_mod.ensure_dirs()
    yield


app = FastAPI(title='Job Assistant', version='2.0',
              description='Personal job discovery and application assistant.',
              docs_url='/api/docs', redoc_url=None, lifespan=lifespan)


for router in ROUTERS:
    app.include_router(router)


@app.get('/api/health')
def health():
    return {'status': 'ok', 'database': str(jsdb.get_db_path())}


@app.get('/')
def index():
    return FileResponse(str(STATIC_DIR / 'index.html'))


@app.exception_handler(404)
def not_found(request, exc):
    if request.url.path.startswith('/api/'):
        return JSONResponse({'detail': 'Not found'}, status_code=404)
    return FileResponse(str(STATIC_DIR / 'index.html'))


app.mount('/', StaticFiles(directory=str(STATIC_DIR), html=True), name='static')
