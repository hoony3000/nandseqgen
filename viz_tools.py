# viz_tools.py
# Visualization + Validation tools for NAND sequence generator
# - TimelineLogger: per-target logging (die, plane, block, page) with op metadata
# - plot_gantt / plot_gantt_by_die
# - plot_block_page_sequence_3d / plot_block_page_sequence_3d_by_die
# - validate_timeline: rule-checker (duplicates, read-before-program, busy overlaps)
#
# Requirements: pandas, matplotlib

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
import pandas as pd
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
import matplotlib.patches as mpatches

# -------------------- Colors (can be customized) --------------------
DEFAULT_COLORS = {
    "ERASE":   "#e74c3c",  # red
    "PROGRAM": "#f1c40f",  # yellow
    "READ":    "#2ecc71",  # green
    "DOUT":    "#9b59b6",  # purple
    "SR":      "#3498db",  # blue

    # aliases -> fallback to base
    "SIN_READ": "#2ecc71",
    "MUL_READ": "#2ecc71",
    "SIN_PROGRAM": "#f1c40f",
    "MUL_PROGRAM": "#f1c40f",
    "SIN_ERASE": "#e74c3c",
    "MUL_ERASE": "#e74c3c",
}

def _color_for(kind: str) -> str:
    return DEFAULT_COLORS.get(kind, DEFAULT_COLORS.get(kind.split("_")[-1], "#7f8c8d"))

def _overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    return not (a1 <= b0 or b1 <= a0)

# -------------------- Logger --------------------

@dataclass
class TimelineLogger:
    """
    Per-target timeline logger.
    - Scheduler에서, 예약 직후 _schedule_operation(...) 안에서 log_op(...)를 호출하세요.
    - READ는 label_for_read를 사용하면 SIN_READ/MUL_READ로 라벨링됩니다.
    - ERASE/SR도 page=0으로 기록하여 주소 스키마를 통일합니다.
    """
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def log_op(self, op, start_us: float, end_us: float, label_for_read: Optional[str] = None):
        label = op.kind.name
        if label_for_read and op.kind.name == "READ":
            label = label_for_read  # SIN_READ / MUL_READ
        for t in op.targets:
            page = t.page if (t.page is not None) else 0  # ERASE/SR도 page=0로 강제
            self.rows.append({
                "start_us": float(start_us),
                "end_us":   float(end_us),
                "die":      int(t.die),
                "plane":    int(t.plane),
                "block":    int(t.block),
                "page":     int(page),
                "kind":     label,          # alias-aware
                "base_kind": op.kind.name,  # ERASE/PROGRAM/READ/DOUT/SR/...
                "source":   op.meta.get("source"),
            })

    def to_dataframe(self) -> pd.DataFrame:
        df = pd.DataFrame(self.rows)
        if not df.empty:
            df = df.sort_values(["die", "block", "start_us", "end_us"]).reset_index(drop=True)
            # sequence numbers
            df["seq_per_block"] = df.groupby(["die", "block"]).cumcount() + 1
            df["seq_global_die"] = df.groupby(["die"]).cumcount() + 1
            df["dur_us"] = df["end_us"] - df["start_us"]
        return df

# -------------------- Gantt --------------------

def plot_gantt(df: pd.DataFrame,
               die: int = 0,
               blocks: Optional[Sequence[int]] = None,
               kinds: Sequence[str] = ("ERASE","PROGRAM","READ","DOUT","SR"),
               linewidth: float = 6.0,
               figsize: Tuple[float,float] = (12, 4),
               title: Optional[str] = None):
    """
    Gantt-like timeline: y=block, x=time(us), color=base_kind.
    """
    if df.empty:
        print("[plot_gantt] empty dataframe"); return
    d = df[df["die"] == die]
    if blocks is not None:
        d = d[d["block"].isin(blocks)]
    d = d[d["base_kind"].isin(kinds)]
    if d.empty:
        print(f"[plot_gantt] no rows for die={die} with given filters"); return

    # order blocks by first activity
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
    if title:
        plt.title(title)
    else:
        plt.title(f"Die {die} timeline (Gantt)")

    handles = [mpatches.Patch(color=_color_for(k), label=k) for k in kinds]
    plt.legend(handles=handles, loc="upper right", frameon=False)
    plt.grid(axis="x", linestyle="--", alpha=0.35)
    plt.tight_layout()
    plt.show()

