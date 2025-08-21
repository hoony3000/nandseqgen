
"""
Interactive NAND Gantt Chart (Dash + Plotly)
--------------------------------------------
Features
- Upload CSV (or start with sample data)
- Explodes multi-target payloads into individual rows (one row per die/block)
- Filters: die, block, operation
- Interactive zoom/pan + x-axis range slider
- Dynamic figure height based on number of lanes

Expected columns (minimum):
- time (numeric; start time for the operation)
- op_name (string; NAND operation name)
- ONE of the following to form 'end':
    * end_time (numeric; explicit end/finish time)
    * duration or latency (numeric; end = time + duration/latency)
- die (int) and block (int):
    * If not present but a 'payload' column contains a list of dicts like
      [{"die":0,"pl":0,"block":17,"page":0}], the app will EXPLODE this into
      per-target rows and fill die/block from each target.

Run:
    pip install dash plotly pandas
    python nand_gantt_dash.py
Then open the local URL shown in the console (http://127.0.0.1:8050 by default).
"""

from __future__ import annotations

import ast
import base64
import io
from typing import List, Dict, Any

import pandas as pd
import numpy as np
import plotly.express as px

from dash import Dash, dcc, html, Input, Output, State, dash_table

# -----------------------------
# Helpers
# -----------------------------

def sample_data(n_dies: int = 2, blocks_per_die: int = 3, n_ops_per_lane: int = 10) -> pd.DataFrame:
    """
    Create a small synthetic dataset for demonstration.
    Columns: time, op_name, die, block, duration
    """
    rng = np.random.default_rng(123)
    ops = ["SIN_ERASE", "MUL_READ", "NOP", "SR", "PROG", "RD", "ER"]
    rows = []
    t = 0
    for die in range(n_dies):
        for block in range(blocks_per_die):
            t = 0
            for _ in range(n_ops_per_lane):
                op = rng.choice(ops)
                duration = int(rng.integers(30, 120))
                rows.append({
                    "time": t,
                    "op_name": op,
                    "die": die,
                    "block": block,
                    "duration": duration,
                    # Example payload showing per-op target;
                    # real-world data may contain multiple targets (MUL_READ etc.).
                    "payload": str([{"die": int(die), "block": int(block)}])
                })
                t += int(rng.integers(40, 150))
    df = pd.DataFrame(rows)
    return df


def _ensure_numeric(x):
    try:
        return pd.to_numeric(x)
    except Exception:
        return x


def expand_payload_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    If 'payload' column contains a list of dicts, explode to per-target rows.
    Fills 'die' and 'block' when present in payload targets.
    """
    if "payload" not in df.columns:
        return df.copy()

    def parse_targets(val) -> List[Dict[str, Any]]:
        try:
            obj = ast.literal_eval(str(val))
            if isinstance(obj, list):
                return obj
        except Exception:
            pass
        return []

    expanded = []
    for _, row in df.iterrows():
        targets = parse_targets(row.get("payload", None))
        if not targets:
            expanded.append(row.to_dict())
            continue

        for tgt in targets:
            new_row = row.to_dict()
            if isinstance(tgt, dict):
                # unify keys (some datasets might use 'pl' vs 'plane')
                if "die" in tgt:
                    new_row["die"] = tgt["die"]
                if "block" in tgt:
                    new_row["block"] = tgt["block"]
                if "plane" in tgt:
                    new_row["plane"] = tgt["plane"]
                if "pl" in tgt and "plane" not in tgt:
                    new_row["plane"] = tgt["pl"]
                if "page" in tgt:
                    new_row["page"] = tgt["page"]
            expanded.append(new_row)

    out = pd.DataFrame(expanded)
    # Coerce numeric where it makes sense
    for col in ["die", "block", "plane", "page"]:
        if col in out.columns:
            out[col] = _ensure_numeric(out[col])
    return out


def normalize_timeline_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure 'start' and 'end' columns are present for timeline plotting.
    - start := df['time']
    - end := df['end_time'] if present
             else df['time'] + df['duration' or 'latency']
             else df['time'] + 1
    """
    out = df.copy()
    if "time" not in out.columns:
        raise ValueError("Input data must contain a 'time' column (numeric).")
    out["start"] = pd.to_numeric(out["time"], errors="coerce")

    if "end_time" in out.columns:
        out["end"] = pd.to_numeric(out["end_time"], errors="coerce")
    elif "duration" in out.columns:
        out["end"] = out["start"] + pd.to_numeric(out["duration"], errors="coerce").fillna(1)
    elif "latency" in out.columns:
        out["end"] = out["start"] + pd.to_numeric(out["latency"], errors="coerce").fillna(1)
    else:
        out["end"] = out["start"] + 1

    # Optional: remove rows where start or end is NaN
    out = out.dropna(subset=["start", "end"])

    # Build lane as die/block
    if "die" in out.columns and "block" in out.columns:
        out["lane"] = out["die"].astype("Int64").astype(str) + "/" + out["block"].astype("Int64").astype(str)
    else:
        # fallback: use op_name only
        out["lane"] = out.get("op_name", pd.Series(["lane"] * len(out)))

    return out


