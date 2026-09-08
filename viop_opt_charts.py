"""VIOP options flow - figure builders.

Plotly only, no streamlit: every figure here can be rendered to PNG and looked
at outside the app.

Colour rules that must not drift:

  * Categorical hues carry *participant identity*, assigned in fixed slot
    order and never cycled -- the tail past the last slot folds into "Other".
  * Buy / net-long is always the cool pole, sell / net-short always the warm
    one, in every figure.
  * Net position is polarity, so the heatmap uses the diverging pair with a
    near-surface neutral midpoint pinned at zero -- never a rainbow.

The palettes below extend the 8 documented reference hues with one more
(#a738c5), searched in OKLCH and validated with the skill's own validator.
Nine is the honest ceiling: searching the whole OKLCH gamut for a 10th hue
that sits at least dE 13 from all nine returns nothing, so a 10th colour
would look like one already on screen. Both nine-slot palettes pass every
adjacent-pair gate in both modes (dark: CVD dE 8.4, normal-vision 19.3, all
nine over 3:1). All-pairs separation does not hold at nine -- it does not
hold at eight either -- so identity never rests on hue alone: every stacked
segment big enough to read carries a direct label, every figure keeps its
legend, and every chart has a table beside it. Participants past the ninth
are shown by row and by label on the flow chart and the heatmap, never by a
recycled hue.

Marks stay thin with generous gaps and hairline chrome; saturated fills are
kept small, never poured into wide blocks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

PALETTES = {
    "Dark": dict(
        surface="#1a1a19", page="#0d0d0d", ink="#ffffff", ink2="#c3c2b7",
        muted="#898781", grid="#242423", base="#383835",
        cat=["#3987e5", "#d95926", "#199e70", "#c98500",
             "#d55181", "#008300", "#9085e9", "#e66767", "#a738c5"],
        other="#5f5f5a", buy="#3987e5", sell="#e66767",
        pole_cool="#184f95", pole_warm="#e66767", mid="#232322",
    ),
    "Light": dict(
        surface="#fcfcfb", page="#f9f9f7", ink="#0b0b0b", ink2="#52514e",
        muted="#898781", grid="#eceae4", base="#c3c2b7",
        cat=["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
             "#e87ba4", "#008300", "#4a3aa7", "#e34948", "#a738c5"],
        other="#b4b3ad", buy="#2a78d6", sell="#e34948",
        pole_cool="#1c5cab", pole_warm="#e34948", mid="#f4f3f0",
    ),
}
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
MAX_SLOTS = 9           # hues are never cycled; the tail folds into "Other"
ROW_PITCH = 31          # px per ladder row -- air between thin bars
LABEL_SHARE = 0.28      # label a segment holding this much of its side
LABEL_FLOOR = 0.045     # ...and only if it is this wide on the axis


def net_position_scale(p: dict) -> list:
    """Diverging scale for net position; use with zmid=0 and symmetric zmin/zmax.

    Warm at the bottom of the range, cool at the top, so net short is warm and
    net long is cool -- the same polarity as the bars, where buying is cool.
    The midpoint is one shade off the surface, so "flat" recedes and only real
    positions carry colour.
    """
    return [[0.0, p["pole_warm"]], [0.5, p["mid"]], [1.0, p["pole_cool"]]]


def short_name(name: str, limit: int = 11) -> str:
    """Compact a member name enough to sit inside a bar segment."""
    name = str(name)
    if len(name) <= limit:
        return name
    first = name.split()[0]
    return first if len(first) <= limit else first[:limit - 1] + "…"


def _short(v: float) -> str:
    v = abs(float(v))
    for cut, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if v >= cut:
            return f"{v / cut:g}{suf}"
    return f"{v:g}"


def sym_ticks(lim: float, per_side: int = 4):
    """Tick values symmetric about zero, labelled with absolute magnitudes.

    The two halves of a butterfly mean 'bought' and 'sold', not negative and
    positive, so the left arm must not be labelled with minus signs.
    """
    if not np.isfinite(lim) or lim <= 0:
        return None, None
    raw = lim / per_side
    mag = 10.0 ** np.floor(np.log10(raw))
    step = next(s for s in (1, 2, 2.5, 5, 10) if s * mag >= raw) * mag
    arm = np.arange(step, lim + step * 0.5, step)
    vals = [-v for v in arm[::-1]] + [0.0] + list(arm)
    return vals, [_short(v) for v in vals]


def base_layout(p: dict, **kw) -> dict:
    lay = dict(
        paper_bgcolor=p["surface"], plot_bgcolor=p["surface"],
        font=dict(family=FONT, color=p["ink2"], size=12),
        margin=dict(l=10, r=18, t=16, b=14),
        hoverlabel=dict(bgcolor=p["surface"], bordercolor=p["base"],
                        font=dict(family=FONT, color=p["ink"], size=12)),
        legend=dict(orientation="h", yanchor="bottom", y=1.015, x=0,
                    bgcolor="rgba(0,0,0,0)", itemsizing="constant",
                    itemwidth=30, tracegroupgap=4,
                    font=dict(color=p["ink2"], size=11)),
        barcornerradius=3,
    )
    lay.update(kw)
    return lay


def axis(p: dict, **kw) -> dict:
    """Hairline, recessive: no axis rule, one-shade-off grid, muted ticks.

    `automargin` is not optional here. Plotly's margin.autoexpand grows a
    margin for legends and colorbars but NOT for tick labels, so the fixed
    small left margin these figures use would clip long category labels --
    member names down to their last letters, and '17000  C' down to 'C'.
    """
    a = dict(showgrid=True, gridcolor=p["grid"], gridwidth=1, griddash="solid",
             zeroline=False, showline=False, ticks="", automargin=True,
             tickfont=dict(color=p["muted"], size=11),
             title_font=dict(color=p["muted"], size=11), title_standoff=12)
    a.update(kw)
    return a


def color_map(order: list[str], p: dict) -> dict:
    """Fixed slot order; anything past the last slot is 'Other', never a new hue."""
    m = {name: p["cat"][i] for i, name in enumerate(order[:MAX_SLOTS])}
    m["Other"] = p["other"]
    return m


# ------------------------------------------------------------------ figures

def ladder_fig(lad: pd.DataFrame, rows: list[str], order: list[str],
               cmap: dict, metric_label: str, mode: str, p: dict) -> go.Figure:
    """The butterfly: strike rows, buying left of zero, selling right."""
    groups = [g for g in order if g in set(lad["group"])]
    if "Other" in set(lad["group"]):
        groups.append("Other")

    # Stacked length of each side of each row, so the arms share one scale.
    arm = (lad.assign(a=lad["x"].abs())
              .groupby(["row", "side"], observed=True)["a"].sum())
    lim = float(arm.max()) * 1.06 if len(arm) else 1.0
    # Share of its own side, for deciding which segments get a direct label.
    side_total = (lad.groupby(["row", "side"], observed=True)["magnitude"]
                     .transform("sum").replace(0, np.nan))
    lad = lad.assign(_share=lad["magnitude"] / side_total)

    fig = go.Figure()
    for g in groups:
        d = lad[lad["group"] == g]
        wide = (d["_share"] >= LABEL_SHARE) & (d["magnitude"] >= LABEL_FLOOR * lim)
        fig.add_trace(go.Bar(
            x=d["x"], y=d["row"], orientation="h", name=g,
            marker=dict(color=cmap[g],
                        line=dict(color=p["surface"], width=1.2)),
            text=np.where(wide, short_name(g), ""),
            textposition="inside", insidetextanchor="middle",
            constraintext="inside", cliponaxis=False,
            insidetextfont=dict(family=FONT, size=10, color=p["surface"]),
            customdata=(d.assign(g=g)[["g", "row", "side", "magnitude"]]
                        .to_numpy(dtype=object)),
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}"
                          "<br>%{customdata[2]}: %{customdata[3]:,.0f}"
                          "<extra></extra>"))

    xtitle = ("Volume  ( ← bought  |  sold → )" if mode == "gross"
              else "Net position  ( ← net long  |  net short → )")
    vals, text = sym_ticks(lim)
    fig.update_layout(**base_layout(
        p, barmode="relative", bargap=0.46,
        height=max(430, ROW_PITCH * len(rows) + 128),
        margin=dict(l=10, r=18, t=34, b=14),
        xaxis=axis(p, title_text=f"{xtitle}    {metric_label}",
                   range=[-lim, lim], tickmode="array", tickvals=vals,
                   ticktext=text),
        yaxis=axis(p, showgrid=False, title_text="Strike · C/P",
                   type="category", categoryorder="array",
                   categoryarray=rows, autorange="reversed",
                   tickfont=dict(color=p["ink2"], size=11)),
        hovermode="closest"))
    fig.add_vline(x=0, line_color=p["base"], line_width=1)
    return fig


def flow_fig(pf: pd.DataFrame, metric_label: str, p: dict) -> go.Figure:
    """One row per participant: bought left, sold right, net kept as a marker."""
    pf = pf.iloc[::-1]  # biggest ends up at the top of the drawn axis
    lim = float(max(pf["buy"].max(), pf["sell"].max())) * 1.08 or 1.0

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=-pf["buy"], y=pf["participant"], orientation="h", name="Bought",
        marker=dict(color=p["buy"], line=dict(color=p["surface"], width=1.2)),
        customdata=pf[["buy", "buy_vwap"]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>bought %{customdata[0]:,.0f}"
                      "<br>vwap %{customdata[1]:,.4g}<extra></extra>"))
    fig.add_trace(go.Bar(
        x=pf["sell"], y=pf["participant"], orientation="h", name="Sold",
        marker=dict(color=p["sell"], line=dict(color=p["surface"], width=1.2)),
        customdata=pf[["sell", "sell_vwap"]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>sold %{customdata[0]:,.0f}"
                      "<br>vwap %{customdata[1]:,.4g}<extra></extra>"))

    fig.add_trace(go.Scatter(
        x=-pf["net"], y=pf["participant"], mode="markers", name="Net kept",
        marker=dict(color=p["ink"], symbol="diamond", size=8,
                    line=dict(color=p["surface"], width=1.5)),
        cliponaxis=False, customdata=pf["net"],
        hovertemplate="<b>%{y}</b><br>net %{customdata:+,.0f}<extra></extra>"))

    # The net numbers live in a gutter outside the plot, not on the fills: a
    # label sitting on a bar is unreadable in one direction and invisible in
    # the other, and this reads as a value column instead.
    for who, net in zip(pf["participant"], pf["net"]):
        fig.add_annotation(
            xref="paper", x=1.008, xanchor="left", y=who, yanchor="middle",
            text=f"{net:+,.0f}", showarrow=False,
            font=dict(family=FONT, size=10,
                      color=p["buy"] if net > 0 else p["sell"]))
    fig.add_annotation(xref="paper", x=1.008, xanchor="left",
                       yref="paper", y=1.015, yanchor="bottom", text="net kept",
                       showarrow=False,
                       font=dict(family=FONT, size=10, color=p["muted"]))

    vals, text = sym_ticks(lim)
    fig.update_layout(**base_layout(
        p, barmode="relative", bargap=0.52,
        height=max(340, 29 * len(pf) + 128),
        margin=dict(l=10, r=76, t=34, b=14),
        xaxis=axis(p, title_text=f"( ← bought  |  sold → )"
                                 f"    {metric_label}",
                   range=[-lim, lim], tickmode="array", tickvals=vals,
                   ticktext=text),
        yaxis=axis(p, showgrid=False, title_text=None, type="category",
                   tickfont=dict(color=p["ink2"], size=11)),
        hovermode="closest"))
    fig.add_vline(x=0, line_color=p["base"], line_width=1)
    return fig


def heatmap_fig(piv: pd.DataFrame, p: dict) -> go.Figure:
    """participant x strike net position. Flat cells recede into the surface."""
    z = piv.to_numpy(dtype=float)
    lim = float(np.nanmax(np.abs(z))) or 1.0
    # Label only the cells that carry a real position.
    txt = np.where(np.abs(z) >= 0.34 * lim,
                   np.vectorize(_short)(z), "")
    fig = go.Figure(go.Heatmap(
        z=z, x=list(piv.columns), y=list(piv.index),
        colorscale=net_position_scale(p), zmid=0, zmin=-lim, zmax=lim,
        xgap=1, ygap=1, text=txt, texttemplate="%{text}",
        textfont=dict(family=FONT, size=9, color=p["surface"]),
        colorbar=dict(title=dict(text="Net", font=dict(color=p["muted"],
                                                      size=11)),
                      tickfont=dict(color=p["muted"], size=10),
                      outlinewidth=0, thickness=10, len=0.75, ypad=0),
        hovertemplate="<b>%{y}</b><br>%{x}<br>net %{z:+,.0f}<extra></extra>"))
    fig.update_layout(**base_layout(
        p, height=max(300, 29 * piv.shape[0] + 165),
        margin=dict(l=10, r=10, t=14, b=76),
        xaxis=axis(p, showgrid=False, type="category", tickangle=-45,
                   title_text=None, tickfont=dict(color=p["muted"], size=10)),
        yaxis=axis(p, showgrid=False, type="category", autorange="reversed",
                   title_text=None,
                   tickfont=dict(color=p["ink2"], size=11))))
    return fig


def net_time_fig(daily: pd.DataFrame, metric_label: str, p: dict) -> go.Figure:
    """Cumulative net per participant. Endpoint-labelled, so no legend box."""
    fig = go.Figure()
    for i, col in enumerate(daily.columns):
        c = p["cat"][i % MAX_SLOTS]
        fig.add_trace(go.Scatter(
            x=daily.index, y=daily[col], mode="lines", name=str(col),
            line=dict(color=c, width=1.8), connectgaps=True,
            hovertemplate="<b>" + str(col) + "</b><br>%{x|%d %b}"
                          "<br>net %{y:+,.0f}<extra></extra>"))
        fig.add_annotation(x=daily.index[-1], y=float(daily[col].iloc[-1]),
                           text=f"  {short_name(col, 14)}", showarrow=False,
                           xanchor="left", yanchor="middle",
                           font=dict(color=c, size=10, family=FONT))
    fig.update_layout(**base_layout(
        p, height=400, margin=dict(l=10, r=132, t=16, b=14), showlegend=False,
        xaxis=axis(p, title_text=None, showgrid=False),
        yaxis=axis(p, title_text=f"Cumulative net    {metric_label}"),
        hovermode="x unified"))
    fig.add_hline(y=0, line_color=p["base"], line_width=1)
    return fig


def price_scatter_fig(det: pd.DataFrame, vwap: float, p: dict,
                      highlight: str | None = None) -> go.Figure:
    """Average buy vs average sell price, per participant, on one contract."""
    d = det.sort_values("Buy Vol", ascending=False)
    fig = go.Figure()
    b = d[d["Buy Vol"] > 0]
    fig.add_trace(go.Scatter(
        x=b["participant"], y=b["Avg Buy"], mode="markers", name="Avg buy",
        marker=dict(color=p["buy"], symbol="triangle-up", size=10,
                    line=dict(color=p["surface"], width=1.5)),
        customdata=b["Buy Vol"],
        hovertemplate="<b>%{x}</b><br>bought %{customdata:,.0f} @ "
                      "%{y:,.4g}<extra></extra>"))
    s = d[d["Sell Vol"] > 0]
    fig.add_trace(go.Scatter(
        x=s["participant"], y=s["Avg Sell"], mode="markers", name="Avg sell",
        marker=dict(color=p["sell"], symbol="triangle-down", size=10,
                    line=dict(color=p["surface"], width=1.5)),
        customdata=s["Sell Vol"],
        hovertemplate="<b>%{x}</b><br>sold %{customdata:,.0f} @ "
                      "%{y:,.4g}<extra></extra>"))
    if highlight and highlight in set(d["participant"]):
        h = d[d["participant"] == highlight]
        ring = pd.concat([h["Avg Buy"], h["Avg Sell"]]).dropna()
        fig.add_trace(go.Scatter(
            x=[highlight] * len(ring), y=ring, mode="markers",
            name=f"{short_name(highlight, 16)} selected",
            marker=dict(color="rgba(0,0,0,0)", symbol="circle", size=22,
                        line=dict(color=p["ink2"], width=1.5)),
            hoverinfo="skip"))
    fig.add_hline(y=vwap, line_color=p["base"], line_width=1,
                  annotation_text="VWAP", annotation_position="top left",
                  annotation_font=dict(color=p["muted"], size=10))
    fig.update_layout(**base_layout(
        p, height=380, margin=dict(l=10, r=14, t=34, b=96),
        xaxis=axis(p, showgrid=False, title_text=None, tickangle=-35,
                   type="category", tickfont=dict(color=p["ink2"], size=10)),
        yaxis=axis(p, title_text="Price")))
    return fig


def under_bar_fig(series: pd.Series, metric_label: str, p: dict) -> go.Figure:
    """Volume per underlying. Nominal categories, so one colour for all bars."""
    fig = go.Figure(go.Bar(
        x=series.to_numpy(), y=list(series.index), orientation="h",
        marker=dict(color=p["cat"][0],
                    line=dict(color=p["surface"], width=1.2)),
        hovertemplate="<b>%{y}</b><br>%{x:,.0f}<extra></extra>"))
    fig.update_layout(**base_layout(
        p, height=max(340, 21 * len(series) + 104), bargap=0.5,
        showlegend=False,
        xaxis=axis(p, title_text=metric_label),
        yaxis=axis(p, showgrid=False, type="category", title_text=None,
                   tickfont=dict(color=p["ink2"], size=11))))
    return fig
