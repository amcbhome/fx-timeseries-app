# app.py
import io
import time
import datetime as dt
from typing import List, Dict, Any, Optional

import pandas as pd
import requests
import streamlit as st

# -----------------------------
# App config
# -----------------------------
st.set_page_config(page_title="FX Timeseries (exchangerate.host)", page_icon="💱", layout="wide")

st.title("💱 FX Timeseries Downloader (exchangerate.host)")
st.caption("Query historical FX rates for a chosen month/year (or custom dates) and export to Excel.")

API_BASE = "https://api.exchangerate.host"   # we use the /timeframe endpoint

# -----------------------------
# Secrets helper
# -----------------------------
def get_api_key_from_secrets_or_input() -> Optional[str]:
    """
    Prefer st.secrets['EXCHANGERATE_API_KEY'].
    Fallback to masked text_input so you can test locally without committing a key.
    """
    key = st.secrets.get("EXCHANGERATE_API_KEY", None)
    if key:
        return key
    with st.sidebar:
        st.info(
            "Add EXCHANGERATE_API_KEY to .streamlit/secrets.toml (or Cloud Secrets).\n"
            "Using a temporary key here is fine for local testing."
        )
        return st.text_input("API key (masked)", type="password", key="api_key_input") or None

# -----------------------------
# API call (single request, cached)
# -----------------------------
@st.cache_data(ttl=60 * 60 * 24)  # cache for 24 hours to respect limited quota
def fetch_timeframe(
    access_key: str,
    currencies: List[str],
    start_date: dt.date,
    end_date: dt.date,
    pause_seconds: float = 0.0,
) -> Dict[str, Any]:
    """
    Calls /timeframe once. We don't send 'source' so it uses the provider default (often USD).
    We then convert locally to the requested base later.
    """
    if pause_seconds > 0:
        time.sleep(pause_seconds)

    params = {
        "access_key": access_key,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "currencies": ",".join(sorted(set(currencies))) if currencies else None,
        "format": 1,  # request JSON with full precision if supported
    }
    params = {k: v for k, v in params.items() if v is not None}

    url = f"{API_BASE}/timeframe"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Typical error envelope from exchangerate.host
    if not data.get("success", True) and "error" in data:
        raise RuntimeError(f"API error: {data['error'].get('info','unknown error')}")

    # Ensure we have date->rates/quotes
    if not any(k in data for k in ("rates", "quotes")):
        raise RuntimeError("Unexpected API response: missing 'rates'/'quotes'.")

    return data

# -----------------------------
# JSON -> DataFrame conversion
# -----------------------------
def _convert_quotes_block_to_base(day_block: Dict[str, float], base: str) -> Dict[str, float]:
    """
    For 'quotes' style data like {'USDEUR': 0.91, 'USDGBP': 0.78, ...}
    Return dict mapping currency->rate in 'base' terms using:
      (USD->cur) / (USD->base) = base->cur
    Also ensures a USD column exists: USD in base = 1 / (USD->base).
    """
    usd_to = {}
    for pair, val in day_block.items():
        # defensive parse: take last 3 letters as currency
        cur = pair[-3:]
        usd_to[cur] = float(val)

    if base not in usd_to:
        # can't convert this day if base not present
        return {}

    base_row = {c: (usd_to[c] / usd_to[base]) for c in usd_to.keys()}
    base_row[base] = 1.0
    # Explicit USD column (important for export when USD selected)
    base_row["USD"] = 1.0 / usd_to[base]
    return base_row

def _convert_rates_block_to_base(day_block: Dict[str, float], base: str) -> Dict[str, float]:
    """
    For 'rates' style data like {'EUR': 0.91, 'GBP': 0.78, ...} all vs a hidden provider base (often USD).
    Convert to chosen base using: cur_in_base = (rate[cur] / rate[base]).
    Also ensure a USD column: USD in base = 1 / rate[base].
    """
    r = {k: float(v) for k, v in day_block.items()}
    if base not in r:
        return {}
    converted = {c: (r[c] / r[base]) for c in r.keys()}
    converted[base] = 1.0
    # Ensure USD column (works when underlying provider base is USD)
    converted.setdefault("USD", 1.0 / r[base])
    return converted

