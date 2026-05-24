"""Tests for schema_matching.normalize."""

from schema_matching.normalize import name_similarity, normalize_identifier, pick_canonical_name


def test_normalize_preserves_address():
    assert normalize_identifier("address") == "address"
    assert normalize_identifier("status") == "status"


def test_id_not_substring_match_to_cust_id():
    assert name_similarity("id", "cust_id") < 0.92


def test_name_not_substring_match_to_customer_name():
    assert name_similarity("name", "customer_name") < 0.92


def test_customers_customer_high_similarity():
    assert name_similarity("customers", "customer") >= 0.72


def test_pick_canonical_prefers_ddl_name():
    assert pick_canonical_name(
        ["cust_id", "customer_id"],
        prefer=["customer_id"],
    ) == "customer_id"
