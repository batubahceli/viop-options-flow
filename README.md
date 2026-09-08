# VIOP Options Flow

A Streamlit dashboard for BIST VIOP **options** order flow, built on the
exchange's time-and-sales files (`ViopDefterYYYYMMDD.csv`). It answers three
questions over any date range:

| Tab | Question |
|---|---|
| **Strike Ladder** | Where did volume and net position land, strike by strike? |
| **Participant Flow** | Who bought, who sold, and what are they left holding? |
| **Market Overview** | The same two questions across every underlying at once |

Clicking any bar opens a strike-detail panel: per-participant volumes, average
buy and sell prices against the contract VWAP, and net.

Two things it is careful about, because both are easy to get wrong:

- **Tailor-made (`TM_`) contracts are included.** They carry a six-digit
  `DDMMYY` expiry where standard contracts carry `MMYY`. A parser that assumes
  four digits silently drops every one of them — in the dataset this was built
  on, that was 30% of all option volume.
- **`USDTRYK` is quoted ×1000.** `O_USDTRYKE0926C50000` is strike 50.000
  against a ~49.37 future, and its `257.9` premium is 0.2579. Strike, price and
  premium all need the same divisor, or FX sits on a different scale from
  everything else.

## Layout

    viop_opt_core.py     parse, cache, aggregate    (pure pandas, no streamlit)
    viop_opt_charts.py   figure builders            (pure plotly, no streamlit)
    viop_opt_app.py      filters and layout

The two lower layers import no streamlit, so every figure can be rendered to
PNG and inspected, and every aggregation tested, without running the app.

## Setup

    pip install -r requirements.txt

Point it at your data, either with environment variables:

    set VIOP_DATA_DIR=D:\path\to\bistzamansatis
    set VIOP_CACHE_DIR=\\your-nas\share\opt_cache

or with a `.streamlit/secrets.toml` (gitignored):

    cache_dir = '\\your-nas\share\opt_cache'
    data_dir  = 'D:\path\to\bistzamansatis'

Both default to `./data` and `./opt_cache` if unset. The folders can also be
edited live in the sidebar.

## Run

    streamlit run viop_opt_app.py

## The cache, and running without the raw files

Options are a rounding error of each daily file — roughly 0.3% of rows, a few
thousand out of 12 million. So the loader filters raw bytes for `;O_` and
`;TM_O_` before pandas sees anything, then stores the parsed result as one
parquet per trade date. A 123-day cold build takes about a minute; six months
of parsed option trades is under 6 MB.

Because loading is **cache-first**, a machine that can reach the cache folder
needs no raw CSVs at all — put the cache on a share and everyone reads from it:

    python viop_opt_core.py                 # warm the cache from local raw files
    python viop_opt_core.py --force         # re-parse dates already cached
    python viop_opt_core.py --cache-dir X --data-dir Y

Bumping `CACHE_VERSION` in `viop_opt_core.py` invalidates every parquet, which
is what you want after changing the parser.

## Reading the charts

- **Buying is always left of zero and cool-coloured; selling is right and
  warm.** That holds in every figure, so Gross and Net modes read the same way.
- Both arms share one symmetric scale and are labelled with absolute values —
  the left arm means "bought", not "negative".
- **Gross** mode shows turnover. **Net** mode shows what each participant is
  left holding, so every strike row mirrors exactly around zero.
- Net is **bought − sold within the selected date range**. Contracts trade
  before any window you pick, so this is net flow, not exchange open interest.
  The app says so wherever it matters.

### A note on colour

Participant identity is carried by nine categorical hues, assigned in fixed
slot order and never recycled; everyone past the ninth folds into "Other".
Nine is not arbitrary — searching OKLCH space for a tenth hue at least ΔE 13
from the other nine returns nothing, so a tenth colour would look like one
already on screen. All-pairs separation does not hold at nine (it does not hold
at eight either), so identity never rests on hue alone: dominant segments are
labelled on the bar, every figure keeps its legend, and every chart has a table
beside it. Participants beyond the ninth are shown by row on the flow chart and
heatmap, where each row is labelled directly and there is no ceiling.

## Data

**No market data is in this repository, and none should be.** These files are
member-identified: they name which member is net short which strike.
Redistribution is a licensing question for whoever holds your Borsa İstanbul
data agreement. `.gitignore` blocks `data/`, `opt_cache/`, `*.csv` and
`*.parquet` — leave those rules alone.

The same applies to deployment. Streamlit Community Cloud runs on Streamlit's
infrastructure and cannot see an internal share, so hosting it there means
moving the data off your network. If the goal is a link colleagues can open,
self-hosting inside the network behind authentication keeps the share reachable
and the data where it is.
