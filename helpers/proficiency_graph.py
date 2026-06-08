from __future__ import annotations

from io import BytesIO
import math

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from helpers import db


def render_proficiency_chart(gamertag: str, summary: dict) -> BytesIO | None:
    fields = list(db.PROFICIENCY_FIELDS)
    by_field = {
        str(row.get("field")): float(row.get("score") or 0)
        for row in summary.get("proficiencies", [])
        if row.get("field")
    }
    values = [by_field.get(field, 0.0) for field in fields]
    if not any(value > 0 for value in values):
        return None

    max_value = max(25.0, math.ceil((max(values) + 10.0) / 25.0) * 25.0)
    angles = np.linspace(0, 2 * np.pi, len(fields), endpoint=False).tolist()
    values_closed = values + values[:1]
    angles_closed = angles + angles[:1]

    bg = "#071014"
    panel = "#0b1820"
    grid = "#284956"
    accent = "#f4d06f"
    text = "#eef8fb"
    muted = "#8fb9c4"

    plt.rcParams["font.family"] = "DejaVu Sans"
    fig = plt.figure(figsize=(8.8, 7.2), facecolor=bg)
    ax = fig.add_subplot(111, polar=True)
    ax.set_facecolor(panel)
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_ylim(0, max_value)
    ax.set_xticks(angles)
    ax.set_xticklabels(fields, color=text, fontsize=9, fontweight="bold")
    ax.tick_params(axis="x", pad=12)
    ticks = [tick for tick in (25, 50, 75, 100, 150, 200, 250, 300) if tick <= max_value]
    ax.set_yticks(ticks)
    ax.set_yticklabels([str(tick) for tick in ticks], color=muted, fontsize=8)
    ax.grid(color=grid, linewidth=0.9, alpha=0.85)
    for spine in ax.spines.values():
        spine.set_color("#4aa3b5")

    ax.plot(angles_closed, values_closed, color=accent, linewidth=3)
    ax.fill(angles_closed, values_closed, color=accent, alpha=0.24)
    ax.scatter(angles, values, s=48, color=accent, edgecolor=bg, linewidth=1.1, zorder=5)

    fig.text(0.08, 0.94, gamertag.upper(), color=text, fontsize=22, fontweight="bold")
    fig.text(0.08, 0.90, "MEDAL PROFICIENCY PROFILE", color=muted, fontsize=10, fontweight="bold")
    fig.text(
        0.08,
        0.055,
        f"Medals: {int(summary.get('medal_count') or 0)}   Proficiency score: {int(summary.get('proficiency_score') or 0)}",
        color=muted,
        fontsize=10,
    )

    output = BytesIO()
    fig.savefig(output, format="png", dpi=160, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    output.seek(0)
    return output
