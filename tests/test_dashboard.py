"""Dashboard tests with Streamlit's AppTest; Deribit is faked, so no network is needed."""

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from test_volab import fake_deribit_summary

import volab.deribit


@pytest.fixture(autouse=True)
def fresh_cache():
    """Streamlit's data cache outlives a single app run; clear it so tests can't see each other's data."""
    st.cache_data.clear()


def start():
    return AppTest.from_file("../dashboard/app.py", default_timeout=60).run()


def test_all_three_tabs_render_with_live_data(monkeypatch):
    monkeypatch.setattr(volab.deribit, "fetch_book_summary", lambda *a, **k: fake_deribit_summary())
    at = start()
    assert not at.exception
    labels = {m.label for m in at.metric}
    assert {"Price", "Delta", "Vega"} <= labels                           # pricer
    assert "Median gap vs Deribit IV" in labels                          # live smile
    assert {"Premium received", "Mean P&L", "Std. dev. of P&L"} <= labels  # hedging lab
    assert any("Implied volatility" in s.value for s in at.success)       # IV solver round-trips the price


def test_pricer_reacts_to_inputs(monkeypatch):
    monkeypatch.setattr(volab.deribit, "fetch_book_summary", lambda *a, **k: fake_deribit_summary())
    at = start()
    price = lambda: float(next(m for m in at.metric if m.label == "Price").value.strip("$").replace(",", ""))  # noqa: E731
    atm = price()
    at.number_input[3].set_value(90.0).run()  # volatility 60% -> 90%
    assert price() > atm


def test_deribit_outage_is_handled(monkeypatch):
    def fail(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(volab.deribit, "fetch_book_summary", fail)
    at = start()
    assert not at.exception
    assert any("Couldn't fetch data from Deribit" in e.value for e in at.error)
    assert any(m.label == "Price" for m in at.metric)  # the rest of the app still works
