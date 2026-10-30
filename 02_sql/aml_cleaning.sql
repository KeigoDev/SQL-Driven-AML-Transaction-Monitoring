/*
  aml_cleaning.sql  (SQLite 3.25+, needs window functions)

  Cleans and de-duplicates the PaySim transactions, applies the AML rules and builds
  the tables Tableau reads:
    raw_transactions -> stg_transactions -> clean_transactions -> txn_alerts -> mart_*

  Run with: python 02_sql/run_sql.py
  The "-- [CELL]" lines split the script into sections for aml_sql_cleaning.ipynb.
  aml_cleaning_mysql.sql is the MySQL version of this file.
*/


-- [CELL] 0. Session settings
-- Faster writes. Safe here because the db can always be rebuilt from the CSV.
PRAGMA journal_mode = OFF;
PRAGMA synchronous  = OFF;
PRAGMA temp_store   = MEMORY;
PRAGMA cache_size   = -500000;   -- about 500 MB page cache


-- [CELL] 1. Profile the raw data
-- Read-only checks before changing anything.

-- 1a. Row count, fraud rate, legacy flag count
SELECT COUNT(*)                        AS raw_rows,
       SUM(isFraud)                    AS fraud_rows,
       ROUND(100.0 * AVG(isFraud), 4)  AS fraud_rate_pct,
       SUM(isFlaggedFraud)             AS legacy_rule_flags
FROM raw_transactions;

-- 1b. Nulls per column (COUNT(col) skips nulls)
SELECT COUNT(*) - COUNT(step)           AS null_step,
       COUNT(*) - COUNT(type)           AS null_type,
       COUNT(*) - COUNT(amount)         AS null_amount,
       COUNT(*) - COUNT(nameOrig)       AS null_nameOrig,
       COUNT(*) - COUNT(nameDest)       AS null_nameDest,
       COUNT(*) - COUNT(oldbalanceOrg)  AS null_oldbalanceOrg,
       COUNT(*) - COUNT(newbalanceOrig) AS null_newbalanceOrig,
       COUNT(*) - COUNT(oldbalanceDest) AS null_oldbalanceDest,
       COUNT(*) - COUNT(newbalanceDest) AS null_newbalanceDest,
       COUNT(*) - COUNT(isFraud)        AS null_isFraud
FROM raw_transactions;

-- 1c. Fraud by type. Only TRANSFER and CASH_OUT have fraud, so the rules in
--     section 9 only look at those two.
SELECT type,
       COUNT(*)      AS txns,
       SUM(isFraud)  AS fraud_txns,
       ROUND(SUM(amount), 2) AS total_amount
FROM raw_transactions
GROUP BY type
ORDER BY txns DESC;

-- 1d. Bad amounts and account ids (ids should start with C or M)
SELECT SUM(amount <= 0)                                   AS non_positive_amounts,
       SUM(SUBSTR(TRIM(nameOrig), 1, 1) NOT IN ('C','M')) AS bad_orig_prefix,
       SUM(SUBSTR(TRIM(nameDest), 1, 1) NOT IN ('C','M')) AS bad_dest_prefix,
       SUM(nameOrig = nameDest)                           AS self_transfers
FROM raw_transactions;


-- [CELL] 2. Staging
-- Trimmed, upper-cased, rounded copy of raw. No rows dropped yet, and raw is never changed.
DROP TABLE IF EXISTS stg_transactions;

CREATE TABLE stg_transactions AS
SELECT
    src_row_num,                                      -- line number in the source file
    CAST(step AS INTEGER)                  AS step,
    UPPER(TRIM(type))                      AS txn_type,       -- ' transfer' -> 'TRANSFER'
    ROUND(CAST(amount AS REAL), 2)         AS amount,         -- 2 decimals
    UPPER(TRIM(nameOrig))                  AS orig_acct,
    ROUND(CAST(oldbalanceOrg  AS REAL), 2) AS orig_bal_before,
    ROUND(CAST(newbalanceOrig AS REAL), 2) AS orig_bal_after,
    UPPER(TRIM(nameDest))                  AS dest_acct,
    ROUND(CAST(oldbalanceDest AS REAL), 2) AS dest_bal_before,
    ROUND(CAST(newbalanceDest AS REAL), 2) AS dest_bal_after,
    CAST(isFraud        AS INTEGER)        AS is_fraud,
    CAST(isFlaggedFraud AS INTEGER)        AS legacy_flag,
    source_file,
    batch_id
