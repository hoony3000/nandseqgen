# viz_tools.py
# Drop-in visualization helpers for NAND sequence generator
# - TimelineLogger: per-target logging (die, plane, block, page) with op metadata
# - plot_gantt: Gantt-like time chart by block
# - plot_block_page_sequence_3d: 3D scatter (x=block, y=page, z=order, color=op)
#
# Requirements: pandas, matplotlib (no seaborn)

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
import pandas as pd
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 (needed for 3D)
import matplotlib.patches as mpatches

# ---- Color map (operation kinds) ----
DEFAULT_COLORS = {
    "ERASE":   "#e74c3c",  # red
    "PROGRAM": "#f1c40f",  # yellow
    "READ":    "#2ecc71",  # green
    "DOUT":    "#9b59b6",  # purple
    "SR":      "#3498db",  # blue
    # aliases (fall back to base)
    "SIN_READ": "#2ecc71",
    "MUL_READ": "#2ecc71",
    "SIN_PROGRAM": "#f1c40f",
    "MUL_PROGRAM": "#f1c40f",
    "SIN_ERASE": "#e74c3c",
    "MUL_ERASE": "#e74c3c",
}

def _color_for(kind: str) -> str:
    return DEFAULT_COLORS.get(kind, DEFAULT_COLORS.get(kind.split("_")[-1], "#7f8c8d"))

# ---- Logger ----------------------------------------------------------------

@dataclass
class TimelineLogger:
    """
    Per-target timeline logger.
    Call `log_op(op, start_us, end_us)` from the scheduler once per scheduled operation.
    It records one row per target (die, plane, block, page), including op kind and alias label.
    """
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def log_op(self, op, start_us: float, end_us: float, label_for_read=None):
        """Append one row per target. If `label_for_read` is given, use it for READ (SIN_/MUL_) label."""
        label = op.kind.name
        if label_for_read and op.kind.name == "READ":
            label = label_for_read  # SIN_READ / MUL_READ
        for t in op.targets:
            # page: ensure ERASE/SR also have a page number (0) for uniformity
            page = t.page if (t.page is not None) else 0
            self.rows.append({
                "start_us": float(start_us),
                "end_us":   float(end_us),
                "die":      int(t.die),
                "plane":    int(t.plane),
                "block":    int(t.block),
                "page":     int(page),
                "kind":     label,          # alias aware
                "base_kind": op.kind.name,  # ERASE/PROGRAM/READ/DOUT/SR/...
                "source":   op.meta.get("source"),
            })

    # ---- DataFrame & sequencing --------------------------------------------

    def to_dataframe(self) -> pd.DataFrame:
        df = pd.DataFrame(self.rows)
        if not df.empty:
            df = df.sort_values(["die", "block", "start_us", "end_us"]).reset_index(drop=True)
            # order within block (time order)
            df["seq_per_block"] = df.groupby(["die", "block"]).cumcount() + 1
            # global order within die (time order)
            df["seq_global"] = df.groupby(["die"]).cumcount() + 1
            # duration (helpful)
            df["dur_us"] = df["end_us"] - df["start_us"]
        return df

# ---- Gantt chart ------------------------------------------------------------

def plot_gantt(df: pd.DataFrame,
               die: int = 0,
               blocks: Optional[Sequence[int]] = None,
               kinds: Sequence[str] = ("ERASE","PROGRAM","READ","DOUT","SR"),
               linewidth: float = 6.0,
               figsize: Tuple[float,float] = (12, 4),
               title: Optional[str] = None):
    """
    Gantt-like timeline: y=block, x=time, each op is a horizontal bar.
    - Filters by `die`, `blocks`, and `kinds` (by base_kind).
    - Colors by base_kind.
    """
    if df.empty:
        print("[plot_gantt] empty dataframe"); return

    d = df[df["die"] == die]
    if blocks is not None:
        d = d[d["block"].isin(blocks)]
    d = d[d["base_kind"].isin(kinds)]
    if d.empty:
        print(f"[plot_gantt] no rows for die={die} with given filters"); return

    # order blocks by first appearance
    blocks_sorted = list(d.groupby("block")["start_us"].min().sort_values().index)
    ymap = {b:i for i,b in enumerate(blocks_sorted)}

    plt.figure(figsize=figsize)
    for _, r in d.iterrows():
        y = ymap[r["block"]]
        c = _color_for(r["base_kind"])
        plt.hlines(y, r["start_us"], r["end_us"], colors=c, linewidth=linewidth)

    plt.yticks(list(ymap.values()), [f"blk{b}" for b in blocks_sorted])
    plt.xlabel("time (us)")
    plt.ylabel("block")
    plt.grid(axis="x", linestyle="--", alpha=0.4)

    ttl = title or f"Die {die} timeline (Gantt)"
    plt.title(ttl)

    # legend
    handles = [mpatches.Patch(color=_color_for(k), label=k) for k in kinds]
    plt.legend(handles=handles, loc="upper right", frameon=False)

    plt.tight_layout()
    plt.show()

# ---- 3D plot: block-page-order ---------------------------------------------

def plot_block_page_sequence_3d(df: pd.DataFrame,
                                die: int = 0,
                                kinds: Sequence[str] = ("ERASE","PROGRAM","READ"),
                                z_mode: str = "per_block",    # "per_block" | "global"
                                blocks: Optional[Sequence[int]] = None,
                                figsize: Tuple[float,float] = (10, 6),
                                title: Optional[str] = None):
    """
    3D scatter: x=block, y=page, z=order, color=base_kind (operation).
    - Use z=seq_per_block (default) or z=seq_global.
    - Multi-plane ops are included naturally (one point per target row).
    - ERASE / SR rows are present with page=0 (by design).
    """
    if df.empty:
        print("[plot_block_page_sequence_3d] empty dataframe"); return

    d = df[df["die"] == die].copy()
    if blocks is not None:
        d = d[d["block"].isin(blocks)]
    d = d[d["base_kind"].isin(kinds)]
    if d.empty:
        print(f"[plot_block_page_sequence_3d] no rows for die={die} with given filters"); return

    zcol = "seq_per_block" if z_mode == "per_block" else "seq_global"

    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111, projection="3d")

    for k in kinds:
        dk = d[d["base_kind"] == k]
        if dk.empty: continue
        ax.scatter(dk["block"], dk["page"], dk[zcol], s=28, depthshade=True, c=_color_for(k), label=k)

    ax.set_xlabel("block")
    ax.set_ylabel("page")
    ax.set_zlabel("order" + (" (per block)" if z_mode=="per_block" else " (global)"))

    ttl = title or f"Die {die} block-page-order (3D)"
    ax.set_title(ttl)

    # tidy axes ticks
    if blocks is not None:
        ax.set_xticks(sorted(set(blocks)))
    # set minimal margins
    ax.margins(x=0.01, y=0.02, z=0.02)

    ax.legend(loc="upper left")
    plt.tight_layout()
    plt.show()