"""Request context tracking: request ID, tenant ID, feature, provider, and model context."""

import contextvars
import uuid

# Context variables to hold request metadata across async tasks
_request_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_requested_model_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "requested_model", default=""
)
_tenant_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default="")
_feature_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("feature", default="")
_provider_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "provider_id", default=""
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


def set_tenant_id(tenant_id: str) -> contextvars.Token[str]:
    """Set the authenticated tenant ID in the context var."""
    return _tenant_id_ctx_var.set(tenant_id)


def get_tenant_id() -> str:
    """Retrieve the authenticated tenant ID from context var."""
    return _tenant_id_ctx_var.get()


def reset_tenant_id(token: contextvars.Token[str]) -> None:
    """Reset the tenant ID context var token."""
    _tenant_id_ctx_var.reset(token)


def set_feature(feature: str) -> contextvars.Token[str]:
    """Set the feature name in the context var."""
    return _feature_ctx_var.set(feature)


def get_feature() -> str:
    """Retrieve the feature name from context var."""
    return _feature_ctx_var.get()


def reset_feature(token: contextvars.Token[str]) -> None:
    """Reset the feature context var token."""
    _feature_ctx_var.reset(token)


def set_provider_id(provider_id: str) -> contextvars.Token[str]:
    """Set the active provider ID in the context var."""
    return _provider_id_ctx_var.set(provider_id)


def get_provider_id() -> str:
    """Retrieve the active provider ID from context var."""
    return _provider_id_ctx_var.get()


def reset_provider_id(token: contextvars.Token[str]) -> None:
    """Reset the provider ID context var token."""
    _provider_id_ctx_var.reset(token)
