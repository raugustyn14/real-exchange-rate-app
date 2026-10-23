"""
Real Exchange Rate (RER) Explorer using IMF SDMX data.

Definitions
-----------
S(t)   = units of Country 2 currency per 1 unit of Country 1 currency
P1(t), P2(t) = monthly CPI indices of Country 1 and Country 2
RER(t) = S(t) * P1(t) / P2(t)
RER*   = mean of RER(t) over valid months in the selected sample
Real Index(t) = 100 * RER(t) / RER*

IMF exchange rates are local currency units per USD (XDC_USD), so
S(t) = ER2(t) / ER1(t)  (Country 2 per USD divided by Country 1 per USD).
"""

from __future__ import annotations

import io
import time

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
IMF_BASE_30 = "https://api.imf.org/external/sdmx/3.0"
IMF_BASE_21 = "https://api.imf.org/external/sdmx/2.1"
START_PERIOD = "1976-01"
EURO_AREA_CODE = "G163"
USER_AGENT = "rer-explorer/1.0 (streamlit)"

# IMF country codes (ISO3 in nearly all cases) + Euro area aggregate.
_COUNTRY_TEXT = """
AFG:Afghanistan|ALB:Albania|DZA:Algeria|AGO:Angola|ATG:Antigua and Barbuda|ARG:Argentina|ARM:Armenia|ABW:Aruba|AUS:Australia|AUT:Austria|
AZE:Azerbaijan|BHS:Bahamas, The|BHR:Bahrain|BGD:Bangladesh|BRB:Barbados|BLR:Belarus|BEL:Belgium|BLZ:Belize|BEN:Benin|BTN:Bhutan|
BOL:Bolivia|BIH:Bosnia and Herzegovina|BWA:Botswana|BRA:Brazil|BRN:Brunei Darussalam|BGR:Bulgaria|BFA:Burkina Faso|BDI:Burundi|CPV:Cabo Verde|KHM:Cambodia|
CMR:Cameroon|CAN:Canada|CAF:Central African Republic|TCD:Chad|CHL:Chile|CHN:China, P.R.: Mainland|HKG:China, P.R.: Hong Kong|MAC:China, P.R.: Macao|COL:Colombia|COM:Comoros|
COD:Congo, Dem. Rep. of the|COG:Congo, Republic of|CRI:Costa Rica|CIV:Cote d'Ivoire|HRV:Croatia|CYP:Cyprus|CZE:Czech Republic|DNK:Denmark|DJI:Djibouti|DMA:Dominica|
DOM:Dominican Republic|ECU:Ecuador|EGY:Egypt|SLV:El Salvador|GNQ:Equatorial Guinea|ERI:Eritrea|EST:Estonia|SWZ:Eswatini|ETH:Ethiopia|FJI:Fiji|
FIN:Finland|FRA:France|GAB:Gabon|GMB:Gambia, The|GEO:Georgia|DEU:Germany|GHA:Ghana|GRC:Greece|GRD:Grenada|GTM:Guatemala|
GIN:Guinea|GNB:Guinea-Bissau|GUY:Guyana|HTI:Haiti|HND:Honduras|HUN:Hungary|ISL:Iceland|IND:India|IDN:Indonesia|IRN:Iran|
IRQ:Iraq|IRL:Ireland|ISR:Israel|ITA:Italy|JAM:Jamaica|JPN:Japan|JOR:Jordan|KAZ:Kazakhstan|KEN:Kenya|KIR:Kiribati|
KOR:Korea, Republic of|XKX:Kosovo|KWT:Kuwait|KGZ:Kyrgyz Republic|LAO:Lao P.D.R.|LVA:Latvia|LBN:Lebanon|LSO:Lesotho|LBR:Liberia|LBY:Libya|
LTU:Lithuania|LUX:Luxembourg|MDG:Madagascar|MWI:Malawi|MYS:Malaysia|MDV:Maldives|MLI:Mali|MLT:Malta|MHL:Marshall Islands|MRT:Mauritania|
MUS:Mauritius|MEX:Mexico|FSM:Micronesia, Fed. States of|MDA:Moldova|MNG:Mongolia|MNE:Montenegro|MAR:Morocco|MOZ:Mozambique|MMR:Myanmar|NAM:Namibia|
NRU:Nauru|NPL:Nepal|NLD:Netherlands|NZL:New Zealand|NIC:Nicaragua|NER:Niger|NGA:Nigeria|MKD:North Macedonia|NOR:Norway|OMN:Oman|
PAK:Pakistan|PLW:Palau|PAN:Panama|PNG:Papua New Guinea|PRY:Paraguay|PER:Peru|PHL:Philippines|POL:Poland|PRT:Portugal|QAT:Qatar|
ROU:Romania|RUS:Russian Federation|RWA:Rwanda|WSM:Samoa|STP:Sao Tome and Principe|SAU:Saudi Arabia|SEN:Senegal|SRB:Serbia|SYC:Seychelles|SLE:Sierra Leone|
SGP:Singapore|SVK:Slovak Republic|SVN:Slovenia|SLB:Solomon Islands|SOM:Somalia|ZAF:South Africa|SSD:South Sudan|ESP:Spain|LKA:Sri Lanka|KNA:St. Kitts and Nevis|
LCA:St. Lucia|VCT:St. Vincent and the Grenadines|SDN:Sudan|SUR:Suriname|SWE:Sweden|CHE:Switzerland|SYR:Syria|TWN:Taiwan Province of China|TJK:Tajikistan|TZA:Tanzania|
THA:Thailand|TLS:Timor-Leste|TGO:Togo|TON:Tonga|TTO:Trinidad and Tobago|TUN:Tunisia|TUR:Turkiye|TKM:Turkmenistan|TUV:Tuvalu|UGA:Uganda|
UKR:Ukraine|ARE:United Arab Emirates|GBR:United Kingdom|USA:United States|URY:Uruguay|UZB:Uzbekistan|VUT:Vanuatu|VEN:Venezuela|VNM:Vietnam|PSE:West Bank and Gaza|
YEM:Yemen|ZMB:Zambia|ZWE:Zimbabwe|G163:Euro Area
"""