FROM raw_transactions;

-- used by the duplicate checks
CREATE INDEX ix_stg_dupkey ON stg_transactions (step, orig_acct, dest_acct, txn_type, amount);


-- [CELL] 3. Find exact duplicates
-- Same value in every column, balances included. A customer can't produce that twice,
-- so these come from loading (file sent twice, retried call, overlapping batch).
SELECT step, txn_type, amount, orig_acct, dest_acct,
       COUNT(*)          AS copies,
       MIN(src_row_num)  AS first_seen_row,   -- this one is kept
       MAX(src_row_num)  AS last_seen_row
FROM stg_transactions
GROUP BY step, txn_type, amount, orig_acct, orig_bal_before, orig_bal_after,
         dest_acct, dest_bal_before, dest_bal_after, is_fraud, legacy_flag
HAVING COUNT(*) > 1
ORDER BY copies DESC, amount DESC
LIMIT 50;

-- Number of duplicate groups and extra rows to drop
WITH dup_groups AS (
    SELECT COUNT(*) AS copies
    FROM stg_transactions
    GROUP BY step, txn_type, amount, orig_acct, orig_bal_before, orig_bal_after,
             dest_acct, dest_bal_before, dest_bal_after, is_fraud, legacy_flag
    HAVING COUNT(*) > 1
)
SELECT COUNT(*)                    AS duplicate_groups,
       COALESCE(SUM(copies - 1),0) AS extra_rows_to_remove
FROM dup_groups;


-- [CELL] 4. Tag duplicates
-- dup_rank 1 is the first copy in the file and is kept. Higher ranks get removed.
--
-- Near-duplicates (same hour, parties, type and amount, different balances) stay in.
-- Repeated payments in one hour can be structuring, so they get flagged for review
-- instead of deleted.
DROP TABLE IF EXISTS stg_dedup_tagged;

CREATE TABLE stg_dedup_tagged AS
WITH ranked AS (
    SELECT s.*,
           ROW_NUMBER() OVER (
               PARTITION BY step, txn_type, amount, orig_acct, orig_bal_before, orig_bal_after,
                            dest_acct, dest_bal_before, dest_bal_after, is_fraud, legacy_flag
               ORDER BY src_row_num
           ) AS dup_rank                                -- 1 = keep, >1 = exact copy
    FROM stg_transactions AS s
)
-- Count near-duplicates among kept rows only, otherwise the original of an exact
-- duplicate would also show up as a near-duplicate.
SELECT r.*,
       CASE WHEN r.dup_rank = 1 THEN
           SUM(CASE WHEN r.dup_rank = 1 THEN 1 ELSE 0 END) OVER (
               PARTITION BY step, orig_acct, dest_acct, txn_type, amount
           )
       END AS near_dup_count                            -- >1 = same payment repeated in the hour
FROM ranked AS r;

CREATE INDEX ix_tagged_rank ON stg_dedup_tagged (dup_rank);


-- [CELL] 5. Review table and removal log

-- 5a. Every row in an exact or near-duplicate group, and what happened to it
DROP TABLE IF EXISTS dq_duplicate_review;

CREATE TABLE dq_duplicate_review AS
SELECT src_row_num, step, txn_type, amount, orig_acct, dest_acct,
       dup_rank, near_dup_count,
       CASE
           WHEN dup_rank > 1        THEN 'EXACT_DUPLICATE - REMOVED'
           WHEN near_dup_count > 1  THEN 'NEAR_DUPLICATE - KEPT, FLAGGED FOR REVIEW'
       END AS decision
FROM stg_dedup_tagged
WHERE dup_rank > 1 OR near_dup_count > 1;

-- 5b. One row per removed record with the reason. Section 6 adds to it too.
DROP TABLE IF EXISTS dq_removed_rows;

CREATE TABLE dq_removed_rows (
    src_row_num  INTEGER,
    reason       TEXT,
    removed_at   TEXT DEFAULT (DATETIME('now'))
);

INSERT INTO dq_removed_rows (src_row_num, reason)
SELECT src_row_num, 'EXACT_DUPLICATE (dup_rank > 1)'
FROM stg_dedup_tagged
WHERE dup_rank > 1;

-- 5c. Count per decision
SELECT decision, COUNT(*) AS row_count
FROM dq_duplicate_review
GROUP BY decision;


