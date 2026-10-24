"""Point-in-time features for every payment a customer pushes to a person or business.

Every feature uses only what was known *before* the payment: each window query
excludes the payment itself and anything after it. tests/test_features.py
checks this.

Three signal families, matching the architecture:
  J  journey   - the payer's own recent history (data the bank already holds)
  P  payee     - the receiving account (in production: borrowed from NPCI /
                 MuleHunter.AI / IDPIC risk scores, DoT FRI and the I4C
                 Suspect Registry; here computed from the simulated network)
  C  context   - live, on-device, opt-in flags while paying (no call audio,
                 no message content, no contact list)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MIN_PER_DAY, WARMUP_END
from .simulate import (CH_UPI_P2M, KIND, SCAM_SIDE_KINDS, T_MERCHANT, World,
                       pair_key)
from .campaign import detect_campaigns

J_FEATURES = [
    "amount_log", "channel", "hour", "is_night",
    "is_new_payee", "n_prev_to_payee", "days_since_first_to_payee",
    "amt_vs_usual", "amt_vs_prev_same_payee", "growth_vs_payee_first",
    "new_payees_7d", "recent_payee_payments_7d", "recent_payee_sum_7d_log",
    "growth_vs_recent_avg", "push_out_24h_vs_usual", "n_payments_1h",
    "fd_break_48h", "stranger_credits_24h", "is_investor",
]
P_FEATURES = [
    "payee_type", "payee_is_valid", "payee_age_days", "payee_idle_days",
    "payee_new_payers_24h", "payee_new_payers_7d", "payee_inflow_24h_log",
    "payee_passthrough_24h", "payee_registry", "payee_fri_band",
    "payee_in_campaign", "payee_campaign_size",
]
C_FEATURES = [
    "on_call", "caller_unknown", "call_ott", "call_min", "caller_fri_band",
    "screen_share", "remote_app", "app_switches",
]
FAMILIES = {"journey": J_FEATURES, "payee": P_FEATURES, "context": C_FEATURES}
ALL_FEATURES = J_FEATURES + P_FEATURES + C_FEATURES
CATEGORICAL = ["channel", "payee_type"]

SCALE = np.int64(10_000_000)  # entity * SCALE + seconds


class EventIndex:
    """Events sorted by (entity, time) for fast trailing-window queries."""

    def __init__(self, entity, t_min, values=None):
        entity = np.asarray(entity, dtype=np.int64)
        sec = np.floor(np.asarray(t_min, dtype=float) * 60).astype(np.int64)
        order = np.lexsort((sec, entity))
        self.entity = entity[order]
        self.sec = sec[order]
        self.key = self.entity * SCALE + self.sec
        self.values = {}
        if values:
            for name, v in values.items():
                v = np.asarray(v, dtype=float)[order]
                self.values[name] = np.concatenate([[0.0], np.cumsum(v)])
        self.count = np.concatenate([[0.0], np.arange(1, len(order) + 1, dtype=float)])

    def _bounds(self, ent_q, t_q, window_min):
        ent_q = np.asarray(ent_q, dtype=np.int64)
        sec_q = np.floor(np.asarray(t_q, dtype=float) * 60).astype(np.int64)
        hi = np.searchsorted(self.key, ent_q * SCALE + sec_q, side="left")
        lo = np.searchsorted(self.key, ent_q * SCALE + sec_q - int(window_min * 60), side="left")
        return lo, hi

    def window(self, name, ent_q, t_q, window_min):
        """Sum of `name` over events of the same entity in [t - window, t)."""
        lo, hi = self._bounds(ent_q, t_q, window_min)
        cs = self.count if name == "count" else self.values[name]
        return cs[hi] - cs[lo]

    def last_before(self, ent_q, t_q):
        """Time (minutes) of the entity's last event strictly before t, NaN if none."""
        ent_q = np.asarray(ent_q, dtype=np.int64)
        sec_q = np.floor(np.asarray(t_q, dtype=float) * 60).astype(np.int64)
        idx = np.searchsorted(self.key, ent_q * SCALE + sec_q, side="left") - 1
        ok = idx >= 0
        idx_c = np.where(ok, idx, 0)
        ok &= self.entity[idx_c] == ent_q
        return np.where(ok, self.sec[idx_c] / 60.0, np.nan)


