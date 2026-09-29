"""Authentication core utilities, key generation, hashing, and FastAPI dependencies."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import Annotated

from fastapi import Depends, Header

from app.core.auth_context import TenantContext
from app.core.exceptions import AuthenticationError, GatewayError, PermissionDeniedError
from app.core.request_context import set_tenant_id
from app.db.base import utc_now
from app.db.repositories.api_key_repo import ApiKeyRepository, api_key_repository
from app.observability.metrics import GatewayMetrics, gateway_metrics

logger = logging.getLogger(__name__)

KEY_PREFIX_LENGTH = 16  # e.g., 'gw_live_k7x9m2pq'
SECRET_BYTES = 32


def generate_api_key(environment: str = "live") -> tuple[str, str, str]:
    """Generate a cryptographically secure random API key.

    Returns:
        tuple of (full_plaintext_key, key_prefix, hashed_key)
    """
    prefix_rnd = secrets.token_hex(4)  # 8 hex chars
    key_prefix = f"gw_{environment}_{prefix_rnd}"
    secret_rnd = secrets.token_hex(SECRET_BYTES)  # 64 hex chars
    full_key = f"{key_prefix}_{secret_rnd}"
    hashed_key = hash_api_key(full_key)
    return full_key, key_prefix, hashed_key


def hash_api_key(api_key: str) -> str:
    """Compute deterministic SHA-256 hex digest of the plaintext API key."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_hash(api_key: str, expected_hash: str) -> bool:
    """Constant-time comparison between computed hash and expected stored hash."""
    computed_hash = hash_api_key(api_key)
    return hmac.compare_digest(computed_hash, expected_hash)


class Authenticator:
    """Validates API keys against PostgreSQL durable storage and resolves TenantContext."""

    def __init__(
        self,
        repo: ApiKeyRepository | None = None,
        metrics: GatewayMetrics | None = None,
    ) -> None:
        self.repo = repo or api_key_repository
        self.metrics = metrics or gateway_metrics

    async def authenticate_key(self, api_key: str) -> TenantContext:
        """Authenticate a plaintext API key and resolve the associated TenantContext.

        Raises:
            AuthenticationError: if key is invalid, revoked, or expired.
            PermissionDeniedError: if the associated tenant account is inactive.
            GatewayError (500): if database access fails.
        """
        if not api_key or not isinstance(api_key, str) or not api_key.strip():
            self.metrics.auth_failures_total.labels(reason="missing_credentials").inc()
            raise AuthenticationError(message="Authentication required")

        hashed_key = hash_api_key(api_key.strip())

        try:
            key_record = await self.repo.get_by_hashed_key(hashed_key)
        except Exception as exc:
            logger.error("Database error during API key verification: %s", exc)
            raise GatewayError(
                message="Authentication service temporarily unavailable",
                status_code=500,
            ) from exc

        if key_record is None:
            # Safe generic error message without leaking key existence
            self.metrics.auth_failures_total.labels(reason="invalid_credentials").inc()
            raise AuthenticationError(message="Invalid authentication credentials")

        # Check key status
        if key_record.status != "active":
            self.metrics.auth_failures_total.labels(reason="invalid_credentials").inc()
            raise AuthenticationError(message="Invalid authentication credentials")

        # Check expiration
        now = utc_now()
        if key_record.expires_at is not None and key_record.expires_at <= now:
            self.metrics.auth_failures_total.labels(reason="expired_key").inc()
            raise AuthenticationError(message="Invalid authentication credentials")

        # Check tenant status
        tenant = key_record.tenant
        if tenant is None or tenant.status != "active":
            raise PermissionDeniedError(message="Tenant account is inactive or disabled")

        set_tenant_id(tenant.id)
        return TenantContext(
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            api_key_id=key_record.id,
            key_prefix=key_record.key_prefix,
            is_admin=key_record.is_admin,
        )


# Global authenticator instance
authenticator = Authenticator()


async def get_authenticated_tenant(
    authorization: Annotated[str | None, Header()] = None,
) -> TenantContext:
    """FastAPI dependency for authenticating Bearer API keys.

    Extracts Bearer token from 'Authorization' header and resolves TenantContext.
    """
    if not authorization:
        authenticator.metrics.auth_failures_total.labels(reason="missing_credentials").inc()
        raise AuthenticationError(message="Authentication required")

    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        authenticator.metrics.auth_failures_total.labels(reason="malformed_header").inc()
        raise AuthenticationError(message="Invalid authentication scheme or malformed header")

    api_key = parts[1].strip()
    ctx = await authenticator.authenticate_key(api_key)
    set_tenant_id(ctx.tenant_id)
    return ctx


async def require_admin_auth(
    tenant_context: Annotated[TenantContext, Depends(get_authenticated_tenant)],
) -> TenantContext:
    """FastAPI dependency requiring admin authorization.

    Ensures the caller is authenticated AND has `is_admin=True`.
    """
    if not tenant_context.is_admin:
        raise PermissionDeniedError(message="Administrative privileges required")
    return tenant_context