-- [CELL] 6. Remove invalid rows
-- Only rows that can't be real: missing keys, amount <= 0, bad ids, unknown type.
-- Rows that look odd but are possible (balance mismatches) stay in.
INSERT INTO dq_removed_rows (src_row_num, reason)
SELECT src_row_num,
       CASE
           WHEN step IS NULL OR txn_type IS NULL OR amount IS NULL
                OR orig_acct IS NULL OR dest_acct IS NULL         THEN 'MISSING_KEY_FIELD'
           WHEN amount <= 0                                       THEN 'NON_POSITIVE_AMOUNT'
           WHEN SUBSTR(orig_acct,1,1) NOT IN ('C','M')
                OR SUBSTR(dest_acct,1,1) NOT IN ('C','M')         THEN 'INVALID_ACCOUNT_ID'
           WHEN txn_type NOT IN ('CASH_IN','CASH_OUT','DEBIT','PAYMENT','TRANSFER')
                                                                  THEN 'UNKNOWN_TXN_TYPE'
       END
FROM stg_dedup_tagged
WHERE dup_rank = 1                                   -- skip copies already removed
  AND (   step IS NULL OR txn_type IS NULL OR amount IS NULL
       OR orig_acct IS NULL OR dest_acct IS NULL
       OR amount <= 0
       OR SUBSTR(orig_acct,1,1) NOT IN ('C','M')
       OR SUBSTR(dest_acct,1,1) NOT IN ('C','M')
       OR txn_type NOT IN ('CASH_IN','CASH_OUT','DEBIT','PAYMENT','TRANSFER'));

CREATE INDEX ix_removed ON dq_removed_rows (src_row_num);

-- Removed rows by reason
SELECT reason, COUNT(*) AS rows_removed
FROM dq_removed_rows
GROUP BY reason
ORDER BY rows_removed DESC;


-- [CELL] 7. Clean table
-- Every row that isn't in dq_removed_rows (LEFT JOIN ... IS NULL), plus a few derived columns.
DROP TABLE IF EXISTS clean_transactions;

CREATE TABLE clean_transactions AS
SELECT
    t.src_row_num                                   AS txn_id,         -- = source line number
    t.step,
    ((t.step - 1) / 24) + 1                         AS sim_day,        -- day 1..31
    (t.step - 1) % 24                               AS hour_of_day,    -- 0..23
    t.txn_type,
    t.amount,
    t.orig_acct,
    t.dest_acct,
    SUBSTR(t.dest_acct, 1, 1)                       AS dest_acct_kind, -- C = customer, M = merchant
    t.orig_bal_before, t.orig_bal_after,
    t.dest_bal_before, t.dest_bal_after,
    -- Ledger gaps: before - amount should equal after. Kept as features.
    ROUND(t.orig_bal_before - t.amount - t.orig_bal_after, 2) AS orig_balance_gap,
    ROUND(t.dest_bal_before + t.amount - t.dest_bal_after, 2) AS dest_balance_gap,
    CASE WHEN t.near_dup_count > 1 THEN 1 ELSE 0 END          AS near_dup_flag,
    t.is_fraud,
    t.legacy_flag
FROM stg_dedup_tagged AS t
LEFT JOIN dq_removed_rows AS r
       ON r.src_row_num = t.src_row_num
WHERE r.src_row_num IS NULL;

CREATE INDEX ix_clean_pair  ON clean_transactions (step, amount, txn_type);
CREATE INDEX ix_clean_orig  ON clean_transactions (orig_acct);
CREATE INDEX ix_clean_dest  ON clean_transactions (dest_acct);

-- 7b. Check: raw should equal clean + removed
SELECT (SELECT COUNT(*) FROM raw_transactions)   AS raw_rows,
       (SELECT COUNT(*) FROM clean_transactions) AS clean_rows,
       (SELECT COUNT(*) FROM dq_removed_rows)    AS removed_rows,
       CASE WHEN (SELECT COUNT(*) FROM raw_transactions)
               = (SELECT COUNT(*) FROM clean_transactions) + (SELECT COUNT(*) FROM dq_removed_rows)
            THEN 'PASS' ELSE 'FAIL' END          AS reconciliation;

-- Not needed anymore
DROP TABLE IF EXISTS stg_dedup_tagged;


-- [CELL] 8. Account table
-- Sent and received activity per account. Lots of distinct senders paying into one
-- account (fan-in) is a money-mule sign, used by rule R6.
DROP TABLE IF EXISTS dim_account;

