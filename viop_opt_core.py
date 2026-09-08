"""VIOP options flow - data layer.

Reads BIST VIOP time & sales files (``ViopDefterYYYYMMDD.csv``), keeps the
option trades only (``O_`` / ``TM_O_``), parses the contract code and caches
the result as one parquet file per trade date.

Only ~0.3% of the rows in a daily file are options (36k out of 12M across the
15 files in bistzamansatis), so the loader pre-filters raw bytes line by line
before handing anything to pandas -- that turns a ~60s parse into a few
seconds, and the parquet cache makes every later load instant.

No streamlit in here on purpose: everything is importable and testable alone.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Paths come from the environment so nothing site-specific is baked into the
# source: point VIOP_DATA_DIR at the folder holding ViopDefterYYYYMMDD.csv and
# VIOP_CACHE_DIR at the shared parquet cache (a NAS share is fine). Both
# defaults are repo-relative, so a fresh clone runs without editing code.
DATA_DIR = Path(os.environ.get("VIOP_DATA_DIR", "data"))
CACHE_DIR = Path(os.environ.get("VIOP_CACHE_DIR", "opt_cache"))
CACHE_VERSION = 3  # bump to invalidate every cached parquet

FILE_RE = re.compile(r"^ViopDefter(?P<d>\d{8})\.csv$", re.IGNORECASE)
CACHE_RE = re.compile(r"^opt_v(?P<v>\d+)_(?P<d>\d{8})\.parquet$", re.IGNORECASE)

# Contract codes, e.g.
#   O_XU030E1026P17000.00      standard, MMYY expiry -> month-end
#   TM_O_AKBNKE301026P64.00    tailor-made, DDMMYY expiry -> 30 Oct 2026
#
# `root` is greedy on purpose. It is what makes TM_O_SISEE300926P41.01 parse
# as root=SISE and not root=SIS -- with a lazy root, "SIS" + "E" + "300926"
# is also a legal parse and wins. Validated: all 1426 distinct option codes
# in bistzamansatis parse, into 28 known roots, zero failures.
OPT_RE = re.compile(
    r"^(?P<tm>TM_)?O_(?P<root>[A-Z0-9]+)(?P<style>[EA])"
    r"(?P<date>\d{6}|\d{4})(?P<cp>[CP])(?P<strike>\d+(?:\.\d+)?)$"
)

# Roots quoted x1000 in the contract code and on the tape:
# O_USDTRYKE0926C50000 is strike 50.000 against a 49.37 future, and its
# 257.9 premium is 0.2579. Strike, price and TL all need the same divisor.
SCALED_ROOTS = {"USDTRYK": 1000.0}

# Contract-code root -> underlying as people say it.
ROOT_ALIASES = {"USDTRYK": "USDTRY"}

RAW_COLS = ["SAAT", "SEMBOL", "FIYAT", "LOT", "TL", "ALAN", "SATAN"]

OUT_COLS = [
    "date", "time", "symbol", "tm", "under", "cp", "strike",
    "expiry_ym", "expiry_exact", "lot", "price", "tl", "buyer", "seller",
]

METRICS = {"lot": "Lots", "tl": "Premium (TL)"}


# ---------------------------------------------------------------- discovery

def _listdir(d: Path | str) -> list[Path]:
    """Directory contents, empty if it is missing or unreachable (dead NAS)."""
    d = Path(d)
    try:
        return list(d.iterdir()) if d.is_dir() else []
    except OSError:
        return []


def discover_files(data_dir: Path | str = DATA_DIR) -> pd.DataFrame:
    """Trade dates with a raw ViopDefter file on disk, oldest first."""
    rows = []
    for p in _listdir(data_dir):
        m = FILE_RE.match(p.name)
        if m:
            try:
                mb = p.stat().st_size / 1e6
            except OSError:
                continue
            rows.append({"date": pd.Timestamp(m.group("d")), "path": p,
                         "mb": mb})
    out = pd.DataFrame(rows, columns=["date", "path", "mb"])
    return out.sort_values("date").reset_index(drop=True)


def discover_cache(cache_dir: Path | str = CACHE_DIR) -> pd.DataFrame:
    """Trade dates already parsed into the cache, for the current version."""
    rows = []
    for p in _listdir(cache_dir):
        m = CACHE_RE.match(p.name)
        if m and int(m.group("v")) == CACHE_VERSION:
            rows.append({"date": pd.Timestamp(m.group("d")), "cache": p})
    out = pd.DataFrame(rows, columns=["date", "cache"])
    return out.sort_values("date").reset_index(drop=True)


def available_dates(data_dir: Path | str = DATA_DIR,
                    cache_dir: Path | str = CACHE_DIR) -> pd.DataFrame:
    """Every trade date that can be loaded, from a raw file *or* the cache.

    A date whose parquet is in the cache needs no raw CSV, so a machine with
    only the shared cache folder can run the whole app. Columns: date, path
    (NaN when the raw file is absent), mb, cached.
    """
    raw = discover_files(data_dir).set_index("date")
    hit = discover_cache(cache_dir).set_index("date")
    idx = raw.index.union(hit.index)
    out = pd.DataFrame({
        "date": idx,
        "path": raw["path"].reindex(idx).to_numpy(),
        "mb": raw["mb"].reindex(idx).to_numpy(),
        "cached": idx.isin(hit.index),
    })
    return out.sort_values("date").reset_index(drop=True)


# ------------------------------------------------------------------ reading

def _option_lines(path: Path, chunk_bytes: int = 1 << 23):
    """Yield raw CSV lines whose SEMBOL field is an option contract.

    Chunked so memory stays bounded on the 130MB files. ';TM_O_' has to be
    tested separately -- it does not contain ';O_'. The header is yielded
    last so callers can prepend it.
    """
    with open(path, "rb") as fh:
        header = fh.readline()
        tail = b""
        while True:
            block = fh.read(chunk_bytes)
            if not block:
                break
            block = tail + block
            lines = block.split(b"\n")
            tail = lines.pop()
            for ln in lines:
                if b";O_" in ln or b";TM_O_" in ln:
                    yield ln
        if b";O_" in tail or b";TM_O_" in tail:
            yield tail
    yield header


def read_raw_options(path: Path) -> pd.DataFrame:
    """Option rows of one daily file, still unparsed."""
    lines = list(_option_lines(Path(path)))
    header = lines.pop()  # _option_lines yields it last
    if not lines:
        return pd.DataFrame(columns=RAW_COLS)
    buf = io.BytesIO(header + b"\n".join(lines) + b"\n")
    return pd.read_csv(
        buf, sep=";", engine="c", low_memory=False,
        usecols=lambda c: c in RAW_COLS,
        dtype={"SAAT": "string", "SEMBOL": "string",
               "ALAN": "string", "SATAN": "string"},
    )


# ------------------------------------------------------------------ parsing

def parse_options(raw: pd.DataFrame, trade_date) -> pd.DataFrame:
    """Explode the contract code into underlying / expiry / strike / C-P."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=OUT_COLS)

    df = raw.copy()
    df["SEMBOL"] = df["SEMBOL"].astype("string").str.strip()
    ex = df["SEMBOL"].str.extract(OPT_RE)
    good = ex["root"].notna()
    df = df.loc[good].reset_index(drop=True)
    ex = ex.loc[good].reset_index(drop=True)
    if df.empty:
        return pd.DataFrame(columns=OUT_COLS)

    root = ex["root"].astype(str)
    scale = root.map(SCALED_ROOTS).astype("float64").fillna(1.0)

    dt = ex["date"].astype(str)
    is_tm = ex["tm"].notna().to_numpy()
    six = dt.str.len().eq(6).to_numpy()

    # TM contracts carry a real DDMMYY date; standard ones only MMYY, whose
    # settlement is the last business day of that month -- not derivable from
    # this file, so it stays NaT and both fold into the same YYYY-MM bucket.
    exact = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    if six.any():
        exact.loc[six] = pd.to_datetime(dt[six], format="%d%m%y", errors="coerce")

    ym = np.where(
        six,
        exact.dt.strftime("%Y-%m"),
        "20" + dt.str.slice(2, 4) + "-" + dt.str.slice(0, 2),
    )

    out = pd.DataFrame({
        "date": pd.Timestamp(trade_date),
        "time": df["SAAT"].astype("string"),
        "symbol": df["SEMBOL"],
        "tm": is_tm,
        "under": root.map(lambda r: ROOT_ALIASES.get(r, r)).astype("string"),
        "cp": ex["cp"].astype("string"),
        "strike": (pd.to_numeric(ex["strike"], errors="coerce")
                   .astype("float64") / scale),
        "expiry_ym": pd.Series(ym, index=df.index, dtype="string"),
        "expiry_exact": exact,
        "lot": pd.to_numeric(df["LOT"], errors="coerce"),
        "price": pd.to_numeric(df["FIYAT"], errors="coerce") / scale,
        "tl": pd.to_numeric(df["TL"], errors="coerce") / scale,
        "buyer": df["ALAN"].astype("string").str.strip(),
        "seller": df["SATAN"].astype("string").str.strip(),
    })

    out = out.dropna(subset=["strike", "cp", "lot", "expiry_ym"])
    if len(out) and (out["lot"] % 1 == 0).all():
        out["lot"] = out["lot"].astype("int64")
    return out[OUT_COLS].reset_index(drop=True)


