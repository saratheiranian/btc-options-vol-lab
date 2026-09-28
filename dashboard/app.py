"""BTC Options Volatility Lab dashboard.

    python -m streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so `volab` imports under `streamlit run`

import altair as alt  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from volab import bs  # noqa: E402
from volab.deribit import build_chain, fetch_book_summary, otm_only  # noqa: E402
from volab.hedging import gbm_paths, hedge_short_call, summarise  # noqa: E402
from volab.iv import implied_vol_details  # noqa: E402
from volab.smile import agreement, term_structure  # noqa: E402

st.set_page_config(page_title="BTC Options Volatility Lab", layout="wide")
st.title("BTC Options Volatility Lab")
st.caption("Black-Scholes pricing, the live Bitcoin volatility smile from Deribit, and a delta-hedging simulator. "
           "Research and education only: no trading, not financial advice.")

pricer, smile_tab, hedge_tab = st.tabs(["Pricer", "Live volatility smile", "Hedging lab"])

# --- 1. Pricer ------------------------------------------------------------------------
with pricer:
    c = st.columns(6)
    S = c[0].number_input("Spot (USD)", 1.0, value=60_000.0, step=1_000.0)
    K = c[1].number_input("Strike (USD)", 1.0, value=60_000.0, step=1_000.0)
    days = c[2].number_input("Days to expiry", 1, 1_000, value=30)
    sigma = c[3].number_input("Volatility (%)", 1.0, 500.0, value=60.0) / 100
    r = c[4].number_input("Interest rate (%)", -5.0, 20.0, value=0.0) / 100
    kind = c[5].selectbox("Type", ["call", "put"])
    T = days / 365
    g = {k: float(v) for k, v in bs.greeks(S, K, T, r, sigma, kind).items()}
    m = st.columns(6)
    m[0].metric("Price", f"${g['price']:,.2f}")
    m[1].metric("Delta", f"{g['delta']:.4f}", help="Change in price per $1 move in the underlying")
    m[2].metric("Gamma", f"{g['gamma']:.2e}", help="Change in delta per $1 move")
    m[3].metric("Vega", f"${g['vega'] / 100:,.2f}", help="Change in price per 1 volatility point")
    m[4].metric("Theta", f"${g['theta'] / 365:,.2f}", help="Change in price per day that passes")
    m[5].metric("Rho", f"${g['rho'] / 100:,.2f}", help="Change in price per 1% interest rate")

    spots = np.linspace(0.6 * K, 1.4 * K, 121)
    curve = pd.DataFrame({"Spot": spots, "Option value": bs.price(spots, K, T, r, sigma, kind),
                          "Value at expiry": np.maximum(spots - K, 0) if kind == "call" else np.maximum(K - spots, 0)})
    st.line_chart(curve.set_index("Spot"), height=280)

    st.subheader("Implied volatility from a price")
    ci = st.columns([1, 3])
    market = ci[0].number_input("Market price (USD)", 0.0, value=round(g["price"], 2))
    res = implied_vol_details(market, S, K, T, r, kind)
    if np.isnan(res["sigma"]):
        ci[1].warning(f"No implied volatility: {res['method']}.")
    else:
        ci[1].success(f"Implied volatility {res['sigma'] * 100:.2f}%, found by {res['method']} "
                      f"in {res['iterations']} iteration(s).")

# --- 2. Live smile -------------------------------------------------------------------
with smile_tab:
    @st.cache_data(ttl=60, show_spinner="Fetching BTC options from Deribit...")
    def load_chain() -> pd.DataFrame:
        return build_chain(fetch_book_summary("BTC"))

    try:
        chain = load_chain()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Couldn't fetch data from Deribit ({type(exc).__name__}). Check your internet connection.")
        chain = pd.DataFrame()

    if not chain.empty:
        otm = otm_only(chain)
        check = agreement(chain)
        a = st.columns(3)
        a[0].metric("Options priced", f"{check['options_compared']:,}")
        a[1].metric("Median gap vs Deribit IV", f"{check['median_abs_diff_vol_pts']:.3f} pts",
                    help="Our solver on Deribit's mark prices vs Deribit's published mark IV")
        a[2].metric("Within 0.5 vol points", f"{check['share_within_0_5_pts'] * 100:.1f}%")

        labels = {e: f"{pd.Timestamp(e):%d %b %y}" for e in sorted(otm["expiry"].unique())}
        chosen = st.multiselect("Expiries", list(labels), default=list(labels)[:4], format_func=labels.get)
        plot = otm[otm["expiry"].isin(chosen) & (otm["log_moneyness"].abs() <= 0.6)].dropna(subset=["our_iv"]).copy()
        plot["Expiry"] = plot["expiry"].map(labels)
        plot["Implied vol (%)"] = plot["our_iv"] * 100
        st.altair_chart(
            alt.Chart(plot).mark_line(point=True).encode(
                x=alt.X("log_moneyness:Q", title="ln(K / F): downside puts ← → upside calls"),
                y=alt.Y("Implied vol (%):Q", scale=alt.Scale(zero=False)),
                color="Expiry:N", tooltip=["instrument", "strike", "Implied vol (%)"]),
            use_container_width=True)
        ts = term_structure(otm)
        ts_view = pd.DataFrame({
            "Expiry": ts["expiry"].map(lambda e: f"{pd.Timestamp(e):%d %b %y}"), "Days": ts["days"],
            "ATM IV (%)": (ts["atm_iv"] * 100).round(1), "Skew (vol pts)": (ts["skew_10pct"] * 100).round(1),
        })
        st.dataframe(ts_view, hide_index=True, use_container_width=True)
        st.caption("Skew: implied vol 10% below the forward minus 10% above. A flat smile would mean Black-Scholes "
                   "prices every strike correctly; the curve shows where it doesn't.")

# --- 3. Hedging lab --------------------------------------------------------------------
with hedge_tab:
    st.write("Sell a 30-day at-the-money call on BTC at $60,000 and delta-hedge it. Change the settings to see what "
             "drives the hedger's final profit or loss.")
    h = st.columns(4)
    every = h[0].select_slider("Rebalance every", options=[1, 2, 4, 8, 12, 24], value=24, format_func=lambda x: f"{x} h")
    implied = h[1].slider("Implied vol sold at (%)", 20, 120, 60) / 100
    realised = h[2].slider("Realised vol (%)", 20, 120, 60) / 100
    cost = h[3].slider("Trading cost (bps)", 0.0, 10.0, 0.0, 0.5)
    T_h, S0 = 30 / 365, 60_000.0
    result = hedge_short_call(gbm_paths(S0, realised, T_h, 720, 2000, seed=0), S0, T_h, implied,
                              rebalance_every=every, cost_bps=cost)
    s = summarise(result)
    k = st.columns(4)
    k[0].metric("Premium received", f"${result['premium'].iloc[0]:,.0f}")
    k[1].metric("Mean P&L", f"${s['mean_pnl']:+,.0f}")
    k[2].metric("Std. dev. of P&L", f"${s['std_pnl']:,.0f}")
    k[3].metric("Worst 5%", f"${s['p5_pnl']:+,.0f}")
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.hist(result["pnl"], bins=60, color="#2F6F62", alpha=0.85)
    ax.axvline(0, color="grey", lw=0.8)
    ax.set(xlabel="Final P&L per option (USD), 2,000 simulated paths", ylabel="Paths")
    fig.tight_layout()
    st.pyplot(fig)
