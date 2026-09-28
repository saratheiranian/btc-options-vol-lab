"""Live BTC option data from Deribit's public API (no account needed).

https://docs.deribit.com/#public-get_book_summary_by_currency

Deribit specifics that matter for pricing:
  * Option prices are quoted in BTC, not USD. USD value = BTC price x underlying price.
  * Each expiry is priced against its own underlying (a futures price, or a
    synthetic forward), so the Black-76 forward model is used, with r = 0 as in
    Deribit's own mark-IV calculation.
  * Options expire at 08:00 UTC; time to expiry uses an ACT/365 year.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from .iv import implied_vol_black76

API = "https://www.deribit.com/api/v2/public"
INSTRUMENT = re.compile(r"^(?P<cur>[A-Z]+)-(?P<day>\d{1,2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})-(?P<strike>[\dd]+)-(?P<cp>[CP])$")
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def parse_instrument(name: str) -> dict:
    """'BTC-27DEC26-60000-C' -> currency, expiry (08:00 UTC), strike, kind."""
    m = INSTRUMENT.match(name)
    if not m:
        raise ValueError(f"not a Deribit option name: {name}")
    expiry = datetime(2000 + int(m["yy"]), MONTHS[m["mon"]], int(m["day"]), 8, tzinfo=timezone.utc)
    return {"currency": m["cur"], "expiry": expiry, "strike": float(m["strike"].replace("d", ".")),
            "kind": "call" if m["cp"] == "C" else "put"}


def fetch_book_summary(currency: str = "BTC", timeout: float = 15) -> list[dict]:
    resp = requests.get(f"{API}/get_book_summary_by_currency",
                        params={"currency": currency, "kind": "option"}, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"Deribit error: {body['error']}")
    return body["result"]


def _to_usd(btc_price: float | None, underlying: float) -> float:
    """Deribit quotes option prices in BTC; value them in USD at the option's underlying price."""
    return btc_price * underlying if btc_price else np.nan


def build_chain(summary: list[dict], now: datetime | None = None, min_days: float = 1.0) -> pd.DataFrame:
    """Tidy option chain with our own implied volatility next to Deribit's.

    Keeps options with at least `min_days` to expiry and a usable mark price.
    Columns include: expiry, T (years), strike, kind, forward, log_moneyness,
    mark_usd, bid_usd, ask_usd, deribit_iv, our_iv, open_interest.
    """
    now = now or datetime.now(timezone.utc)
    rows = []
    for s in summary:
        try:
            inst = parse_instrument(s["instrument_name"])
        except ValueError:
            continue
        T = (inst["expiry"] - now).total_seconds() / (365 * 24 * 3600)
        fwd, mark = s.get("underlying_price"), s.get("mark_price")
        if T < min_days / 365 or not fwd or not mark or mark <= 0:
            continue
        rows.append({
            "instrument": s["instrument_name"], "expiry": inst["expiry"], "T": T,
            "strike": inst["strike"], "kind": inst["kind"], "forward": fwd,
            "log_moneyness": np.log(inst["strike"] / fwd),
            "mark_usd": _to_usd(mark, fwd), "bid_usd": _to_usd(s.get("bid_price"), fwd),
            "ask_usd": _to_usd(s.get("ask_price"), fwd),
            "deribit_iv": s["mark_iv"] / 100 if s.get("mark_iv") else np.nan,
            "open_interest": s.get("open_interest", 0.0),
        })
    chain = pd.DataFrame(rows)
    if chain.empty:
        return chain
    chain["our_iv"] = [implied_vol_black76(p, f, k, t, 0.0, kd)
                       for p, f, k, t, kd in chain[["mark_usd", "forward", "strike", "T", "kind"]].itertuples(index=False)]
    return chain.sort_values(["expiry", "strike", "kind"]).reset_index(drop=True)


def otm_only(chain: pd.DataFrame) -> pd.DataFrame:
    """One price per strike: puts below the forward, calls at or above it.

    Out-of-the-money options are the liquid ones, and their prices carry the
    information; in-the-money prices are mostly intrinsic value.
    """
    otm = ((chain["kind"] == "put") & (chain["strike"] < chain["forward"])) | \
          ((chain["kind"] == "call") & (chain["strike"] >= chain["forward"]))
    return chain[otm].reset_index(drop=True)


def fetch_hourly_closes(days: int = 365, instrument: str = "BTC-PERPETUAL", timeout: float = 30) -> pd.Series:
    """Hourly BTC closing prices from Deribit's public chart data, for hedging on real paths."""
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start, closes = end - days * 24 * 3600 * 1000, {}
    step = 60 * 24 * 3600 * 1000  # request 60 days at a time
    for chunk_start in range(start, end, step):
        resp = requests.get(f"{API}/get_tradingview_chart_data", timeout=timeout, params={
            "instrument_name": instrument, "resolution": "60",
            "start_timestamp": chunk_start, "end_timestamp": min(chunk_start + step, end)})
        resp.raise_for_status()
        r = resp.json()["result"]
        closes.update(zip(r["ticks"], r["close"], strict=True))
    s = pd.Series(closes).sort_index()
    s.index = pd.to_datetime(s.index, unit="ms", utc=True)
    return s[~s.index.duplicated()]
