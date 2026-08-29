"""Process-local session memory store."""

from app.memory.session_memory import SessionMemory


class MemoryStore:
    """A simple in-memory mapping from session id to SessionMemory."""

    def __init__(self):
        self._sessions: dict[str, SessionMemory] = {}

    def get_or_create(self, session_id: str) -> SessionMemory:
        """Return an existing session memory or create a new one."""

        if session_id not in self._sessions:
            self._sessions[session_id] = SessionMemory(session_id=session_id)
        return self._sessions[session_id]

    def clear(self, session_id: str) -> None:
        """Remove one session from memory."""

        self._sessions.pop(session_id, None)


memory_store = MemoryStore()
