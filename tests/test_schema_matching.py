"""Tests for schema_matching.matcher."""

import pytest

from schema_matching.matcher import match_schemas


def _source(source_id, table_name, columns, schema_only=False):
    return {
        "source_id": source_id,
        "filename": f"{source_id.split('::')[0]}",
        "table_name": table_name,
        "schema_only": schema_only,
        "columns": columns,
    }


def _col(name, dtype="integer"):
    return {
        "name": name,
        "inferred_data_type": dtype,
        "raw_type": dtype,
        "possible_primary_key": False,
        "sample_values": [],
    }


def test_cross_source_customer_tables_match():
    sources = [
        _source(
            "0:a.csv::customers",
            "customers",
            [
                _col("cust_id"),
                _col("customer_name", "text"),
                _col("email_addr", "text"),
            ],
        ),
        _source(
            "1:b.sql::customer",
            "customer",
            [
                _col("customer_id"),
                _col("name", "text"),
                _col("email", "text"),
            ],
            schema_only=True,
        ),
    ]
    result = match_schemas(sources)
    assert result.summary["tables_matched"] == 1
    assert len(result.table_entity_matches[0].column_matches) == 3


def test_same_source_columns_do_not_merge():
    """Columns from the same file must never appear in the same ColumnMatch group."""
    sources = [
        _source(
            "0:data.csv::orders",
            "orders",
            [_col("id"), _col("cust_id"), _col("amount", "float")],
        ),
        _source(
            "1:other.sql::order",
            "order",
            [_col("order_id"), _col("customer_id"), _col("total", "float")],
            schema_only=True,
        ),
    ]
    result = match_schemas(sources)
    entity = result.table_entity_matches[0]
    for cm in entity.column_matches:
        by_source: dict[str, list[str]] = {}
        for m in cm.mappings:
            by_source.setdefault(m.source_id, []).append(m.column_name)
        for names in by_source.values():
            assert len(names) == 1, f"same-source columns merged: {names}"
    assert not any(
        "id" in [m.column_name for m in cm.mappings]
        and "cust_id" in [m.column_name for m in cm.mappings]
        for cm in entity.column_matches
    )


def test_requires_two_sources():
    with pytest.raises(ValueError, match="at least two"):
        match_schemas([_source("0:a::t", "t", [_col("x")])])
