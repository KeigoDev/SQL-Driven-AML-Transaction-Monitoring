"""
Step 3: writes AML_Fraud_Monitoring.twb from the CSVs in 03_tableau/data/.

The workbook stores the full path to the data folder, so run this on the machine that
will open it (run_all.py does that already).

    python 03_tableau/build_twb.py

It also writes AML_Fraud_Monitoring.twbx, a packaged copy with the data as built-in extracts
(.hyper), which is what Tableau Public needs.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "AML_Fraud_Monitoring.twb"
OUT_PACKAGED = HERE / "AML_Fraud_Monitoring.twbx"  # data built in, for Tableau Public
EXTRACT_DIR = "Data/Extracts"  # where the .hyper files sit inside the .twbx

# colours: navy for structure, red for fraud, violet for the new rules, grey for the old flag
NAVY, RED, VIOLET, GREY = "#14213D", "#D62839", "#5B4BDB", "#A0A7B4"
CANVAS, CARD, INK, MUTED, BORDER = "#F3F4F8", "#FFFFFF", "#14213D", "#6B7280", "#DDE1EA"
BAND, BAND_BORDER = "#ECEAFB", "#C9C3F5"  # lavender filter strip under the header
FONT = "Tableau Book"
FONT_BOLD = "Tableau Semibold"

# Tableau number formats. "mart.column" overrides "column".
FORMATS = {
    # "$" is quoted so Tableau doesn't swap in the local currency symbol
    "mart_kpi.fraud_amount": '*"$"#,##0,,"M"',
    "mart_hourly.fraud_amount": '*"$"#,##0,,"M"',
    "amount": '*"$"#,##0',
    "fraud_count": "n#,##0", "row_count": "n#,##0",
    "legacy_recall": "p0.0%", "model_recall": "p0.0%", "model_precision": "p0.0%",
    "precision_rate": "p0.0%", "recall_rate": "p0%",
}
# names shown on axes and headers
CAPTIONS = {
    "fraud_amount": "Fraud value", "fraud_count": "Fraud cases", "sim_day": "Day", "hour_of_day": "Hour of day",
    "txn_type": "Type", "recall_rate": "Recall", "rule_name": "Rule", "rule_group": "Rule group",
    "dq_issue": "Check", "action": "Action", "row_count": "Rows", "queue_rank": "#", "risk_score": "Score",
    "alert_reasons": "Why it was flagged", "outcome": "Outcome", "amount": "Amount",
}
# integer columns Tableau should treat as categories
DIMENSION_INTS = {"queue_rank", "sim_day", "hour_of_day", "txn_id", "risk_score"}
# fixed category colours, so a colour means the same thing on every sheet
COLOR_MAPS = {
    "mart_rule_performance": ("rule_group", {"Legacy": GREY, "Rule": VIOLET, "Model": RED}),
    "mart_alert_queue": ("outcome", {"Confirmed fraud": RED, "Not fraud": GREY}),
}
# dashboard filters as (mart, field, kind). Each one applies to every sheet built on that mart.
# kind "range" = slider, "list" = multi-select dropdown. The alert queue gets none: its top 100
# rows are all TRANSFER / score 95 / confirmed fraud, so a filter there would change nothing.
FILTERS = [
    ("mart_hourly", "sim_day", "range"),
    ("mart_hourly", "hour_of_day", "range"),
    ("mart_hourly", "txn_type", "list"),
    ("mart_rule_performance", "rule_group", "list"),
]
# titles on the filter cards: say what each one changes
FILTER_TITLES = {
    "sim_day": "Simulation day", "hour_of_day": "Hour of day",
    "txn_type": "Transaction type", "rule_group": "Rule group (rule chart)",
}


def ds_name(mart: str) -> str:
    return f"federated.{mart.replace('_', '')}"


def fmt(mart: str, col: str) -> str | None:
    return FORMATS.get(f"{mart}.{col}", FORMATS.get(col))


def column_meta(mart: str) -> list[tuple[str, str, str, str]]:
    """(name, datatype, role, type) for every CSV column, inferred from the exported file."""
    df = pd.read_csv(DATA / f"{mart}.csv", nrows=500)
    cols = []
    for name, dtype in df.dtypes.items():
        if pd.api.types.is_integer_dtype(dtype):
            dt = "integer"
        elif pd.api.types.is_float_dtype(dtype):
            dt = "real"
        else:
            dt = "string"
        is_dim = dt == "string" or name in DIMENSION_INTS
        cols.append((name, dt, "dimension" if is_dim else "measure",
                     ("ordinal" if dt == "integer" else "nominal") if is_dim else "quantitative"))
    return cols


def write_hyper(mart: str, path: Path) -> None:
    """Copy one mart CSV into a Tableau extract (.hyper) with the table Extract.Extract."""
    from tableauhyperapi import (Connection, CreateMode, HyperProcess, Inserter, SqlType, TableDefinition,
                                 TableName, Telemetry)
    sql_type = {"integer": SqlType.big_int(), "real": SqlType.double(), "string": SqlType.text()}
    cols = column_meta(mart)
    df = pd.read_csv(DATA / f"{mart}.csv")
    table = TableDefinition(TableName("Extract", "Extract"),
                            [TableDefinition.Column(n, sql_type[dt]) for n, dt, _, _ in cols])
    rows = df[[n for n, *_ in cols]].astype(object).where(df.notna(), None).values.tolist()
    # log_dir keeps hyperd.log out of the project folder
    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={"log_dir": str(path.parent)}) as hyper, \
            Connection(hyper.endpoint, str(path), CreateMode.CREATE_AND_REPLACE) as con:
        con.catalog.create_schema("Extract")
        con.catalog.create_table(table)
        with Inserter(con, table) as ins:
            ins.add_rows(rows)
            ins.execute()


def datasource_xml(mart: str, caption: str, extract: bool = False) -> str:
    """extract=False: live link to the CSV (absolute path). extract=True: the packaged .hyper copy,
    which Tableau Public requires."""
    conn = f"{'hyper' if extract else 'textscan'}.{mart.replace('_', '')}"
    cols = column_meta(mart)
    remote_type = {"integer": 20, "real": 5, "string": 129}
    agg = {"integer": "Sum", "real": "Sum", "string": "Count"}

    csv_cols = "\n".join(
        f"            <column datatype='{dt}' name={quoteattr(n)} ordinal='{i}' />"
        for i, (n, dt, _, _) in enumerate(cols))
    parent = "Extract" if extract else f"{mart}.csv"
    meta = "\n".join(f"""          <metadata-record class='column'>
            <remote-name>{escape(n)}</remote-name><remote-type>{remote_type[dt]}</remote-type>
            <local-name>[{escape(n)}]</local-name><parent-name>[{parent}]</parent-name>
            <remote-alias>{escape(n)}</remote-alias><ordinal>{i}</ordinal>
            <local-type>{dt}</local-type><aggregation>{agg[dt]}</aggregation><contains-null>true</contains-null>
          </metadata-record>""" for i, (n, dt, _, _) in enumerate(cols))
    df = pd.read_csv(DATA / f"{mart}.csv")
    col_defs = []
    for n, dt, role, typ in cols:
        head = (f"      <column caption={quoteattr(CAPTIONS.get(n, n.replace('_', ' ').title()))} datatype='{dt}' "
                f"name='[{n}]' role='{role}' type='{typ}'"
                + (f" default-format={quoteattr(fmt(mart, n))}" if fmt(mart, n) else ""))
        # show CASH_IN as "CASH IN": a display alias only, the data keeps the SQL value
        raw = sorted(v for v in df[n].dropna().unique() if "_" in str(v)) if dt == "string" else []
        aliases = "".join(f"<alias key={quoteattr(chr(34) + v + chr(34))} value={quoteattr(v.replace('_', ' '))} />" for v in raw)
        col_defs.append(f"{head}><aliases>{aliases}</aliases></column>" if aliases else f"{head} />")
    col_defs = "\n".join(col_defs)

    if extract:
        source = (f"<connection authentication='auth-none' author-locale='en_US' class='hyper' "
                  f"dbname='{EXTRACT_DIR}/{mart}.hyper' default-settings='yes' port='' sslmode='' "
                  f"username='tableau_internal_user' />")
        relation = f"<relation connection='{conn}' name='Extract' table='[Extract].[Extract]' type='table' />"
    else:
        source = (f"<connection class='textscan' directory={quoteattr(str(DATA))} filename='{mart}.csv' "
                  f"password='' server='' />")
        relation = f"""<relation connection='{conn}' name='{mart}.csv' table='[{mart}#csv]' type='table'>
          <columns character-set='UTF-8' header='yes' locale='en_US' separator=','>
{csv_cols}
          </columns>
        </relation>"""
    return f"""    <datasource caption={quoteattr(caption)} inline='true' name='{ds_name(mart)}' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='{mart}' name='{conn}'>
            {source}
          </named-connection>
        </named-connections>
        {relation}
        <metadata-records>
{meta}
        </metadata-records>
      </connection>
      <aliases enabled='yes' />
{col_defs}
{color_map(mart)}
    </datasource>"""


def color_map(mart: str) -> str:
    if mart not in COLOR_MAPS:
        return ""
    field, mapping = COLOR_MAPS[mart]
    buckets = "\n".join(
        f"            <map to='{c}'><bucket>&quot;{escape(k)}&quot;</bucket></map>" for k, c in mapping.items())
    return f"""      <column-instance column='[{field}]' derivation='None' name='[none:{field}:nk]' pivot='key' type='nominal' />
      <style>
        <style-rule element='mark'>
          <encoding attr='color' field='[none:{field}:nk]' type='palette'>
{buckets}
          </encoding>
        </style-rule>
      </style>"""


def inst(field: str, kind: str) -> tuple[str, str]:
    """Return (column-instance name, xml) for a field. kind: nk / ok / sum."""
    if kind == "sum":
        name = f"[sum:{field}:qk]"
        xml = f"<column-instance column='[{field}]' derivation='Sum' name='{name}' pivot='key' type='quantitative' />"
    else:
        typ = "nominal" if kind == "nk" else "ordinal"
        name = f"[none:{field}:{kind}]"
        xml = f"<column-instance column='[{field}]' derivation='None' name='{name}' pivot='key' type='{typ}' />"
    return name, xml


def filter_field(field: str, kind: str) -> str:
    return f"[none:{field}:{'qk' if kind == 'range' else 'nk'}]"


def shared_views_xml() -> str:
    """Filters stored per data source, so they apply to all sheets using it (same as Tableau's
    'Apply to all using this data source')."""
    views = []
    for mart in dict.fromkeys(m for m, _, _ in FILTERS):
        ds = ds_name(mart)
        meta = {n: (dt, role, typ) for n, dt, role, typ in column_meta(mart)}
        df = pd.read_csv(DATA / f"{mart}.csv")
        deps, filters = [], []
        for _, f, kind in (x for x in FILTERS if x[0] == mart):
            dt, role, typ = meta[f]
            col, param = filter_field(f, kind), f"[{ds}].{filter_field(f, kind)}"
            deps.append(f"<column caption={quoteattr(CAPTIONS.get(f, f))} datatype='{dt}' name='[{f}]' "
                        f"role='{role}' type='{typ}' />")
            deps.append(f"<column-instance column='[{f}]' derivation='None' name='{col}' pivot='key' "
                        f"type='{'quantitative' if kind == 'range' else 'nominal'}' />")
            if kind == "range":
                filters.append(f"<filter class='quantitative' column='{param}' included-values='in-range'>"
                               f"<min>{df[f].min()}</min><max>{df[f].max()}</max></filter>")
            else:
                filters.append(f"<filter class='categorical' column='{param}'><groupfilter function='level-members' "
                               f"level='{col}' user:ui-enumeration='all' user:ui-marker='enumerate' />"
                               f"<target field='{param}' /></filter>")
        views.append(f"""    <shared-view name='{ds}'>
      <datasources><datasource caption='{mart}' name='{ds}' /></datasources>
      <datasource-dependencies datasource='{ds}'>
        {chr(10).join('        ' + d for d in deps).lstrip()}
      </datasource-dependencies>
      {chr(10).join('      ' + x for x in filters).lstrip()}
    </shared-view>""")
    return "  <shared-views>\n" + "\n".join(views) + "\n  </shared-views>"


def filter_style(sheet: str) -> str:
    mine = [(m, f, k) for m, f, k in FILTERS if FILTER_SHEET[m] == sheet]
    if not mine:
        return ""
    titles = "".join(
        f"<format attr='title' field='[{ds_name(m)}].{filter_field(f, k)}' value={quoteattr(FILTER_TITLES[f])}>"
        f"<formatted-text><run bold='true' fontcolor='{VIOLET}' fontname='{FONT_BOLD}' fontsize='10'>"
        f"{escape(FILTER_TITLES[f].upper())}</run></formatted-text></format>" for m, f, k in mine)
    return (f"<style-rule element='quick-filter'>{titles}<format attr='background-color' value='{CARD}' />"
            f"<format attr='font-size' value='10' /><format attr='color' value='{INK}' /></style-rule>"
            f"<style-rule element='quick-filter-title'><format attr='font-family' value='{FONT_BOLD}' />"
            f"<format attr='font-size' value='10' /><format attr='color' value='{VIOLET}' /></style-rule>")


def worksheet(name: str, title: str, mart: str, *, mark: str, rows=(), cols=(), color=None,
              color_value=None, text=None, kpi_color=None, subtitle="", labels=False, sort=None,
              widths=None, hide_axis=None) -> str:
    """rows/cols are lists of (field, kind). sort = (dimension, measure) for a descending sort."""
    ds = ds_name(mart)
    ref = lambda f, k: f"[{ds}].{inst(f, k)[0]}"
    meta = {n: (dt, role, typ) for n, dt, role, typ in column_meta(mart)}
    fields = [*rows, *cols, *([color] if color else []), *([text] if text else [])]
    deps = "\n".join(
        f"            <column datatype='{meta[f][0]}' name='[{f}]' role='{meta[f][1]}' type='{meta[f][2]}'"
        + (f" default-format={quoteattr(fmt(mart, f))}" if fmt(mart, f) else "") + " />"
        for f in dict.fromkeys(f for f, _ in fields))
    deps += "\n" + "\n".join(f"            {x}" for x in dict.fromkeys(inst(f, k)[1] for f, k in fields))
    sort_xml = (f"<sort class='computed' column='{ref(sort[0], 'nk')}' direction='DESC' using='{ref(sort[1], 'sum')}' />"
                if sort else "")

    enc = []
    if color:
        enc.append(f"<color column='{ref(*color)}' />")
    if text:
        enc.append(f"<text column='{ref(*text)}' />")
    encodings = f"<encodings>{''.join(enc)}</encodings>" if enc else ""
    mark_fmt = f"<format attr='mark-color' value='{color_value}' />" if color_value else ""
    mark_fmt += f"<format attr='mark-labels-show' value='{'true' if labels else 'false'}' />"
    label = ""
    if kpi_color:  # KPI tile: caption, big number, note
        label = (f"<customized-label><formatted-text>"
                 f"<run fontcolor='{MUTED}' fontname='{FONT_BOLD}' fontsize='11'>{escape(title.upper())}&#10;</run>"
                 f"<run bold='true' fontcolor='{kpi_color}' fontname='{FONT_BOLD}' fontsize='26'>&lt;{ref(*text)}&gt;</run>"
                 f"<run fontcolor='{MUTED}' fontname='{FONT}' fontsize='10'>&#10;{escape(subtitle)}</run>"
                 f"</formatted-text></customized-label>")
        mark_fmt = "<format attr='mark-labels-show' value='true' />"
    is_table = mark == "Text" and not kpi_color
    width_fmt = "".join(f"<format attr='width' field='{ref(f, k)}' value='{w}' />" for (f, k), w in (widths or {}).items())

    sub = f"<run fontcolor='{MUTED}' fontname='{FONT}' fontsize='10'>&#10;{escape(subtitle)}</run>" if subtitle else ""
    return f"""    <worksheet name={quoteattr(name)}>
      <layout-options>
        <title><formatted-text><run fontcolor='{INK}' fontname='{FONT_BOLD}' fontsize='14'>{escape(title)}</run>{sub}</formatted-text></title>
      </layout-options>
      <table>
        <view>
          <datasources><datasource caption='{mart}' name='{ds}' /></datasources>
          <datasource-dependencies datasource='{ds}'>
{deps}
          </datasource-dependencies>
          {sort_xml}
          <aggregation value='true' />
        </view>
        <style>
          <style-rule element='worksheet'>
            <format attr='font-family' value='{FONT}' />
            <format attr='display-field-labels' scope='rows' value='{'true' if is_table else 'false'}' />
            <format attr='display-field-labels' scope='cols' value='false' />
          </style-rule>
          <style-rule element='axis'>
            <format attr='font-size' value='10' /><format attr='color' value='{MUTED}' />
            <format attr='tick-color' value='#00000000' />{
            f"<format attr='display' class='0' field='{ref(hide_axis, 'sum')}' scope='cols' value='false' />"
            if hide_axis else ""}
          </style-rule>
          <style-rule element='header'>
            <format attr='font-size' value='10' /><format attr='color' value='{INK}' />{width_fmt}
          </style-rule>
          <style-rule element='label'><format attr='font-size' value='10' /><format attr='color' value='{INK}' /></style-rule>
          <style-rule element='cell'><format attr='text-align' value='{'center' if kpi_color else 'left'}' />{
            "<format attr='width' value='96' />" if is_table else ""}</style-rule>
          <style-rule element='gridline'><format attr='line-visibility' value='off' /></style-rule>{filter_style(name)}
          <style-rule element='zeroline'><format attr='line-visibility' value='off' /></style-rule>
          <style-rule element='table'><format attr='show-null-value-warning' value='false' /></style-rule>
        </style>
        <panes>
          <pane selection-relaxation-option='selection-relaxation-allow'>
            <view><breakdown value='auto' /></view>
            <mark class='{mark}' />
            {encodings}
            {label}
            <style><style-rule element='mark'>{mark_fmt}</style-rule></style>
          </pane>
        </panes>
        <rows>{" / ".join(ref(f, k) for f, k in rows)}</rows>
        <cols>{" / ".join(ref(f, k) for f, k in cols)}</cols>
      </table>
    </worksheet>"""


KPI = dict(mart="mart_kpi", mark="Text")
KPI_FILTERED = dict(KPI, mart="mart_hourly")  # same totals as mart_kpi, but follows the dashboard filters
SHEETS = [
    # KPI tiles. mart_kpi has one row, so SUM is just the value.
    dict(name="KPI Fraud Value", title="Fraud value", text=("fraud_amount", "sum"), kpi_color=RED,
         subtitle="confirmed fraud, current filters", **KPI_FILTERED),
    dict(name="KPI Fraud Cases", title="Fraud cases", text=("fraud_count", "sum"), kpi_color=RED,
         subtitle="labelled fraud, current filters", **KPI_FILTERED),
    dict(name="KPI Legacy Recall", title="Old flag recall", text=("legacy_recall", "sum"), kpi_color=GREY,
         subtitle="share of fraud the old flag caught", **KPI),
    dict(name="KPI Model Recall", title="Risk score recall", text=("model_recall", "sum"), kpi_color=VIOLET,
         subtitle="share of fraud the new rules caught", **KPI),
    dict(name="KPI Model Precision", title="Alert precision", text=("model_precision", "sum"), kpi_color=VIOLET,
         subtitle="share of alerts that were fraud", **KPI),
    # charts
    dict(name="Fraud Trend", title="Fraud value by day", mart="mart_hourly", mark="Area",
         cols=[("sim_day", "ok")], rows=[("fraud_amount", "sum")], color_value=RED,
         subtitle="30-day simulation"),
    dict(name="Rule Performance", title="Share of fraud caught, by rule", mart="mart_rule_performance", mark="Bar",
         rows=[("rule_name", "nk")], cols=[("recall_rate", "sum")], color=("rule_group", "nk"), labels=True,
         sort=("rule_name", "recall_rate"), hide_axis="recall_rate", subtitle="grey = old flag, violet = single rule, red = combined score"),
    dict(name="Fraud by Hour", title="Fraud cases by hour of day", mart="mart_hourly", mark="Bar",
         cols=[("hour_of_day", "ok")], rows=[("fraud_count", "sum")], color_value=VIOLET,
         subtitle="fraud keeps going overnight while normal traffic drops"),
    dict(name="Fraud by Type", title="Fraud value by type", mart="mart_hourly", mark="Bar",
         rows=[("txn_type", "nk")], cols=[("fraud_amount", "sum")], color_value=RED, labels=True,
         sort=("txn_type", "fraud_amount"), hide_axis="fraud_amount", subtitle="only TRANSFER and CASH OUT have fraud"),
    dict(name="Data Quality", title="What cleaning did", mart="mart_data_quality", mark="Text",
         rows=[("dq_issue", "nk"), ("action", "nk")], text=("row_count", "sum"),
         widths={("dq_issue", "nk"): 160, ("action", "nk"): 116},
         subtitle="every removed row is logged in dq_removed_rows"),
    dict(name="Alert Queue", title="Top 100 alerts by risk score", mart="mart_alert_queue", mark="Text",
         rows=[("queue_rank", "ok"), ("txn_type", "nk"), ("alert_reasons", "nk"), ("outcome", "nk")],
         text=("amount", "sum"), color=("outcome", "nk"),
         widths={("queue_rank", "ok"): 34, ("txn_type", "nk"): 86, ("alert_reasons", "nk"): 290,
                 ("outcome", "nk"): 112},
         subtitle="what an investigator would work through first"),
]

# layout drawn in pixels on a 1400x900 grid, converted to Tableau's 100000-unit grid
W, H = 1400, 900
SIZE_W, SIZE_H = 1600, 1000  # actual dashboard size; the layout above scales to it
_kw = (W - 32 - 4 * 12) / 5
HEADER_H, BAND_H, FILTER_Y, FILTER_H, LABEL_W = 72, 84, 80, 68, 132
LAYOUT = {  # name: (x, y, w, h)
    **{s["name"]: (16 + i * (_kw + 12), 164, _kw, 88) for i, s in enumerate(SHEETS[:5])},
    "Fraud Trend": (16, 264, 752, 252), "Rule Performance": (780, 264, 604, 252),
    "Fraud by Hour": (16, 528, 380, 356), "Fraud by Type": (408, 528, 360, 168),
    "Data Quality": (408, 708, 360, 176), "Alert Queue": (780, 528, 604, 356),
}
# a filter zone is attached to one sheet of its mart; the shared view carries it to the rest
FILTER_SHEET = {"mart_hourly": "Fraud Trend", "mart_rule_performance": "Rule Performance"}
FIT_WIDTH = {"Alert Queue", "Data Quality"}  # tables scroll down instead of squashing


def grid(x, y, w, h):
    sx, sy = 100000 / W, 100000 / H
    return f"x='{round(x * sx)}' y='{round(y * sy)}' w='{round(w * sx)}' h='{round(h * sy)}'"


def dashboard_xml() -> str:
    card = lambda pad: (f"<zone-style><format attr='background-color' value='{CARD}' />"
                        f"<format attr='border-style' value='solid' /><format attr='border-width' value='1' />"
                        f"<format attr='border-color' value='{BORDER}' /><format attr='margin' value='0' />"
                        f"<format attr='padding' value='{pad}' /></zone-style>")
    zones = [f"          <zone {grid(*box)} id='{i}' name={quoteattr(name)} "
             f"show-title='{'false' if name.startswith('KPI') else 'true'}'>{card(4 if name.startswith('KPI') else 12)}</zone>"
             for i, (name, box) in enumerate(LAYOUT.items(), start=3)]
    next_id = 3 + len(LAYOUT)
    # Tiled zones can't overlap, so the band is a horizontal container holding the label and filter cards.
    band = [f"            <zone {grid(16, FILTER_Y, LABEL_W, FILTER_H)} id='{next_id + 1}' type-v2='text'><formatted-text>"
                 f"<run bold='true' fontcolor='{VIOLET}' fontname='{FONT_BOLD}' fontsize='14'>FILTERS&#10;</run>"
                 f"<run fontcolor='{MUTED}' fontname='{FONT}' fontsize='9'>Change the charts and the fraud value / cases tiles</run>"
                 f"</formatted-text><zone-style><format attr='border-style' value='none' />"
                 f"<format attr='padding' value='6' /><format attr='margin' value='6' /></zone-style></zone>"]
    x0 = 16 + LABEL_W + 12
    fw = (W - 16 - x0 - (len(FILTERS) - 1) * 12) / len(FILTERS)
    filter_card = (f"<zone-style><format attr='background-color' value='{CARD}' />"
                   f"<format attr='border-style' value='solid' /><format attr='border-width' value='1' />"
                   f"<format attr='border-color' value='{BAND_BORDER}' /><format attr='margin' value='6' />"
                   f"<format attr='padding' value='6' /></zone-style>")
    for i, (mart, f, kind) in enumerate(FILTERS):
        mode = "" if kind == "range" else " mode='checkdropdown'"
        band.append(f"            <zone {grid(x0 + i * (fw + 12), FILTER_Y, fw, FILTER_H)} id='{next_id + 2 + i}'{mode} "
                     f"name={quoteattr(FILTER_SHEET[mart])} param='[{ds_name(mart)}].{filter_field(f, kind)}' "
                     f"show-domain='false' show-null-ctrls='false' type-v2='filter'>{filter_card}</zone>")
    zones.insert(0, f"          <zone {grid(0, HEADER_H, W, BAND_H)} id='{next_id}' param='horz' type-v2='layout-flow'>\n"
                    + "\n".join(band) + f"\n            <zone-style><format attr='background-color' value='{BAND}' />"
                    f"<format attr='border-style' value='none' /><format attr='padding' value='8' /></zone-style>\n          </zone>")
    header = (f"<zone {grid(0, 0, W, HEADER_H)} id='2' type-v2='text'><formatted-text>"
              f"<run fontcolor='#FFFFFF' fontname='{FONT_BOLD}' fontsize='22'>AML Transaction Monitoring&#10;</run>"
              f"<run fontcolor='#C9CEDA' fontname='{FONT}' fontsize='11'>PaySim mobile-money data, 6.3M transactions. "
              f"Can a few explainable SQL rules beat a flag that misses almost all fraud?</run></formatted-text>"
              f"<zone-style><format attr='background-color' value='{NAVY}' /><format attr='border-style' value='none' />"
              f"<format attr='padding' value='10' /></zone-style></zone>")
    return f"""    <dashboard name='AML Monitoring Dashboard'>
      <style>
        <style-rule element='table'><format attr='background-color' value='{CANVAS}' /></style-rule>
      </style>
      <size maxheight='{SIZE_H}' maxwidth='{SIZE_W}' minheight='{SIZE_H}' minwidth='{SIZE_W}' />
      <zones>
        <zone h='100000' id='1' type-v2='layout-basic' w='100000' x='0' y='0'>
          {header}
{chr(10).join(zones)}
        </zone>
      </zones>
    </dashboard>"""


def main() -> None:
    marts = {"mart_kpi": "KPI", "mart_hourly": "Hourly Activity", "mart_rule_performance": "Rule Performance",
             "mart_alert_queue": "Alert Queue", "mart_data_quality": "Data Quality"}
    missing = [m for m in marts if not (DATA / f"{m}.csv").exists()]
    if missing:
        raise SystemExit(f"Missing {missing} in {DATA}. Run 02_sql/run_sql.py first.")

    # Tableau needs a cards block in each worksheet window and viewpoints in the dashboard window
    cards = ("<cards><edge name='left'><strip size='160'><card type='pages' /><card type='filters' />"
             "<card type='marks' /></strip></edge><edge name='top'><strip size='2147483647'><card type='columns' />"
             "</strip><strip size='2147483647'><card type='rows' /></strip><strip size='2147483647'>"
             "<card type='title' /></strip></edge></cards>")
    windows = "\n".join(f"    <window class='worksheet' name={quoteattr(s['name'])}>{cards}</window>"
                        for s in SHEETS)
    viewpoints = "".join(
        f"<viewpoint name={quoteattr(s['name'])}><zoom type='{'fit-width' if s['name'] in FIT_WIDTH else 'entire-view'}' />"
        f"</viewpoint>" for s in SHEETS)
    OUT.write_text(workbook_xml(marts, windows, viewpoints, extract=False), encoding="utf-8")
    print(f"[tableau] wrote {OUT}\n[tableau] data folder: {DATA}")

    # packaged copy: same workbook, but each data source reads a .hyper extract stored inside the zip
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(OUT_PACKAGED, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(OUT.name, workbook_xml(marts, windows, viewpoints, extract=True))
        for m in marts:
            write_hyper(m, Path(tmp) / f"{m}.hyper")
            z.write(Path(tmp) / f"{m}.hyper", f"{EXTRACT_DIR}/{m}.hyper")
    print(f"[tableau] wrote {OUT_PACKAGED} (extracts built in, opens in Tableau Public)")


def workbook_xml(marts: dict, windows: str, viewpoints: str, extract: bool) -> str:
    return f"""<?xml version='1.0' encoding='utf-8' ?>
<workbook original-version='18.1' source-build='2023.1.0 (20231.23.0310.1045)' source-platform='mac' version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <preferences>
    <preference name='ui.encoding.shelf.height' value='24' />
    <preference name='ui.shelf.height' value='26' />
  </preferences>
  <datasources>
{chr(10).join(datasource_xml(m, c, extract) for m, c in marts.items())}
  </datasources>
{shared_views_xml()}
  <worksheets>
{chr(10).join(worksheet(**s) for s in SHEETS)}
  </worksheets>
  <dashboards>
{dashboard_xml()}
  </dashboards>
  <windows>
    <window class='dashboard' maximized='true' name='AML Monitoring Dashboard'>
      <viewpoints>{viewpoints}</viewpoints>
      <active id='-1' />
    </window>
{windows}
  </windows>
</workbook>
"""


if __name__ == "__main__":
    main()
