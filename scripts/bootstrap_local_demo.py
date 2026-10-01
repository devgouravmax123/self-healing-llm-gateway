#!/usr/bin/env python3
"""Bootstrap script for local demo tenant and API key generation.

This script runs inside the gateway environment (or container) to securely
generate and persist an API key for local testing and demonstration without
hardcoding credentials or storing plaintext secrets.
"""

import asyncio
import sys

from sqlalchemy import select

from app.core.auth import generate_api_key
from app.db.models.tenant import Tenant
from app.db.repositories.api_key_repo import api_key_repository
from app.db.session import database_manager

DEMO_TENANT_ID = "demo_tenant"
DEMO_TENANT_NAME = "Local Demo Tenant"
DEMO_KEY_NAME = "Local Demo Key"


async def bootstrap_local_demo(
    tenant_id: str = DEMO_TENANT_ID,
    tenant_name: str = DEMO_TENANT_NAME,
    key_name: str = DEMO_KEY_NAME,
    is_admin: bool = False,
) -> str:
    """Ensure the local demo tenant exists and generate a fresh API key.

    Returns the plaintext API key.
    """
    await database_manager.initialize()

    # 1. Ensure Tenant exists in PostgreSQL
    async with database_manager.session() as session:
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        res = await session.execute(stmt)
        tenant = res.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(id=tenant_id, name=tenant_name, status="active")
            session.add(tenant)
            await session.commit()

    # 2. Generate secure API key pair
    plaintext_key, key_prefix, hashed_key = generate_api_key(environment="live")

    # 3. Persist only the SHA-256 hashed key
    await api_key_repository.create_api_key(
        tenant_id=tenant_id,
        key_prefix=key_prefix,
        hashed_key=hashed_key,
        name=key_name,
        is_admin=is_admin,
    )

    return plaintext_key


def main() -> None:
    try:
        plaintext_key = asyncio.run(bootstrap_local_demo())
        print("\n" + "=" * 60)
        print("Self-Healing LLM Gateway — Local Demo API Key Generated")
        print("=" * 60)
        print(f"Tenant ID : {DEMO_TENANT_ID}")
        print(f"API Key   : {plaintext_key}")
        print("-" * 60)
        print("Keep this key private. It is stored hashed and printed once.")
        print("=" * 60 + "\n")
    except Exception as exc:
        print(f"Error bootstrapping local demo key: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
