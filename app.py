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

# -----------------------------
# API helpers
# -----------------------------
API_BASE = "https://api.exchangerate.host"

def get_api_key_from_secrets_or_input() -> Optional[str]:
    """
    Prefer st.secrets['EXCHANGERATE_API_KEY'].
    Fallback to a masked text_input so you can test locally without committing a key.
    """
    key = st.secrets.get("EXCHANGERATE_API_KEY", None)
    if key:
        return key
    with st.sidebar:
        st.info("Add EXCHANGERATE_API_KEY to .streamlit/secrets.toml for best security.\n"
                "Using a temporary key here is fine for local testing.")
        return st.text_input("API key (masked)", type="password", key="api_key_input") or None

@st.cache_data(ttl=60 * 60 * 24)  # cache results for 24 hours to respect limited quota
def fetch_timeseries(
    access_key: str,
    base: str,
    symbols: List[str],
    start_date: dt.date,
    end_date: dt.date,
    pause_seconds: float = 0.0,
) -> Dict[str, Any]:
    """
    Calls the /timeseries endpoint once (efficient + quota-friendly).
    Caches results by function args (incl. dates) for 24h.
    """
    # Optional tiny pause to be extra gentle if you chain queries
    if pause_seconds > 0:
        time.sleep(pause_seconds)

    params = {
        "access_key": access_key,
        "base": base,
        "symbols": ",".join(symbols) if symbols else None,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    # Remove None params
    params = {k: v for k, v in params.items() if v is not None}

    url = f"{API_BASE}/timeseries"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Basic sanity checks that help when rate-limited or mis-keyed
    if not data.get("success", True) and "error" in data:
        # exchangerate.host sometimes returns {success: false, error: {...}}
        raise RuntimeError(f"API error: {data['error'].get('type','unknown')} – {data['error'].get('info','')}")
    if "rates" not in data:
        raise RuntimeError("Unexpected API response shape: 'rates' missing")

    return data

def rates_to_dataframe(data: Dict[str, Any], base: str) -> pd.DataFrame:
    """
    Convert API JSON to DataFrame:
    index: date, columns: currency symbols (and include base as a column of 1.0)
    """
    rates = data.get("rates", {})
    if not rates:
        return pd.DataFrame()

    # Build wide table: one row per date
    rows = []
    for date_str, day_rates in rates.items():
        row = {"date": pd.to_datetime(date_str)}
        for cur, val in day_rates.items():
            row[cur] = val
        # include base column as 1.0 for convenience
        row[base] = 1.0
        rows.append(row)

    df = pd.DataFrame(rows).sort_values("date").set_index("date")
    # Order columns alphabetically, but keep base first
    cols = [base] + sorted([c for c in df.columns if c != base])
    return df[cols]

def make_excel_download(df: pd.DataFrame, meta: Dict[str, Any]) -> bytes:
    """
    Build an .xlsx in-memory file with a 'Rates' sheet and a small 'Meta' sheet.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Rates")
        meta_df = pd.DataFrame(
            {
                "Key": list(meta.keys()),
                "Value": [str(v) for v in meta.values()],
            }
        )
        meta_df.to_excel(writer, sheet_name="Meta", index=False)
    buffer.seek(0)
    return buffer.read()

# -----------------------------
# UI controls
# -----------------------------

with st.sidebar:
    st.header("🔧 Query Options")

    # Common ISO currency list (short starter set; you can expand)
    common_currencies = [
        "GBP","EUR","USD","CHF","JPY","AUD","CAD","NZD","SEK","NOK","DKK",
        "PLN","CZK","HUF","TRY","ZAR","CNY","HKD","SGD","INR","MXN","BRL"
    ]

    base = st.selectbox("Base currency", options=sorted(common_currencies), index=sorted(common_currencies).index("GBP"))
    symbols = st.multiselect(
        "Target currencies",
        options=[c for c in sorted(common_currencies) if c != base],
        default=["EUR","USD","CHF"]
    )

    # Preset period (Month/Year friendly)
    preset = st.radio("Period", options=["This month", "Last month", "Custom"], index=0)

    today = dt.date.today().replace(day=1)
    if preset == "This month":
        start_date = today
        # end today or end-of-month? we'll use today for freshest partial data
        end_date = dt.date.today()
    elif preset == "Last month":
        first_this = today
        last_month_end = first_this - dt.timedelta(days=1)
        start_date = last_month_end.replace(day=1)
        end_date = last_month_end
    else:
        # Custom date pickers
        start_date = st.date_input("Start date", value=today)
        end_date = st.date_input("End date", value=dt.date.today())
        if start_date > end_date:
            st.error("Start date must be on or before end date.")

    # Gentle throttle (adds a small sleep before calling API)
    throttle = st.slider("Request throttle (seconds)", 0.0, 2.0, 0.0, 0.1,
                         help="Optional pause before sending a request, if you're doing several in a row.")

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

    with st.spinner("Calling exchangerate.host…"):
        try:
            data = fetch_timeseries(
                access_key=api_key,
                base=base,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                pause_seconds=throttle
            )
        except Exception as e:
            st.error(f"Could not retrieve data: {e}")
            st.stop()

    df = rates_to_dataframe(data, base=base)

    if df.empty:
        st.warning("No data returned for the chosen period/currencies.")
        st.stop()

    st.subheader("Preview")
    st.dataframe(df, use_container_width=True)

    st.caption(f"Rows: {len(df):,} · Columns: {len(df.columns):,}   "
               f"({start_date.isoformat()} → {end_date.isoformat()})")

    # Export to Excel
    meta = {
        "Base": base,
        "Symbols": ", ".join(symbols),
        "Start": start_date,
        "End": end_date,
        "Source": "exchangerate.host /timeseries",
        "Generated": dt.datetime.utcnow().isoformat(timespec='seconds') + "Z",
    }
    xlsx_bytes = make_excel_download(df, meta)
    st.download_button(
        label="⬇️ Download .xlsx",
        data=xlsx_bytes,
        file_name=f"fx_timeseries_{base}_{start_date}_{end_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # Simple quick stats
    with st.expander("Quick stats (min / max / mean)"):
        stats = df.describe().loc[["min","max","mean"]]
        st.dataframe(stats.style.format("{:.6f}"), use_container_width=True)

else:
    st.info("Pick your currencies and period on the left, then click **Fetch rates**.")