# -------------------------------------------------------------------- cache

def cache_path(trade_date, cache_dir: Path | str = CACHE_DIR) -> Path:
    d = pd.Timestamp(trade_date).strftime("%Y%m%d")
    return Path(cache_dir) / f"opt_v{CACHE_VERSION}_{d}.parquet"


def ensure_cached(trade_date, src: Path | None = None,
                  cache_dir: Path | str = CACHE_DIR,
                  force: bool = False) -> Path:
    """Path to one day's parquet, parsing the raw file only if it is missing.

    `src=None` means "cache or nothing" -- used on machines that have the
    shared cache folder but not the 1.2GB of raw CSVs. `force` can only
    rebuild a date whose raw file is actually present.
    """
    dst = cache_path(trade_date, cache_dir)
    if dst.exists() and not (force and src is not None):
        return dst
    if src is None:
        raise FileNotFoundError(
            f"{pd.Timestamp(trade_date).date()} is not in the cache and its "
            f"raw ViopDefter file is not available")
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    frame = parse_options(read_raw_options(src), trade_date)
    frame.to_parquet(dst, index=False)
    return dst


def load_range(start, end, data_dir: Path | str = DATA_DIR,
               cache_dir: Path | str = CACHE_DIR,
               force: bool = False, progress=None) -> pd.DataFrame:
    """Every option trade between two trade dates, inclusive.

    Cache-first: a date already in `cache_dir` is read straight from parquet
    and never needs its raw file, so the app runs off the shared cache alone.
    """
    avail = available_dates(data_dir, cache_dir)
    if avail.empty:
        return pd.DataFrame(columns=OUT_COLS)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    want = avail[(avail["date"] >= start) & (avail["date"] <= end)]

    parts = []
    for i, row in enumerate(want.itertuples(index=False)):
        if progress is not None:
            progress(i, len(want), row.date)
        src = None if pd.isna(row.path) else row.path
        try:
            parts.append(pd.read_parquet(
                ensure_cached(row.date, src, cache_dir, force)))
        except (FileNotFoundError, OSError):
            continue
    if progress is not None:
        progress(len(want), len(want), None)
    if not parts:
        return pd.DataFrame(columns=OUT_COLS)
    return pd.concat(parts, ignore_index=True)


