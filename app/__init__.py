"""Linkit Fundability Analyser Flask application."""
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    from app.routes import register_routes
    register_routes(app)
    return app
