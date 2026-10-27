from fastapi import FastAPI, HTTPException, Response, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from anthropic import APIError as AnthropicAPIError
from openai import APIError as OpenAIAPIError

import secrets
from instructor import exceptions
from pydantic import ValidationError

from app import store
from app.config import settings
from app.llm.parse import parse_command
from app.models import UserInput, AgentResponse
from app.db import init_db
from app.llm.agent.loop import run_agent
from app.models import PendingAction, AgentResponse

# Setup app
app = FastAPI(title="Cadence")

# Setup db
init_db()
store.delete_stale_sessions(7)
if settings.reset_db_on_start:
    store.clear_all()

# rate limiting using slow api
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
llm_limit = limiter.shared_limit("10/minute;40/day", scope='llm')
agent_limit = limiter.limit("7/minute;20/day")

# cookies
def get_session_id(request: Request, response: Response) -> str:
    sid = request.cookies.get('session_id')
    if not sid or len(sid) != 32: 
        sid = secrets.token_hex(16) # 1 byte is 2 hex characters
    response.set_cookie('session_id', sid, httponly=True, samesite="lax", max_age=604800, secure=True) # max_age = 7days
    store.touch_session(sid)
    return sid

# Exception functions
@app.exception_handler(exceptions.InstructorRetryException)
async def llm_retry_handler(request: Request, exc: exceptions.InstructorRetryException):
    return JSONResponse(status_code=502, content={"detail": "LLM exceeded retry limit"})

@app.exception_handler(AnthropicAPIError)
@app.exception_handler(OpenAIAPIError)
async def llm_api_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=502, content={"detail": "LLM provider error"})

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=429, content={"detail": "dont finish my api key plz (rate limit exceeded try again later)"})

# API Endpoints

@app.post("/agent")
@agent_limit
def process_agent_request(request: Request, text_request: UserInput, sid: str = Depends(get_session_id),
                          reponse_model = AgentResponse):

    if settings.llm_provider != "anthropic":
        raise HTTPException(501, "can't run agent calls without anthropic provider")
    
    result = run_agent(sid, text_request.request)
    return result

@app.post("/command")
@llm_limit
def process_command(request: Request, text_request: UserInput, sid: str = Depends(get_session_id)):
    processed_command = parse_command(sid, text_request.request).command

    if processed_command.action == "add":
        return store.add_event(sid, processed_command)

    if processed_command.event_id is None:
        raise HTTPException(404, "No event matches that request")

    if processed_command.action == "update":
        try:
            result = store.update_event(sid, processed_command.event_id, processed_command)

            if result is None:
                raise HTTPException(404, "No event matches that request")

            return result
        except ValidationError:
            raise HTTPException(422, "end time can not be before event start.")

    removed_event = store.remove_event(sid, processed_command.event_id)
    return {"status": "removed", "event": removed_event}

# Pending endpoints
@app.get("/pending")
def get_pending(sid: str = Depends(get_session_id), response_model=PendingAction):

    event = store.get_pending(sid)
    return {"pending": event}

@app.post("/pending/confirm")
def confirm_pending(sid: str = Depends(get_session_id)):

    confirmed = store.confirm_pending(sid)

    if confirmed is None:
        raise HTTPException(404, "Nothing to confirm (it may have been cancelled or deleted)")

    return {"status": "confirmed", **confirmed}

@app.delete("/pending")
def delete_pending(sid: str = Depends(get_session_id)):

    store.clear_pending(sid)
    return {'cleared': True}

# Does not add to database, just used for checking without storing
@app.post("/parse")
@llm_limit
def parse(request: Request, text_request: UserInput, sid: str = Depends(get_session_id)):
    processed_command = parse_command(sid, text_request.request)
    return processed_command.command

@app.get("/events")
def get_events(sid: str = Depends(get_session_id)):
    return store.list_events(sid)

# For deleting manually without LLM. Need exact ID
@app.delete("/events/{event_id}")
def delete(event_id: str, sid: str = Depends(get_session_id)):
    removed_event = store.remove_event(sid, event_id)
    if removed_event is None:
        raise HTTPException(404, "No event matches that request")
    return {"removed_event": removed_event}

# Reset: clears every event for a session
@app.delete("/events")
def clear_events(sid: str = Depends(get_session_id)):
    store.clear_store(sid)
    return {"cleared": True}

app.mount("/", StaticFiles(directory="../frontend", html=True), name='frontend')
