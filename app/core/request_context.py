"""Request context tracking, request ID management, and request model context."""

import contextvars
import uuid

# Context variable to hold the current request ID across async tasks
_request_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

# Context variable to hold the requested model name across async tasks
_requested_model_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "requested_model", default=""
)


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


def set_requested_model(model: str) -> contextvars.Token[str]:
    """Set the requested model in the context var."""
    return _requested_model_ctx_var.set(model)


def get_requested_model() -> str:
    """Retrieve the requested model from context var."""
    return _requested_model_ctx_var.get()


def reset_requested_model(token: contextvars.Token[str]) -> None:
    """Reset the requested model context var token."""
    _requested_model_ctx_var.reset(token)
