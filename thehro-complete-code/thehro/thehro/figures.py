"""Charts for the README and the deck appendix (matplotlib, PNG)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

INK, MUTED, ACCENT, WARN = "#1f2933", "#7b8794", "#0f766e", "#b45309"


def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK, labelsize=9)
    ax.grid(axis="y", color="#e4e7eb", lw=0.8)
    ax.set_axisbelow(True)


def budget_curve(curve: pd.DataFrame, rules: dict, chosen: dict, path: Path):
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=160)
    ax.plot(curve.genuine_per_10k, curve.victims_warned * 100, "-o", color=ACCENT, ms=4, label="Thehro (budget sweep)")
    ax.scatter([chosen["genuine_t2plus_per_10k"]], [chosen["victims_warned"] * 100], s=90, color=ACCENT,
               edgecolor="white", zorder=5)
    ax.annotate("chosen: 1 per 10,000", (chosen["genuine_t2plus_per_10k"], chosen["victims_warned"] * 100),
                textcoords="offset points", xytext=(8, -14), fontsize=8, color=ACCENT)
    for name, r in rules.items():
        ax.scatter([r["genuine_t2plus_per_10k"]], [r["victims_warned"] * 100], marker="s", color=WARN, s=36)
        ax.annotate(name, (r["genuine_t2plus_per_10k"], r["victims_warned"] * 100), textcoords="offset points",
                    xytext=(6, 4), fontsize=7, color=WARN)
    ax.set_xscale("log")
    ax.set_xlabel("Genuine payments interrupted per 10,000 (log scale)", fontsize=9, color=INK)
    ax.set_ylabel("Scam victims warned (%)", fontsize=9, color=INK)
    ax.set_ylim(0, 100)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def bars(df: pd.DataFrame, cols: list[str], labels: list[str], title: str, path: Path, pct=True):
    fig, ax = plt.subplots(figsize=(7, 0.45 * len(df) + 1.2), dpi=160)
    y = range(len(df))
    h = 0.8 / len(cols)
    colors = [ACCENT, MUTED, WARN]
    for i, (c, lab) in enumerate(zip(cols, labels)):
        vals = df[c].values * (100 if pct else 1)
        ax.barh([v + i * h for v in y], vals, height=h, color=colors[i % 3], label=lab)
    ax.set_yticks([v + h * (len(cols) - 1) / 2 for v in y])
    ax.set_yticklabels(df.index, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 100 if pct else None)
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    _style(ax)
    ax.grid(axis="x", color="#e4e7eb", lw=0.8)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