CREATE TABLE dim_account AS
WITH sent AS (                       -- behaviour as sender
    SELECT orig_acct                 AS acct,
           COUNT(*)                  AS sent_txns,
           ROUND(SUM(amount), 2)     AS sent_amount,
           COUNT(DISTINCT dest_acct) AS distinct_receivers
    FROM clean_transactions
    GROUP BY orig_acct
),
received AS (                        -- behaviour as receiver
    SELECT dest_acct                 AS acct,
           COUNT(*)                  AS recv_txns,
           ROUND(SUM(amount), 2)     AS recv_amount,
           COUNT(DISTINCT orig_acct) AS distinct_senders,
           SUM(txn_type = 'TRANSFER') AS transfers_in
    FROM clean_transactions
    GROUP BY dest_acct
),
all_accts AS (                       -- UNION dedupes the ids
    SELECT acct FROM sent
    UNION
    SELECT acct FROM received
)
SELECT a.acct,
       SUBSTR(a.acct, 1, 1)               AS acct_kind,
       COALESCE(s.sent_txns, 0)           AS sent_txns,
       COALESCE(s.sent_amount, 0)         AS sent_amount,
       COALESCE(s.distinct_receivers, 0)  AS distinct_receivers,
       COALESCE(r.recv_txns, 0)           AS recv_txns,
       COALESCE(r.recv_amount, 0)         AS recv_amount,
       COALESCE(r.distinct_senders, 0)    AS distinct_senders,
       COALESCE(r.transfers_in, 0)        AS transfers_in
FROM all_accts      AS a
LEFT JOIN sent      AS s ON s.acct = a.acct
LEFT JOIN received  AS r ON r.acct = a.acct;

CREATE UNIQUE INDEX ix_dim_account ON dim_account (acct);


-- [CELL] 9. AML rules and risk score
-- TRANSFER and CASH_OUT only (see 1c). The rules don't use is_fraud; that's only used
-- in section 10 to check them.
--
--  R1 ACCOUNT_DRAIN      sender's whole balance taken out, balance ends at 0
--  R2 TRANSFER_CASHOUT   a TRANSFER and a CASH_OUT for the same amount in the same hour
--                        (layering)
--  R3 GHOST_DESTINATION  customer receiver shows 0 balance before and after even
--                        though money arrived (pass-through)
--  R4 HIGH_VALUE         amount >= 200,000 (like a CTR threshold)
--  R5 LEDGER_MISMATCH    sender ledger does not reconcile by more than 1.00
--  R6 MULE_FAN_IN        receiver collected money from 10+ distinct senders
DROP TABLE IF EXISTS txn_alerts;

CREATE TABLE txn_alerts AS
WITH scoped AS (
    SELECT *
    FROM clean_transactions
    WHERE txn_type IN ('TRANSFER', 'CASH_OUT')
),
-- R2: (hour, amount) pairs that show up as both a TRANSFER and a CASH_OUT.
-- Distinct keys first so the join doesn't multiply rows.
transfer_keys AS (
    SELECT DISTINCT step, amount FROM scoped WHERE txn_type = 'TRANSFER'
),
cashout_keys AS (
    SELECT DISTINCT step, amount FROM scoped WHERE txn_type = 'CASH_OUT'
),
layering_pairs AS (
    SELECT t.step, t.amount
    FROM transfer_keys AS t
    INNER JOIN cashout_keys AS c
            ON c.step   = t.step
           AND c.amount = t.amount
),
flags AS (
    SELECT s.*,
           CASE WHEN s.orig_bal_before > 0
                 AND s.amount >= s.orig_bal_before
                 AND s.orig_bal_after = 0                       THEN 1 ELSE 0 END AS r1_account_drain,
           CASE WHEN lp.step IS NOT NULL                        THEN 1 ELSE 0 END AS r2_transfer_cashout,
           CASE WHEN s.dest_acct_kind = 'C'
                 AND s.dest_bal_before = 0
                 AND s.dest_bal_after  = 0                      THEN 1 ELSE 0 END AS r3_ghost_destination,
           CASE WHEN s.amount >= 200000                         THEN 1 ELSE 0 END AS r4_high_value,
           CASE WHEN ABS(s.orig_balance_gap) > 1
                 AND s.orig_bal_before >= s.amount              THEN 1 ELSE 0 END AS r5_ledger_mismatch,
           CASE WHEN COALESCE(d.distinct_senders, 0) >= 10      THEN 1 ELSE 0 END AS r6_mule_fan_in,
           COALESCE(d.distinct_senders, 0)                                         AS dest_distinct_senders
    FROM scoped AS s
    LEFT JOIN layering_pairs AS lp                -- keep every txn
           ON lp.step = s.step AND lp.amount = s.amount
    LEFT JOIN dim_account AS d                    -- bring in receiver behaviour
           ON d.acct = s.dest_acct
)
SELECT f.*,
       -- Score 0-100. Weights are rough, tune them with mart_rule_performance.
       (40 * r1_account_drain
      + 25 * r2_transfer_cashout
      + 15 * r3_ghost_destination
      + 10 * r4_high_value
      +  5 * r5_ledger_mismatch
      +  5 * r6_mule_fan_in)                       AS risk_score,
       CASE WHEN (40 * r1_account_drain + 25 * r2_transfer_cashout + 15 * r3_ghost_destination
                + 10 * r4_high_value + 5 * r5_ledger_mismatch + 5 * r6_mule_fan_in) >= 50
            THEN 1 ELSE 0 END                       AS model_alert
