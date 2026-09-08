"""VIOP options flow - cumulative dashboard.

    streamlit run viop_opt_app.py

Cumulative version of the single-day butterfly in matriks.py. Data can come
from browser-uploaded ViopDefter/cache files or from server-side folders. It
keeps options only (TM_ contracts included) and answers three questions:

  * Strike Ladder    - where volume and net position landed, by strike
  * Participant Flow - who bought, who sold, and what they are left holding
  * Market Overview  - the same two questions across every underlying at once

Clicking a bar in any of them opens the strike-detail panel for that contract.

    viop_opt_core.py    data: parse, cache, aggregate  (no streamlit)
    viop_opt_charts.py  figures                        (no streamlit)
    viop_opt_app.py     this file: filters and layout
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

import viop_opt_charts as V
import viop_opt_core as C

st.set_page_config(page_title="VIOP Options Flow", layout="wide",
                   initial_sidebar_state="expanded")


# ------------------------------------------------------------------ config

def configured(key: str, fallback) -> str:
    """A path from .streamlit/secrets.toml if there is one, else the default."""
    try:
        return str(st.secrets[key])
    except Exception:          # no secrets file, or no such key
        return str(fallback)


# ------------------------------------------------------------------ loading

@st.cache_data(ttl=30, show_spinner=False)
def cached_files(data_dir: str, cache_dir: str) -> pd.DataFrame:
    """What is loadable from raw files or <cache root>/daily right now."""
    return C.available_dates(data_dir, cache_dir)


@st.cache_data(show_spinner=False)
def cached_load(data_dir: str, cache_dir: str, start, end,
                token: int, fingerprint: tuple = ()) -> pd.DataFrame:
    """Load a local/server-side date range using the normal cache-first path."""
    bar = st.progress(0.0, text="Loading option trades...")

    def prog(i, n, d):
        label = f"{d.date()}" if d is not None else "done"
        bar.progress(i / max(n, 1), text=f"Loading option trades... {label}")

    df = C.load_range(start, end, data_dir=data_dir, cache_dir=cache_dir,
                      progress=prog)
    bar.empty()
    return df


def session_uploaded_file(uploaded) -> pd.DataFrame:
    """Parse one upload once per browser session, not in Streamlit's global cache.

    Uploaded trade data is member-identified, so keeping this cache in
    ``st.session_state`` avoids sharing uploaded DataFrames across user
    sessions while still preventing expensive raw-CSV reparsing on every
    chart interaction.
    """
    file_id = getattr(uploaded, "file_id", None)
    key = f"{file_id or 'no-id'}|{uploaded.name}|{uploaded.size}"
    store = st.session_state.setdefault("_uploaded_option_frames", {})
    if key not in store:
        store[key] = C.load_uploaded_bytes(uploaded.name, uploaded.getvalue())
    return store[key]


def uploaded_days(files):
    """Load uploads and resolve overlaps to exactly one source per trade date.

    A monthly parquet may contain many dates. If uploads overlap, the order of
    preference is daily parquet > monthly parquet > raw daily CSV. This avoids
    double counting while still allowing a daily file to override one day of a
    monthly bundle.
    """
    chosen = {}
    rejected = []
    errors = []
    valid = []
    for f in files:
        info = C.uploaded_file_info(f.name)
        if info is None:
            rejected.append(f.name)
        else:
            valid.append((f, info[0]))

    bar = st.progress(0.0, text="Loading uploaded option data...")
    for i, (f, kind) in enumerate(valid, start=1):
        bar.progress((i - 1) / max(len(valid), 1), text=f"Loading {f.name}...")
        try:
            frame = session_uploaded_file(f)
        except Exception as exc:
            errors.append(f"{f.name}: {exc}")
            continue
        if frame is None or frame.empty:
            continue

        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        priority = C.upload_priority(kind)
        for day, part in frame.groupby("date", sort=True):
            day = pd.Timestamp(day).normalize()
            old = chosen.get(day)
            if old is None or priority > old[0]:
                chosen[day] = (priority, kind, f.name,
                               part.reset_index(drop=True))

    bar.progress(1.0, text="Loading uploaded option data... done")
    bar.empty()
    return chosen, rejected, errors


# ------------------------------------------------------------------ sidebar

st.sidebar.header("Data")
source_mode = st.sidebar.radio(
    "Source",
    ["Upload files", "Local folders"],
    horizontal=True,
    help="Upload files works on Streamlit Cloud. Local folders are paths on "
         "the machine that is actually running Streamlit.")

if source_mode == "Upload files":
    uploads = st.sidebar.file_uploader(
        "Monthly cache, daily cache, or ViopDefter CSV",
        type=["csv", "parquet"],
        accept_multiple_files=True,
        help=f"Best for cloud use: upload opt_v{C.CACHE_VERSION}_YYYYMM.parquet monthly bundles. "
             "Daily opt_v*_YYYYMMDD.parquet caches and raw "
             "ViopDefterYYYYMMDD.csv files also work.")

    if not uploads:
        st.title("VIOP Options Flow")
        st.info(
            f"Upload one or more monthly `opt_v{C.CACHE_VERSION}_YYYYMM.parquet` files from "
            "the sidebar. Daily cache files and raw ViopDefter CSVs are also "
            "accepted. Monthly files are the recommended cloud workflow: one "
            "small upload contains every option trade date in that month.")
        st.stop()

    chosen, rejected, errors = uploaded_days(uploads)
    if rejected:
        st.sidebar.warning(
            "Ignored files with unsupported names: " + ", ".join(rejected))
    if errors:
        st.sidebar.error("Some files could not be loaded.")
        with st.sidebar.expander("Upload errors"):
            for msg in errors:
                st.write(msg)
    if not chosen:
        st.title("VIOP Options Flow")
        st.error(
            "No option trades were loaded. Recognized names are "
            "`opt_vN_YYYYMM.parquet`, `opt_vN_YYYYMMDD.parquet`, and "
            "`ViopDefterYYYYMMDD.csv`.")
        st.stop()

    all_dates = [d.date() for d in sorted(chosen)]
    if len(all_dates) > 1:
        start_d, end_d = st.sidebar.select_slider(
            "Trade dates (cumulative)", options=all_dates,
            value=(all_dates[0], all_dates[-1]))
    else:
        start_d = end_d = all_dates[0]
        st.sidebar.caption(f"Only one trade date: {start_d}")

    theme = st.sidebar.selectbox("Theme", list(V.PALETTES))
    P = V.PALETTES[theme]

    selected_days = [
        d for d in sorted(chosen)
        if pd.Timestamp(start_d) <= d <= pd.Timestamp(end_d)
    ]
    parts = [chosen[d][3] for d in selected_days]
    df_all = pd.concat(parts, ignore_index=True)

    used_files = {chosen[d][2] for d in selected_days}
    monthly_files = {
        chosen[d][2] for d in selected_days
        if chosen[d][1] == "parquet_monthly"
    }
    label = (f"{len(selected_days)} trade day(s) from {len(used_files)} "
             f"uploaded file(s)")
    if monthly_files:
        label += f" · {len(monthly_files)} monthly bundle(s)"
    st.sidebar.caption(label + f" · {len(df_all):,} option trades")

else:
    cache_dir = st.sidebar.text_input(
        "Cache root (shared)", value=configured("cache_dir", C.CACHE_DIR))
    data_dir = st.sidebar.text_input(
        "Raw ViopDefter folder", value=configured("data_dir", C.DATA_DIR),
        help="Only needed for dates that are not cached yet. Cached dates "
             "load without it.")
    files = cached_files(data_dir, cache_dir)

    if files.empty:
        st.sidebar.error("Nothing loadable from either folder.")
        st.title("VIOP Options Flow")
        st.warning(f"No daily cached parquet in `{C.daily_cache_dir(cache_dir)}` and no "
                   f"ViopDefterYYYYMMDD.csv in `{data_dir}`.")
        st.markdown(
            "Switch **Source** to **Upload files**, or point this server at "
            "folders it can actually reach. A browser user's own `D:` drive "
            "is never visible to a remotely hosted Streamlit process.")
        st.stop()

    n_cached = int(files["cached"].sum())
    n_raw_only = int((~files["cached"] & files["path"].notna()).sum())
    st.sidebar.caption(
        f"{len(files)} dates loadable — {n_cached} from cache"
        + (f", {n_raw_only} still to parse" if n_raw_only else ""))

    all_dates = [d.date() for d in files["date"]]
    if len(all_dates) > 1:
        start_d, end_d = st.sidebar.select_slider(
            "Trade dates (cumulative)", options=all_dates,
            value=(all_dates[0], all_dates[-1]))
    else:
        start_d = end_d = all_dates[0]
        st.sidebar.caption(f"Only one file: {start_d}")

    st.session_state.setdefault("cache_token", 0)
    c1, c2 = st.sidebar.columns(2)
    if c1.button("Reload", width="stretch",
                 help="Re-scan both folders and parse any date not yet cached"):
        st.session_state["cache_token"] += 1
        cached_files.clear()
        cached_load.clear()
    theme = c2.selectbox("Theme", list(V.PALETTES),
                         label_visibility="collapsed")
    P = V.PALETTES[theme]

    df_all = cached_load(
        data_dir, cache_dir, pd.Timestamp(start_d), pd.Timestamp(end_d),
        st.session_state["cache_token"], (len(files), n_cached))
    if df_all.empty:
        st.warning("No option trades in the selected range.")
        st.stop()

st.sidebar.markdown("---")
st.sidebar.header("Scope")
metric = st.sidebar.radio("Measure", list(C.METRICS), horizontal=True,
                          format_func=lambda k: C.METRICS[k])
MLABEL = C.METRICS[metric]

unders = (df_all.groupby("under", observed=True)["lot"].sum()
          .sort_values(ascending=False))
sel_under = st.sidebar.selectbox(
    "Underlying", list(unders.index),
    format_func=lambda u: f"{u}  ({unders[u]:,.0f} lots)")

df_u = df_all[df_all["under"] == sel_under]
exp = C.expiry_options(df_u)
exp_labels = dict(zip(exp["expiry_ym"], exp["label"]))
sel_exp = st.sidebar.selectbox("Expiry", list(exp["expiry_ym"]),
                               format_func=lambda y: exp_labels[y])
st.sidebar.caption(
    "TM contracts fold into the calendar month of their real expiry date; "
    "the exact dates are shown in the picker and in every table.")

scope = df_u[df_u["expiry_ym"] == sel_exp].copy()
scope_key = f"{sel_under}|{sel_exp}"

n_colour = st.sidebar.slider(
    "Participants coloured", 4, V.MAX_SLOTS, V.MAX_SLOTS,
    help=f"{V.MAX_SLOTS} validated hues; everyone past that folds into "
         f"'Other'. Hues are never reused.")
n_rows = st.sidebar.slider("Participant rows", 8, 40, 24,
                           help="Rows on the who-bought-who-sold charts. "
                                "These are labelled per row, so they are not "
                                "limited by the number of hues.")
min_share = st.sidebar.slider(
    "Hide strikes quieter than", 0, 10, 2, 1, format="%d%%",
    help="Share of the busiest strike row. Over a long range the ends of the "
         "strike grid are hairlines that only cost height.") / 100

# Colour follows the participant for as long as the scope holds, so toggling
# measure / mode / a click never repaints the bars under the reader.
scope_order = list(C.rank_participants(scope, metric).head(n_colour).index)
CMAP = V.color_map(scope_order, P)

st.sidebar.markdown("---")
st.sidebar.caption(
    f"{len(df_all):,} option trades · {df_all['date'].nunique()} trade days · "
    f"{int(df_all['tm'].sum()):,} tailor-made")


# ------------------------------------------------------------------ helpers

def click_point(event):
    """First selected point of a plotly chart, or None."""
    try:
        pts = event["selection"]["points"]
    except (TypeError, KeyError):
        return None
    return pts[0] if pts else None


def show(fig, key: str):
    return st.plotly_chart(fig, width="stretch", theme=None, key=key)


def show_clickable(fig, key: str):
    ev = st.plotly_chart(fig, width="stretch", theme=None, key=key,
                         on_select="rerun")
    return click_point(ev)


def net_note(container=st):
    container.caption(
        f"Net = bought − sold **inside the selected date range**. The files "
        f"here start {all_dates[0]}, so this is net flow over the window, not "
        f"exchange open interest.")


def strike_detail_panel(frame: pd.DataFrame, strike: float, cp: str,
                        title: str, key: str, highlight: str | None = None):
    """Picture-2 panel: average prices and volumes, per participant."""
    det = C.strike_details(frame, strike, cp)
    if det.empty:
        st.info("No trades on that contract in this scope.")
        return

    sub = frame[(frame["strike"] == strike) & (frame["cp"] == cp)]
    vwap = (sub["price"] * sub["lot"]).sum() / sub["lot"].sum()
    st.markdown(f"#### {title}")
    k = st.columns(4)
    k[0].metric("Lots traded", f"{sub['lot'].sum():,.0f}")
    k[1].metric("Trades", f"{len(sub):,}")
    k[2].metric("VWAP", f"{vwap:,.4g}")
    k[3].metric("Participants", f"{det['participant'].nunique():,}")

    left, right = st.columns([1.1, 1])
    with left:
        st.caption("Average price per participant")
        show(V.price_scatter_fig(det, vwap, P, highlight), f"px_{key}")
    with right:
        st.caption("Volume, average price and net")
        table = det.copy()
        if highlight:
            table.insert(0, "Sel", np.where(
                table["participant"] == highlight, "●", ""))
        bmax = max(float(det["Buy Vol"].max()), 1.0)
        smax = max(float(det["Sell Vol"].max()), 1.0)
        st.dataframe(
            table, height=380, width="stretch", hide_index=True,
            column_config={
                "Sel": st.column_config.TextColumn(" ", width="small"),
                "participant": st.column_config.TextColumn("Participant"),
                "Buy Vol": st.column_config.ProgressColumn(
                    "Buy vol", format="%d", min_value=0, max_value=bmax),
                "Sell Vol": st.column_config.ProgressColumn(
                    "Sell vol", format="%d", min_value=0, max_value=smax),
                "Avg Buy": st.column_config.NumberColumn("Avg buy",
                                                         format="%.4g"),
                "Avg Sell": st.column_config.NumberColumn("Avg sell",
                                                          format="%.4g"),
                "Net": st.column_config.NumberColumn("Net", format="%d"),
            })


def largest_contract(frame: pd.DataFrame, participant: str):
    """The single contract a participant traded most of, within `frame`."""
    mine = frame[(frame["buyer"] == participant) |
                 (frame["seller"] == participant)]
    if mine.empty:
        return None
    key = ["under", "expiry_ym", "strike", "cp"]
    return dict(zip(key, mine.groupby(key, observed=True)["lot"].sum().idxmax()))


def ladder_block(frame: pd.DataFrame, mode: str, key: str):
    lad, rows = C.strike_ladder(frame, metric, mode, scope_order, min_share)
    if lad.empty:
        st.info("Nothing to plot.")
        return None
    pt = show_clickable(V.ladder_fig(lad, rows, scope_order, CMAP, MLABEL,
                                     mode, P), key)
    with st.expander("Table"):
        st.dataframe(
            lad[["row", "strike", "cp", "group", "side", "magnitude"]]
            .rename(columns={"row": "Strike / C-P", "group": "Participant",
                             "side": "Side", "magnitude": MLABEL}),
            width="stretch", hide_index=True, height=280)
    return pt


def flow_block(frame: pd.DataFrame, key: str, top: int = 24):
    pf = C.participant_flow(frame, metric)
    if min_share > 0 and len(pf):
        # Drop the long tail of members with a rounding error of turnover --
        # otherwise most rows are an empty axis with a marker on zero.
        pf = pf[pf["turnover"] >= min_share * pf["turnover"].max()]
    pf = pf.head(top)
    if pf.empty:
        st.info("Nothing to plot.")
        return None
    pt = show_clickable(V.flow_fig(pf, MLABEL, P), key)
    with st.expander("Table"):
        st.dataframe(
            pf[["participant", "buy", "sell", "net", "buy_vwap", "sell_vwap"]]
            .rename(columns={"participant": "Participant", "buy": "Bought",
                             "sell": "Sold", "net": "Net",
                             "buy_vwap": "Buy vwap",
                             "sell_vwap": "Sell vwap"}),
            width="stretch", hide_index=True, height=280)
    return pt


# --------------------------------------------------------------------- app

st.title("VIOP Options Flow")
st.caption(f"{start_d} → {end_d}  ·  options only (O_ and TM_O_)  ·  "
           f"cumulative over {df_all['date'].nunique()} trade days")

tab_lad, tab_flow, tab_mkt = st.tabs(
    ["Strike Ladder", "Participant Flow", "Market Overview"])

# ---------------------------------------------------------- strike ladder
with tab_lad:
    head = st.columns([2, 3])
    lad_mode = head[0].radio(
        "Mode", ["gross", "net"], horizontal=True, key="lad_mode",
        format_func=lambda m: ("Gross (buy vs sell)" if m == "gross"
                               else "Net (position left over)"))
    head[1].markdown(
        f"### {sel_under} · {exp_labels[sel_exp]}\n"
        f"{scope['lot'].sum():,.0f} lots · {len(scope):,} trades · "
        f"{int(scope['tm'].sum()):,} tailor-made")
    if lad_mode == "net":
        net_note(head[1])

    pt = ladder_block(scope, lad_mode,
                      key=f"lad_{scope_key}_{lad_mode}_{metric}_{min_share}")
    if pt is not None and pt.get("y"):
        st.session_state["lad_sel"] = pt["y"]

    sel_row = st.session_state.get("lad_sel")
    valid_rows = {C.row_label(s, c)
                  for s, c in zip(scope["strike"], scope["cp"])}
    st.markdown("---")
    if sel_row in valid_rows:
        k_strike, k_cp = C.split_row_label(sel_row)
        strike_detail_panel(
            scope, k_strike, k_cp,
            f"{sel_under} {exp_labels[sel_exp]} · {k_strike:g} {k_cp}",
            key=f"lad_{scope_key}_{k_strike}_{k_cp}")
    else:
        st.info("Click any bar above to open the strike detail panel.")

    st.markdown("---")
    st.markdown("#### Net position built up over the range")
    net_note()
    daily = C.daily_net(scope, metric)
    if len(daily) > 1:
        show(V.net_time_fig(daily, MLABEL, P), f"nt_{scope_key}_{metric}")
    else:
        st.info("Needs at least two trade days in the range.")

# ------------------------------------------------------- participant flow
with tab_flow:
    all_exp = st.checkbox(f"All expiries of {sel_under}", value=False,
                          key="flow_all_exp")
    fscope = df_u if all_exp else scope
    label = (f"{sel_under} · all expiries" if all_exp
             else f"{sel_under} · {exp_labels[sel_exp]}")
    st.markdown(f"### Who bought, who sold — {label}")
    net_note()

    pt = flow_block(fscope, key=f"flow_{scope_key}_{all_exp}_{metric}",
                    top=n_rows)
    if pt is not None and pt.get("y"):
        st.session_state["flow_sel"] = pt["y"]

    who = st.session_state.get("flow_sel")
    st.markdown("---")
    if who and who in set(fscope["buyer"]) | set(fscope["seller"]):
        mine = fscope[(fscope["buyer"] == who) | (fscope["seller"] == who)]
        rows = (mine.groupby(["expiry_ym", "strike", "cp"], observed=True)
                ["lot"].sum().sort_values(ascending=False).reset_index())
        choices = [f"{exp_labels.get(r.expiry_ym, r.expiry_ym)} · "
                   f"{r.strike:g} {r.cp}  ({r.lot:,.0f} lots)"
                   for r in rows.itertuples(index=False)]
        pick = st.selectbox(
            f"{who} — contract to inspect (their biggest first)",
            range(len(choices)), format_func=lambda i: choices[i],
            key=f"pick_{who}_{scope_key}_{all_exp}")
        r = rows.iloc[pick]
        ym = str(r["expiry_ym"])
        strike_detail_panel(
            df_u[df_u["expiry_ym"] == ym], float(r["strike"]), str(r["cp"]),
            f"{sel_under} {exp_labels.get(ym, ym)} · "
            f"{r['strike']:g} {r['cp']}",
            key=f"flow_{scope_key}_{ym}_{r['strike']}_{r['cp']}",
            highlight=who)
    else:
        st.info("Click a participant's bar above to open their strike "
                "detail panel.")

    st.markdown("---")
    st.markdown("#### Net position by participant and strike")
    st.caption("Cool = net long, warm = net short. The fastest way to see who "
               "carries which side of which strike.")
    hm_order = list(C.rank_participants(fscope, metric)
                    .head(min(n_rows, 20)).index)
    piv = C.participant_strike_net(fscope, metric, hm_order, min_share)
    if piv.empty or piv.shape[1] == 0:
        st.info("Nothing to plot.")
    else:
        show(V.heatmap_fig(piv, P), f"hm_{scope_key}_{all_exp}_{metric}")

# --------------------------------------------------------- market overview
with tab_mkt:
    lots = df_all["lot"].sum()
    cv = df_all.loc[df_all["cp"] == "C", "lot"].sum()
    pv = df_all.loc[df_all["cp"] == "P", "lot"].sum()
    k = st.columns(6)
    k[0].metric("Option trades", f"{len(df_all):,}")
    k[1].metric("Lots", f"{lots:,.0f}")
    k[2].metric("Call lots", f"{cv:,.0f}")
    k[3].metric("Put lots", f"{pv:,.0f}")
    k[4].metric("Put / Call", f"{(pv / cv if cv else 0):.2f}")
    k[5].metric("Tailor-made lots",
                f"{df_all.loc[df_all['tm'], 'lot'].sum():,.0f}")

    st.markdown("---")
    st.markdown("### Who bought, who sold — whole market")
    net_note()
    pt = flow_block(df_all, key=f"flow_mkt_{metric}", top=n_rows)
    if pt is not None and pt.get("y"):
        st.session_state["mkt_sel"] = pt["y"]

    who = st.session_state.get("mkt_sel")
    if who:
        big = largest_contract(df_all, who)
        if big:
            frame = df_all[(df_all["under"] == big["under"]) &
                           (df_all["expiry_ym"] == big["expiry_ym"])]
            st.markdown("---")
            st.caption(f"{who}'s largest single contract across the market")
            strike_detail_panel(
                frame, float(big["strike"]), str(big["cp"]),
                f"{big['under']} {big['expiry_ym']} · "
                f"{big['strike']:g} {big['cp']}",
                key=f"mkt_{who}_{big['strike']}_{big['cp']}", highlight=who)

    st.markdown("---")
    left, right = st.columns(2)
    with left:
        st.markdown("### Most active contracts")
        top = (df_all.groupby(["under", "expiry_ym", "strike", "cp"],
                              observed=True)
               .agg(lots=("lot", "sum"), trades=("lot", "size"),
                    premium=("premium", "sum"))
               .sort_values("lots", ascending=False).head(25).reset_index())
        top["contract"] = (top["under"].astype(str) + " " +
                           top["expiry_ym"].astype(str) + " " +
                           top["strike"].map(lambda s: f"{s:g}") + " " +
                           top["cp"].astype(str))
        st.dataframe(
            top[["contract", "lots", "trades", "premium"]].rename(
                columns={"contract": "Contract", "lots": "Lots",
                         "trades": "Trades", "premium": "Premium"}),
            width="stretch", hide_index=True, height=560)
    with right:
        st.markdown(f"### {MLABEL} by underlying")
        u = (df_all.groupby("under", observed=True)[metric].sum()
             .sort_values(ascending=True))
        show(V.under_bar_fig(u, MLABEL, P), f"mkt_under_{metric}")
