"""Unit tests for Phase 11 declarative models metadata, column types, and constraints."""

import uuid
from decimal import Decimal

from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base
from app.db.models import ProviderEvent, RequestRecord, Tenant, UsageRecord


class TestDatabaseModelsMetadata:
    """Test suite verifying declarative models definitions without needing live connection."""

    def test_registered_tables(self) -> None:
        """Verify all Phase 11 & Phase 13 tables are registered in Base.metadata."""
        table_names = set(Base.metadata.tables.keys())
        expected = {"tenants", "requests", "usage_records", "provider_events", "api_keys"}
        assert expected.issubset(table_names)

    def test_api_key_model_schema(self) -> None:
        """Verify ApiKey table columns, types, foreign keys, and indexes."""
        table = Base.metadata.tables["api_keys"]
        assert table.columns["id"].primary_key is True
        assert isinstance(table.columns["id"].type, UUID)
        assert table.columns["tenant_id"].nullable is False
        assert table.columns["key_prefix"].nullable is False
        assert table.columns["hashed_key"].nullable is False
        assert table.columns["hashed_key"].unique is True
        assert table.columns["status"].nullable is False
        assert table.columns["is_admin"].nullable is False

        # Verify foreign key to tenants
        fk = list(table.columns["tenant_id"].foreign_keys)[0]
        assert fk.column.table.name == "tenants"
        assert fk.ondelete == "RESTRICT"

        index_names = {idx.name for idx in table.indexes}
        assert "ix_api_keys_tenant_id" in index_names
        assert "ix_api_keys_key_prefix" in index_names
        assert "ix_api_keys_hashed_key" in index_names
        assert "ix_api_keys_tenant_status" in index_names

    def test_tenant_model_schema(self) -> None:
        """Verify Tenant table columns, types, and constraints."""
        table = Base.metadata.tables["tenants"]
        assert table.columns["id"].primary_key is True
        assert table.columns["id"].nullable is False
        assert table.columns["name"].nullable is False
        assert table.columns["status"].nullable is False
        assert table.columns["created_at"].nullable is False
        assert table.columns["updated_at"].nullable is False

    def test_request_record_model_schema(self) -> None:
        """Verify RequestRecord table columns, types, foreign keys, and indexes."""
        table = Base.metadata.tables["requests"]
        assert table.columns["id"].primary_key is True
        assert isinstance(table.columns["id"].type, UUID)
        assert table.columns["request_id"].unique is True
        assert table.columns["request_id"].nullable is False
        assert table.columns["tenant_id"].nullable is True
        assert table.columns["status"].nullable is False
        assert table.columns["started_at"].nullable is False

        # Verify foreign key ondelete constraint is RESTRICT
        fk = list(table.columns["tenant_id"].foreign_keys)[0]
        assert fk.column.table.name == "tenants"
        assert fk.ondelete == "RESTRICT"

        # Verify composite and single-column indexes exist
        index_names = {idx.name for idx in table.indexes}
        assert "ix_requests_request_id" in index_names
        assert "ix_requests_tenant_id" in index_names
        assert "ix_requests_started_at" in index_names
        assert "ix_requests_tenant_started_at" in index_names

    def test_usage_record_model_schema(self) -> None:
        """Verify UsageRecord table columns, types, foreign keys, and indexes."""
        table = Base.metadata.tables["usage_records"]
        assert table.columns["id"].primary_key is True
        assert isinstance(table.columns["id"].type, UUID)
        assert table.columns["provider"].nullable is False
        assert table.columns["model"].nullable is False
        assert table.columns["input_tokens"].nullable is True
        assert table.columns["output_tokens"].nullable is True
        assert table.columns["total_tokens"].nullable is True
        assert table.columns["estimated_cost"].nullable is True

        # Verify FK constraints ondelete
        req_fk = list(table.columns["request_id"].foreign_keys)[0]
        assert req_fk.column.table.name == "requests"
        assert req_fk.ondelete == "RESTRICT"

        tenant_fk = list(table.columns["tenant_id"].foreign_keys)[0]
        assert tenant_fk.column.table.name == "tenants"
        assert tenant_fk.ondelete == "RESTRICT"

        # Verify indexes
        index_names = {idx.name for idx in table.indexes}
        assert "ix_usage_records_request_id" in index_names
        assert "ix_usage_records_tenant_id" in index_names
        assert "ix_usage_records_created_at" in index_names
        assert "ix_usage_records_tenant_created_at" in index_names
        assert "ix_usage_records_provider_model_created_at" in index_names

    def test_provider_event_model_schema(self) -> None:
        """Verify ProviderEvent table columns, types, and indexes."""
        table = Base.metadata.tables["provider_events"]
        assert table.columns["id"].primary_key is True
        assert isinstance(table.columns["id"].type, UUID)
        assert table.columns["provider_id"].nullable is False
        assert table.columns["event_type"].nullable is False
        assert table.columns["timestamp"].nullable is False
        assert isinstance(table.columns["event_metadata"].type, JSONB)

        req_fk = list(table.columns["request_id"].foreign_keys)[0]
        assert req_fk.column.table.name == "requests"
        assert req_fk.ondelete == "RESTRICT"

        index_names = {idx.name for idx in table.indexes}
        assert "ix_provider_events_provider_id" in index_names
        assert "ix_provider_events_timestamp" in index_names
        assert "ix_provider_events_provider_timestamp" in index_names
        assert "ix_provider_events_type_timestamp" in index_names

    def test_model_instantiation(self) -> None:
        """Verify model instances can be created with Python dataclass-like attributes."""
        tenant = Tenant(id="tenant_test", name="Test Tenant", status="active")
        assert tenant.id == "tenant_test"
        assert tenant.name == "Test Tenant"

        req_id = f"req_{uuid.uuid4().hex[:12]}"
        req = RequestRecord(
            request_id=req_id,
            tenant_id=tenant.id,
            feature="chat",
            requested_model="qwen2.5:3b",
            final_provider="ollama",
            final_model="qwen2.5:3b",
            status="success",
            latency_ms=120.5,
            attempt_count=1,
        )
        assert req.request_id == req_id
        assert req.attempt_count == 1

        usage = UsageRecord(
            request_id=req_id,
            tenant_id=tenant.id,
            provider="ollama",
            model="qwen2.5:3b",
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            estimated_cost=Decimal("0.000150"),
        )
        assert usage.total_tokens == 30

        event = ProviderEvent(
            provider_id="ollama",
            event_type="circuit_opened",
            error_type="timeout",
            latency_ms=30000.0,
            event_metadata={"reason": "5 consecutive timeouts"},
        )
        assert event.provider_id == "ollama"
        assert event.event_metadata == {"reason": "5 consecutive timeouts"}
