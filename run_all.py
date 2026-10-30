"""One command for the whole pipeline:  python run_all.py   (or: python run_all.py --csv path/to/paysim.csv)"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
extra = sys.argv[1:]  # e.g. --csv ~/Downloads/PS_20174392719_1491204439457_log.csv

for step in (["01_extract/extract_paysim.py", *extra], ["02_sql/run_sql.py"], ["03_tableau/build_twb.py"]):
    print(f"\n>>> python {' '.join(step)}")
    subprocess.run([sys.executable, str(ROOT / step[0]), *step[1:]], check=True)

print("\nAll done. Open 03_tableau/AML_Fraud_Monitoring.twb in Tableau Desktop / Tableau Public.")
