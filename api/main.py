# api/main.py
from fastapi import FastAPI
from psycopg2.extensions import connection
from api.routes import articles, search, categories


def create_app(db_conn: connection = None) -> FastAPI:
    app = FastAPI(title="EvaGeeks Wiki API", version="1.0.0")
    app.state.db = db_conn
    app.include_router(articles.router)
    app.include_router(search.router)
    app.include_router(categories.router)
    return app
