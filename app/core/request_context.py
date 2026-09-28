"""Request context tracking and request ID management."""

import contextvars
import uuid

# Context variable to hold the current request ID across async tasks
_request_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


def generate_request_id() -> str:
    """Generate a standard UUID-based request ID with prefix."""
    return f"req_{uuid.uuid4().hex}"


def set_request_id(request_id: str) -> contextvars.Token[str]:
    """Set the current request ID in the context var."""
    return _request_id_ctx_var.set(request_id)


def get_request_id() -> str:
    """Retrieve the current request ID from context var."""
    return _request_id_ctx_var.get()


def reset_request_id(token: contextvars.Token[str]) -> None:
    """Reset the context var token."""
    _request_id_ctx_var.reset(token)
