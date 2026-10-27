import tempfile
from pathlib import Path

from app import config 
from app import db, store
from app.models import Event

# Fixed session id: the evals run against a temp DB as a single fake visitor
EVAL_SESSION = "eval-session"

def use_temp_db():
    db.DB_PATH = Path(tempfile.mkdtemp()) / "eval.db"
    db.init_db()

def seed_events(events: dict[str, Event]):
    store.clear_store(EVAL_SESSION)
    for event_id, event in events.items():
        store.insert_event(EVAL_SESSION, event, event_id)