def _build_countries() -> dict[str, str]:
    out: dict[str, str] = {}
    for item in _COUNTRY_TEXT.replace("\n", "").split("|"):
        item = item.strip()
        if not item:
            continue
        code, name = item.split(":", 1)
        out[code.strip()] = name.strip()
    return out


COUNTRIES = _build_countries()
LABELS = {code: f"{name} ({code})" for code, name in COUNTRIES.items()}


# --------------------------------------------------------------------------
# IMF data retrieval and parsing
# --------------------------------------------------------------------------
class IMFNoData(Exception):
    """Raised internally when the IMF has no series for the requested key."""


def _to_month(period: str) -> pd.Timestamp | None:
    """Convert '1976-M01', '1976-01' or '1976-01-01' to a month-start Timestamp."""
    p = str(period).strip().replace("-M", "-")
    try:
        parts = p.split("-")
        if len(parts) >= 2:
            return pd.Timestamp(year=int(parts[0]), month=int(parts[1]), day=1)
    except (ValueError, TypeError):
        pass
    return None


def _parse_sdmx_json(js: dict) -> pd.Series:
    """Parse SDMX-JSON (1.0 or 2.0) into a monthly pandas Series.

    If several series are returned, the one with the most observations is used.
    """
    data = js.get("data", js)
    structures = data.get("structures")
    if not structures and "structure" in data:
        structures = [data["structure"]]
    if not structures:
        raise IMFNoData("No structure in response")

    obs_dims = structures[0].get("dimensions", {}).get("observation", [])
    time_dim = next((d for d in obs_dims if d.get("id") == "TIME_PERIOD"), None)
    if time_dim is None and obs_dims:
        time_dim = obs_dims[0]
    if time_dim is None:
        raise IMFNoData("No time dimension in response")
    time_ids = [v.get("id") or v.get("value") or v.get("name") for v in time_dim["values"]]

    datasets = data.get("dataSets") or []
    if not datasets:
        raise IMFNoData("No datasets")
    series_map = datasets[0].get("series") or {}
    if not series_map:
        raise IMFNoData("No series")

    best: pd.Series | None = None
    for ser in series_map.values():
        rows = {}
        for idx, obs in (ser.get("observations") or {}).items():
            try:
                val = obs[0]
                if val is None:
                    continue
                ts = _to_month(time_ids[int(idx)])
                if ts is None:
                    continue
                rows[ts] = float(val)
            except (ValueError, TypeError, IndexError):
                continue
        s = pd.Series(rows, dtype="float64").sort_index()
        if best is None or len(s) > len(best):
            best = s
    if best is None or best.empty:
        raise IMFNoData("Empty series")
    return best


