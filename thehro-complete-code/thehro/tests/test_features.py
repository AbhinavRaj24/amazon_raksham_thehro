"""Point-in-time correctness: no feature may use anything that happened after the payment."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from thehro.config import MIN_PER_DAY
from thehro.features import ALL_FEATURES, EventIndex, build_features
from thehro.models import training_labels
from thehro.simulate import SimConfig, simulate


def test_window_excludes_the_event_itself_and_later_events():
    ent = np.array([1, 1, 1, 2])
    t = np.array([10.0, 20.0, 30.0, 20.0])
    idx = EventIndex(ent, t, {"amount": np.array([5.0, 7.0, 11.0, 100.0])})
    # at t=20 for entity 1: only the event at t=10 is strictly earlier
    assert idx.window("amount", [1], [20.0], 60)[0] == 5.0
    assert idx.window("count", [1], [20.0], 60)[0] == 1
    # window of 15 minutes at t=30 sees only t=20
    assert idx.window("amount", [1], [30.0], 15)[0] == 7.0
    # other entities never leak in
    assert idx.window("amount", [2], [25.0], 60)[0] == 100.0
    assert np.isnan(idx.last_before([2], [20.0])[0])
    assert idx.last_before([1], [30.0])[0] == 20.0


@pytest.fixture(scope="module")
def small_world():
    return simulate(SimConfig(seed=11, n_users=3_000, n_merchants=2_000, n_vendors=400, n_external=20_000,
                              n_suppliers=600), verbose=False)


def test_features_do_not_change_when_the_future_is_deleted(small_world):
    """Build features on the full world and on the world cut at day 40: every payment before
    the cut must get exactly the same features."""
    w = small_world
    cut = 40 * MIN_PER_DAY
    p = w.payments[w.payments.t < cut].reset_index(drop=True)
    w_cut = dataclasses.replace(w, payments=p, fd_events=w.fd_events[w.fd_events.t < cut].reset_index(drop=True))
    f_full, _ = build_features(w, verbose=False)
    f_cut, _ = build_features(w_cut, verbose=False)
    a = f_full[f_full.t < cut].set_index("pid")[ALL_FEATURES].sort_index()
    b = f_cut.set_index("pid")[ALL_FEATURES].sort_index()
    assert len(a) == len(b) > 1000
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_training_labels_come_only_from_reports_filed_before_the_cutoff(small_world):
    f, _ = build_features(small_world, verbose=False)
    window, y = training_labels(f)
    labelled = y == 1
    assert (f.is_scam.values[labelled] == 1).all()
    assert (f.report_t.values[labelled] < 38 * MIN_PER_DAY).all()
    # some scams are never reported, so labels are fewer than true scams
    assert labelled.sum() < (f.is_scam.values == 1).sum()
