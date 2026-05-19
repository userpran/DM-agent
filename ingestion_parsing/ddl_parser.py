import sqlparse
from sqlparse.tokens import DDL
import re

def extract_table_name(statement) -> str:
    """
    Loops through the tokenized parts of a CREATE statement to isolate the exact table name.
    """
    # Filter out whitespace tokens to get clean keyword sequences
    tokens = [t for t in statement.tokens if not t.is_whitespace]
    for i, token in enumerate(tokens):
        # Look for the start of the 'CREATE' definition.
        if token.ttype is DDL and token.normalized.upper() == "CREATE":
            # Jump past common SQL modifier keywords to find the table name token
            for j in range(i + 1, len(tokens)):
                val = tokens[j].normalized.upper()
                if val in ("TABLE", "OR", "REPLACE", "IF", "NOT", "EXISTS"):
                    continue
                # Clean off SQL wrappers like double quotes or backticks and return the clean name
                return str(tokens[j]).strip().strip('"').strip('`')
            
    return ""   #if no name found return empty string

def process_column(col_def: str, columns: list):
    """
    Parses a raw column declaration string to extract the name, data type, 
    and full definition, appending the structured metadata to the columns list.
    """

    col_def = col_def.strip()
    if not col_def:
        return

    # Skip table-level constraints (like PRIMARY KEY declarations at the bottom of a table block)
    # We only want individual column lines here  
    upper = col_def.upper()
    if any(upper.startswith(kw) for kw in ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX")): 
        return

    # A valid line must have at least a name and a datatype (e.g.: 'customer_id INT').
    parts = col_def.split()
    if len(parts) < 2:
        return

    # Cleanly separate the column name and the datatype. Strip quotes from the name and remove trailing(right) commas from the datatype 
    col_name = parts[0].strip('"').strip('`').strip("'")
    # note: types with spaces inside (e.g. DECIMAL(10, 2), not DECIMAL(10,2)) will be truncated by split() i.e. parts[1] gives only DECIMAL(10,
    col_type = parts[1].rstrip(',')

    columns.append({
        "name": col_name,
        "type": col_type,
        "raw_definition": col_def  #Use this for full type inference as it retains information even if a space breaks the col_type parsing.
    })

def extract_columns(raw_sql: str) -> list:
    """
    Isolates the block inside the table's parentheses and splits it into column definitions.
    """
    # Extract everything inside the main outer brackets of the CREATE TABLE(...) block
    match = re.search(r'\((.*)\)', raw_sql, re.DOTALL)
    if not match:
        return []

    inner = match.group(1) #isolates just the text content (the entire list of columns)
    columns = []
    depth = 0
    current = ""

    # Track parenthesis depth to avoid splitting lines at inline commas found inside types like DECIMAL(10,2)
    # Only split at commas that separate actual column definitions (i.e., those at depth 0)
    for char in inner:
        if char == '(': depth += 1
        elif char == ')': depth -= 1
 
        if char == ',' and depth == 0:
            process_column(current.strip(), columns)
            current = ""
        else:
            current += char

    # processes the very last column definition left in the buffer after the loop ends (since the final column won't end in a trailing comma)
    if current.strip():
        process_column(current.strip(), columns)

    return columns 

def parse_ddl(content: str) -> dict:
    """
    Cleans raw DDL input strings, finds valid CREATE TABLE blocks, and maps out their structures.
    """
    if not content or content.strip() == "": #checks if incoming SQL text string is completely blank
        return {"error": "DDL file is empty"}

    # Use Regular Expressions to strip out single-line (--) and multi-line (/* */) comments before parsing
    content_clean = re.sub(r'--[^\n]*', '', content) 
    content_clean = re.sub(r'/\*.*?\*/', '', content_clean, flags=re.DOTALL)

    #sqlparse splits the script into a clean array of SQL statements
    parsed = sqlparse.parse(content_clean)
    if not parsed:
        return {"error": "Could not parse DDL content"}

    tables = []
    for statement in parsed:
        stmt_str = str(statement).strip()
        # If the statement is blank or it isn't a CREATE operation (INSERT, DELETE, etc), skip it 
        #
        if not stmt_str or statement.get_type() != "CREATE":
            continue

        table_name = extract_table_name(statement) 
        if not table_name:      #ignore empty table names
            continue

        # Extract the column array for this table
        columns = extract_columns(stmt_str)

        tables.append({
            "table_name": table_name,
            "column_count": len(columns),
            "columns": columns
        })

    if not tables:    #no valid tables found
        return {"error": "No valid CREATE TABLE statements found"}

    return {
        "source_type": "ddl",
        "table_count": len(tables),
        "tables": tables
    }
