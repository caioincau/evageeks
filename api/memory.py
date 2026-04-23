# api/memory.py
import time
import uuid
from collections import OrderedDict


class ConversationStore:
    """In-memory conversation store with LRU eviction and TTL."""

    def __init__(self, max_sessions: int = 1000, max_turns: int = 20, ttl_seconds: int = 3600):
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self._sessions: OrderedDict[str, dict] = OrderedDict()

    def create_session(self) -> str:
        session_id = uuid.uuid4().hex[:16]
        self._evict_expired()
        if len(self._sessions) >= self.max_sessions:
            self._sessions.popitem(last=False)
        self._sessions[session_id] = {
            "history": [],
            "created_at": time.time(),
            "last_active": time.time(),
        }
        return session_id

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
        session["history"].append({"role": role, "content": content})
        if len(session["history"]) > self.max_turns * 2:
            session["history"] = session["history"][-(self.max_turns * 2):]
        session["last_active"] = time.time()
        self._sessions.move_to_end(session_id)

    def get_history(self, session_id: str) -> list[dict]:
        session = self._sessions.get(session_id)
        if not session:
            return []
        if time.time() - session["last_active"] > self.ttl_seconds:
            del self._sessions[session_id]
            return []
        return session["history"]

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - s["last_active"] > self.ttl_seconds
        ]
        for sid in expired:
            del self._sessions[sid]
