"""Black-Scholes-Merton prices and Greeks, vectorised with NumPy.

Model: the underlying follows geometric Brownian motion with constant volatility
sigma, risk-free rate r and continuous yield q (for crypto, q plays the role of
a "convenience yield"; it is usually 0 here).

    d1 = (ln(S/K) + (r - q + sigma^2/2) T) / (sigma sqrt(T))
    d2 = d1 - sigma sqrt(T)
    call = S e^{-qT} N(d1) - K e^{-rT} N(d2)
    put  = K e^{-rT} N(-d2) - S e^{-qT} N(-d1)

`black76` prices options on a forward F (Deribit quotes BTC options against the
futures price for each expiry), which is Black-Scholes with S = F and r = q.

Conventions: T in years, rates and volatility annualised as decimals
(0.6 = 60%). vega and rho are per 1.00 change (divide by 100 for "per 1%"),
theta is per year (divide by 365 for "per day").
"""

from __future__ import annotations

import numpy as np
from scipy.special import ndtr  # standard normal CDF, vectorised

SQRT_2PI = np.sqrt(2 * np.pi)


def _pdf(x):
    return np.exp(-0.5 * x * x) / SQRT_2PI


def _check_kind(kind: str) -> None:
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put'")


def d1_d2(S, K, T, r, sigma, q=0.0):
    S, K, T, sigma = (np.asarray(a, dtype=float) for a in (S, K, T, sigma))
    vol_sqrt_t = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


def price(S, K, T, r, sigma, kind="call", q=0.0):
    _check_kind(kind)
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    disc_s, disc_k = S * np.exp(-q * np.asarray(T)), K * np.exp(-r * np.asarray(T))
    if kind == "call":
        return disc_s * ndtr(d1) - disc_k * ndtr(d2)
    return disc_k * ndtr(-d2) - disc_s * ndtr(-d1)


def delta(S, K, T, r, sigma, kind="call", q=0.0):
    _check_kind(kind)
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    carry = np.exp(-q * np.asarray(T))
    return carry * ndtr(d1) if kind == "call" else carry * (ndtr(d1) - 1)


def gamma(S, K, T, r, sigma, q=0.0):
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    return np.exp(-q * np.asarray(T)) * _pdf(d1) / (np.asarray(S) * sigma * np.sqrt(T))


def vega(S, K, T, r, sigma, q=0.0):
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    return np.asarray(S) * np.exp(-q * np.asarray(T)) * _pdf(d1) * np.sqrt(T)


def theta(S, K, T, r, sigma, kind="call", q=0.0):
    """Rate of change of the price as time passes (per year; usually negative)."""
    _check_kind(kind)
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    T = np.asarray(T, dtype=float)
    decay = -np.asarray(S) * np.exp(-q * T) * _pdf(d1) * sigma / (2 * np.sqrt(T))
    if kind == "call":
        return decay + q * S * np.exp(-q * T) * ndtr(d1) - r * K * np.exp(-r * T) * ndtr(d2)
    return decay - q * S * np.exp(-q * T) * ndtr(-d1) + r * K * np.exp(-r * T) * ndtr(-d2)


def rho(S, K, T, r, sigma, kind="call", q=0.0):
    _check_kind(kind)
    _, d2 = d1_d2(S, K, T, r, sigma, q)
    T = np.asarray(T, dtype=float)
    if kind == "call":
        return K * T * np.exp(-r * T) * ndtr(d2)
    return -K * T * np.exp(-r * T) * ndtr(-d2)


def greeks(S, K, T, r, sigma, kind="call", q=0.0) -> dict:
    return {
        "price": price(S, K, T, r, sigma, kind, q),
        "delta": delta(S, K, T, r, sigma, kind, q),
        "gamma": gamma(S, K, T, r, sigma, q),
        "vega": vega(S, K, T, r, sigma, q),
        "theta": theta(S, K, T, r, sigma, kind, q),
        "rho": rho(S, K, T, r, sigma, kind, q),
    }


def black76(F, K, T, r, sigma, kind="call"):
    """Option on a forward/futures price F: Black-Scholes with S = F and q = r."""
    return price(F, K, T, r, sigma, kind, q=r)


def black76_delta(F, K, T, r, sigma, kind="call"):
    """Sensitivity to the forward price."""
    return delta(F, K, T, r, sigma, kind, q=r)
