"""
schema_matching/matcher.py

Identifies overlapping entities across multiple profiled sources,
resolves naming differences, and proposes merge groupings.

Public API:  match_schemas(sources: list[SchemaSource | dict]) -> SchemaMatchingResult
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from schema_matching.models import (
    ColumnMapping,
    ColumnMatch,
    MergeSuggestion,
    SchemaMatchingResult,
    SchemaSource,
    SourceColumn,
    TableEntityMatch,
    UnmatchedColumn,
    UnmatchedTable,
)
from schema_matching.normalize import name_similarity, pick_canonical_name

TABLE_MATCH_THRESHOLD = 0.72
COLUMN_MATCH_THRESHOLD = 0.68
COLUMN_NAME_WEIGHT = 0.62
COLUMN_TYPE_WEIGHT = 0.38

_TYPE_GROUPS = [
    {"integer", "float", "decimal"},
    {"text", "categorical", "uuid", "json"},
    {"date", "datetime", "time"},
]


def _type_compatibility(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a == "unknown" or b == "unknown":
        return 0.55
    for group in _TYPE_GROUPS:
        if a in group and b in group:
            return 0.78
    return 0.0


def _column_score(col_a: SourceColumn, col_b: SourceColumn) -> float:
    name_score = name_similarity(col_a.name, col_b.name)
    type_score = _type_compatibility(col_a.inferred_data_type, col_b.inferred_data_type)
    return round(name_score * COLUMN_NAME_WEIGHT + type_score * COLUMN_TYPE_WEIGHT, 4)


def _table_score(src_a: SchemaSource, src_b: SchemaSource) -> float:
    return name_similarity(src_a.table_name, src_b.table_name)


def _union_find_cluster(
    pairs: List[Tuple[int, int, float]],
    n: int,
    threshold: float,
) -> List[List[int]]:
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i, j, score in pairs:
        if score >= threshold:
            union(i, j)

    clusters: Dict[int, List[int]] = {}
    for idx in range(n):
        root = find(idx)
        clusters.setdefault(root, []).append(idx)
    return list(clusters.values())


def _match_columns_in_cluster(
    sources: List[SchemaSource],
) -> Tuple[List[ColumnMatch], List[UnmatchedColumn]]:
    """
    Match columns across different sources only (never within the same source).
    At most one column per source_id per match group.
    """
    entries: List[Tuple[int, SourceColumn]] = []
    for si, src in enumerate(sources):
        for col in src.columns:
            entries.append((si, col))

    if not entries:
        return [], []

    used: Set[int] = set()
    column_matches: List[ColumnMatch] = []
    unmatched: List[UnmatchedColumn] = []

    for i, (si_a, col_a) in enumerate(entries):
        if i in used:
            continue

        src_a = sources[si_a]
        group_indices = [i]
        group_mappings = [
            ColumnMapping(
                source_id=src_a.source_id,
                table_name=src_a.table_name,
                column_name=col_a.name,
                inferred_data_type=col_a.inferred_data_type,
            )
        ]
        sources_in_group: Set[str] = {src_a.source_id}

        for j in range(i + 1, len(entries)):
            if j in used:
                continue
            si_b, col_b = entries[j]
            src_b = sources[si_b]

            # Cross-source only; one column per source per group
            if src_b.source_id == src_a.source_id:
                continue
            if src_b.source_id in sources_in_group:
                continue

            if _column_score(col_a, col_b) >= COLUMN_MATCH_THRESHOLD:
                group_indices.append(j)
                sources_in_group.add(src_b.source_id)
                group_mappings.append(
                    ColumnMapping(
                        source_id=src_b.source_id,
                        table_name=src_b.table_name,
                        column_name=col_b.name,
                        inferred_data_type=col_b.inferred_data_type,
                    )
                )

        if len(group_mappings) == 1:
            unmatched.append(
                UnmatchedColumn(
                    source_id=src_a.source_id,
                    table_name=src_a.table_name,
                    column_name=col_a.name,
                    reason="no_cross_source_column_match",
                )
            )
            used.add(i)
            continue

        scores = [
            _column_score(col_a, entries[j][1])
            for j in group_indices[1:]
        ]
        confidence = round(sum(scores) / len(scores), 4) if scores else 1.0

        source_by_id = {s.source_id: s for s in sources}
        ddl_names = [
            m.column_name
            for m in group_mappings
            if source_by_id.get(m.source_id) and source_by_id[m.source_id].schema_only
        ]
        canonical = pick_canonical_name(
            [m.column_name for m in group_mappings],
            prefer=ddl_names or None,
        )

        column_matches.append(
            ColumnMatch(
                canonical_name=canonical,
                confidence=confidence,
                match_reason="name_and_type",
                mappings=group_mappings,
            )
        )
        used.update(group_indices)

    return column_matches, unmatched


def match_schemas(sources: List[SchemaSource | dict]) -> SchemaMatchingResult:
    """
    Match tables and columns across multiple profiled sources.
    """
    if len(sources) < 2:
        raise ValueError("Schema matching requires at least two sources")

    parsed: List[SchemaSource] = [
        s if isinstance(s, SchemaSource) else SchemaSource.model_validate(s)
        for s in sources
    ]

    n = len(parsed)
    table_pairs: List[Tuple[int, int, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            table_pairs.append((i, j, _table_score(parsed[i], parsed[j])))

    clusters = _union_find_cluster(table_pairs, n, TABLE_MATCH_THRESHOLD)

    table_entity_matches: List[TableEntityMatch] = []
    unmatched_tables: List[UnmatchedTable] = []
    all_unmatched_columns: List[UnmatchedColumn] = []
    merge_suggestions: List[MergeSuggestion] = []

    for cluster in clusters:
        cluster_sources = [parsed[i] for i in cluster]

        if len(cluster) == 1:
            src = cluster_sources[0]
            unmatched_tables.append(
                UnmatchedTable(
                    source_id=src.source_id,
                    filename=src.filename,
                    table_name=src.table_name,
                    reason="no_cross_source_table_match",
                )
            )
            _, col_unmatched = _match_columns_in_cluster(cluster_sources)
            all_unmatched_columns.extend(col_unmatched)
            continue

        table_names = [s.table_name for s in cluster_sources]
        ddl_table_names = [s.table_name for s in cluster_sources if s.schema_only]
        canonical_table = pick_canonical_name(table_names, prefer=ddl_table_names)

        pair_scores = [
            score for i, j, score in table_pairs if i in cluster and j in cluster
        ]
        table_confidence = (
            round(sum(pair_scores) / len(pair_scores), 4) if pair_scores else 1.0
        )

        col_matches, col_unmatched = _match_columns_in_cluster(cluster_sources)
        all_unmatched_columns.extend(col_unmatched)

        table_entity_matches.append(
            TableEntityMatch(
                canonical_table_name=canonical_table,
                confidence=table_confidence,
                source_ids=[s.source_id for s in cluster_sources],
                table_names=table_names,
                filenames=sorted({s.filename for s in cluster_sources}),
                column_matches=col_matches,
            )
        )

        merge_suggestions.append(
            MergeSuggestion(
                entity_type="table",
                canonical_name=canonical_table,
                merged_from=[
                    {
                        "source_id": s.source_id,
                        "filename": s.filename,
                        "original_name": s.table_name,
                    }
                    for s in cluster_sources
                ],
                confidence=table_confidence,
                notes="Tables from different inputs likely describe the same entity",
            )
        )

        for cm in col_matches:
            if len(cm.mappings) < 2:
                continue
            merge_suggestions.append(
                MergeSuggestion(
                    entity_type="column",
                    canonical_name=cm.canonical_name,
                    merged_from=[
                        {
                            "source_id": m.source_id,
                            "table_name": m.table_name,
                            "original_name": m.column_name,
                        }
                        for m in cm.mappings
                    ],
                    confidence=cm.confidence,
                    notes=f"Resolved naming within entity '{canonical_table}'",
                )
            )

    return SchemaMatchingResult(
        source_count=n,
        table_entity_matches=table_entity_matches,
        unmatched_tables=unmatched_tables,
        unmatched_columns=all_unmatched_columns,
        merge_suggestions=merge_suggestions,
        summary={
            "tables_matched": len(table_entity_matches),
            "tables_unmatched": len(unmatched_tables),
            "columns_matched": sum(len(t.column_matches) for t in table_entity_matches),
            "columns_unmatched": len(all_unmatched_columns),
            "merge_suggestions_count": len(merge_suggestions),
        },
    )
