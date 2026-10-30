# Graph Report - aml-fraud-project  (2026-10-31)

## Corpus Check
- Corpus is ~8,243 words - fits in a single context window. You may not need a graph.

## Summary
- 113 nodes · 186 edges · 6 communities
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 10 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- PaySim Extract & SQL Runner
- MySQL Cleaning Schema
- SQLite Cleaning Schema
- Tableau Workbook Builder
- AML Rules & Dataset
- Pipeline Entry & Dependencies

## God Nodes (most connected - your core abstractions)
1. `README: AML transaction monitoring with SQL` - 9 edges
2. `Risk Score (alert at >= 50)` - 9 edges
3. `extract_and_load()` - 8 edges
4. `main()` - 7 edges
5. `worksheet()` - 7 edges
6. `locate_source_csv()` - 6 edges
7. `validate_header()` - 6 edges
8. `profile()` - 6 edges
9. `main()` - 6 edges
10. `datasource_xml()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `locate_source_csv()` --references--> `kagglehub>=0.2`  [INFERRED]
  01_extract/extract_paysim.py → requirements.txt
- `extract_and_load()` --shares_data_with--> `PaySim Dataset`  [INFERRED]
  01_extract/extract_paysim.py → README.md
- `jupyter` --conceptually_related_to--> `aml_sql_cleaning.ipynb notebook`  [INFERRED]
  requirements.txt → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Weighted AML rules R1-R6 combined into risk score** — readme_r1_account_drain, readme_r2_transfer_cash_out, readme_r3_ghost_destination, readme_r4_high_value, readme_r5_ledger_mismatch, readme_r6_mule_fan_in, readme_risk_score [EXTRACTED 1.00]
- **Extract -> SQL -> Tableau pipeline orchestrated by run_all** — run_all, 01_extract_extract_paysim, 02_sql_run_sql, 02_sql_aml_cleaning, 03_tableau_build_twb, readme_aml_fraud_monitoring_dashboard [EXTRACTED 1.00]
- **Data cleaning policy with audit trail** — readme_exact_duplicate_removal, readme_near_duplicate_flagging, readme_dq_removed_rows, 02_sql_aml_cleaning [EXTRACTED 1.00]

## Communities (6 total, 0 thin omitted)

### Community 0 - "PaySim Extract & SQL Runner"
Cohesion: 0.15
Nodes (8): extract_and_load(), locate_source_csv(), main(), profile(), validate_header(), is_query(), iter_statements(), main()

### Community 1 - "MySQL Cleaning Schema"
Cohesion: 0.12
Nodes (23): clean_transactions, dim_account, dq_duplicate_review, dq_removed_rows, ix_alerts_id, ix_alerts_model, ix_clean_dest, ix_clean_id (+15 more)

### Community 2 - "SQLite Cleaning Schema"
Cohesion: 0.12
Nodes (21): clean_transactions, dim_account, dq_duplicate_review, dq_removed_rows, ix_alerts_model, ix_clean_dest, ix_clean_orig, ix_clean_pair (+13 more)

### Community 3 - "Tableau Workbook Builder"
Cohesion: 0.23
Nodes (10): color_map(), column_meta(), dashboard_xml(), datasource_xml(), ds_name(), fmt(), grid(), inst() (+2 more)

### Community 4 - "AML Rules & Dataset"
Cohesion: 0.16
Nodes (13): AML_Fraud_Monitoring.twb Tableau Dashboard, isFlaggedFraud (operator's old flag), López-Rojas, Elmir & Axelsson (2016) PaySim paper, mart_kpi table, mart_rule_performance table, PaySim Dataset, R1 Account Drain (weight 40), R2 Transfer -> Cash-out (weight 25) (+5 more)

### Community 5 - "Pipeline Entry & Dependencies"
Cohesion: 0.22
Nodes (5): README: AML transaction monitoring with SQL, aml_sql_cleaning.ipynb notebook, jupyter, kagglehub>=0.2, pandas>=2.0

## Knowledge Gaps
- **6 isolated node(s):** `López-Rojas, Elmir & Axelsson (2016) PaySim paper`, `dq_removed_rows table`, `R1 Account Drain (weight 40)`, `R2 Transfer -> Cash-out (weight 25)`, `R3 Ghost Destination (weight 15)` (+1 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 36 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `README: AML transaction monitoring with SQL` connect `Pipeline Entry & Dependencies` to `PaySim Extract & SQL Runner`, `MySQL Cleaning Schema`, `SQLite Cleaning Schema`, `Tableau Workbook Builder`, `AML Rules & Dataset`?**
  _High betweenness centrality (0.424) - this node is a cross-community bridge._
- **What connects `López-Rojas, Elmir & Axelsson (2016) PaySim paper`, `dq_removed_rows table`, `R1 Account Drain (weight 40)` to the rest of the system?**
  _6 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `PaySim Extract & SQL Runner` be split into smaller, more focused modules?**
  _Cohesion score 0.1476923076923077 - nodes in this community are weakly interconnected._
- **Why does `Risk Score (alert at >= 50)` connect `AML Rules & Dataset` to `SQLite Cleaning Schema`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Should `MySQL Cleaning Schema` be split into smaller, more focused modules?**
  _Cohesion score 0.11956521739130435 - nodes in this community are weakly interconnected._
- **Why does `main()` connect `PaySim Extract & SQL Runner` to `SQLite Cleaning Schema`?**
  _High betweenness centrality (0.088) - this node is a cross-community bridge._
- **Should `SQLite Cleaning Schema` be split into smaller, more focused modules?**
  _Cohesion score 0.11857707509881422 - nodes in this community are weakly interconnected._