def build_cache(data_dir: Path | str = DATA_DIR,
                cache_dir: Path | str = CACHE_DIR,
                force: bool = False, log=print) -> pd.DataFrame:
    """Warm the cache for every raw file present. Use this to fill the NAS."""
    files = discover_files(data_dir)
    rows = []
    for row in files.itertuples(index=False):
        dst = cache_path(row.date, cache_dir)
        fresh = force or not dst.exists()
        ensure_cached(row.date, row.path, cache_dir, force)
        n = len(pd.read_parquet(dst))
        rows.append({"date": row.date, "trades": n,
                     "action": "built" if fresh else "kept"})
        log(f"  {row.date.date()}  {n:>7,} option trades  "
            f"({'built' if fresh else 'already cached'})")
    return pd.DataFrame(rows)


# -------------------------------------------------------------- aggregation

def long_format(df: pd.DataFrame, metric: str = "lot") -> pd.DataFrame:
    """One row per (trade, side): the buyer's leg and the seller's leg."""
    keep = ["date", "strike", "cp", "symbol", "tm", "price", "lot", "tl"]
    buy = df[keep + ["buyer"]].rename(columns={"buyer": "participant"})
    buy["side"] = "Buy"
    sell = df[keep + ["seller"]].rename(columns={"seller": "participant"})
    sell["side"] = "Sell"
    out = pd.concat([buy, sell], ignore_index=True)
    out["value"] = out[metric]
    return out


def rank_participants(df: pd.DataFrame, metric: str = "lot") -> pd.Series:
    """Participants by two-sided turnover, biggest first."""
    b = df.groupby("buyer", observed=True)[metric].sum()
    s = df.groupby("seller", observed=True)[metric].sum()
    total = b.add(s, fill_value=0).sort_values(ascending=False)
    total.index.name = "participant"
    return total


