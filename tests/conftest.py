"""Global test fixtures and dependency overrides for test suite."""

from collections.abc import Generator
from uuid import uuid4

import pytest

from app.core.auth import get_authenticated_tenant
from app.core.auth_context import TenantContext
from app.main import app


@pytest.fixture(autouse=True)
def default_auth_override() -> Generator[None, None, None]:
    """Override get_authenticated_tenant dependency for standard test cases.


    Tests that specifically test authentication will remove this override.
    """
    app.dependency_overrides[get_authenticated_tenant] = lambda: TenantContext(
        tenant_id="test_tenant_default",
        tenant_name="Default Test Org",
        api_key_id=uuid4(),
        key_prefix="gw_live_test",
        is_admin=False,
    )
    yield
    app.dependency_overrides.pop(get_authenticated_tenant, None)
