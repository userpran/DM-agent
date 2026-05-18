import sqlparse
from sqlparse.tokens import DDL
import re

def extract_table_name(statement) -> str:
    tokens = [t for t in statement.tokens if not t.is_whitespace]
    for i, token in enumerate(tokens):
        if token.ttype is DDL and token.normalized.upper() == "CREATE":
            for j in range(i + 1, len(tokens)):
                val = tokens[j].normalized.upper()
                if val in ("TABLE", "OR", "REPLACE", "IF", "NOT", "EXISTS"):
                    continue
                return str(tokens[j]).strip().strip('"').strip('`')
    return ""

def process_column(col_def: str, columns: list):
    col_def = col_def.strip()
    if not col_def:
        return
        
    upper = col_def.upper()
    if any(upper.startswith(kw) for kw in ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX")):
        return

    parts = col_def.split()
    if len(parts) < 2:
        return

    col_name = parts[0].strip('"').strip('`').strip("'")
    col_type = parts[1].rstrip(',')

    columns.append({
        "name": col_name,
        "type": col_type,
        "raw_definition": col_def
    })

def extract_columns(raw_sql: str) -> list:
    match = re.search(r'\((.*)\)', raw_sql, re.DOTALL)
    if not match:
        return []

    inner = match.group(1)
    columns = []
    depth = 0
    current = ""

    for char in inner:
        if char == '(': depth += 1
        elif char == ')': depth -= 1

        if char == ',' and depth == 0:
            process_column(current.strip(), columns)
            current = ""
        else:
            current += char

    if current.strip():
        process_column(current.strip(), columns)

    return columns

def parse_ddl(content: str) -> dict:
    if not content or content.strip() == "":
        return {"error": "DDL file is empty"}

    content_clean = re.sub(r'--[^\n]*', '', content)
    content_clean = re.sub(r'/\*.*?\*/', '', content_clean, flags=re.DOTALL)

    parsed = sqlparse.parse(content_clean)
    if not parsed:
        return {"error": "Could not parse DDL content"}

    tables = []
    for statement in parsed:
        stmt_str = str(statement).strip()
        if not stmt_str or statement.get_type() != "CREATE":
            continue

        table_name = extract_table_name(statement)
        if not table_name:
            continue

        columns = extract_columns(stmt_str)

        tables.append({
            "table_name": table_name,
            "column_count": len(columns),
            "columns": columns
        })

    if not tables:
        return {"error": "No valid CREATE TABLE statements found"}

    return {
        "source_type": "ddl",
        "table_count": len(tables),
        "tables": tables
    }
