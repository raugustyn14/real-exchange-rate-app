# IMF Real Exchange Rate Explorer

A Streamlit app that pulls monthly IMF CPI and exchange-rate data and plots the
real exchange rate (RER) index and/or the nominal exchange rate for any pair of
IMF countries (or the Euro area).

## Economics

- `S(t)` = units of **Country 2** currency per 1 unit of **Country 1** currency.
- IMF exchange rates are local currency per USD (`XDC_USD`, period average), so
  `S(t) = ER2(t) / ER1(t)`.
- `RER(t) = S(t) * P1(t) / P2(t)` (P = monthly all-items CPI index).
- Over the selected start/end months: `RER* = mean(RER(t))` over valid months
  (months where S, P1 and P2 are all available and positive).
- `Real Index(t) = 100 * RER(t) / RER*`. RER* is recomputed whenever the dates change.
- Nominal `S(t)` is plotted in actual currency units (never normalized).
  With "Both", the Real Index uses the left axis and nominal S the right axis.
- A rising index means Country 1's prices have risen relative to Country 2's
  in common currency (real appreciation of Country 1).

## Data

| Series | IMF dataflow | Key |
|---|---|---|
| CPI | `IMF.STA/CPI` | `{ISO3}.CPI._T.IX.M` |
| Exchange rate | `IMF.STA/ER` | `{ISO3}.XDC_USD.PA_RT.M` |

Base URL: `https://api.imf.org/external/sdmx/3.0` (SDMX 2.1 endpoint used as fallback).
Euro area code: `G163`. Data are requested from January 1976 where available.
Responses are cached for 6 hours (sidebar button clears the cache).

Notes and limitations:
- Coverage varies; the sidebar shows each series' range and the joint valid range.
- For the United States, USD per USD is set to 1.
- Euro members' exchange rates reflect national currency before euro adoption.
- CPI series may have different base years across countries; this does not affect
  the Real Index, which is normalized by RER*.
- The country list is embedded in `app.py`; countries without IMF data report that clearly.
- The exact IMF API response format could change; if a fetch fails, adjust the URL
  or `Accept` header in `_request_series` / `fetch_cpi` / `fetch_er_per_usd`.

## Run

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## CSV export

The download button exports every month in the selected sample with: both
exchange rates (local per USD), both CPIs, S, RER, RER*, Real Index, and a
`valid_month` flag.
