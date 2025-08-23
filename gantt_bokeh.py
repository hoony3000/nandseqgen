from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
from bokeh.io import curdoc
from bokeh.layouts import column, row
from bokeh.models import (
    Button,
    ColumnDataSource,
    HoverTool,
    RangeSlider,
    MultiSelect,
    Div,
)
from bokeh.plotting import figure
from bokeh.models import Slider
from bokeh.palettes import Category20, Category10, Turbo256
from bokeh.transform import factor_cmap


def _normalize_timeline_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure columns: start, end, lane, op_name exist.
    Accepts nand_timeline.csv schema (start_us/end_us/kind).
    """
    out = df.copy()

    # start
    if "start" not in out.columns:
        if "time" in out.columns:
            out["start"] = pd.to_numeric(out["time"], errors="coerce")
        elif "start_us" in out.columns:
            out["start"] = pd.to_numeric(out["start_us"], errors="coerce")
        else:
            raise ValueError("Missing time column: one of ['start', 'time', 'start_us'] required")

    # end
    if "end" not in out.columns:
        if "end_time" in out.columns:
            out["end"] = pd.to_numeric(out["end_time"], errors="coerce")
        elif "end_us" in out.columns:
            out["end"] = pd.to_numeric(out["end_us"], errors="coerce")
        elif "duration" in out.columns:
            out["end"] = out["start"] + pd.to_numeric(out["duration"], errors="coerce").fillna(1)
        elif "latency" in out.columns:
            out["end"] = out["start"] + pd.to_numeric(out["latency"], errors="coerce").fillna(1)
        else:
            out["end"] = out["start"] + 1

    # op_name
    if "op_name" not in out.columns:
        if "kind" in out.columns:
            out["op_name"] = out["kind"].astype(str)
        else:
            out["op_name"] = "OP"

    # lane
    if "lane" not in out.columns:
        if "die" in out.columns and "block" in out.columns:
            out["lane"] = (
                out["die"].astype("Int64").astype(str) + "/" + out["block"].astype("Int64").astype(str)
            )
        else:
            out["lane"] = out["op_name"].astype(str)

    # Clean
    out = out.dropna(subset=["start", "end", "lane", "op_name"])  # minimal requirements
    return out


def _build_color_map(df: pd.DataFrame) -> Dict[str, str]:
    """Auto-assign colors for each op_name using palettes; stable by sorted order."""
    ops = sorted({str(x) for x in df["op_name"].astype(str).unique()})
    n = len(ops)
    palette: List[str]
    # choose base palette
    if n <= 10:
        palette = list(Category10[10])
    elif n <= 20:
        palette = list(Category20[20])
    elif n <= 256:
        # sample evenly from Turbo256
        step = max(1, len(Turbo256) // n)
        palette = [Turbo256[i] for i in range(0, step * n, step)][:n]
    else:
        palette = [Turbo256[i % 256] for i in range(n)]
    cmap = {op: palette[i % len(palette)] for i, op in enumerate(ops)}
    return cmap


def _lane_indexing(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    lanes = df[["lane"]].drop_duplicates().reset_index(drop=True)
    lanes["yidx"] = lanes.index
    out = df.merge(lanes, on="lane", how="left")
    lane_order = lanes["lane"].tolist()
    return out, lane_order


def _compute_height(n_lanes: int) -> int:
    return int(min(1200, max(400, 28 * max(n_lanes, 3) + 120)))


def _make_doc_layout(df_in: pd.DataFrame):
    df = _normalize_timeline_columns(df_in)
    df, lane_order = _lane_indexing(df)
    color_map = _build_color_map(df)
    ops = sorted(color_map.keys())
    palette = [color_map[o] for o in ops]

    # Widgets
    die_vals = sorted(df["die"].dropna().unique().tolist()) if "die" in df.columns else []
    block_vals = sorted(df["block"].dropna().unique().tolist()) if "block" in df.columns else []
    op_vals = sorted(df["op_name"].astype(str).dropna().unique().tolist())

    die_select = MultiSelect(title="Die", value=[], options=[str(v) for v in die_vals], size=6)
    block_select = MultiSelect(title="Block", value=[], options=[str(v) for v in block_vals], size=8)
    op_select = MultiSelect(title="Operation", value=[], options=op_vals, size=8)

    tmin = float(df["start"].min()) if len(df) else 0.0
    tmax = float(df["end"].max()) if len(df) else 1.0
    time_slider = RangeSlider(title="Time Range", start=tmin, end=tmax, value=(tmin, tmax), step=max((tmax - tmin) / 1000.0, 0.001))
    reset_btn = Button(label="Reset filters")
    zoom_in_btn = Button(label="Zoom In (x)")
    zoom_out_btn = Button(label="Zoom Out (x)")
    # Size controls
    width_slider = Slider(title="Width (px)", start=600, end=2200, step=20, value=1200)
    scale_slider = Slider(title="Height Scale (x)", start=0.5, end=2.5, step=0.1, value=1.0)

    # Sources
    cols = ["lane", "yidx", "start", "end", "op_name"]
    for c in ("die", "block", "plane", "page"):
        if c in df.columns:
            cols.append(c)
    base_df = df[cols].copy()
    src_all = ColumnDataSource(base_df)
    src = ColumnDataSource(base_df.copy())

    # Figure
    p = figure(
        title="NAND Gantt (Bokeh)",
        x_axis_label="time",
        y_range=list(reversed(lane_order)),  # top-most first lane
        height=_compute_height(len(lane_order)),
        tools="xpan,xwheel_zoom,reset,save",
        active_scroll="xwheel_zoom",
        output_backend="webgl",
    )
    p.x_range.start = tmin
    p.x_range.end = tmax
    p.width = int(width_slider.value)

    # Glyph
    mapper = factor_cmap("op_name", palette=palette, factors=ops)
    p.hbar(
        y="lane",
        left="start",
        right="end",
        height=0.8,
        fill_color=mapper,
        line_color=None,
        legend_field="op_name",
        source=src,
    )
    p.legend.title = "Operation"
    p.legend.location = "top_left"
    p.legend.click_policy = "hide"

    # Hover
    tips = [("op", "@op_name"), ("lane", "@lane"), ("start", "@start"), ("end", "@end")]
    for c in ("die", "block", "plane", "page"):
        if c in base_df.columns:
            tips.append((c, f"@{c}"))
    p.add_tools(HoverTool(tooltips=tips))

    info = Div(text="", sizing_mode="stretch_width")

    def _apply_filters():
        nonlocal lane_order
        f = src_all.to_df().copy()
        # die filter
        if "die" in f.columns and die_select.value:
            want = {int(v) for v in die_select.value}
            f = f[f["die"].isin(want)]
        # block filter
        if "block" in f.columns and block_select.value:
            want = {int(v) for v in block_select.value}
            f = f[f["block"].isin(want)]
        # op filter
        if op_select.value:
            want = set(op_select.value)
            f = f[f["op_name"].astype(str).isin(want)]
        # time filter
        t0, t1 = time_slider.value
        f = f[(f["end"] >= t0) & (f["start"] <= t1)]

        # update y_range and source
        lanes_new = f[["lane"]].drop_duplicates().sort_values("lane")["lane"].tolist()
        if not lanes_new:
            lanes_new = ["(empty)"]
        p.y_range.factors = list(reversed(lanes_new))
        base_h = _compute_height(len(lanes_new))
        p.height = int(base_h * float(scale_slider.value))
        src.data = ColumnDataSource.from_df(f)
        info.text = f"Rows: {len(f)} Lanes: {len(lanes_new)} Range: [{t0:.2f}, {t1:.2f}]"

    # wiring
    for w in (die_select, block_select, op_select, time_slider):
        w.on_change("value", lambda attr, old, new: _apply_filters())
    width_slider.on_change("value", lambda attr, old, new: setattr(p, "width", int(new)))
    scale_slider.on_change("value", lambda attr, old, new: _apply_filters())
    def _zoom(factor: float):
        try:
            x0 = float(p.x_range.start)
            x1 = float(p.x_range.end)
            c = 0.5 * (x0 + x1)
            w = max(1e-9, (x1 - x0) * float(factor))
            w = min(w, max(1e-9, tmax - tmin))
            nx0 = max(tmin, c - 0.5 * w)
            nx1 = min(tmax, c + 0.5 * w)
            if nx1 - nx0 < 1e-6:
                return
            p.x_range.start = nx0
            p.x_range.end = nx1
            time_slider.value = (nx0, nx1)
            _apply_filters()
        except Exception:
            pass
    zoom_in_btn.on_click(lambda: _zoom(0.5))
    zoom_out_btn.on_click(lambda: _zoom(1.5))
    reset_btn.on_click(
        lambda: (
            die_select.update(value=[]),
            block_select.update(value=[]),
            op_select.update(value=[]),
            time_slider.update(value=(tmin, tmax)),
            _apply_filters(),
        )
    )

    _apply_filters()

    controls = column(
        die_select,
        block_select,
        op_select,
        time_slider,
        row(zoom_in_btn, zoom_out_btn),
        row(width_slider, scale_slider),
        reset_btn,
        sizing_mode="fixed",
    )
    layout = column(row(controls, p), info)
    return layout


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    return pd.read_csv(path)


def build():
    csv_path = Path("nand_timeline.csv")
    df = _load_csv(csv_path)
    layout = _make_doc_layout(df)
    curdoc().add_root(layout)
    curdoc().title = "NAND Gantt (Bokeh)"


build()