def timeframe_to_dataframe(data: Dict[str, Any], base: str) -> pd.DataFrame:
    """
    Handles both schemas:
      1) {'quotes': {'YYYY-MM-DD': {'USDEUR': x, 'USDGBP': y, ...}, ...}}
      2) {'rates' : {'YYYY-MM-DD': {'EUR': x, 'GBP': y, ...}, ...}}
    Returns DataFrame indexed by date with columns for each currency (base first).
    """
    container = data.get("quotes") or data.get("rates") or {}
    rows = []

    for date_str, block in container.items():
        if not isinstance(block, dict):
            # Some providers nest differently; try 'quotes'/'rates' child
            block = block.get("quotes") or block.get("rates") or {}

        if data.get("quotes") is not None:
            converted = _convert_quotes_block_to_base(block, base)
        else:
            converted = _convert_rates_block_to_base(block, base)

        if not converted:
            # skip dates we cannot convert (e.g., base missing)
            continue

        converted["date"] = pd.to_datetime(date_str)
        rows.append(converted)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("date").set_index("date")
    cols = [base] + sorted([c for c in df.columns if c != base])
    return df[cols]

def make_excel_download(df: pd.DataFrame, meta: Dict[str, Any]) -> bytes:
    """
    Build an .xlsx in-memory file with a 'Rates' sheet and a small 'Meta' sheet.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Rates")
        meta_df = pd.DataFrame({"Key": list(meta.keys()), "Value": [str(v) for v in meta.values()]})
        meta_df.to_excel(writer, sheet_name="Meta", index=False)
    buffer.seek(0)
    return buffer.read()

# -----------------------------
# UI controls
# -----------------------------
with st.sidebar:
    st.header("🔧 Query Options")

    common_currencies = [
        "GBP","EUR","USD","CHF","JPY","AUD","CAD","NZD","SEK","NOK","DKK",
        "PLN","CZK","HUF","TRY","ZAR","CNY","HKD","SGD","INR","MXN","BRL"
    ]

    base = st.selectbox(
        "Base currency",
        options=sorted(common_currencies),
        index=sorted(common_currencies).index("GBP"),
    )

    symbols = st.multiselect(
        "Target currencies",
        options=[c for c in sorted(common_currencies) if c != base],
        default=["EUR", "USD", "CHF"],
    )

    preset = st.radio("Period", options=["This month", "Last month", "Custom"], index=1)

    today_1st = dt.date.today().replace(day=1)
    if preset == "This month":
        start_date = today_1st
        end_date = dt.date.today()
    elif preset == "Last month":
        first_this = today_1st
        last_month_end = first_this - dt.timedelta(days=1)
        start_date = last_month_end.replace(day=1)
        end_date = last_month_end
    else:
        start_date = st.date_input("Start date", value=today_1st)
        end_date = st.date_input("End date", value=dt.date.today())
        if start_date > end_date:
            st.error("Start date must be on or before end date.")

    throttle = st.slider(
        "Request throttle (seconds)", 0.0, 2.0, 0.0, 0.1,
        help="Optional pause before sending the request (quota friendly)."
    )

    run_btn = st.button("Fetch rates")

# -----------------------------
# Main logic
# -----------------------------
api_key = get_api_key_from_secrets_or_input()

if run_btn:
    if not api_key:
        st.error("Please provide your API key (via secrets or the masked input).")
        st.stop()
    if not symbols:
        st.error("Choose at least one target currency.")
        st.stop()
    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        st.stop()

    # Request the target currencies **plus** the base so we can convert.
    requested = sorted(set(symbols + [base]))

    with st.spinner("Calling exchangerate.host /timeframe…"):
        try:
            raw = fetch_timeframe(
                access_key=api_key,
                currencies=requested,
                start_date=start_date,
                end_date=end_date,
                pause_seconds=throttle,
            )
        except Exception as e:
            st.error(f"Could not retrieve data: {e}")
            st.stop()

    df = timeframe_to_dataframe(raw, base=base)

    if df.empty:
        st.warning("No data returned (or base not present in API response). Try a different period or currencies.")
        st.stop()

    # Keep only the columns the user asked for (and base first)
    wanted = [base] + [c for c in symbols if c != base]
    df = df[[c for c in wanted if c in df.columns]]

    st.subheader("Preview")
    st.dataframe(df, use_container_width=True)

    st.caption(
        f"Rows: {len(df):,} · Columns: {len(df.columns):,}   "
        f"({start_date.isoformat()} → {end_date.isoformat()})"
    )

    # Export to Excel
    meta = {
        "Base": base,
        "Symbols": ", ".join(symbols),
        "Start": start_date,
        "End": end_date,
        "Endpoint": "/timeframe",
        "Generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    xlsx_bytes = make_excel_download(df, meta)
    st.download_button(
        label="⬇️ Download .xlsx",
        data=xlsx_bytes,
        file_name=f"fx_timeseries_{base}_{start_date}_{end_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # Quick stats
    with st.expander("Quick stats (min / max / mean)"):
        stats = df.describe().loc[["min", "max", "mean"]]
        st.dataframe(stats.style.format("{:.6f}"), use_container_width=True)

else:
    st.info("Pick your currencies and period on the left, then click **Fetch rates**.")
