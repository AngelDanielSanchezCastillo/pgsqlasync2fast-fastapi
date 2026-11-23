"""Tests for migration helpers (DEPRECATED)."""
import pytest
import warnings

from pgsqlasync2fast_fastapi import (
    migrate_pk_to_uuid,
    migrate_fk_to_uuid,
    backfill_uuids,
)


def test_migration_helpers_exist():
    """Migration helpers should be importable."""
    assert migrate_pk_to_uuid is not None
    assert migrate_fk_to_uuid is not None
    assert backfill_uuids is not None


@pytest.mark.asyncio
async def test_migrate_pk_to_uuid_emits_deprecation_warning():
    """migrate_pk_to_uuid should emit a deprecation warning."""
    # We can't actually run the migration without a real database,
    # but we can verify the warning is emitted
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        
        # Try to call the function (it will fail without a DB, but that's OK)
        # We just want to verify the warning is emitted
        try:
            await migrate_pk_to_uuid(None, "test_table")
        except (AttributeError, TypeError):
            # Expected to fail without a real session
            pass
        
        # Check that a deprecation warning was emitted
        deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecation_warnings) > 0
        assert "deprecated" in str(deprecation_warnings[0].message).lower()


@pytest.mark.asyncio
async def test_migrate_fk_to_uuid_emits_deprecation_warning():
    """migrate_fk_to_uuid should emit a deprecation warning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        
        try:
            await migrate_fk_to_uuid(None, "test_table", "fk_column", "referenced_table")
        except (AttributeError, TypeError):
            pass
        
        deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecation_warnings) > 0
        assert "deprecated" in str(deprecation_warnings[0].message).lower()


@pytest.mark.asyncio
async def test_backfill_uuids_emits_deprecation_warning():
    """backfill_uuids should emit a deprecation warning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        
        try:
            await backfill_uuids(None, "test_table")
        except (AttributeError, TypeError):
            pass
        
        deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecation_warnings) > 0
        assert "deprecated" in str(deprecation_warnings[0].message).lower()
