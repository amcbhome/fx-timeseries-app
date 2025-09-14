# fx-timeseries-app
# FX Timeseries Downloader (exchangerate.host)

A tiny Streamlit app to fetch historical FX rates for a month/year (or custom dates) and export to Excel.

## Features
- Uses **/timeseries** endpoint → only **one API call per query** (quota-friendly)
- Pick **base** and **multiple target currencies**
- Choose **This month**, **Last month**, or **Custom dates**
- **Excel export** with a Meta sheet
- **Caching (24 hours)** via `st.cache_data`
- Optional **throttle** slider for gentle pacing

## Setup

```bash
git clone <your-repo-url>
cd fx-timeseries-app
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
