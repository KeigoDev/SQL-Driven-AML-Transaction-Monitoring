# AML transaction monitoring with SQL

Fraud detection rules in SQL on the PaySim mobile-money dataset (6.3M transactions),
with a Tableau dashboard on top.

Python (pandas) for loading, SQL for everything else (SQLite, plus a MySQL 8 version),
Tableau for the dashboard.

## Why

PaySim comes with the operator's own fraud flag (`isFlaggedFraud`), a single rule that
flags transfers over a fixed amount. It catches 16 of roughly 8,200 fraud cases, about
0.2%. I wanted to see how far a handful of simple, explainable rules could get instead,
with every alert showing which rules it hit.

## Data

PaySim, from Kaggle: https://www.kaggle.com/datasets/ealaxi/paysim1

6,362,620 transactions, 11 columns, about 470 MB. 743 hourly steps, so roughly 30 days.

It's synthetic. Real bank transactions aren't public, so this is a simulation calibrated on
a month of real logs from a mobile-money service in Africa (López-Rojas, Elmir & Axelsson,
2016). It has fraud labels, which is what makes it possible to measure the rules.

| Column | Meaning |
|---|---|
| `step` | hour of the simulation (1-743) |
| `type` | CASH_IN, CASH_OUT, DEBIT, PAYMENT, TRANSFER |
| `amount` | transaction amount |
| `nameOrig` / `nameDest` | sender / receiver id (`C` = customer, `M` = merchant) |
| `oldbalanceOrg`, `newbalanceOrig` | sender balance before/after |
| `oldbalanceDest`, `newbalanceDest` | receiver balance before/after |
| `isFraud` | fraud label |
| `isFlaggedFraud` | the operator's existing flag |

## How it works

1. `01_extract/extract_paysim.py` downloads the CSV (or takes `--csv`), checks the
   header, and loads it into SQLite in 500k-row chunks without changing anything. It adds
   the source line number so every row can be traced back to the file.
2. `02_sql/aml_cleaning.sql` does the rest. `run_sql.py` runs it and exports the
   Tableau tables to CSV. The notebook runs the same script one section at a time.
3. `03_tableau/build_twb.py` writes the Tableau workbook pointing at those CSVs.

### Cleaning

Exact duplicates (every column identical, balances included) are removed, keeping the
first copy in the file. A customer can't produce the same row with the same balances
twice, so these come from the load.

Near-duplicates (same hour, sender, receiver, type and amount, but different balances)
are kept and flagged. Repeated payments like that can be structuring, so deleting them
would throw away signal.

Rows that can't be real (missing keys, amount <= 0, bad account ids) are removed.
Balance mismatches are kept as a feature.

Every removed row goes into `dq_removed_rows` with a reason before the clean table is
built, and a check confirms raw = clean + removed.

### Rules

The rules only look at TRANSFER and CASH_OUT, the only types with fraud. None of them
use the fraud label.

| Rule | What it looks for | Weight |
|---|---|---|
| R1 Account drain | whole sender balance moved, balance ends at 0 | 40 |
| R2 Transfer -> cash-out | TRANSFER and CASH_OUT for the same amount in the same hour | 25 |
| R3 Ghost destination | receiver balance is 0 before and after despite receiving money | 15 |
| R4 High value | amount >= 200,000 | 10 |
| R5 Ledger mismatch | sender balance off by more than 1.00 | 5 |
| R6 Mule fan-in | receiver got money from 10+ different senders | 5 |

A score of 50 or more raises an alert. `mart_rule_performance` has precision and recall
for the old flag, each rule, and the combined score.

### Dashboard

`03_tableau/AML_Fraud_Monitoring.twb`, 1600x1000. KPI tiles across the top (fraud value,
fraud cases, old flag recall, new recall, precision), then fraud by day, recall by rule,
fraud by hour, fraud by type, a data quality panel and the alert queue.

Red is fraud, violet is the new rules, grey is the old flag.

## Running it

```bash
pip install -r requirements.txt
python run_all.py                       # downloads from Kaggle (needs ~/.kaggle/kaggle.json)
python run_all.py --csv ~/Downloads/PS_20174392719_1491204439457_log.csv
```

Then open `03_tableau/AML_Fraud_Monitoring.twb`. The workbook stores the full path to
`03_tableau/data`, so if you move the project, run `python 03_tableau/build_twb.py` again.

For Tableau Public, use `03_tableau/AML_Fraud_Monitoring.twbx` instead. It is the same dashboard
with the data built in as extracts (Tableau Public won't take live CSV connections), and it opens
anywhere without the CSVs.

A full run takes a few minutes and the database ends up around 2-3 GB.

### MySQL Workbench

`02_sql/aml_cleaning_mysql.sql` is the same script for MySQL 8. It loads the CSV itself.
The setup steps (local infile, timeout, CSV path) are at the top of the file.

## Results

From `mart_kpi` after a full run.

| | Old flag | Risk score |
|---|---|---|
| Recall | | |
| Precision | | |
| Alerts | | |

## Files

```
01_extract/extract_paysim.py        download, check and load the CSV
02_sql/aml_cleaning.sql             cleaning, rules and Tableau tables (SQLite)
02_sql/aml_cleaning_mysql.sql       same, for MySQL 8
02_sql/aml_sql_cleaning.ipynb       the SQL script, section by section
02_sql/run_sql.py                   runs the SQL and exports the CSVs
03_tableau/build_twb.py             writes the Tableau workbook
03_tableau/AML_Fraud_Monitoring.twb
03_tableau/AML_Fraud_Monitoring.twbx    same dashboard with built-in extracts (Tableau Public)
run_all.py                          runs all three steps
```
