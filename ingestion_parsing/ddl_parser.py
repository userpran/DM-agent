import sqlparse
from sqlparse.tokens import DDL
import re

def extract_table_name(statement) -> str:
    """
    Loops through the tokenized parts of a CREATE statement to isolate the exact table name.
    """
    # Remove blank space tokens
    tokens = [t for t in statement.tokens if not t.is_whitespace]
    for i, token in enumerate(tokens):
        # Look for the start of the 'CREATE' definition.
        if token.ttype is DDL and token.normalized.upper() == "CREATE":
            # Jump past common SQL modifier keywords to find the table name token
            for j in range(i + 1, len(tokens)):
                val = tokens[j].normalized.upper()
                if val in ("TABLE", "OR", "REPLACE", "IF", "NOT", "EXISTS"):
                    continue
                # Clean off SQL wrappers like double quotes or backticks before returning.
                return str(tokens[j]).strip().strip('"').strip('`')
    return ""

def process_column(col_def: str, columns: list):
    """
    Cleans an individual column declaration string and adds it to column index.
    """
    col_def = col_def.strip()
    if not col_def:
        return

    # Skip table-level constraints (like PRIMARY KEY declarations at the bottom of a block)
    # We only want to capture individual column lines here  
    upper = col_def.upper()
    if any(upper.startswith(kw) for kw in ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX")):
        return

    # A valid line must have at least a name and a datatype (e.g.: 'customer_id INT').
    parts = col_def.split()
    if len(parts) < 2:
        return

    # Strip identifiers/quotes from the column name and cleanly separate the datatype.
    col_name = parts[0].strip('"').strip('`').strip("'")
    col_type = parts[1].rstrip(',')

    columns.append({
        "name": col_name,
        "type": col_type,
        "raw_definition": col_def
    })

def extract_columns(raw_sql: str) -> list:
    """
    Isolates the block inside the table's parentheses and splits it into column definitions.
    """
    # Get everything inside the outer brackets of the CREATE TABLE(...) block
    match = re.search(r'\((.*)\)', raw_sql, re.DOTALL)
    if not match:
        return []

    inner = match.group(1)
    columns = []
    depth = 0
    current = ""

    # Track parenthesis depth so we don't accidentally split lines at inline commas found inside types like DECIMAL(10,2)
    # We only want to split at commas that separate actual column definitions
    for char in inner:
        if char == '(': depth += 1
        elif char == ')': depth -= 1
 
        # Only split lines at top level commas where depth is 0
        if char == ',' and depth == 0:
            process_column(current.strip(), columns)
            current = ""
        else:
            current += char

    # the very last column definition left in the buffer.
    if current.strip():
        process_column(current.strip(), columns)

    return columns

def parse_ddl(content: str) -> dict:
    """
    Cleans raw DDL input strings, finds valid CREATE TABLE blocks, and maps out their structures.
    """
    if not content or content.strip() == "":
        return {"error": "DDL file is empty"}

    # Use regular expressions to strip out single-line (--) and multi-line (/* */) comments 
    # so that they don't break the split logic
    content_clean = re.sub(r'--[^\n]*', '', content)
    content_clean = re.sub(r'/\*.*?\*/', '', content_clean, flags=re.DOTALL)

    #sqlparse splits the script into a clean array of statements
    parsed = sqlparse.parse(content_clean)
    if not parsed:
        return {"error": "Could not parse DDL content"}

    tables = []
    for statement in parsed:
        stmt_str = str(statement).strip()
        # Filter out inserts, updates, deletes, drops, etc. Strictly looking for table schemas.
        if not stmt_str or statement.get_type() != "CREATE":
            continue

        table_name = extract_table_name(statement)
        if not table_name:
            continue

        # Extract the column array for this table.
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
