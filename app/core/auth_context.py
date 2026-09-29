"""Authentication models and context structures."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TenantContext:
    """Immutable context representing an authenticated tenant identity.

    Guarantees:
    - Originates solely from validated API keys in the database.
    - Cannot be overridden by client request metadata.
    """

    tenant_id: str
    tenant_name: str
    api_key_id: UUID
    key_prefix: str
    is_admin: bool = False
