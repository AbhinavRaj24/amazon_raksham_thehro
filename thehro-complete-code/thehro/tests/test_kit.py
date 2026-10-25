"""Test-kit helpers: intervals, right/wrong rules, the split attack and PaySim's point-in-time features.
None of these need the cached world, so they run in the normal `pytest -q`."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from thehro import kit, redteam


def test_wilson_matches_the_deck():
    lo, hi = kit.wilson(679, 720)                 # slide 6: 94.3% (95% CI 92.4-95.8)
    assert round(lo, 3) == 0.924 and round(hi, 3) == 0.958


def test_poisson_matches_the_deck():
    lo, hi = kit.poisson(70)                      # slide 6: 0.89 per 10,000 (95% CI 0.69-1.12/1.13)
    assert round(lo / 786_121 * 1e4, 2) == 0.69 and 1.12 <= round(hi / 786_121 * 1e4, 2) <= 1.13
    assert kit.poisson(0) == (0.0, pytest.approx(3.689, abs=1e-3))   # 0 holds: bound 1 in 213,000


def test_right_rules():
    assert kit.is_right("interrupt", 2) and not kit.is_right("interrupt", 1)
    assert kit.is_right("no_interrupt", 1) and not kit.is_right("no_interrupt", 2)
    assert kit.is_right("never_hold", 3) and not kit.is_right("never_hold", 4)
    with pytest.raises(ValueError):
        kit.is_right("maybe", 0)


@pytest.mark.parametrize("amount", [10_000, 10_500, 19_800, 19_801, 75_313, 5_000_000])
def test_split_parts_sum_and_cap(amount):
    parts = redteam._split_amount(float(amount), np.random.default_rng(0))
    assert parts.sum() == amount
    assert (parts <= redteam.SPLIT_MAX).all() and (parts > 0).all()
    assert len(parts) == int(np.ceil(amount / redteam.SPLIT_MAX))
    assert (parts == np.floor(parts)).all()


def _paysim():
    p = Path(__file__).resolve().parents[1] / "scripts" / "paysim_benchmark.py"
    spec = importlib.util.spec_from_file_location("paysim_benchmark", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_paysim_features_use_earlier_steps_only():
    ps = _paysim()
    p = pd.DataFrame({
        "step": [1, 1, 2, 2, 30],
        "type": ["TRANSFER"] * 5,
        "amount": [100.0, 200.0, 300.0, 400.0, 500.0],
        "nameOrig": ["C1", "C2", "C3", "C1", "C4"],
        "oldbalanceOrg": [100.0, 0.0, 1000.0, 50.0, 500.0],
        "nameDest": ["C9"] * 5,
        "oldbalanceDest": [0.0] * 5, "isFraud": [0] * 5, "isFlaggedFraud": [0] * 5,
    })
    f = ps.features(p)
    # same-step transactions never see each other; step 2 sees both step-1 payments
    assert f.dest_prev_in_count.tolist() == [0, 0, 2, 2, 4]
    assert f.dest_inflow_24_log.round(4).tolist()[:4] == [0, 0, round(np.log10(301), 4), round(np.log10(301), 4)]
    assert f.dest_inflow_24_log.iloc[4] == 0                 # steps 1-2 are outside the 24-step window at step 30
    assert f.dest_new_payers_24.tolist()[:4] == [0, 0, 2, 2]
    assert np.isnan(f.dest_steps_since_first_seen.iloc[0]) and f.dest_steps_since_first_seen.iloc[4] == 29
    assert f.empties_account.tolist() == [1, 0, 0, 1, 1]
    assert "newbalanceOrig" not in f and "isFlaggedFraud" not in f
