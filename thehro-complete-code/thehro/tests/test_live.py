"""Live features must equal the training features: same code on the event slice, network state replayed."""
import numpy as np
import pandas as pd
import pytest

from thehro.campaign import detect_campaigns  # noqa: F401  (the detector the replay mirrors)
from thehro.config import MIN_PER_DAY
from thehro.features import ALL_FEATURES, build_features
from thehro.live import LiveWorld
from thehro.simulate import SimConfig, simulate


@pytest.fixture(scope="module")
def small():
    w = simulate(SimConfig(seed=11, n_users=3_000, n_merchants=2_000, n_vendors=400, n_external=20_000,
                           n_suppliers=600), verbose=False)
    f, denom = build_features(w, verbose=False, keep_state=True)
    return w, f, denom


def test_defaults_are_unchanged(small):
    """keep_state only adds denom['state']; the features themselves are the plain build_features output."""
    w, f, _ = small
    f_plain, _ = build_features(w, verbose=False)
    pd.testing.assert_frame_equal(f[ALL_FEATURES], f_plain[ALL_FEATURES])


def test_live_features_equal_batch_features(small):
    w, f, denom = small
    live = LiveWorld(w, denom["state"])
    rows = f[f.t.values >= 20 * MIN_PER_DAY].sample(200, random_state=0).sort_values("t")
    # include every scam payment's neighbourhood too: those are the rows where the network flag matters
    rows = pd.concat([rows, f[f.is_scam.values == 1].sample(50, random_state=1)]).drop_duplicates("pid").sort_values("t")
    got = pd.DataFrame([live.historical_features(int(pid))[ALL_FEATURES] for pid in rows.pid.values])
    want = rows[ALL_FEATURES].reset_index(drop=True)
    got = got.reset_index(drop=True).astype(float)
    want = want.astype(float)
    same = np.isclose(got.values, want.values, rtol=1e-9, atol=1e-12, equal_nan=True)
    bad = [(rows.pid.values[i], ALL_FEATURES[j], want.iat[i, j], got.iat[i, j]) for i, j in zip(*np.where(~same))]
    assert not bad, f"{len(bad)} mismatches, first: {bad[:5]}"


def test_new_payment_sees_its_own_history(small):
    """A payment added live gets features from the events before it, including ones added in the session."""
    w, f, denom = small
    live = LiveWorld(w, denom["state"])
    now = 45 * MIN_PER_DAY
    live.set_now(now)
    cust = int(f[(f.t.values < now)].payer.value_counts().index[0])
    payee = live.pick_payee(cust, "new_individual")
    earlier = live.payment_rows([{"t": now - 60, "payer": cust, "payee": live.pick_payee(cust, "new_individual", 1),
                                  "amount": 9_000.0, "channel": 0, "kind": 7}])
    new = live.payment_rows([{"t": now, "payer": cust, "payee": payee, "amount": 20_000.0, "channel": 0, "kind": 7}])
    feats = live.slice_features(cust, payee, now, pd.concat([earlier, new], ignore_index=True))
    row = feats.loc[feats.pid.values == int(new.pid.iloc[0])].iloc[0]
    assert row.is_new_payee == 1
    assert row.recent_payee_payments_7d >= 1          # the earlier session payment counts
    assert row.n_payments_1h >= 1