def build_features(world: World, verbose: bool = True,
                   campaign_size: int = 3) -> tuple[pd.DataFrame, dict]:
    """Return (scored payments with features, denominators).

    Scored payments = customer payments to people or businesses (not shop
    payments to registered merchants, which are fast-pathed).
    """
    cfg = world.cfg
    U = cfg.n_users
    p = world.payments
    acc = world.accounts
    acc_type = acc.type.values
    is_sink = acc.is_sink.values

    t = p.t.values
    payer = p.payer.values.astype(np.int64)
    payee = p.payee.values.astype(np.int64)
    amount = p.amount.values.astype(float)
    kind = p.kind.values

    # pair history (all payments, in time order)
    keys = pair_key(payer, payee)
    pre = np.isin(keys, world.pre_pairs)
    _, first_idx = np.unique(keys, return_index=True)
    first = np.zeros(len(p), dtype=bool)
    first[first_idx] = True
    s_keys = pd.Series(keys)
    n_prev = s_keys.groupby(keys).cumcount().values
    first_t = pd.Series(t).groupby(keys).transform("min").values
    first_amt = pd.Series(amount).groupby(keys).transform("first").values
    prev_amt = pd.Series(amount).groupby(keys).shift(1).values
    is_new_payee = first & ~pre
    days_since_first = np.where(pre, 365.0, (t - first_t) / MIN_PER_DAY)

    customer = (payer < U) & ~np.isin(kind, SCAM_SIDE_KINDS)
    push = customer & (acc_type[payee] != T_MERCHANT)
    scored = push.copy()

    # ---------------- customer baselines from the warm-up weeks ----------------
    warm = push & (t < WARMUP_END * MIN_PER_DAY)
    wdf = pd.DataFrame({"payer": payer[warm], "amount": amount[warm]})
    base_med = wdf.groupby("payer").amount.median()
    base_daily = wdf.groupby("payer").amount.sum() / WARMUP_END
    seg = world.users.segment.values
    seg_med = pd.Series(base_med.reindex(np.arange(U)).values).groupby(seg).median()
    seg_daily = pd.Series(base_daily.reindex(np.arange(U)).values).groupby(seg).median()
    usual_amt = base_med.reindex(np.arange(U)).values
    usual_amt = np.where(np.isnan(usual_amt), seg_med.reindex(seg).values, usual_amt)
    usual_daily = base_daily.reindex(np.arange(U)).values
    usual_daily = np.where(np.isnan(usual_daily), seg_daily.reindex(seg).values, usual_daily)

    # ---------------- event indexes ----------------
    E_pay = EventIndex(payer[customer], t[customer], {"amount": amount[customer]})
    E_push = EventIndex(payer[push], t[push], {"amount": amount[push],
                                              "new": is_new_payee[push].astype(float)})
    recent = push & ~pre & (days_since_first <= 7.0)
    E_recent = EventIndex(payer[recent], t[recent], {"amount": amount[recent]})
    fd = world.fd_events
    E_fd = EventIndex(fd.user.values, fd.t.values)
    stranger = np.isin(kind, [KIND["stranger_credit"], KIND["mule_credit"]]) & (payee < U)
    E_stranger = EventIndex(payee[stranger], t[stranger])
    new_pair_in = first & ~pre
    E_in = EventIndex(payee, t, {"amount": amount, "new": new_pair_in.astype(float),
                                 "new_amt": amount * new_pair_in})
    E_out = EventIndex(payer, t, {"amount": amount})
    E_any = EventIndex(np.concatenate([payer, payee]), np.concatenate([t, t]))

    idx = np.where(scored)[0]
    q_payer, q_payee, q_t, q_amt = payer[idx], payee[idx], t[idx], amount[idx]
    f = pd.DataFrame({"pid": p.pid.values[idx], "t": q_t, "payer": q_payer, "payee": q_payee,
                      "amount": q_amt, "kind": kind[idx]})
    f["day"] = (q_t // MIN_PER_DAY).astype(int)

    # ---------------- J: journey ----------------
    hour = ((q_t % MIN_PER_DAY) // 60).astype(int)
    f["amount_log"] = np.log10(np.maximum(q_amt, 1))
    f["channel"] = p.channel.values[idx]
    f["hour"] = hour
    f["is_night"] = ((hour < 6) | (hour >= 23)).astype(np.int8)
    f["is_new_payee"] = is_new_payee[idx].astype(np.int8)
    f["n_prev_to_payee"] = np.where(pre[idx], 50, n_prev[idx])
    f["days_since_first_to_payee"] = days_since_first[idx]
    f["amt_vs_usual"] = q_amt / np.maximum(usual_amt[q_payer], 50)
    f["amt_vs_prev_same_payee"] = q_amt / prev_amt[idx]
    f["growth_vs_payee_first"] = np.where(pre[idx] | first[idx], np.nan, q_amt / first_amt[idx])
    f["new_payees_7d"] = E_push.window("new", q_payer, q_t, 7 * MIN_PER_DAY)
    cnt_r = E_recent.window("count", q_payer, q_t, 7 * MIN_PER_DAY)
    sum_r = E_recent.window("amount", q_payer, q_t, 7 * MIN_PER_DAY)
    f["recent_payee_payments_7d"] = cnt_r
    f["recent_payee_sum_7d_log"] = np.log10(sum_r + 1)
    f["growth_vs_recent_avg"] = np.where(cnt_r > 0, q_amt / np.maximum(sum_r / np.maximum(cnt_r, 1), 1), np.nan)
    f["push_out_24h_vs_usual"] = E_push.window("amount", q_payer, q_t, MIN_PER_DAY) / np.maximum(usual_daily[q_payer], 100)
    f["n_payments_1h"] = E_pay.window("count", q_payer, q_t, 60)
    f["fd_break_48h"] = (E_fd.window("count", q_payer, q_t, 2 * MIN_PER_DAY) > 0).astype(np.int8)
    f["stranger_credits_24h"] = E_stranger.window("count", q_payer, q_t, MIN_PER_DAY)
    f["is_investor"] = world.users.investor.values[q_payer].astype(np.int8)

    # ---------------- P: payee ----------------
    inflow = E_in.window("amount", q_payee, q_t, MIN_PER_DAY)
    outflow = E_out.window("amount", q_payee, q_t, MIN_PER_DAY)
    f["payee_type"] = acc_type[q_payee]
    f["payee_is_valid"] = acc.is_valid_handle.values[q_payee].astype(np.int8)
    q_day = q_t / MIN_PER_DAY
    f["payee_age_days"] = q_day - acc.open_day.values[q_payee]
    last = E_any.last_before(q_payee, q_t)
    never = np.minimum(acc.last_active_before.values[q_payee] + q_day, f["payee_age_days"].values)
    f["payee_idle_days"] = np.where(np.isnan(last), never, (q_t - last) / MIN_PER_DAY)
    f["payee_new_payers_24h"] = E_in.window("new", q_payee, q_t, MIN_PER_DAY)
    f["payee_new_payers_7d"] = E_in.window("new", q_payee, q_t, 7 * MIN_PER_DAY)
    f["payee_inflow_24h_log"] = np.log10(inflow + 1)
    f["payee_passthrough_24h"] = np.where(inflow > 0, np.minimum(outflow / np.maximum(inflow, 1), 2.0), np.nan)
    f["payee_registry"] = (q_t >= world.registry_t[q_payee]).astype(np.int8)
    f["payee_fri_band"] = np.where(q_t >= world.fri_t[q_payee], world.fri_band[q_payee], 0).astype(np.int8)

    # campaign detector (streaming graph over all money movement)
    src_in = E_in.window("amount", payer, t, MIN_PER_DAY)
    src_out = E_out.window("amount", payer, t, MIN_PER_DAY)
    src_new = E_in.window("new", payer, t, MIN_PER_DAY)
    src_new_amt = E_in.window("new_amt", payer, t, MIN_PER_DAY)
    day_all = t / MIN_PER_DAY
    open_day, idle_before = acc.open_day.values, acc.last_active_before.values
    young_or_dormant = ((day_all - open_day[payer]) < 180) | (idle_before[payer] > 90)
    passthrough_after = np.where(src_in > 0, (src_out + amount) / np.maximum(src_in, 1), 0)
    fan_in = (src_new >= 2) & (src_new_amt >= 5000)          # real money from several new payers
    mule_like = (src_in > 0) & (passthrough_after >= 0.8) & (fan_in | young_or_dormant)
    # never link through established accounts that keep their money (shops, suppliers, brokers)
    dst_in7 = E_in.window("amount", payee, t, 7 * MIN_PER_DAY)
    dst_out7 = E_out.window("amount", payee, t, 7 * MIN_PER_DAY)
    dst_young_or_dormant = ((day_all - open_day[payee]) < 180) | (idle_before[payee] > 90)
    dst_passes_on = (dst_in7 > 0) & (dst_out7 >= 0.5 * dst_in7)
    forward_like = amount >= 0.5 * np.maximum(src_in, 1)   # moves on most of what just came in
    union = (mule_like & ~is_sink[payee] & ~is_sink[payer]
             & (dst_young_or_dormant | (dst_passes_on & forward_like)))
    camp = detect_campaigns(t, payer, payee, union, scored, size_threshold=campaign_size)
    f["payee_in_campaign"] = camp["in_campaign"][idx].astype(np.int8)
    f["payee_campaign_size"] = camp["size"][idx]

    # ---------------- C: context (opt-in; NaN when the customer did not consent) ----------------
    consent = world.users.consent.values[q_payer]
    sim = p.caller_sim.values[idx]
    if len(world.sim_fri_t):
        sim_c = np.where(sim >= 0, sim, 0)
        sim_band = np.where((sim >= 0) & (q_t >= world.sim_fri_t[sim_c]), world.sim_fri_band[sim_c], 0)
    else:
        sim_band = np.zeros(len(idx), dtype=np.int8)
    caller_band = np.where(sim >= 0, sim_band, p.caller_fri_static.values[idx])
    ctx = {
        "on_call": p.on_call.values[idx], "caller_unknown": p.caller_unknown.values[idx],
        "call_ott": p.call_ott.values[idx], "call_min": p.call_min.values[idx],
        "caller_fri_band": caller_band, "screen_share": p.screen_share.values[idx],
        "remote_app": p.remote_app.values[idx], "app_switches": p.app_switches.values[idx],
    }
    for k, v in ctx.items():
        f[k] = np.where(consent, v.astype(float), np.nan)

    # ---------------- labels and audit attributes (never used as model inputs) ----------------
    f["is_scam"] = (kind[idx] == KIND["scam"]).astype(np.int8)
    f["workflow"] = p.workflow.values[idx]
    f["journey"] = p.journey.values[idx]
    f["step"] = p.step.values[idx]
    f["campaign"] = p.campaign.values[idx]
    rep = world.journeys.set_index("journey").report_t
    f["report_t"] = np.where(f.journey.values >= 0, rep.reindex(f.journey.values).values, np.inf)
    users = world.users
    for col in ["age_band", "lang", "region", "consent", "trusted_contact", "mule_holder"]:
        f[col] = users[col].values[q_payer]
    f = f.reset_index(drop=True)

    # denominators: all customer payments (including shop payments), by day and by user
    cust_day = (t[customer] // MIN_PER_DAY).astype(int)
    denom = {
        "customer_payments_by_day": np.bincount(cust_day, minlength=cfg.days),
        "customer_payments_by_user_day": pd.DataFrame({"payer": payer[customer], "day": cust_day}),
        "campaign_components": camp["components"],
        "n_users": U,
    }
    if verbose:
        print(f"scored payments: {len(f):,} of {customer.sum():,} customer payments "
              f"({len(f) / customer.sum():.0%}); scams: {f.is_scam.sum():,}")
    return f, denom