def make_timeline_figure(df: pd.DataFrame, lane_order: List[str] | None = None) -> "plotly.graph_objs._figure.Figure":
    """
    Build a Plotly timeline figure. If lane_order is provided, respect that order.
    """
    category_orders = {"lane": lane_order} if lane_order else None
    fig = px.timeline(
        df,
        x_start="start",
        x_end="end",
        y="lane",
        color="op_name",
        hover_data=[
            "op_name",
            "start",
            "end",
            "die" if "die" in df.columns else None,
            "block" if "block" in df.columns else None,
            "plane" if "plane" in df.columns else None,
            "page" if "page" in df.columns else None,
        ],
        category_orders=category_orders,
    )
    # Put first lane at top (Plotly timelines invert y by default)
    fig.update_yaxes(autorange="reversed")

    # Make it easy to zoom into a specific interval
    fig.update_xaxes(rangeslider_visible=True)

    fig.update_layout(
        margin=dict(l=10, r=10, t=40, b=40),
        legend_title_text="Operation",
        hovermode="closest",
    )
    return fig


def filter_dataframe(df: pd.DataFrame, die_vals, block_vals, op_vals, time_range) -> pd.DataFrame:
    """
    Filter dataframe based on the UI selections.
    time_range: [tmin, tmax]
    """
    out = df.copy()
    if die_vals:
        out = out[out["die"].isin(die_vals)]
    if block_vals:
        out = out[out["block"].isin(block_vals)]
    if op_vals:
        out = out[out["op_name"].isin(op_vals)]
    if time_range and len(time_range) == 2:
        tmin, tmax = time_range
        out = out[(out["end"] >= tmin) & (out["start"] <= tmax)]
    return out


# -----------------------------
# Dash App
# -----------------------------

app = Dash(__name__)
app.title = "NAND Gantt (Interactive)"

def layout_components():
    return html.Div(
        style={"fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", "padding": "10px"},
        children=[
            html.H2("NAND Operation Gantt (Interactive Zoom)"),
            html.P("Upload your CSV or use the sample. X=time, Y=die/block. Use the rangeslider and zoom to focus on intervals."),

            dcc.Upload(
                id="upload-data",
                children=html.Div(["📤 Drag & Drop CSV here, or ", html.A("Select a file")]),
                style={
                    "width": "100%",
                    "height": "70px",
                    "lineHeight": "70px",
                    "borderWidth": "1px",
                    "borderStyle": "dashed",
                    "borderRadius": "8px",
                    "textAlign": "center",
                    "margin": "10px 0",
                },
                multiple=False,
            ),

            html.Div(
                style={"display": "flex", "gap": "10px", "flexWrap": "wrap", "alignItems": "flex-end"},
                children=[
                    html.Div(
                        children=[
                            html.Label("Die"),
                            dcc.Dropdown(id="die-filter", options=[], multi=True, placeholder="All"),
                        ],
                        style={"minWidth": "180px"},
                    ),
                    html.Div(
                        children=[
                            html.Label("Block"),
                            dcc.Dropdown(id="block-filter", options=[], multi=True, placeholder="All"),
                        ],
                        style={"minWidth": "220px"},
                    ),
                    html.Div(
                        children=[
                            html.Label("Operation"),
                            dcc.Dropdown(id="op-filter", options=[], multi=True, placeholder="All"),
                        ],
                        style={"minWidth": "260px"},
                    ),
                    html.Div(
                        children=[
                            html.Label("Time Range"),
                            dcc.RangeSlider(
                                id="time-range",
                                min=0,
                                max=1000,
                                step=1,
                                value=[0, 1000],
                                tooltip={"always_visible": False, "placement": "bottom"},
                                allowCross=False,
                            ),
                            html.Div(id="time-range-label", style={"fontSize": "12px", "marginTop": "4px", "opacity": 0.7}),
                        ],
                        style={"flex": 1, "minWidth": "280px"},
                    ),
                    html.Button("Reset filters", id="reset-btn", n_clicks=0, style={"height": "38px"}),
                ],
            ),

            dcc.Loading(
                id="loading-graph",
                type="default",
                children=[
                    dcc.Graph(id="gantt-graph", figure={}, style={"height": "70vh"}),
                ],
            ),

            html.Hr(),
            html.Details([
                html.Summary("Input Data (preview)"),
                dash_table.DataTable(
                    id="data-preview",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={"fontSize": "12px", "padding": "6px"},
                )
            ]),

            dcc.Store(id="data-store"),  # normalized, exploded data
        ],
    )


