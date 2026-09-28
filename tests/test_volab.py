"""Tests for the volatility lab. Run with:  pytest -q   (no network needed)"""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from volab import bs
from volab.deribit import build_chain, otm_only, parse_instrument
from volab.hedge import run as run_hedge
from volab.hedging import gbm_paths, hedge_short_call, historical_paths, realised_vol, summarise
from volab.iv import implied_vol, implied_vol_details
from volab.smile import agreement, term_structure
from volab.snapshot import run as run_snapshot

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def smile(k):
    """The 'true' vol surface used to build fake market data: skewed and smiling."""
    return 0.50 + 0.30 * k**2 - 0.10 * k


def fake_deribit_summary(forward=60_000.0, days=(7, 30, 90)):
    """A Deribit-shaped book summary priced from a known smile, quoted in BTC like the real API."""
    out = []
    for d in days:
        expiry = (NOW + timedelta(days=d)).replace(hour=8, minute=0)
        T = (expiry - NOW).total_seconds() / (365 * 24 * 3600)
        for K in np.arange(30_000, 100_001, 2_500):
            for kind, cp in (("call", "C"), ("put", "P")):
                vol = smile(np.log(K / forward))
                usd = float(bs.black76(forward, K, T, 0.0, vol, kind))
                out.append({
                    "instrument_name": f"BTC-{expiry.day}{MONTHS[expiry.month - 1]}{expiry:%y}-{int(K)}-{cp}",
                    "underlying_price": forward, "mark_price": usd / forward, "mark_iv": vol * 100,
                    "bid_price": usd / forward * 0.97, "ask_price": usd / forward * 1.03, "open_interest": 10.0,
                })
    out.append({"instrument_name": "BTC-PERPETUAL", "mark_price": 1.0})  # not an option: must be skipped
    return out


# --- Black-Scholes -------------------------------------------------------------------

def test_textbook_values():
    """Hull, Options, Futures and Other Derivatives: S=42, K=40, r=10%, sigma=20%, T=0.5."""
    assert float(bs.price(42, 40, 0.5, 0.1, 0.2, "call")) == pytest.approx(4.76, abs=0.005)
    assert float(bs.price(42, 40, 0.5, 0.1, 0.2, "put")) == pytest.approx(0.81, abs=0.005)


@pytest.mark.parametrize("S,K,T,r,sigma,q", [(100, 90, 0.25, 0.03, 0.4, 0.0), (60_000, 75_000, 0.1, 0.0, 0.8, 0.02)])
def test_put_call_parity(S, K, T, r, sigma, q):
    c, p = bs.price(S, K, T, r, sigma, "call", q), bs.price(S, K, T, r, sigma, "put", q)
    assert float(c - p) == pytest.approx(S * np.exp(-q * T) - K * np.exp(-r * T), rel=1e-10)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_greeks_match_finite_differences(kind):
    S, K, T, r, sig, q, h = 100.0, 105.0, 0.4, 0.03, 0.35, 0.01, 1e-4
    p = lambda **kw: float(bs.price(**{"S": S, "K": K, "T": T, "r": r, "sigma": sig, "kind": kind, "q": q, **kw}))  # noqa: E731
    assert float(bs.delta(S, K, T, r, sig, kind, q)) == pytest.approx((p(S=S + h) - p(S=S - h)) / (2 * h), rel=1e-5)
    assert float(bs.gamma(S, K, T, r, sig, q)) == pytest.approx((p(S=S + 1e-2) - 2 * p() + p(S=S - 1e-2)) / 1e-4, rel=1e-4)
    assert float(bs.vega(S, K, T, r, sig, q)) == pytest.approx((p(sigma=sig + h) - p(sigma=sig - h)) / (2 * h), rel=1e-5)
    assert float(bs.theta(S, K, T, r, sig, kind, q)) == pytest.approx(-(p(T=T + h) - p(T=T - h)) / (2 * h), rel=1e-5)
    assert float(bs.rho(S, K, T, r, sig, kind, q)) == pytest.approx((p(r=r + h) - p(r=r - h)) / (2 * h), rel=1e-5)