FROM flags AS f;

CREATE INDEX ix_alerts_model ON txn_alerts (model_alert, risk_score);


-- [CELL] 10. Rule performance
-- Precision: share of alerts that were fraud. Recall: share of fraud that got an alert.
-- The legacy flag is measured on the same rows, so the numbers are comparable.
DROP TABLE IF EXISTS mart_rule_performance;

CREATE TABLE mart_rule_performance AS
WITH totals AS (
    SELECT SUM(is_fraud) AS fraud_total,
           SUM(CASE WHEN is_fraud = 1 THEN amount ELSE 0 END) AS fraud_amount_total
    FROM txn_alerts
),
long_form AS (                       -- one row per rule per txn
    SELECT 'LEGACY: isFlaggedFraud' AS rule_name, 'Legacy' AS rule_group, legacy_flag AS hit, is_fraud, amount FROM txn_alerts
    UNION ALL SELECT 'R1 Account drain',        'Rule',  r1_account_drain,     is_fraud, amount FROM txn_alerts
    UNION ALL SELECT 'R2 Transfer->Cash-out',   'Rule',  r2_transfer_cashout,  is_fraud, amount FROM txn_alerts
    UNION ALL SELECT 'R3 Ghost destination',    'Rule',  r3_ghost_destination, is_fraud, amount FROM txn_alerts
    UNION ALL SELECT 'R4 High value >=200k',    'Rule',  r4_high_value,        is_fraud, amount FROM txn_alerts
    UNION ALL SELECT 'R5 Ledger mismatch',      'Rule',  r5_ledger_mismatch,   is_fraud, amount FROM txn_alerts
    UNION ALL SELECT 'R6 Mule fan-in',          'Rule',  r6_mule_fan_in,       is_fraud, amount FROM txn_alerts
    UNION ALL SELECT 'MODEL: risk score >= 50', 'Model', model_alert,          is_fraud, amount FROM txn_alerts
)
SELECT l.rule_name,
       l.rule_group,
       SUM(l.hit)                                         AS alerts,
       SUM(l.hit * l.is_fraud)                            AS true_positives,
       SUM(l.hit * (1 - l.is_fraud))                      AS false_positives,
       t.fraud_total - SUM(l.hit * l.is_fraud)            AS missed_fraud,
       ROUND(1.0 * SUM(l.hit * l.is_fraud) / NULLIF(SUM(l.hit), 0), 4) AS precision_rate,
       ROUND(1.0 * SUM(l.hit * l.is_fraud) / NULLIF(t.fraud_total, 0), 4) AS recall_rate,
       ROUND(SUM(l.hit * l.is_fraud * l.amount), 2)       AS fraud_amount_caught,
       ROUND(1.0 * SUM(l.hit * l.is_fraud * l.amount) / NULLIF(t.fraud_amount_total, 0), 4) AS amount_recall_rate
FROM long_form AS l
CROSS JOIN totals AS t                     -- totals has one row
GROUP BY l.rule_name, l.rule_group, t.fraud_total, t.fraud_amount_total
ORDER BY CASE l.rule_group WHEN 'Legacy' THEN 0 WHEN 'Model' THEN 1 ELSE 2 END, recall_rate DESC;