def _fmt_strike(s: float) -> str:
    return f"{s:g}"


def row_label(strike: float, cp: str) -> str:
    """The ladder's y-axis category. Exact, so clicks need no arithmetic."""
    return f"{_fmt_strike(strike)}  {cp}"


def split_row_label(label: str) -> tuple[float, str]:
    strike_txt, cp = label.rsplit("  ", 1)
    return float(strike_txt), cp.strip()


def _with_groups(lf: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """Fold everyone outside `order` into 'Other' -- hues are never cycled."""
    lf = lf.copy()
    lf["group"] = np.where(lf["participant"].isin(order),
                           lf["participant"], "Other")
    return lf


def strike_ladder(df: pd.DataFrame, metric: str = "lot", mode: str = "gross",
                  order: list[str] | None = None, min_share: float = 0.0):
    """Rows for the buy/sell ladder.

    Returns (frame, row_order). `x` is signed with buying to the left in both
    modes, so the two modes read the same way: gross Buy and net-long both
    sit left of zero. `min_share` drops strike rows quieter than that
    fraction of the busiest row -- over a long date range the tails of the
    strike grid are hairlines that only cost vertical space.
    """
    if df.empty:
        return pd.DataFrame(), []
    order = order or list(rank_participants(df, metric).head(12).index)
    lf = _with_groups(long_format(df, metric), order)

    if mode == "gross":
        out = (lf.groupby(["strike", "cp", "group", "side"], observed=True)["value"]
                 .sum().reset_index())
        out["x"] = np.where(out["side"].eq("Buy"), -out["value"], out["value"])
        out["magnitude"] = out["value"].abs()
    else:
        piv = (lf.pivot_table(index=["strike", "cp", "group"], columns="side",
                              values="value", aggfunc="sum", observed=True)
                 .reindex(columns=["Buy", "Sell"]).fillna(0.0))
        out = piv.reset_index()
        out["value"] = out["Buy"] - out["Sell"]
        out = out[out["value"] != 0].copy()
        out["side"] = np.where(out["value"] > 0, "Net long", "Net short")
        out["x"] = -out["value"]           # net long -> left, same as Buy
        out["magnitude"] = out["value"].abs()

    if out.empty:
        return out, []
    out["row"] = [row_label(s, c) for s, c in zip(out["strike"], out["cp"])]
    if min_share > 0:
        row_tot = out.groupby("row", observed=True)["magnitude"].sum()
        keep = row_tot[row_tot >= min_share * row_tot.max()].index
        out = out[out["row"].isin(set(keep))].copy()
        if out.empty:
            return out, []
    present = set(out["row"])
    strikes = np.sort(out["strike"].unique())
    row_order = [row_label(s, cp) for s in strikes for cp in ("C", "P")]
    return out, [r for r in row_order if r in present]


def participant_flow(df: pd.DataFrame, metric: str = "lot") -> pd.DataFrame:
    """Per participant: bought, sold, net, and the VWAP of each side."""
    cols = ["participant", "buy", "sell", "net", "buy_vwap", "sell_vwap",
            "turnover"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    b = df.groupby("buyer", observed=True).agg(buy=(metric, "sum"),
                                               buy_lot=("lot", "sum"),
                                               buy_tl=("tl", "sum"))
    s = df.groupby("seller", observed=True).agg(sell=(metric, "sum"),
                                                sell_lot=("lot", "sum"),
                                                sell_tl=("tl", "sum"))
    out = b.join(s, how="outer").fillna(0.0)
    out.index.name = "participant"
    out["net"] = out["buy"] - out["sell"]
    out["turnover"] = out["buy"] + out["sell"]
    out["buy_vwap"] = np.where(out["buy_lot"] > 0,
                               out["buy_tl"] / out["buy_lot"].replace(0, np.nan),
                               np.nan)
    out["sell_vwap"] = np.where(out["sell_lot"] > 0,
                                out["sell_tl"] / out["sell_lot"].replace(0, np.nan),
                                np.nan)
    return out.sort_values("turnover", ascending=False).reset_index()


def participant_strike_net(df: pd.DataFrame, metric: str = "lot",
                           order: list[str] | None = None,
                           min_share: float = 0.0) -> pd.DataFrame:
    """participant x '<strike> <C|P>' matrix of net position.

    `min_share` drops columns whose biggest net position is below that
    fraction of the biggest anywhere, matching the ladder's row filter.
    """
    if df.empty:
        return pd.DataFrame()
    order = order or list(rank_participants(df, metric).head(14).index)
    lf = long_format(df, metric)
    lf = lf[lf["participant"].isin(order)].copy()
    if lf.empty:
        return pd.DataFrame()
    lf["signed"] = np.where(lf["side"].eq("Buy"), lf["value"], -lf["value"])
    lf["row"] = [row_label(s, c) for s, c in zip(lf["strike"], lf["cp"])]
    piv = lf.pivot_table(index="participant", columns="row", values="signed",
                         aggfunc="sum", observed=True).fillna(0.0)
    strikes = np.sort(lf["strike"].unique())
    cols = [row_label(s, cp) for s in strikes for cp in ("C", "P")]
    piv = piv.reindex(columns=[c for c in cols if c in piv.columns])
    piv = piv.reindex([p for p in order if p in piv.index])
    if min_share > 0 and piv.shape[1]:
        peak = piv.abs().max()                       # per strike column
        piv = piv[[c for c in piv.columns
                   if peak[c] >= min_share * peak.max()]]
        rowpeak = piv.abs().max(axis=1)              # per participant row
        piv = piv.loc[rowpeak >= min_share * rowpeak.max()]
    return piv


def strike_details(df: pd.DataFrame, strike: float, cp: str) -> pd.DataFrame:
    """The picture-2 table: per participant volumes and average prices."""
    sub = df[(df["strike"] == strike) & (df["cp"] == cp)]
    if sub.empty:
        return pd.DataFrame()
    out = participant_flow(sub, "lot")
    out = out.rename(columns={"buy": "Buy Vol", "sell": "Sell Vol",
                              "buy_vwap": "Avg Buy", "sell_vwap": "Avg Sell"})
    out["Net"] = out["Buy Vol"] - out["Sell Vol"]
    return out[["participant", "Buy Vol", "Avg Buy", "Sell Vol", "Avg Sell",
                "Net"]]


def daily_net(df: pd.DataFrame, metric: str = "lot",
              order: list[str] | None = None) -> pd.DataFrame:
    """Cumulative net position per participant per trade date."""
    if df.empty:
        return pd.DataFrame()
    order = order or list(rank_participants(df, metric).head(8).index)
    lf = long_format(df, metric)
    lf = lf[lf["participant"].isin(order)].copy()
    if lf.empty:
        return pd.DataFrame()
    lf["signed"] = np.where(lf["side"].eq("Buy"), lf["value"], -lf["value"])
    daily = (lf.groupby(["date", "participant"], observed=True)["signed"]
               .sum().unstack("participant").fillna(0.0).sort_index())
    daily = daily.reindex(columns=[p for p in order if p in daily.columns])
    return daily.cumsum()


def expiry_options(df: pd.DataFrame) -> pd.DataFrame:
    """Expiry buckets present, with the TM exact dates folded into each."""
    if df.empty:
        return pd.DataFrame(columns=["expiry_ym", "label", "lot"])
    rows = []
    for ym, grp in df.groupby("expiry_ym", observed=True):
        tm_dates = sorted(
            pd.to_datetime(grp.loc[grp["tm"], "expiry_exact"].dropna().unique())
        )
        suffix = ""
        if tm_dates:
            shown = ", ".join(d.strftime("%d-%m") for d in tm_dates[:3])
            more = "..." if len(tm_dates) > 3 else ""
            suffix = f"  + TM {shown}{more}"
        rows.append({"expiry_ym": ym, "label": f"{ym}{suffix}",
                     "lot": grp["lot"].sum()})
    return pd.DataFrame(rows).sort_values("expiry_ym").reset_index(drop=True)


# ---------------------------------------------------------------------- cli

def _main(argv=None):
    """Warm the shared cache: python viop_opt_core.py [--force]"""
    import argparse
    ap = argparse.ArgumentParser(description=_main.__doc__)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--cache-dir", default=str(CACHE_DIR))
    ap.add_argument("--force", action="store_true",
                    help="re-parse dates that are already cached")
    a = ap.parse_args(argv)

    print(f"raw   : {a.data_dir}")
    print(f"cache : {a.cache_dir}")
    done = build_cache(a.data_dir, a.cache_dir, a.force)
    avail = available_dates(a.data_dir, a.cache_dir)
    print(f"\n{len(done)} file(s) processed · {len(avail)} date(s) now "
          f"loadable · {int(avail['cached'].sum())} in cache")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