def test_black76_is_the_forward_formula():
    F, K, T, r, sig = 61_000, 60_000, 0.2, 0.05, 0.6
    d1 = (np.log(F / K) + 0.5 * sig**2 * T) / (sig * np.sqrt(T))
    from scipy.stats import norm
    expected = np.exp(-r * T) * (F * norm.cdf(d1) - K * norm.cdf(d1 - sig * np.sqrt(T)))
    assert float(bs.black76(F, K, T, r, sig, "call")) == pytest.approx(expected, rel=1e-12)


def test_invalid_kind_is_rejected():
    with pytest.raises(ValueError):
        bs.price(100, 100, 1, 0, 0.2, "straddle")


# --- Implied volatility --------------------------------------------------------------

def test_implied_vol_round_trips_across_a_wide_grid():
    worst = 0.0
    for T in (1 / 365, 30 / 365, 1.0):
        for m in (0.5, 0.8, 1.0, 1.2, 1.5):
            for sig in (0.1, 0.5, 1.0, 2.0):
                for kind in ("call", "put"):
                    target = float(bs.price(100, 100 * m, T, 0.02, sig, kind))
                    if target < 1e-6:  # prices this small carry no information about sigma
                        continue
                    worst = max(worst, abs(implied_vol(target, 100, 100 * m, T, 0.02, kind) - sig))
    assert worst < 1e-6


def test_bisection_rescues_cases_newton_cannot_handle():
    """Short-dated, deep out-of-the-money options have tiny vega, where Newton breaks down."""
    methods = []
    for m in (1.3, 1.6, 2.0, 3.0):
        target = float(bs.price(100, 100 * m, 7 / 365, 0.0, 1.5, "call"))
        res = implied_vol_details(target, 100, 100 * m, 7 / 365, 0.0, "call")
        assert res["sigma"] == pytest.approx(1.5, abs=1e-5)
        methods.append(res["method"])
    assert "bisection" in methods


def test_prices_outside_no_arbitrage_bounds_have_no_implied_vol():
    assert np.isnan(implied_vol(1.0, 100, 90, 0.5, 0.0, "call"))    # below intrinsic value (10)
    assert np.isnan(implied_vol(101.0, 100, 90, 0.5, 0.0, "call"))  # above the spot price
    assert np.isnan(implied_vol(5.0, 100, 90, 0.0, 0.0, "call"))    # already expired


# --- Deribit data --------------------------------------------------------------------

def test_parse_instrument_names():
    p = parse_instrument("BTC-3JAN27-95000-P")
    assert p["expiry"] == datetime(2027, 1, 3, 8, tzinfo=timezone.utc)
    assert (p["strike"], p["kind"], p["currency"]) == (95_000.0, "put", "BTC")
    with pytest.raises(ValueError):
        parse_instrument("BTC-PERPETUAL")


def test_chain_conversion_recovers_the_exchange_iv_exactly():
    """If BTC quoting, forwards, expiry times or the solver were wrong, this would disagree."""
    chain = build_chain(fake_deribit_summary(), now=NOW)
    assert chain["expiry"].nunique() == 3 and len(chain) == 3 * 29 * 2
    solved = chain.dropna(subset=["our_iv"])
    assert (solved["our_iv"] - solved["deribit_iv"]).abs().max() < 1e-6  # 0.0001 vol points
    # The solver declines only options whose time value is numerically zero (worth < $0.000001 here);
    # on the real exchange the minimum price tick is far above this.
    unsolved = chain[chain["our_iv"].isna()]
    time_value = unsolved["mark_usd"] - (unsolved["forward"] - unsolved["strike"]).abs()
    assert len(unsolved) < 0.1 * len(chain) and (time_value < 1e-6).all()
    assert agreement(chain)["share_within_0_5_pts"] == 1.0


def test_options_about_to_expire_are_excluded():
    chain = build_chain(fake_deribit_summary(days=(0.5, 30)), now=NOW, min_days=1)
    assert chain["T"].min() * 365 >= 1


def test_otm_selection_keeps_one_option_per_strike():
    otm = otm_only(build_chain(fake_deribit_summary(), now=NOW))
    assert (otm.loc[otm.kind == "put", "strike"] < 60_000).all()
    assert (otm.loc[otm.kind == "call", "strike"] >= 60_000).all()
    assert not otm.duplicated(["expiry", "strike"]).any()