def plot_gantt_by_die(df: pd.DataFrame,
                      dies: Optional[Sequence[int]] = None,
                      **kwargs):
    """
    Convenience: 각 die에 대해 별도의 Gantt를 그립니다(루프).
    kwargs는 plot_gantt에 그대로 전달됩니다.
    """
    if df.empty:
        print("[plot_gantt_by_die] empty dataframe"); return
    all_dies = sorted(df["die"].unique()) if dies is None else dies
    for d in all_dies:
        plot_gantt(df, die=d, **kwargs)

# -------------------- 3D plot (block–page–order) --------------------

def plot_block_page_sequence_3d(df: pd.DataFrame,
                                die: int = 0,
                                kinds: Sequence[str] = ("ERASE","PROGRAM","READ"),
                                z_mode: str = "per_block",    # "per_block" | "global_die"
                                blocks: Optional[Sequence[int]] = None,
                                figsize: Tuple[float,float] = (11, 7),
                                title: Optional[str] = None,
                                draw_lines: bool = True,
                                line_color: str = "#7f8c8d",
                                line_alpha: float = 0.6,
                                line_width: float = 1.2):
    """
    3D scatter: x=block, y=page, z=order, color=base_kind.
    - z_mode:
        * "per_block": seq_per_block (블록별 순번)
        * "global_die": seq_global_die (die 전체 순번)
    - 동일 block 내 포인트들을 얇은 선으로 연결하여 '흐름'을 표시(draw_lines=True).
    """
    if df.empty:
        print("[plot_block_page_sequence_3d] empty dataframe"); return

    d = df[df["die"] == die].copy()
    if blocks is not None:
        d = d[d["block"].isin(blocks)]
    d = d[d["base_kind"].isin(kinds)]
    if d.empty:
        print(f"[plot_block_page_sequence_3d] no rows for die={die} with given filters"); return

    zcol = "seq_per_block" if z_mode == "per_block" else "seq_global_die"

    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111, projection="3d")

    # scatter by kind
    for k in kinds:
        dk = d[d["base_kind"] == k]
        if dk.empty: continue
        ax.scatter(dk["block"], dk["page"], dk[zcol],
                   s=28, depthshade=True, c=_color_for(k), label=k)

    # connect within each block to show flow
    if draw_lines:
        for b, grp in d.groupby("block"):
            # 시간 순 또는 z 순으로 정렬 (둘 다 동일 성질)
            grp = grp.sort_values(["start_us", "end_us"]).copy()
            ax.plot(grp["block"].values, grp["page"].values, grp[zcol].values,
                    color=line_color, alpha=line_alpha, linewidth=line_width)

    ax.set_xlabel("block")
    ax.set_ylabel("page")
    ax.set_zlabel("order" + (" (per block)" if z_mode=="per_block" else " (per die)"))
    if title:
        ax.set_title(title)
    else:
        ax.set_title(f"Die {die} block–page–order (3D)")

    if blocks is not None:
        ax.set_xticks(sorted(set(blocks)))
    ax.margins(x=0.01, y=0.02, z=0.02)
    ax.legend(loc="upper left")
    plt.tight_layout()
    plt.show()

def plot_block_page_sequence_3d_by_die(df: pd.DataFrame,
                                       dies: Optional[Sequence[int]] = None,
                                       **kwargs):
    """
    각 die에 대해 3D plot을 별도로 그립니다(루프).
    kwargs는 plot_block_page_sequence_3d에 그대로 전달됩니다.
    """
    if df.empty:
        print("[plot_block_page_sequence_3d_by_die] empty dataframe"); return
    all_dies = sorted(df["die"].unique()) if dies is None else dies
    for d in all_dies:
        plot_block_page_sequence_3d(df, die=d, **kwargs)

# -------------------- Validator --------------------

@dataclass
class ValidationIssue:
    kind: str
    die: Optional[int]
    block: Optional[int]
    page: Optional[int]
    t0: float
    t1: float
    detail: str

def _spec_offsets_fixed(op_specs: Dict[str, Any]) -> Dict[str, Dict[str, Tuple[float,float]]]:
    """
    op_specs -> 각 base_kind별로 { 'total': (0,total), 'core_busy': (t0,t1) or None }를 계산.
    (고정 duration만 지원)
    """
    out: Dict[str, Dict[str, Tuple[float,float]]] = {}
    for base, spec in op_specs.items():
        t = 0.0; cb = None
        for s in spec["states"]:
            dur = s["dist"]["value"] if s["dist"]["kind"] == "fixed" else None
            if dur is None:
                raise ValueError("Validator requires fixed durations in op_specs.")
            s0, s1 = t, t + float(dur)
            if s["name"] == "CORE_BUSY":
                cb = (s0, s1)
            t = s1
        out[base] = {"total": (0.0, t), "core_busy": cb}
    return out

