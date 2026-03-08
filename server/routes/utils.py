
from flask import request
import state

def get_current_session() -> state.SessionState:
    """Retrieve session based on query param or JSON body 'id'."""
    # Check query string
    session_id = request.args.get("id") or request.args.get("session_id")
    
    # Check JSON body (if applicable)
    if not session_id and request.is_json:
        try:
            body = request.get_json(silent=True)
            if body:
                session_id = body.get("id") or body.get("session_id")
        except:
            pass

    if not session_id:
        session_id = state.DEFAULT_SESSION_ID
        
    return state.get_session(session_id)
