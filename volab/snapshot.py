"""Take a live snapshot of the BTC options market and analyse the smile.

    python -m volab.snapshot

Fetches every BTC option from Deribit, computes our own implied volatility for
each, checks it against Deribit's published mark IV, and writes:
  data/chain_<timestamp>.parquet   the full chain (reusable, re-analysable)
  results/SNAPSHOT.md              term structure, skew, agreement check
  results/smile.png, results/term_structure.png
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .deribit import build_chain, fetch_book_summary, otm_only
from .smile import agreement, plot_smiles, plot_term_structure, term_structure


def run(out_dir: Path, data_dir: Path, summary: list[dict] | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    chain = build_chain(summary if summary is not None else fetch_book_summary("BTC"), now=now)
    if chain.empty:
        raise SystemExit("no usable options returned")
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    chain.to_parquet(data_dir / f"chain_{now:%Y%m%dT%H%M%SZ}.parquet", index=False)

    otm = otm_only(chain)
    ts = term_structure(otm)
    check = agreement(chain)
    plot_smiles(otm).savefig(out_dir / "smile.png", dpi=130)
    plot_term_structure(ts).savefig(out_dir / "term_structure.png", dpi=130)

    result = {"taken_at": now.isoformat(timespec="seconds"), "options": int(len(chain)),
              "expiries": int(chain["expiry"].nunique()), "agreement_with_deribit": check,
              "term_structure": [{**r, "expiry": str(r["expiry"])[:10]} for r in ts.to_dict("records")]}
    (out_dir / "snapshot.json").write_text(json.dumps(result, indent=2, default=float))
    (out_dir / "SNAPSHOT.md").write_text(_report(result))
    return result


def _pct(x):
    return "n/a" if x is None or x != x else f"{x * 100:.1f}%"


def _report(r: dict) -> str:
    a = r["agreement_with_deribit"]
    rows = "\n".join(
        f"| {t['expiry']} | {t['days']:g} | {t['options']} | {_pct(t['atm_iv'])} | "
        + ("n/a" if t["skew_10pct"] != t["skew_10pct"] else f"{t['skew_10pct'] * 100:+.1f} pts") + " |"
        for t in r["term_structure"])
    return f"""# BTC options snapshot

Taken {r['taken_at']} from Deribit's public API: {r['options']:,} options across {r['expiries']} expiries.

## Does our pricing engine agree with the exchange?

Our Newton-Raphson/bisection solver was run on every option's mark price and compared with Deribit's published mark IV.

| | |
|---|---|
| Options compared | {a['options_compared']:,} |
| Median difference | {a['median_abs_diff_vol_pts']:.3f} vol points |
| 95th percentile difference | {a['p95_abs_diff_vol_pts']:.3f} vol points |
| Within 0.5 vol points | {a['share_within_0_5_pts'] * 100:.1f}% |
| Solver returned no answer | {a['solver_failures']} |

## Term structure and skew

Skew = implied vol 10% below the forward minus 10% above it (positive: downside protection costs more).

| Expiry | Days | Options | ATM IV | Skew |
|---|---|---|---|---|
{rows}

![Smile](smile.png)

![Term structure](term_structure.png)
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--data", type=Path, default=Path("data"))
    a = ap.parse_args()
    r = run(a.out, a.data)
    agree = r["agreement_with_deribit"]
    print(f"{r['options']} options, {r['expiries']} expiries; median |our IV - Deribit IV| = "
          f"{agree['median_abs_diff_vol_pts']:.3f} vol pts. Report: {a.out / 'SNAPSHOT.md'}")


if __name__ == "__main__":
    main()