def test_term_structure_and_skew_read_the_smile_correctly():
    ts = term_structure(otm_only(build_chain(fake_deribit_summary(), now=NOW)))
    assert ts["atm_iv"].to_numpy() == pytest.approx(smile(0.0), abs=2e-3)
    assert ts["skew_10pct"].to_numpy() == pytest.approx(smile(-0.1) - smile(0.1), abs=2e-3)  # = +0.02


def test_no_extrapolation_beyond_quoted_strikes():
    chain = otm_only(build_chain(fake_deribit_summary(), now=NOW))
    narrow = chain[chain["log_moneyness"].abs() < 0.05]
    assert np.isnan(term_structure(narrow)["skew_10pct"]).all()


# --- Hedging ------------------------------------------------------------------------

S0, K, T = 60_000.0, 60_000.0, 30 / 365


def test_simulated_paths_have_the_requested_volatility():
    paths = gbm_paths(S0, 0.6, T, 720, 500, seed=3)
    assert paths.shape == (500, 721) and (paths[:, 0] == S0).all()
    assert realised_vol(paths, T).mean() == pytest.approx(0.6, abs=0.01)


def test_hedging_error_shrinks_with_the_square_root_of_rebalances():
    paths = gbm_paths(S0, 0.6, T, 720, 2000, seed=4)
    daily, hourly = (summarise(hedge_short_call(paths, K, T, 0.6, rebalance_every=e)) for e in (24, 1))
    assert 4.0 < daily["std_pnl"] / hourly["std_pnl"] < 6.0          # theory: sqrt(24) = 4.9
    assert abs(hourly["mean_pnl"]) < 3 * hourly["std_pnl"] / np.sqrt(2000)  # fair price: no systematic P&L


def test_selling_volatility_too_cheap_or_too_rich():
    vega = float(bs.vega(S0, K, T, 0, 0.6))
    for true_vol in (0.4, 0.8):
        paths = gbm_paths(S0, true_vol, T, 720, 2000, seed=5)
        mean = summarise(hedge_short_call(paths, K, T, 0.6, rebalance_every=1))["mean_pnl"]
        assert mean == pytest.approx(vega * (0.6 - true_vol), rel=0.1)


def test_costs_grow_with_rebalancing_frequency():
    paths = gbm_paths(S0, 0.6, T, 720, 500, seed=6)
    cost = {e: summarise(hedge_short_call(paths, K, T, 0.6, rebalance_every=e, cost_bps=5))["mean_costs"] for e in (24, 1)}
    assert 0 < cost[24] < cost[1]


def test_historical_windows_do_not_overlap_and_start_at_spot():
    closes = pd.Series(np.linspace(50_000, 70_000, 1_000))
    paths = historical_paths(closes, steps=100, S0=S0)
    assert paths.shape == (9, 101) and (paths[:, 0] == S0).all()


# --- Command-line reports ---------------------------------------------------------------

def test_snapshot_report(tmp_path):
    r = run_snapshot(tmp_path / "results", tmp_path / "data", summary=fake_deribit_summary(), now=NOW)
    assert r["agreement_with_deribit"]["median_abs_diff_vol_pts"] < 1e-3
    assert (tmp_path / "results" / "SNAPSHOT.md").exists() and (tmp_path / "results" / "smile.png").exists()
    assert len(list((tmp_path / "data").glob("chain_*.parquet"))) == 1


def test_hedging_report_with_historical_paths(tmp_path):
    fake_closes = pd.Series(gbm_paths(S0, 0.5, 120 / 365, 120 * 24, 1, seed=7)[0])  # 120 days of "hourly prices"
    r = run_hedge(tmp_path, n_paths=300, seed=0, closes=fake_closes)
    assert r["historical"]["windows"] == 4
    report = (tmp_path / "HEDGING.md").read_text()
    assert "Real BTC prices" in report and "√24" in report
    assert json.loads((tmp_path / "hedging.json").read_text())["setup"]["days"] == 30
