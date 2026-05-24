"""
Pydantic models for schema matching (pipeline stage 3.4).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SourceColumn(BaseModel):
    name: str
    inferred_data_type: str = "unknown"
    raw_type: str = "unknown"
    possible_primary_key: bool = False
    sample_values: List[Any] = Field(default_factory=list)


class SchemaSource(BaseModel):
    """One profiled table from a single input file."""
    source_id: str
    filename: str
    table_name: str
    schema_only: bool = False
    columns: List[SourceColumn]


class ColumnMapping(BaseModel):
    source_id: str
    table_name: str
    column_name: str
    inferred_data_type: str


class ColumnMatch(BaseModel):
    canonical_name: str
    confidence: float
    match_reason: str
    mappings: List[ColumnMapping]


class TableEntityMatch(BaseModel):
    canonical_table_name: str
    confidence: float
    source_ids: List[str]
    table_names: List[str]
    filenames: List[str]
    column_matches: List[ColumnMatch] = Field(default_factory=list)


class UnmatchedTable(BaseModel):
    source_id: str
    filename: str
    table_name: str
    reason: str


class UnmatchedColumn(BaseModel):
    source_id: str
    table_name: str
    column_name: str
    reason: str


class MergeSuggestion(BaseModel):
    """Suggested unified entity after resolving naming differences."""
    entity_type: str  # "table" | "column"
    canonical_name: str
    merged_from: List[Dict[str, str]]
    confidence: float
    notes: Optional[str] = None


class SchemaMatchingResult(BaseModel):
    source_count: int
    table_entity_matches: List[TableEntityMatch] = Field(default_factory=list)
    unmatched_tables: List[UnmatchedTable] = Field(default_factory=list)
    unmatched_columns: List[UnmatchedColumn] = Field(default_factory=list)
    merge_suggestions: List[MergeSuggestion] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
