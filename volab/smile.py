"""Summaries of the volatility smile.

If Black-Scholes were exactly right, every option on the same expiry would imply
the same volatility and the smile would be a flat line. The shape of the real
curve is a direct measure of how the market disagrees with the model:
  * skew  - downside puts priced richer than upside calls (crash protection)
  * smile - both wings richer than the middle (fatter tails than a normal)
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SKEW_K = 0.10  # compare IV at 10% below vs 10% above the forward (log-moneyness)


def _interp(chain: pd.DataFrame, k: float) -> float:
    c = chain.dropna(subset=["our_iv"]).sort_values("log_moneyness")
    x, y = c["log_moneyness"].to_numpy(), c["our_iv"].to_numpy()
    if len(x) < 2 or not (x.min() <= k <= x.max()):
        return np.nan  # never extrapolate beyond the quoted strikes
    return float(np.interp(k, x, y))


def term_structure(otm_chain: pd.DataFrame) -> pd.DataFrame:
    """Per expiry: days to expiry, ATM implied vol, and skew (put wing minus call wing)."""
    rows = []
    for expiry, g in otm_chain.groupby("expiry"):
        rows.append({
            "expiry": expiry, "days": round(float(g["T"].iloc[0]) * 365, 1), "options": len(g),
            "atm_iv": _interp(g, 0.0),
            "skew_10pct": _interp(g, -SKEW_K) - _interp(g, SKEW_K),
        })
    return pd.DataFrame(rows).sort_values("days").reset_index(drop=True)


def agreement(chain: pd.DataFrame) -> dict:
    """How closely our implied vols match Deribit's published mark IVs (in vol points)."""
    both = chain.dropna(subset=["our_iv", "deribit_iv"])
    diff = (both["our_iv"] - both["deribit_iv"]).abs() * 100
    return {
        "options_compared": int(len(both)),
        "median_abs_diff_vol_pts": float(diff.median()) if len(both) else None,
        "p95_abs_diff_vol_pts": float(diff.quantile(0.95)) if len(both) else None,
        "share_within_0_5_pts": float((diff <= 0.5).mean()) if len(both) else None,
        "solver_failures": int(chain["our_iv"].isna().sum()),
    }


def plot_smiles(otm_chain: pd.DataFrame, max_expiries: int = 5, max_abs_k: float = 0.6):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    expiries = sorted(otm_chain["expiry"].unique())
    step = max(1, len(expiries) // max_expiries)
    for expiry in expiries[::step][:max_expiries]:
        g = otm_chain[(otm_chain["expiry"] == expiry) & (otm_chain["log_moneyness"].abs() <= max_abs_k)]
        g = g.dropna(subset=["our_iv"]).sort_values("log_moneyness")
        days = g["T"].iloc[0] * 365 if len(g) else 0
        label = f"{pd.Timestamp(expiry):%d %b %y} ({days:.0f}d)"
        ax.plot(g["log_moneyness"], g["our_iv"] * 100, marker="o", ms=3, label=label)
    ax.axvline(0, color="grey", lw=0.8, ls="--")
    ax.set(xlabel="log-moneyness  ln(K / F)   (left: downside puts, right: upside calls)",
           ylabel="Implied volatility (%)", title="BTC volatility smile by expiry")
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


def plot_term_structure(ts: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(ts["days"], ts["atm_iv"] * 100, marker="o", color="#2F6F62", label="ATM implied vol")
    ax.set(xlabel="Days to expiry", ylabel="At-the-money implied volatility (%)",
           title="BTC volatility term structure")
    fig.tight_layout()
    return fig
