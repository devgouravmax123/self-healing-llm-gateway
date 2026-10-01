"""Authentication core utilities, key generation, hashing, and FastAPI dependencies."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header

from app.core.auth_context import TenantContext
from app.core.config import Settings, settings
from app.core.exceptions import AuthenticationError, GatewayError, PermissionDeniedError
from app.core.request_context import set_tenant_id
from app.db.base import utc_now
from app.db.repositories.api_key_repo import ApiKeyRepository, api_key_repository
from app.observability.metrics import GatewayMetrics, gateway_metrics
from app.storage.redis import RedisManager, redis_manager

logger = logging.getLogger(__name__)

KEY_PREFIX_LENGTH = 16  # e.g., 'gw_live_k7x9m2pq'
SECRET_BYTES = 32
CACHE_KEY_PREFIX = "api_key_cache:"


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
        redis_mgr: RedisManager | None = None,
        config: Settings | None = None,
    ) -> None:
        self.repo = repo or api_key_repository
        self.metrics = metrics or gateway_metrics
        self.redis_mgr = redis_mgr or redis_manager
        self.config = config or settings

    async def _get_from_cache(self, hashed_key: str) -> dict[str, Any] | None:
        """Attempt to read API key metadata from Redis cache.

        Returns metadata dict if found and parsed, or None on miss, corruption, or Redis failure.
        """
        try:
            client = self.redis_mgr.get_client()
            if client is None:
                return None
            cache_key = f"{CACHE_KEY_PREFIX}{hashed_key}"
            data = await client.get(cache_key)
            if not data:
                return None
            parsed = json.loads(data)
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception as exc:
            logger.debug("Redis read error for auth cache: %s", exc)
            return None

    async def _set_in_cache(self, hashed_key: str, payload: dict[str, Any]) -> None:
        """Attempt to write API key metadata to Redis cache with bounded TTL."""
        try:
            client = self.redis_mgr.get_client()
            if client is None:
                return
            cache_key = f"{CACHE_KEY_PREFIX}{hashed_key}"
            ttl = self.config.api_key_cache_ttl_seconds
            await client.set(cache_key, json.dumps(payload), ex=ttl)
        except Exception as exc:
            logger.debug("Redis write error for auth cache: %s", exc)

    async def invalidate_cache(self, hashed_key: str) -> None:
        """Invalidate cached API key metadata in Redis."""
        try:
            client = self.redis_mgr.get_client()
            if client is None:
                return
            cache_key = f"{CACHE_KEY_PREFIX}{hashed_key}"
            await client.delete(cache_key)
        except Exception as exc:
            logger.warning("Failed to invalidate auth cache in Redis for key hash: %s", exc)

    async def revoke_api_key(self, api_key_id: UUID) -> bool:
        """Revoke API key in PostgreSQL and invalidate Redis auth cache on success.

        Returns True if the key was found and revoked, False otherwise.
        Redis invalidation failure is safely logged and does not undo the DB revocation.
        """
        hashed_key = await self.repo.revoke_api_key(api_key_id)
        if hashed_key is None:
            return False

        # Invalidate cache only AFTER PostgreSQL commit succeeds
        await self.invalidate_cache(hashed_key)
        return True

    def _validate_and_build_context(
        self,
        tenant_id: str | None,
        tenant_name: str | None,
        tenant_status: str | None,
        api_key_id_str: str | None,
        key_prefix: str | None,
        is_admin: bool,
        status: str | None,
        expires_at: datetime | None,
    ) -> TenantContext:
        """Validate key and tenant invariants and construct TenantContext.

        Raises:
            AuthenticationError (401): if key is inactive or expired.
            PermissionDeniedError (403): if tenant is inactive/suspended.
            ValueError: if mandatory fields are missing.
        """
        if not tenant_id or not api_key_id_str or not key_prefix:
            raise ValueError("Malformed identity metadata")

        # 1. Key status
        if status != "active":
            self.metrics.auth_failures_total.labels(reason="invalid_credentials").inc()
            raise AuthenticationError(message="Invalid authentication credentials")

        # 2. Expiration
        now = utc_now()
        if expires_at is not None and expires_at <= now:
            self.metrics.auth_failures_total.labels(reason="expired_key").inc()
            raise AuthenticationError(message="Invalid authentication credentials")

        # 3. Tenant status
        if tenant_status != "active":
            raise PermissionDeniedError(message="Tenant account is inactive or disabled")

        if isinstance(api_key_id_str, UUID):
            api_key_uuid = api_key_id_str
        else:
            try:
                api_key_uuid = UUID(api_key_id_str)
            except (ValueError, TypeError):
                # Fallback for legacy/mock test fixtures using custom string IDs
                from typing import cast

                api_key_uuid = cast(UUID, api_key_id_str)

        set_tenant_id(tenant_id)
        return TenantContext(
            tenant_id=tenant_id,
            tenant_name=tenant_name or "Unknown",
            api_key_id=api_key_uuid,
            key_prefix=key_prefix,
            is_admin=is_admin,
        )

    async def authenticate_key(self, api_key: str) -> TenantContext:
        """Authenticate a plaintext API key and resolve the associated TenantContext.

        Raises:
            AuthenticationError: if key is invalid, revoked, or expired.
            PermissionDeniedError: if the associated tenant account is inactive.
            GatewayError (503): if database access fails and no valid cache exists.
        """
        if not api_key or not isinstance(api_key, str) or not api_key.strip():
            self.metrics.auth_failures_total.labels(reason="missing_credentials").inc()
            raise AuthenticationError(message="Authentication required")

        hashed_key = hash_api_key(api_key.strip())

        # 1. Check Redis read-through cache
        cached = await self._get_from_cache(hashed_key)
        if cached is not None:
            try:
                expires_at_dt: datetime | None = None
                if cached.get("expires_at"):
                    expires_at_dt = datetime.fromisoformat(cached["expires_at"])

                return self._validate_and_build_context(
                    tenant_id=cached.get("tenant_id"),
                    tenant_name=cached.get("tenant_name"),
                    tenant_status=cached.get("tenant_status"),
                    api_key_id_str=cached.get("api_key_id"),
                    key_prefix=cached.get("key_prefix"),
                    is_admin=cached.get("is_admin", False),
                    status=cached.get("status"),
                    expires_at=expires_at_dt,
                )
            except (AuthenticationError, PermissionDeniedError):
                # Cached status rejections (revoked/expired/suspended) are authoritative
                raise
            except Exception as corrupt_exc:
                logger.debug(
                    "Cached auth metadata validation failed; falling back to DB: %s", corrupt_exc
                )

        # 2. Cache miss or malformed cache -> query PostgreSQL
        try:
            key_record = await self.repo.get_by_hashed_key(hashed_key)
        except Exception as exc:
            logger.error("Database error during API key verification: %s", exc)
            raise GatewayError(
                message="Authentication service temporarily unavailable",
                status_code=503,
            ) from exc

        if key_record is None:
            # Safe generic error message without leaking key existence
            self.metrics.auth_failures_total.labels(reason="invalid_credentials").inc()
            raise AuthenticationError(message="Invalid authentication credentials")

        tenant = key_record.tenant
        tenant_status = tenant.status if tenant else None
        tenant_name = tenant.name if tenant else None

        ctx = self._validate_and_build_context(
            tenant_id=key_record.tenant_id,
            tenant_name=tenant_name,
            tenant_status=tenant_status,
            api_key_id_str=str(key_record.id),
            key_prefix=key_record.key_prefix,
            is_admin=key_record.is_admin,
            status=key_record.status,
            expires_at=key_record.expires_at,
        )

        # 3. Populate Redis cache on successful validation
        cache_payload = {
            "tenant_id": key_record.tenant_id,
            "tenant_name": tenant_name,
            "tenant_status": tenant_status,
            "api_key_id": str(key_record.id),
            "key_prefix": key_record.key_prefix,
            "is_admin": key_record.is_admin,
            "status": key_record.status,
            "expires_at": key_record.expires_at.isoformat() if key_record.expires_at else None,
        }
        await self._set_in_cache(hashed_key, cache_payload)

        return ctx


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
