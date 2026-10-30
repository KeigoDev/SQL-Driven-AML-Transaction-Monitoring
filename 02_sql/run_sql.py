"""
Step 2: runs aml_cleaning.sql against data/aml.db, prints the result of each SELECT,
then exports the mart_* tables to 03_tableau/data/ for Tableau.

    python 02_sql/run_sql.py
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "aml.db"
SQL_PATH = ROOT / "02_sql" / "aml_cleaning.sql"
TABLEAU_DATA = ROOT / "03_tableau" / "data"
MARTS = ["mart_kpi", "mart_hourly", "mart_rule_performance", "mart_alert_queue", "mart_data_quality"]

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


def iter_statements(sql_text: str):
    """Yield (cell_title, statement). sqlite3.complete_statement knows where a statement
    ends, even when it contains CTEs or CASE blocks or spans many lines."""
    cell, buffer = "setup", ""
    for line in sql_text.splitlines(keepends=True):
        if line.startswith("-- [CELL]"):
            cell = line.replace("-- [CELL]", "").strip(" -\n")
        buffer += line
        if sqlite3.complete_statement(buffer):
            stmt = buffer.strip()
            buffer = ""
            if stmt:
                yield cell, stmt


def is_query(stmt: str) -> bool:
    """True if the statement returns rows (SELECT or WITH ... SELECT, not CREATE ... AS)."""
    body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
    body = body.split("*/")[-1].strip() if body.startswith("/*") else body
    head = body.upper()
    return head.startswith("SELECT") or (head.startswith("WITH") and "CREATE TABLE" not in head)


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"{DB_PATH} not found. Run 01_extract/extract_paysim.py first.")

    con = sqlite3.connect(DB_PATH)
    current = None
    t0 = time.time()
    for cell, stmt in iter_statements(SQL_PATH.read_text()):
        if cell != current:
            print(f"\n{'=' * 90}\n{cell}  (t+{time.time() - t0:,.0f}s)\n{'=' * 90}")
            current = cell
        if is_query(stmt):
            print(pd.read_sql(stmt, con).to_string(index=False), "\n")
        else:
            con.execute(stmt)
            con.commit()

    TABLEAU_DATA.mkdir(parents=True, exist_ok=True)
    for mart in MARTS:
        df = pd.read_sql(f"SELECT * FROM {mart}", con)
        df.to_csv(TABLEAU_DATA / f"{mart}.csv", index=False)
        print(f"[export] {mart:<24} {len(df):>8,} rows -> 03_tableau/data/{mart}.csv")
    con.close()
    print(f"\nDone in {time.time() - t0:,.0f}s")


if __name__ == "__main__":
    main()
