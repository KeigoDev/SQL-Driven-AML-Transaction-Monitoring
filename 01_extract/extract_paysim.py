"""
Step 1: download the PaySim CSV and load it into SQLite (data/aml.db, table raw_transactions).

Nothing is cleaned here. The data goes in as-is, plus a few lineage columns
(src_row_num = line in the file, source_file, batch_id, loaded_at), and all the
cleaning happens in 02_sql so it's visible in one place.

    python 01_extract/extract_paysim.py                 # download with kagglehub
    python 01_extract/extract_paysim.py --csv path/to/PS_20174392719_1491204439457_log.csv

kagglehub reads your login from ~/.kaggle/kaggle.json or KAGGLE_USERNAME / KAGGLE_KEY.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "aml.db"
KAGGLE_DATASET = "ealaxi/paysim1"
CHUNK_SIZE = 500_000

# Source columns in file order. Fixed dtypes keep memory down and fail the load if a type changes.
EXPECTED_SCHEMA: dict[str, str] = {
    "step": "int32",            # hour of the simulation, 1-743
    "type": "string",           # CASH_IN, CASH_OUT, DEBIT, PAYMENT, TRANSFER
    "amount": "float64",        # transaction amount (local currency)
    "nameOrig": "string",       # sender account id  (C... = customer)
    "oldbalanceOrg": "float64", # sender balance before
    "newbalanceOrig": "float64",# sender balance after
    "nameDest": "string",       # receiver account id (C... = customer, M... = merchant)
    "oldbalanceDest": "float64",# receiver balance before
    "newbalanceDest": "float64",# receiver balance after
    "isFraud": "int8",          # 1 = fraud
    "isFlaggedFraud": "int8",   # the operator's existing fraud flag
}


def locate_source_csv(csv_arg: str | None) -> Path:
    """Return the path of the PaySim CSV: either the one passed in, or download it."""
    if csv_arg:
        path = Path(csv_arg).expanduser().resolve()
        if not path.exists():
            sys.exit(f"[extract] CSV not found: {path}")
        return path

    try:
        import kagglehub  # imported lazily so --csv users don't need it
    except ImportError:
        sys.exit("[extract] pip install kagglehub  (or pass --csv with a manually downloaded file)")

    print(f"[extract] downloading {KAGGLE_DATASET} via kagglehub ...")
    folder = Path(kagglehub.dataset_download(KAGGLE_DATASET))
    csvs = sorted(folder.glob("*.csv"))
    if not csvs:
        sys.exit(f"[extract] no CSV found in {folder}")
    return csvs[0]


def validate_header(csv_path: Path) -> None:
    """Read only the header row and compare it with the expected schema."""
    header = list(pd.read_csv(csv_path, nrows=0).columns)
    expected = list(EXPECTED_SCHEMA)
    missing = [c for c in expected if c not in header]
    extra = [c for c in header if c not in expected]
    if missing or extra:
        sys.exit(f"[extract] schema drift! missing={missing} extra={extra}")
    if header != expected:
        print("[extract] WARNING: column order differs from the expected order (still loadable)")
    print(f"[extract] schema OK: {len(header)} columns")


def extract_and_load(csv_path: Path, db_path: Path) -> int:
    """Chunked read -> add lineage columns -> append to SQLite raw_transactions."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    batch_id = uuid.uuid4().hex[:12]
    loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    con = sqlite3.connect(db_path)
    # Full reload: the raw layer is a faithful copy of the file, rebuilt each run.
    con.execute("DROP TABLE IF EXISTS raw_transactions")

    total = 0
    reader = pd.read_csv(csv_path, dtype=EXPECTED_SCHEMA, chunksize=CHUNK_SIZE)
    for i, chunk in enumerate(reader, start=1):
        # Lineage: src_row_num lets us trace any cleaned row back to its exact line in the file.
        chunk.insert(0, "src_row_num", range(total + 1, total + len(chunk) + 1))
        chunk["source_file"] = csv_path.name
        chunk["batch_id"] = batch_id
        chunk["loaded_at"] = loaded_at

        chunk.to_sql("raw_transactions", con, if_exists="append", index=False)
        total += len(chunk)
        print(f"[extract] chunk {i}: {len(chunk):>9,} rows  (running total {total:,})")

    con.commit()
    con.close()
    return total


def profile(db_path: Path) -> None:
    """Print row counts, fraud rate, type mix and nulls for the loaded table."""
    con = sqlite3.connect(db_path)
    summary = pd.read_sql(
        """
        SELECT COUNT(*)                         AS rows_loaded,
               SUM(isFraud)                     AS fraud_rows,
               ROUND(100.0 * AVG(isFraud), 4)   AS fraud_rate_pct,
               SUM(isFlaggedFraud)              AS legacy_flags,
               MIN(step) AS first_step, MAX(step) AS last_step
        FROM raw_transactions
        """,
        con,
    )
    by_type = pd.read_sql(
        """
        SELECT type, COUNT(*) AS txns, SUM(isFraud) AS fraud
        FROM raw_transactions GROUP BY type ORDER BY txns DESC
        """,
        con,
    )
    nulls = pd.read_sql(
        "SELECT " + ", ".join(f"SUM({c} IS NULL) AS {c}" for c in EXPECTED_SCHEMA) + " FROM raw_transactions",
        con,
    ).T.rename(columns={0: "null_count"})
    con.close()

    print("\n=== LOAD SUMMARY ===")
    print(summary.to_string(index=False))
    print("\n=== TRANSACTIONS BY TYPE ===")
    print(by_type.to_string(index=False))
    print("\n=== NULL COUNTS (raw) ===")
    print(nulls.to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PaySim and load it into SQLite (raw layer).")
    parser.add_argument("--csv", help="path to a manually downloaded PaySim CSV")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite file (default {DB_PATH})")
    args = parser.parse_args()

    csv_path = locate_source_csv(args.csv)
    print(f"[extract] source: {csv_path}")
    validate_header(csv_path)
    rows = extract_and_load(csv_path, Path(args.db))
    print(f"[extract] loaded {rows:,} rows into {args.db} -> raw_transactions")
    profile(Path(args.db))


if __name__ == "__main__":
    main()