def _request_series(urls: list[str]) -> pd.Series:
    """Try each URL in turn (SDMX 3.0 first, then 2.1 fallback)."""
    headers_options = [
        {"Accept": "application/vnd.sdmx.data+json;version=2.0.0", "User-Agent": USER_AGENT},
        {"Accept": "application/vnd.sdmx.data+json", "User-Agent": USER_AGENT},
        {"Accept": "application/json", "User-Agent": USER_AGENT},
    ]
    last_err: Exception | None = None
    saw_no_data = False
    for url in urls:
        for headers in headers_options:
            for attempt in range(2):
                try:
                    r = requests.get(url, headers=headers, timeout=60)
                except requests.RequestException as e:
                    last_err = e
                    time.sleep(1 + attempt)
                    continue
                if r.status_code == 404:
                    saw_no_data = True
                    break
                if r.status_code in (406, 415):
                    break  # try next Accept header
                if r.status_code >= 500 or r.status_code == 429:
                    last_err = RuntimeError(f"HTTP {r.status_code} from {url}")
                    time.sleep(1 + attempt)
                    continue
                if not r.ok:
                    last_err = RuntimeError(f"HTTP {r.status_code} from {url}")
                    break
                try:
                    return _parse_sdmx_json(r.json())
                except IMFNoData:
                    saw_no_data = True
                    break
                except ValueError as e:  # bad JSON
                    last_err = e
                    break
    if saw_no_data and last_err is None:
        raise IMFNoData("No data")
    if saw_no_data:
        raise IMFNoData("No data")
    raise RuntimeError(f"IMF API request failed: {last_err}")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_cpi(code: str) -> pd.Series:
    """Monthly CPI index (all items), key {ISO3}.CPI._T.IX.M. Empty if unavailable."""
    key = f"{code}.CPI._T.IX.M"
    urls = [
        f"{IMF_BASE_30}/data/dataflow/IMF.STA/CPI/+/{key}",
        f"{IMF_BASE_21}/data/IMF.STA,CPI/{key}",
    ]
    try:
        s = _request_series(urls)
    except IMFNoData:
        return pd.Series(dtype="float64")
    return s[s.index >= pd.Timestamp(START_PERIOD + "-01")]


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_er_per_usd(code: str) -> pd.Series:
    """Period-average local currency units per USD, key {ISO3}.XDC_USD.PA_RT.M."""
    key = f"{code}.XDC_USD.PA_RT.M"
    urls = [
        f"{IMF_BASE_30}/data/dataflow/IMF.STA/ER/+/{key}",
        f"{IMF_BASE_21}/data/IMF.STA,ER/{key}",
    ]
    try:
        s = _request_series(urls)
    except IMFNoData:
        return pd.Series(dtype="float64")
    return s[s.index >= pd.Timestamp(START_PERIOD + "-01")]


def get_country_data(code: str) -> tuple[pd.Series, pd.Series]:
    cpi = fetch_cpi(code)
    if code == "USA":
        # USD per USD is identically 1 over the CPI range; build it explicitly.
        er = pd.Series(1.0, index=pd.date_range(START_PERIOD + "-01", pd.Timestamp.today().normalize(), freq="MS"))
    else:
        er = fetch_er_per_usd(code)
    return cpi, er