def validate_timeline(df: pd.DataFrame, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Rule checks:
      1) READ_BEFORE_PROGRAM
      2) PROGRAM_DUPLICATE (erase 없이 동일 page 재프로그램)
      3) PROGRAM_ORDER (page 순서 불일치)
      4) DOUT_OVERLAP (global freeze 위반)
      5) CORE_BUSY_DIEWIDE_OVERLAP (PROGRAM/ERASE의 CORE_BUSY 동안 같은 die의 READ/PROGRAM/ERASE 겹침)
      6) MUL_READ_CORE_BUSY_OVERLAP (MUL_READ의 CORE_BUSY 동안 같은 die의 READ/PROGRAM/ERASE 겹침)
      7) SIN_READ_CORE_BUSY_OVERLAP (SIN_READ의 CORE_BUSY 동안 같은 die의 MUL_READ/PROGRAM/ERASE 겹침)
    반환: {"issues": List[ValidationIssue], "counts": {...}}
    """
    issues: List[ValidationIssue] = []
    counts: Dict[str, int] = {}

    if df.empty:
        return {"issues": [], "counts": {}}

    # 1~3. 블록 상태를 이벤트 순회로 재구성 (commit은 end_us에 일어남)
    state = {}  # (die,block) -> {"last": int, "committed": set()}
    events = []
    for i, r in df.iterrows():
        events.append((float(r["start_us"]), 0, "start", i))  # end 우선 처리 위해 우선순위 1/0 대신 0/1
        events.append((float(r["end_us"]),   -1, "end",   i))
    # sort by time, then 'end' before 'start'
    events.sort()

    for _, _, etype, idx in events:
        r = df.iloc[idx]
        die, block, page = int(r["die"]), int(r["block"]), int(r["page"])
        base = str(r["base_kind"])

        key = (die, block)
        if key not in state:
            state[key] = {"last": -1, "committed": set()}

        if etype == "start":
            if base == "READ":
                if page not in state[key]["committed"]:
                    issues.append(ValidationIssue(
                        kind="READ_BEFORE_PROGRAM", die=die, block=block, page=page,
                        t0=float(r["start_us"]), t1=float(r["end_us"]),
                        detail="READ started before page was programmed/committed"
                    ))
            elif base == "PROGRAM":
                if page in state[key]["committed"]:
                    issues.append(ValidationIssue(
                        kind="PROGRAM_DUPLICATE", die=die, block=block, page=page,
                        t0=float(r["start_us"]), t1=float(r["end_us"]),
                        detail="PROGRAM on a page already committed without prior erase"
                    ))
                # 순서 체크: 기대 = last+1
                expected = state[key]["last"] + 1
                if page != expected:
                    issues.append(ValidationIssue(
                        kind="PROGRAM_ORDER", die=die, block=block, page=page,
                        t0=float(r["start_us"]), t1=float(r["end_us"]),
                        detail=f"PROGRAM order mismatch: expected page {expected}, got {page}"
                    ))

        elif etype == "end":
            if base == "PROGRAM":
                state[key]["committed"].add(page)
                if page > state[key]["last"]:
                    state[key]["last"] = page
            elif base == "ERASE":
                state[key]["committed"].clear()
                state[key]["last"] = -1

    # 4~7. 윈도우 겹침 검증 (고정 duration 전제)
    specs = _spec_offsets_fixed(cfg["op_specs"])

    # DOUT freeze (전 구간)
    dout_rows = df[df["base_kind"]=="DOUT"]
    if not dout_rows.empty:
        others = df[df["base_kind"]!="DOUT"]
        for _, r in dout_rows.iterrows():
            a0, a1 = float(r["start_us"]), float(r["end_us"])
            # 다른 모든 op와 겹침 검사
            ov = others[(others["start_us"] < a1) & (a0 < others["end_us"])]
            for __, rr in ov.iterrows():
                issues.append(ValidationIssue(
                    kind="DOUT_OVERLAP", die=None, block=None, page=None,
                    t0=min(a0, float(rr["start_us"])), t1=max(a1, float(rr["end_us"])),
                    detail=f"DOUT overlaps with {rr['base_kind']} (die={rr['die']}, block={rr['block']})"
                ))

    # CORE_BUSY windows
    def core_busy_window(row) -> Optional[Tuple[float,float]]:
        base = str(row["base_kind"])
        spec = specs.get(base)
        if not spec: return None
        cb = spec.get("core_busy")
        if not cb: return None
        s0 = float(row["start_us"]) + cb[0]
        s1 = float(row["start_us"]) + cb[1]
        return (s0, s1)

    # PROGRAM/ERASE CORE_BUSY: 같은 die에서 READ/PROGRAM/ERASE 전체 금지
    diewide_rows = df[df["base_kind"].isin(["PROGRAM","ERASE"])]
    all_rpe = df[df["base_kind"].isin(["READ","PROGRAM","ERASE"])]
    for _, r in diewide_rows.iterrows():
        win = core_busy_window(r)
        if not win: continue
        s0, s1 = win
        d = int(r["die"])
        # 같은 die의 READ/PROGRAM/ERASE 전 구간과 겹침 검사
        ov = all_rpe[(all_rpe["die"]==d) &
                     (all_rpe.index != r.name) &
                     (all_rpe["start_us"] < s1) & (s0 < all_rpe["end_us"])]
        for __, rr in ov.iterrows():
            issues.append(ValidationIssue(
                kind="CORE_BUSY_DIEWIDE_OVERLAP", die=d, block=None, page=None,
                t0=max(s0, float(rr["start_us"])), t1=min(s1, float(rr["end_us"])),
                detail=f"{r['base_kind']}.CORE_BUSY overlaps with {rr['base_kind']} on die {d}"
            ))

    # MUL_READ CORE_BUSY: 같은 die에서 READ/PROGRAM/ERASE 금지
    mul_rows = df[(df["base_kind"]=="READ") & (df["kind"]=="MUL_READ")]
    for _, r in mul_rows.iterrows():
        win = core_busy_window(r)
        if not win: continue
        s0, s1 = win
        d = int(r["die"])
        ov = all_rpe[(all_rpe["die"]==d) &
                     (all_rpe.index != r.name) &
                     (all_rpe["start_us"] < s1) & (s0 < all_rpe["end_us"])]
        for __, rr in ov.iterrows():
            issues.append(ValidationIssue(
                kind="MUL_READ_CORE_BUSY_OVERLAP", die=d, block=None, page=None,
                t0=max(s0, float(rr["start_us"])), t1=min(s1, float(rr["end_us"])),
                detail=f"MUL_READ.CORE_BUSY overlaps with {rr['base_kind']} on die {d}"
            ))

    # SIN_READ CORE_BUSY: 같은 die에서 MUL_READ/PROGRAM/ERASE 금지 (SIN_READ는 허용)
    sin_rows = df[(df["base_kind"]=="READ") & (df["kind"]=="SIN_READ")]
    ban_for_sin = df[(df["base_kind"].isin(["PROGRAM","ERASE"])) | ((df["base_kind"]=="READ") & (df["kind"]=="MUL_READ"))]
    for _, r in sin_rows.iterrows():
        win = core_busy_window(r)
        if not win: continue
        s0, s1 = win
        d = int(r["die"])
        ov = ban_for_sin[(ban_for_sin["die"]==d) &
                         (ban_for_sin.index != r.name) &
                         (ban_for_sin["start_us"] < s1) & (s0 < ban_for_sin["end_us"])]
        for __, rr in ov.iterrows():
            issues.append(ValidationIssue(
                kind="SIN_READ_CORE_BUSY_OVERLAP", die=d, block=None, page=None,
                t0=max(s0, float(rr["start_us"])), t1=min(s1, float(rr["end_us"])),
                detail=f"SIN_READ.CORE_BUSY overlaps with {rr['kind']} on die {d}"
            ))

    # counts
    for isu in issues:
        counts[isu.kind] = counts.get(isu.kind, 0) + 1

    return {"issues": issues, "counts": counts}

def print_validation_report(report: Dict[str, Any], max_rows: int = 20):
    issues = report.get("issues", [])
    counts = report.get("counts", {})
    print("=== Validation Report ===")
    if not issues:
        print("No issues detected.")
        return
    # summary
    for k, v in sorted(counts.items()):
        print(f"{k:30s}: {v}")
    # sample
    print("\n-- sample --")
    for isu in issues[:max_rows]:
        print(f"[{isu.kind}] die={isu.die} block={isu.block} page={isu.page} "
              f"time=({isu.t0:.2f},{isu.t1:.2f})  {isu.detail}")

def violations_to_dataframe(report: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for isu in report.get("issues", []):
        rows.append({
            "kind": isu.kind, "die": isu.die, "block": isu.block, "page": isu.page,
            "t0": isu.t0, "t1": isu.t1, "detail": isu.detail
        })
    return pd.DataFrame(rows)