"""Single-process turn admission. Never hold this mutex during model/tool work."""

from threading import Lock


_MUTEX = Lock()
_ACTIVE: set[str] = set()


class SessionBusyError(RuntimeError):
    pass


class SessionRun:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.released = False

    def release(self) -> None:
        with _MUTEX:
            if not self.released:
                _ACTIVE.discard(self.session_id)
                self.released = True


def acquire_session_run(session_id: str) -> SessionRun:
    """Reserve before session mutation/turn creation; caller owns final release."""
    with _MUTEX:
        if session_id in _ACTIVE:
            raise SessionBusyError("该会话已有正在执行的请求，请稍后重试。")
        _ACTIVE.add(session_id)
    # ponytail: process-local admission only; use one API worker. A multi-worker
    # deployment needs a shared admission mechanism before it can be supported.
    return SessionRun(session_id)