app.layout = layout_components()


# -----------------------------
# Callbacks
# -----------------------------

def parse_contents(contents, filename):
    if contents is None:
        return None

    content_type, content_string = contents.split(",")
    decoded = base64.b64decode(content_string)
    try:
        if "csv" in filename.lower() or "text" in content_type:
            df = pd.read_csv(io.StringIO(decoded.decode("utf-8")))
        else:
            # Try reading as CSV anyway
            df = pd.read_csv(io.StringIO(decoded.decode("utf-8")))
    except Exception as e:
        raise ValueError(f"Failed to parse file: {e}")
    return df


@app.callback(
    Output("data-store", "data"),
    Output("die-filter", "options"),
    Output("block-filter", "options"),
    Output("op-filter", "options"),
    Output("time-range", "min"),
    Output("time-range", "max"),
    Output("time-range", "value"),
    Input("upload-data", "contents"),
    State("upload-data", "filename"),
    prevent_initial_call=False,
)
def load_data(contents, filename):
    # If no upload yet, start with sample data
    if contents is None:
        df = sample_data()
    else:
        df = parse_contents(contents, filename or "uploaded.csv")

    # Expand payload rows if present, then normalize to timeline cols
    df = expand_payload_rows(df)
    df = normalize_timeline_columns(df)

    # Need die/block for lane labeling—ensure they exist
    if "die" not in df.columns or "block" not in df.columns:
        # Create placeholders to avoid breaking UI
        df["die"] = df.get("die", pd.Series([-1] * len(df))).fillna(-1).astype(int)
        df["block"] = df.get("block", pd.Series([-1] * len(df))).fillna(-1).astype(int)

    die_opts = sorted(df["die"].dropna().unique().tolist())
    block_opts = sorted(df["block"].dropna().unique().tolist())
    op_opts = sorted(df["op_name"].astype(str).dropna().unique().tolist())

    tmin = float(df["start"].min()) if len(df) else 0.0
    tmax = float(df["end"].max()) if len(df) else 100.0
    # Pad a bit for nicer range slider behavior
    pad = max((tmax - tmin) * 0.02, 1.0)
    tmin2, tmax2 = tmin - pad, tmax + pad

    return (
        df.to_dict("records"),
        [{"label": str(v), "value": v} for v in die_opts],
        [{"label": str(v), "value": v} for v in block_opts],
        [{"label": str(v), "value": v} for v in op_opts],
        tmin2,
        tmax2,
        [tmin, tmax],
    )


@app.callback(
    Output("gantt-graph", "figure"),
    Output("gantt-graph", "style"),
    Output("data-preview", "data"),
    Output("data-preview", "columns"),
    Output("time-range-label", "children"),
    Input("data-store", "data"),
    Input("die-filter", "value"),
    Input("block-filter", "value"),
    Input("op-filter", "value"),
    Input("time-range", "value"),
)
def update_graph(data, die_vals, block_vals, op_vals, time_range):
    if not data:
        return {}, {"height": "70vh"}, [], [], ""

    df = pd.DataFrame(data)

    filtered = filter_dataframe(df, die_vals, block_vals, op_vals, time_range)
    # Decide lane order: sort by die then block
    if "die" in filtered.columns and "block" in filtered.columns:
        lanes = filtered[["lane", "die", "block"]].drop_duplicates().sort_values(["die", "block"])
        lane_order = lanes["lane"].tolist()
    else:
        lane_order = None

    fig = make_timeline_figure(filtered, lane_order=lane_order)

    # Dynamic height based on number of lanes (rows)
    n_lanes = len(lane_order) if lane_order else filtered["lane"].nunique()
    height = int(min(120 + 28 * max(n_lanes, 3), 1200))

    # Data preview
    columns = [{"name": c, "id": c} for c in filtered.columns]
    preview_rows = filtered.head(100).to_dict("records")

    label = f"{time_range[0]:.0f}  —  {time_range[1]:.0f}" if time_range else ""

    return fig, {"height": f"{height}px"}, preview_rows, columns, label


@app.callback(
    Output("die-filter", "value"),
    Output("block-filter", "value"),
    Output("op-filter", "value"),
    Output("time-range", "value"),
    Input("reset-btn", "n_clicks"),
    State("data-store", "data"),
    State("time-range", "min"),
    State("time-range", "max"),
    prevent_initial_call=True,
)
def reset_filters(n_clicks, data, tmin, tmax):
    if not data:
        return None, None, None, [0, 1000]
    df = pd.DataFrame(data)
    tmin0 = float(df["start"].min()) if len(df) else 0.0
    tmax0 = float(df["end"].max()) if len(df) else 100.0
    return None, None, None, [tmin0, tmax0]


if __name__ == "__main__":
    app.run_server(debug=True)
