"""
pipeline/source_extractor.py

Convert single-file pipeline outputs into SchemaSource dicts for schema matching.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _columns_from_profiles(column_profiles: List[dict]) -> List[dict]:
    return [
        {
            "name": cp.get("column_name", ""),
            "inferred_data_type": cp.get("inferred_data_type", "unknown"),
            "raw_type": cp.get("raw_type", "unknown"),
            "possible_primary_key": cp.get("possible_primary_key", False),
            "sample_values": cp.get("sample_values", []) or [],
        }
        for cp in column_profiles
        if cp.get("column_name")
    ]


def pipeline_result_to_sources(
    pipeline_result: dict,
    *,
    file_index: int = 0,
) -> List[dict]:
    """
    Extract one or more SchemaSource-compatible dicts from a CSV or DDL pipeline result.

    Raises ValueError when the pipeline did not produce profiling output.
    """
    filename = pipeline_result.get("filename", f"source_{file_index}")
    pipeline = pipeline_result.get("pipeline", "unknown")
    profiling = pipeline_result.get("profiling_output")

    if profiling is None:
        raise ValueError(
            f"No profiling_output in pipeline result for '{filename}' "
            f"(status={pipeline_result.get('status')})"
        )

    sources: List[dict] = []

    if pipeline == "csv":
        table_name = profiling.get("table_name", filename.rsplit(".", 1)[0])
        columns = _columns_from_profiles(profiling.get("column_profiles", []))
        schema_only = profiling.get("table_summary", {}).get("schema_only", False)
        sources.append(
            {
                "source_id": f"{file_index}:{filename}::{table_name}",
                "filename": filename,
                "table_name": table_name,
                "schema_only": schema_only,
                "columns": columns,
            }
        )
        return sources

    if pipeline == "ddl":
        for tbl_idx, tbl in enumerate(profiling):
            if tbl.get("status") != "success":
                continue
            table_name = tbl.get("table_name", f"table_{tbl_idx}")
            columns = _columns_from_profiles(tbl.get("column_profiles", []))
            schema_only = tbl.get("table_summary", {}).get("schema_only", True)
            sources.append(
                {
                    "source_id": f"{file_index}:{filename}::{table_name}",
                    "filename": filename,
                    "table_name": table_name,
                    "schema_only": schema_only,
                    "columns": columns,
                }
            )
        return sources

    raise ValueError(f"Unsupported pipeline type: {pipeline}")


def merge_pipeline_sources(pipeline_results: List[dict]) -> List[dict]:
    """Flatten multiple pipeline results into a single list of SchemaSource dicts."""
    all_sources: List[dict] = []
    for idx, result in enumerate(pipeline_results):
        all_sources.extend(pipeline_result_to_sources(result, file_index=idx))
    return all_sources