SELECT * FROM mart_rule_performance;


-- [CELL] 11. Tables for Tableau
-- Small pre-aggregated tables so Tableau doesn't have to load 6.3M rows.

-- 11a. Fraud per day, hour and type
DROP TABLE IF EXISTS mart_hourly;

CREATE TABLE mart_hourly AS
SELECT sim_day,
       hour_of_day,
       txn_type,
       SUM(is_fraud)                     AS fraud_count,
       ROUND(SUM(is_fraud * amount), 2)  AS fraud_amount
FROM clean_transactions
GROUP BY sim_day, hour_of_day, txn_type;

-- 11b. Top 100 alerts by risk score, with the reasons as text
DROP TABLE IF EXISTS mart_alert_queue;

CREATE TABLE mart_alert_queue AS
SELECT ROW_NUMBER() OVER (ORDER BY risk_score DESC, amount DESC) AS queue_rank,  -- 1 = investigate first
       txn_id, txn_type, amount, risk_score,
       TRIM(
           CASE WHEN r1_account_drain     = 1 THEN 'Drain | '        ELSE '' END ||
           CASE WHEN r2_transfer_cashout  = 1 THEN 'Layering | ' ELSE '' END ||
           CASE WHEN r3_ghost_destination = 1 THEN 'Ghost | '    ELSE '' END ||
           CASE WHEN r4_high_value        = 1 THEN 'High value | '           ELSE '' END ||
           CASE WHEN r5_ledger_mismatch   = 1 THEN 'Ledger | '      ELSE '' END ||
           CASE WHEN r6_mule_fan_in       = 1 THEN 'Fan-in | '          ELSE '' END,
       ' |')                                                    AS alert_reasons,
       CASE WHEN is_fraud = 1 THEN 'Confirmed fraud' ELSE 'Not fraud' END AS outcome
FROM txn_alerts
WHERE model_alert = 1
ORDER BY queue_rank
LIMIT 100;

-- 11c. One-row KPI table
DROP TABLE IF EXISTS mart_kpi;

CREATE TABLE mart_kpi AS
SELECT
    (SELECT COUNT(*) FROM raw_transactions)                    AS raw_rows,
    (SELECT COUNT(*) FROM dq_removed_rows)                     AS rows_removed,
    (SELECT COUNT(*) FROM dq_duplicate_review
      WHERE decision LIKE 'EXACT%')                            AS exact_duplicates_removed,
    (SELECT COUNT(*) FROM dq_duplicate_review
      WHERE decision LIKE 'NEAR%')                             AS near_duplicates_flagged,
    (SELECT COUNT(*) FROM clean_transactions)                  AS clean_rows,
    (SELECT ROUND(SUM(amount), 2) FROM clean_transactions)     AS total_amount,
    (SELECT SUM(is_fraud) FROM clean_transactions)             AS fraud_count,
    (SELECT ROUND(SUM(is_fraud * amount), 2) FROM clean_transactions) AS fraud_amount,
    (SELECT recall_rate FROM mart_rule_performance WHERE rule_group = 'Legacy') AS legacy_recall,
    (SELECT recall_rate FROM mart_rule_performance WHERE rule_group = 'Model')  AS model_recall,
    (SELECT precision_rate FROM mart_rule_performance WHERE rule_group = 'Model') AS model_precision,
    (SELECT alerts FROM mart_rule_performance WHERE rule_group = 'Model')       AS model_alerts;

SELECT * FROM mart_kpi;

-- 11d. Data quality counts for the dashboard
DROP TABLE IF EXISTS mart_data_quality;

CREATE TABLE mart_data_quality AS
SELECT reason AS dq_issue, 'Removed' AS action, COUNT(*) AS row_count
FROM dq_removed_rows GROUP BY reason
UNION ALL
SELECT 'NEAR_DUPLICATE', 'Kept & flagged', COUNT(*)
FROM dq_duplicate_review WHERE decision LIKE 'NEAR%'
UNION ALL
SELECT 'ORIG_LEDGER_GAP > 1', 'Kept as feature', COUNT(*)
FROM clean_transactions WHERE ABS(orig_balance_gap) > 1
UNION ALL
SELECT 'DEST_LEDGER_GAP > 1', 'Kept as feature', COUNT(*)
FROM clean_transactions WHERE ABS(dest_balance_gap) > 1;

SELECT * FROM mart_data_quality;