# --------------------------------------------------------------------------
# Calculations
# --------------------------------------------------------------------------
def build_frame(c1: str, c2: str) -> pd.DataFrame:
    cpi1, er1 = get_country_data(c1)
    cpi2, er2 = get_country_data(c2)
    df = pd.concat(
        {"ER1_per_USD": er1, "ER2_per_USD": er2, "P1": cpi1, "P2": cpi2}, axis=1
    ).sort_index()
    df.index.name = "date"
    # S = Country 2 units per Country 1 unit = (C2 per USD) / (C1 per USD)
    df["S"] = df["ER2_per_USD"] / df["ER1_per_USD"]
    df["RER"] = df["S"] * df["P1"] / df["P2"]
    positive = (df[["S", "P1", "P2"]] > 0).all(axis=1)
    df["valid"] = df[["S", "P1", "P2"]].notna().all(axis=1) & positive
    df.loc[~df["valid"], "RER"] = float("nan")
    return df


def apply_sample(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, float | None]:
    sample = df.loc[(df.index >= start) & (df.index <= end)].copy()
    valid_rer = sample.loc[sample["valid"], "RER"]
    if valid_rer.empty:
        sample["RER_star"] = float("nan")
        sample["Real_Index"] = float("nan")
        return sample, None
    rer_star = float(valid_rer.mean())
    sample["RER_star"] = rer_star
    sample["Real_Index"] = 100.0 * sample["RER"] / rer_star
    return sample, rer_star


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------
def make_chart(sample: pd.DataFrame, mode: str, n1: str, n2: str) -> go.Figure:
    nominal_label = f"Nominal S: {n2} currency per 1 {n1} currency"
    real_label = "Real Index (100 = sample-average RER)"
    real = sample["Real_Index"].dropna()
    nominal = sample["S"].dropna()

    if mode == "Both":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=real.index, y=real.values, name="Real Index", mode="lines",
                                 line=dict(color="#1f77b4")), secondary_y=False)
        fig.add_trace(go.Scatter(x=nominal.index, y=nominal.values, name="Nominal S", mode="lines",
                                 line=dict(color="#d62728")), secondary_y=True)
        fig.update_yaxes(title_text=real_label, secondary_y=False)
        fig.update_yaxes(title_text=nominal_label, secondary_y=True, showgrid=False)
        fig.add_hline(y=100, line_dash="dot", line_color="gray", secondary_y=False)
    else:
        fig = go.Figure()
        if mode == "Real":
            fig.add_trace(go.Scatter(x=real.index, y=real.values, name="Real Index", mode="lines",
                                     line=dict(color="#1f77b4")))
            fig.add_hline(y=100, line_dash="dot", line_color="gray")
            fig.update_yaxes(title_text=real_label)
        else:
            fig.add_trace(go.Scatter(x=nominal.index, y=nominal.values, name="Nominal S", mode="lines",
                                     line=dict(color="#d62728")))
            fig.update_yaxes(title_text=nominal_label)
    fig.update_layout(
        height=520,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    fig.update_xaxes(title_text="Month")
    return fig


# --------------------------------------------------------------------------
# Streamlit UI
# --------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="IMF Real Exchange Rate Explorer", layout="wide")
    st.title("Real Exchange Rate Explorer (IMF data)")
    st.caption(
        "S(t) = units of Country 2 currency per 1 unit of Country 1 currency · "
        "RER(t) = S(t) × P1(t) / P2(t) · Real Index = 100 × RER(t) / RER*"
    )

    codes = list(COUNTRIES.keys())
    with st.sidebar:
        st.header("Selection")
        c1 = st.selectbox("Country 1 (base)", codes, index=codes.index("USA"),
                          format_func=lambda c: LABELS[c])
        c2 = st.selectbox("Country 2 (foreign)", codes, index=codes.index("G163"),
                          format_func=lambda c: LABELS[c])
        mode = st.radio("Chart", ["Real", "Nominal", "Both"], index=0, horizontal=True)
        st.button("Clear cached data", on_click=st.cache_data.clear)

    if c1 == c2:
        st.warning("Please choose two different countries.")
        return

    n1, n2 = COUNTRIES[c1], COUNTRIES[c2]

    try:
        with st.spinner("Fetching IMF data…"):
            df = build_frame(c1, c2)
    except Exception as e:  # network or API failure
        st.error(f"Could not retrieve IMF data: {e}")
        return

    # Coverage diagnostics
    cov = {
        f"CPI {n1}": df["P1"], f"CPI {n2}": df["P2"],
        f"FX {n1} (per USD)": df["ER1_per_USD"], f"FX {n2} (per USD)": df["ER2_per_USD"],
    }
    missing = [k for k, s in cov.items() if s.dropna().empty]
    if missing:
        st.error("No IMF data available for: " + ", ".join(missing) +
                 ". Try a different country pair.")
        return

    valid_months = df.index[df["valid"]]
    if len(valid_months) == 0:
        st.error("The two countries share no month with CPI and exchange-rate data for both.")
        return

    with st.sidebar:
        st.subheader("Data coverage")
        for k, s in cov.items():
            d = s.dropna()
            st.write(f"**{k}**: {d.index.min():%Y-%m} → {d.index.max():%Y-%m}")
        st.write(f"**Valid joint months**: {len(valid_months)} "
                 f"({valid_months.min():%Y-%m} → {valid_months.max():%Y-%m})")

    # Sample period selection (months with all data are the selectable range)
    month_options = [d.strftime("%Y-%m") for d in pd.date_range(valid_months.min(), valid_months.max(), freq="MS")]
    start_lbl, end_lbl = st.select_slider(
        "Sample period (start – end month)",
        options=month_options,
        value=(month_options[0], month_options[-1]),
        key=f"range_{c1}_{c2}",
    )
    start, end = pd.Timestamp(start_lbl + "-01"), pd.Timestamp(end_lbl + "-01")

    sample, rer_star = apply_sample(df, start, end)
    if rer_star is None:
        st.error("No valid months in the selected sample.")
        return

    n_valid = int(sample["valid"].sum())
    n_total = len(sample)
    m1, m2, m3 = st.columns(3)
    m1.metric("RER* (sample average of RER)", f"{rer_star:,.6g}")
    m2.metric("Valid months in sample", f"{n_valid} / {n_total}")
    last_valid = sample[sample["valid"]].iloc[-1]
    m3.metric(f"Latest Real Index ({last_valid.name:%Y-%m})", f"{last_valid['Real_Index']:.1f}")

    if n_valid < n_total:
        st.info(f"{n_total - n_valid} month(s) in the sample have missing data and are excluded "
                "from RER*, the Real Index and the average (nominal S is shown wherever available).")

    st.plotly_chart(make_chart(sample, mode, n1, n2), use_container_width=True)
    st.caption(
        "A rising Real Index means Country 1's goods have become relatively more expensive "
        f"in terms of Country 2's ({n1} real appreciation against {n2}). "
        "RER* is recomputed whenever the sample dates change."
    )

    # CSV export of every observation in the sample
    out = sample.reset_index().rename(columns={
        "date": "month",
        "ER1_per_USD": f"ER_{c1}_local_per_USD",
        "ER2_per_USD": f"ER_{c2}_local_per_USD",
        "P1": f"CPI_{c1}",
        "P2": f"CPI_{c2}",
        "S": "S_Country2_per_Country1",
        "RER": "RER",
        "RER_star": "RER_star",
        "Real_Index": "Real_Index",
        "valid": "valid_month",
    })
    out["month"] = out["month"].dt.strftime("%Y-%m")
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    st.download_button(
        "Download CSV (selected sample)",
        data=buf.getvalue().encode("utf-8"),
        file_name=f"rer_{c1}_{c2}_{start_lbl}_{end_lbl}.csv",
        mime="text/csv",
    )

    with st.expander("Show data table"):
        st.dataframe(out, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
