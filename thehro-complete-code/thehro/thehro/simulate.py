"""A synthetic world of Indian digital payments, with scam journeys and mule networks.

Nothing here is real data. The simulator creates bank customers with normal
payment habits (including many genuine payments that *look* risky), runs scam
campaigns against some of them, routes the stolen money through mule accounts,
and records which victims complain and when. Parameters are illustrative and
loosely calibrated to public Indian figures; see README.md for the assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import MIN_PER_DAY

# ----------------------------------------------------------------------------
# Codes
# ----------------------------------------------------------------------------
T_INDIVIDUAL, T_MERCHANT, T_BUSINESS, T_BROKER = 0, 1, 2, 3
ACCOUNT_TYPES = ["individual", "merchant", "business", "broker"]

CH_UPI_P2P, CH_UPI_P2M, CH_IMPS, CH_NEFT, CH_RTGS = 0, 1, 2, 3, 4
CHANNELS = ["UPI to person", "UPI to merchant", "IMPS", "NEFT", "RTGS"]

KIND = dict(
    p2m=0, p2p_known=1, vendor_regular=2, rent=3, invest_sip=4, invest_lump=5,
    supplier=6, new_individual=7, new_landlord=8, medical=9, gray_invest=10,
    collector_in=11, collector_out=12, stranger_credit=13, vendor_out=14,
    external_out=15, cash_withdrawal=16,
    scam=20, mule_forward=21, collector_cashout=22, mule_credit=23, penny_drop=24,
)
KIND_NAME = {v: k for k, v in KIND.items()}
SCAM_SIDE_KINDS = (KIND["mule_forward"], KIND["collector_cashout"],
                   KIND["mule_credit"], KIND["penny_drop"])

WORKFLOWS = ["investment", "digital_arrest", "task", "customer_care",
             "marketplace", "voice_clone", "loan_fee"]
WORKFLOW_LABEL = {
    "investment": "Fake trading / IPO investment",
    "digital_arrest": "Digital arrest",
    "task": "Task / part-time job",
    "customer_care": "Fake customer care (screen share)",
    "marketplace": "Marketplace 'scan to receive'",
    "voice_clone": "Cloned voice of a relative",
    "loan_fee": "Fake loan processing fee (emerging)",
}
AGE_BANDS = ["18-25", "26-40", "41-59", "60+"]
SEGMENTS = ["student", "salaried", "trader", "homemaker", "retiree"]
LANGS = ["hi", "en", "ta", "te", "bn", "mr", "kn", "gu"]
LANG_P = [0.44, 0.14, 0.08, 0.08, 0.08, 0.09, 0.05, 0.04]
REGIONS = ["metro", "tier-2", "rural"]

# Hour-of-day weights for everyday payments
HOUR_W = np.array([0.3, 0.15, 0.1, 0.1, 0.15, 0.3, 0.8, 1.5, 2.5, 3.2, 3.6, 3.8,
                   3.9, 3.6, 3.3, 3.2, 3.4, 3.7, 4.2, 4.6, 4.5, 3.8, 2.5, 1.2])
HOUR_P = HOUR_W / HOUR_W.sum()

# Context while paying, for genuine payments by kind:
# (P on a call, P caller not saved | call, P internet call | call, median call minutes,
#  mean app switches, P screen sharing, P remote-access app open)
GENUINE_CTX = {
    KIND["p2m"]:            (0.02, 0.30, 0.30, 3, 0.2, 0.0002, 0.0005),
    KIND["p2p_known"]:      (0.12, 0.10, 0.35, 4, 0.6, 0.0003, 0.0010),
    KIND["vendor_regular"]: (0.05, 0.20, 0.20, 3, 0.4, 0.0002, 0.0005),
    KIND["rent"]:           (0.08, 0.10, 0.30, 5, 0.6, 0.0003, 0.0010),
    KIND["invest_sip"]:     (0.03, 0.20, 0.30, 4, 0.8, 0.0005, 0.0020),
    KIND["invest_lump"]:    (0.06, 0.30, 0.30, 8, 1.2, 0.0010, 0.0030),
    KIND["supplier"]:       (0.10, 0.20, 0.30, 5, 1.0, 0.0005, 0.0040),
    KIND["new_individual"]: (0.35, 0.70, 0.35, 6, 2.5, 0.0005, 0.0020),
    KIND["new_landlord"]:   (0.30, 0.60, 0.35, 8, 2.0, 0.0005, 0.0020),
    KIND["medical"]:        (0.60, 0.70, 0.30, 15, 1.5, 0.0005, 0.0020),
    KIND["gray_invest"]:    (0.20, 0.40, 0.40, 12, 1.5, 0.0005, 0.0020),
    KIND["collector_in"]:   (0.10, 0.10, 0.35, 4, 0.6, 0.0003, 0.0010),
    KIND["collector_out"]:  (0.15, 0.30, 0.40, 5, 1.0, 0.0003, 0.0010),
}

# Context for scam payments, by workflow (same tuple layout)
SCAM_CTX = {
    "investment":     (0.15, 1.00, 0.60, 12, 3.0, 0.03, 0.02),
    "digital_arrest": (0.92, 1.00, 0.85, 150, 1.5, 0.12, 0.04),
    "task":           (0.08, 1.00, 0.50, 6, 4.0, 0.01, 0.01),
    "customer_care":  (0.90, 1.00, 0.20, 15, 2.5, 0.60, 0.35),
    "marketplace":    (0.45, 1.00, 0.40, 6, 3.0, 0.01, 0.01),
    "voice_clone":    (0.95, 0.97, 0.30, 8, 1.5, 0.01, 0.01),
    "loan_fee":       (0.50, 1.00, 0.50, 10, 3.0, 0.05, 0.03),
}

# Who each scam targets: relative weight by age band
TARGET_AGE = {
    "investment":     [0.6, 1.0, 1.3, 1.4],
    "digital_arrest": [0.3, 0.6, 1.2, 2.5],
    "task":           [2.0, 1.4, 0.6, 0.4],
    "customer_care":  [0.6, 1.0, 1.2, 1.6],
    "marketplace":    [1.2, 1.3, 0.9, 0.4],
    "voice_clone":    [0.3, 0.7, 1.5, 1.8],
    "loan_fee":       [1.6, 1.5, 0.7, 0.3],
}
REPORT_P = {"investment": 0.50, "digital_arrest": 0.60, "task": 0.40, "customer_care": 0.55,
            "marketplace": 0.35, "voice_clone": 0.55, "loan_fee": 0.45}
REPORT_DELAY_DAYS = {"investment": 3.0, "digital_arrest": 1.0, "task": 1.5, "customer_care": 0.3,
                     "marketplace": 0.5, "voice_clone": 0.2, "loan_fee": 2.0}
SHARED_POOL_P = {"investment": 0.55, "digital_arrest": 0.70, "task": 0.40, "customer_care": 0.35,
                 "marketplace": 0.50, "voice_clone": 0.40, "loan_fee": 0.45}

# workflow -> (campaigns, mean victims per campaign, earliest start day, (min, max) days)
DEFAULT_CAMPAIGNS = {
    "investment":     (26, 20, 3, (20, 40)),
    "digital_arrest": (30, 8, 3, (10, 20)),
    "task":           (22, 15, 3, (7, 15)),
    "customer_care":  (20, 10, 3, (7, 14)),
    "marketplace":    (20, 10, 3, (10, 20)),
    "voice_clone":    (25, 6, 3, (5, 10)),
    "loan_fee":       (15, 14, 30, (7, 15)),   # emerging: first appears on day 30
}


@dataclass
class SimConfig:
    seed: int = 7
    n_users: int = 40_000
    days: int = 60
    n_merchants: int = 12_000
    n_vendors: int = 3_000
    n_external: int = 150_000
    n_brokers: int = 40
    n_suppliers: int = 4_000
    n_crypto: int = 12
    consent_rate: float = 0.70
    campaigns: dict = field(default_factory=lambda: dict(DEFAULT_CAMPAIGNS))


@dataclass
class World:
    cfg: SimConfig
    users: pd.DataFrame
    accounts: pd.DataFrame
    payments: pd.DataFrame
    fd_events: pd.DataFrame
    journeys: pd.DataFrame
    campaigns: pd.DataFrame
    pre_pairs: np.ndarray          # sorted int64 keys payer*PAIR_BASE + payee known before day 0
    registry_t: np.ndarray         # per account: time it enters the I4C Suspect Registry (inf = never)
    fri_t: np.ndarray              # per account: time its linked mobile gets an FRI flag
    fri_band: np.ndarray           # per account: band once flagged (1 medium, 2 high, 3 very high)
    sim_fri_t: np.ndarray          # per scam SIM: time it gets an FRI flag
    sim_fri_band: np.ndarray


PAIR_BASE = np.int64(10_000_000)


def pair_key(payer, payee):
    return np.asarray(payer, dtype=np.int64) * PAIR_BASE + np.asarray(payee, dtype=np.int64)


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def _times(rng, n, days, day_lo=0.0):
    day = rng.integers(int(day_lo), days, size=n)
    hour = rng.choice(24, size=n, p=HOUR_P)
    return day * MIN_PER_DAY + hour * 60.0 + rng.uniform(0, 60, size=n)


def _daytime(rng, t):
    """Move a time into 09:00-21:00 of the same day (or the next morning)."""
    day, minute = divmod(t, MIN_PER_DAY)
    if minute < 9 * 60:
        return day * MIN_PER_DAY + 9 * 60 + rng.uniform(0, 180)
    if minute > 21 * 60:
        return (day + 1) * MIN_PER_DAY + 9 * 60 + rng.uniform(0, 240)
    return t


def _channel_for(amount, person=True, rng=None, bank_share=0.0):
    amount = np.asarray(amount, dtype=float)
    ch = np.where(amount <= 100_000, CH_UPI_P2P if person else CH_UPI_P2M,
                  np.where(amount <= 500_000, CH_IMPS, np.where(amount < 200_000, CH_NEFT, CH_RTGS)))
    if rng is not None and bank_share > 0:
        flip = (rng.random(amount.shape) < bank_share) & (ch == CH_UPI_P2P)
        ch = np.where(flip, np.where(rng.random(amount.shape) < 0.6, CH_IMPS, CH_NEFT), ch)
    return ch.astype(np.int8)


class _Accounts:
    """Growable account table."""

    def __init__(self):
        self.type, self.role, self.open_day, self.last_active, self.name_kind = [], [], [], [], []

    def add(self, n, type_, role, open_day, last_active):
        start = len(self.type)
        type_ = np.broadcast_to(np.asarray(type_), (n,))
        open_day = np.broadcast_to(np.asarray(open_day, dtype=float), (n,))
        last_active = np.broadcast_to(np.asarray(last_active, dtype=float), (n,))
        self.type.extend(type_.tolist())
        self.role.extend([role] * n)
        self.open_day.extend(open_day.tolist())
        self.last_active.extend(last_active.tolist())
        return np.arange(start, start + n)

    def frame(self):
        return pd.DataFrame({
            "type": np.array(self.type, dtype=np.int8),
            "role": pd.Categorical(self.role),
            "open_day": np.array(self.open_day, dtype=float),
            "last_active_before": np.array(self.last_active, dtype=float),
        })


class _Payments:
    """Collects payment columns from many generators, then builds one frame."""

    cols = ["t", "payer", "payee", "amount", "channel", "kind", "workflow", "campaign",
            "journey", "step", "on_call", "caller_unknown", "call_ott", "call_min",
            "screen_share", "remote_app", "app_switches", "caller_sim", "caller_fri_static"]

    def __init__(self):
        self.parts = []

    def add(self, **kw):
        n = len(np.atleast_1d(kw["t"]))
        if n == 0:
            return
        part = {}
        defaults = dict(workflow=-1, campaign=-1, journey=-1, step=-1, on_call=0,
                        caller_unknown=0, call_ott=0, call_min=0.0, screen_share=0,
                        remote_app=0, app_switches=0, caller_sim=-1, caller_fri_static=0)
        for c in self.cols:
            v = kw.get(c, defaults.get(c))
            part[c] = np.broadcast_to(np.asarray(v), (n,)).copy()
        self.parts.append(part)

    def frame(self):
        out = {}
        for c in self.cols:
            out[c] = np.concatenate([p[c] for p in self.parts])
        df = pd.DataFrame(out)
        types = dict(t="float64", payer="int32", payee="int32", amount="float64", channel="int8",
                     kind="int8", workflow="int8", campaign="int32", journey="int32", step="int16",
                     on_call="int8", caller_unknown="int8", call_ott="int8", call_min="float32",
                     screen_share="int8", remote_app="int8", app_switches="int16",
                     caller_sim="int32", caller_fri_static="int8")
        df = df.astype(types)
        df = df.sort_values("t", kind="mergesort").reset_index(drop=True)
        df.insert(0, "pid", np.arange(len(df), dtype=np.int64))
        return df


def _context(rng, n, params):
    """Draw in-app context for n payments. params: tuple or array of shape (n, 7)."""
    p = np.asarray(params, dtype=float)
    if p.ndim == 1:
        p = np.broadcast_to(p, (n, 7))
    on_call = rng.random(n) < p[:, 0]
    unknown = on_call & (rng.random(n) < p[:, 1])
    ott = on_call & (rng.random(n) < p[:, 2])
    minutes = np.where(on_call, rng.lognormal(np.log(np.maximum(p[:, 3], 0.5)), 0.8), 0.0)
    switches = rng.poisson(p[:, 4])
    screen = rng.random(n) < p[:, 5]
    remote = rng.random(n) < p[:, 6]
    return dict(on_call=on_call.astype(np.int8), caller_unknown=unknown.astype(np.int8),
                call_ott=ott.astype(np.int8), call_min=minutes.astype(np.float32),
                app_switches=switches.astype(np.int16), screen_share=screen.astype(np.int8),
                remote_app=remote.astype(np.int8))


# ----------------------------------------------------------------------------
# Population
# ----------------------------------------------------------------------------

def _make_users(cfg, rng):
    U = cfg.n_users
    age = rng.choice(4, size=U, p=[0.18, 0.42, 0.27, 0.13])
    seg = np.empty(U, dtype=np.int8)
    p_seg = {0: [0.60, 0.35, 0.05, 0.00, 0.00], 1: [0.00, 0.70, 0.15, 0.15, 0.00],
             2: [0.00, 0.55, 0.25, 0.20, 0.00], 3: [0.00, 0.00, 0.10, 0.05, 0.85]}
    for a in range(4):
        idx = np.where(age == a)[0]
        seg[idx] = rng.choice(5, size=len(idx), p=p_seg[a])
    lang = rng.choice(len(LANGS), size=U, p=LANG_P)
    region = rng.choice(3, size=U, p=[0.35, 0.40, 0.25])
    spend = np.array([0.5, 1.0, 1.6, 0.8, 0.9])[seg] * rng.lognormal(0, 0.5, U)
    p_inv = np.where((age >= 1) & (age <= 2) & ((seg == 1) | (seg == 2)), 0.25,
                     np.where(age == 3, 0.15, np.where(age == 0, 0.05, 0.08)))
    investor = rng.random(U) < p_inv
    has_fd = rng.random(U) < np.array([0.05, 0.25, 0.45, 0.70])[age]
    consent = rng.random(U) < cfg.consent_rate
    trusted = rng.random(U) < np.where(age == 3, 0.35, 0.05)
    p_rent = np.where(((seg == 1) | (seg == 2)) & (age <= 2), 0.35, np.where(seg == 0, 0.20, 0.05))
    renter = rng.random(U) < p_rent
    return pd.DataFrame({
        "user": np.arange(U), "age_band": age.astype(np.int8), "segment": seg,
        "lang": lang.astype(np.int8), "region": region.astype(np.int8), "spend": spend,
        "investor": investor, "has_fd": has_fd, "consent": consent,
        "trusted_contact": trusted, "renter": renter,
    })


def _zipf_p(n, a=1.1):
    w = 1.0 / np.arange(1, n + 1) ** a
    return w / w.sum()


# ----------------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------------

def simulate(cfg: SimConfig | None = None, verbose: bool = True) -> World:
    cfg = cfg or SimConfig()
    rng = np.random.default_rng(cfg.seed)
    U, D = cfg.n_users, cfg.days
    users = _make_users(cfg, rng)
    age, seg, spend = users.age_band.values, users.segment.values, users.spend.values

    acc = _Accounts()
    acc.add(U, T_INDIVIDUAL, "customer", -rng.uniform(200, 5000, U), rng.uniform(0, 3, U))
    merchants = acc.add(cfg.n_merchants, T_MERCHANT, "merchant",
                        -rng.uniform(100, 3000, cfg.n_merchants), 0)
    vendors = acc.add(cfg.n_vendors, T_INDIVIDUAL, "vendor",
                      -rng.uniform(300, 4000, cfg.n_vendors), 0)
    ext_type = np.where(rng.random(cfg.n_external) < 0.15, T_BUSINESS, T_INDIVIDUAL)
    ext_open = np.where(rng.random(cfg.n_external) < 0.06, -rng.uniform(5, 180, cfg.n_external),
                        -rng.uniform(180, 5000, cfg.n_external))
    ext_idle = np.where(rng.random(cfg.n_external) < 0.15, rng.uniform(90, 800, cfg.n_external),
                        rng.uniform(0, 10, cfg.n_external))
    externals = acc.add(cfg.n_external, ext_type, "external", ext_open, ext_idle)
    brokers = acc.add(cfg.n_brokers, T_BROKER, "broker", -3000, 0)
    suppliers = acc.add(cfg.n_suppliers, T_BUSINESS, "supplier",
                        -rng.uniform(200, 4000, cfg.n_suppliers), 0)
    crypto = acc.add(cfg.n_crypto, T_MERCHANT, "cash-out exchange", -1500, 0)
    atm = acc.add(1, T_MERCHANT, "cash withdrawal", -9000, 0)[0]

    # ---------------- relationships known before day 0 ----------------
    pre = []
    fav_m = merchants[rng.choice(cfg.n_merchants, size=(U, 15), p=_zipf_p(cfg.n_merchants, 0.9))]
    pre.append(pair_key(np.repeat(np.arange(U), 15), fav_m.ravel()))
    n_contacts = 6 + rng.poisson(6, U)
    c_ptr = np.concatenate([[0], np.cumsum(n_contacts)])
    c_list = rng.integers(0, U, size=c_ptr[-1])
    c_owner = np.repeat(np.arange(U), n_contacts)
    c_list = np.where(c_list == c_owner, (c_list + 1) % U, c_list)
    pre.append(pair_key(c_owner, c_list))
    n_vend = rng.choice(4, size=U, p=[0.35, 0.35, 0.2, 0.1])
    v_ptr = np.concatenate([[0], np.cumsum(n_vend)])
    v_list = vendors[rng.choice(cfg.n_vendors, size=v_ptr[-1], p=_zipf_p(cfg.n_vendors, 0.8))]
    pre.append(pair_key(np.repeat(np.arange(U), n_vend), v_list))
    landlord = externals[rng.integers(0, cfg.n_external, U)]
    renters = np.where(users.renter.values)[0]
    pre.append(pair_key(renters, landlord[renters]))
    investors = np.where(users.investor.values)[0]
    broker_of = brokers[rng.choice(cfg.n_brokers, size=U, p=_zipf_p(cfg.n_brokers, 1.0))]
    pre.append(pair_key(investors, broker_of[investors]))
    traders = np.where(seg == 2)[0]
    n_sup = rng.integers(5, 16, len(traders))
    s_ptr = np.concatenate([[0], np.cumsum(n_sup)])
    s_list = suppliers[rng.integers(0, cfg.n_suppliers, s_ptr[-1])]
    trader_pos = np.full(U, -1)
    trader_pos[traders] = np.arange(len(traders))
    pre.append(pair_key(np.repeat(traders, n_sup), s_list))

    P = _Payments()
    fd_t, fd_u = [], []

    def add_user_payments(kind, payer, payee, t, amount, channel):
        n = len(t)
        ctx = _context(rng, n, GENUINE_CTX[kind])
        fri_static = np.where((ctx["caller_unknown"] == 1) & (rng.random(n) < 0.004),
                              rng.choice([1, 2], size=n, p=[0.8, 0.2]), 0)
        P.add(t=t, payer=payer, payee=payee, amount=np.round(amount), channel=channel,
              kind=kind, caller_fri_static=fri_static, **ctx)

    # ---------------- everyday payments ----------------
    # Shop and merchant payments (about 60% of UPI volume)
    rate = np.array([0.60, 0.85, 0.70, 0.70, 0.50])[seg]
    cnt = rng.poisson(rate * D)
    u = np.repeat(np.arange(U), cnt)
    pos = rng.choice(15, size=len(u), p=_zipf_p(15, 0.8))
    amt = np.clip(rng.lognormal(np.log(220 * spend[u]), 0.9), 10, 50_000)
    add_user_payments(KIND["p2m"], u, fav_m[u, pos], _times(rng, len(u), D), amt,
                      np.full(len(u), CH_UPI_P2M))

    # Payments to saved contacts
    cnt = rng.poisson(0.22 * D * np.ones(U))
    u = np.repeat(np.arange(U), cnt)
    k = c_ptr[u] + (rng.random(len(u)) * n_contacts[u]).astype(int)
    amt = np.clip(rng.lognormal(np.log(1200 * spend[u]), 1.0), 10, 200_000)
    add_user_payments(KIND["p2p_known"], u, c_list[k], _times(rng, len(u), D), amt,
                      _channel_for(amt, True, rng, 0.03))

    # Regular local vendors (tutor, milk, help at home)
    cnt = rng.poisson(0.08 * D * (n_vend > 0))
    u = np.repeat(np.arange(U), cnt)
    k = v_ptr[u] + (rng.random(len(u)) * np.maximum(n_vend[u], 1)).astype(int)
    amt = np.clip(rng.lognormal(np.log(700), 0.6), 50, 20_000)
    add_user_payments(KIND["vendor_regular"], u, v_list[np.minimum(k, len(v_list) - 1)],
                      _times(rng, len(u), D), amt, np.full(len(u), CH_UPI_P2P))

    # Rent, twice in 60 days
    rent_amt = np.clip(rng.lognormal(np.log(16000 * np.sqrt(spend[renters])), 0.4), 4000, 80_000)
    for month in (0, 30):
        day = month + rng.integers(0, 5, len(renters))
        t = day * MIN_PER_DAY + rng.choice(24, len(renters), p=HOUR_P) * 60.0 + rng.uniform(0, 60, len(renters))
        add_user_payments(KIND["rent"], renters, landlord[renters], t, rent_amt,
                          _channel_for(rent_amt, True, rng, 0.1))

    # Investments through SEBI-registered brokers (@valid handles)
    for month in (0, 30):
        day = month + rng.integers(0, 28, len(investors))
        t = day * MIN_PER_DAY + rng.choice(24, len(investors), p=HOUR_P) * 60.0 + rng.uniform(0, 60, len(investors))
        amt = np.clip(rng.lognormal(np.log(5000 * spend[investors]), 0.7), 500, 100_000)
        add_user_payments(KIND["invest_sip"], investors, broker_of[investors], t, amt,
                          _channel_for(amt, True, rng, 0.05))
    cnt = rng.poisson(0.25 * np.ones(len(investors)))
    u = np.repeat(investors, cnt)
    t = _times(rng, len(u), D)
    amt = np.clip(rng.lognormal(np.log(80000 * spend[u]), 0.8), 10_000, 1_000_000)
    add_user_payments(KIND["invest_lump"], u, broker_of[u], t, amt, _channel_for(amt, True, rng, 0.3))
    fd = (rng.random(len(u)) < 0.10) & users.has_fd.values[u]
    fd_u.append(u[fd]); fd_t.append(t[fd] - rng.uniform(30, 1440, fd.sum()))

    # Traders paying suppliers (10% to a supplier they have not paid before)
    cnt = rng.poisson(0.3 * D * np.ones(len(traders)))
    u = np.repeat(traders, cnt)
    is_new = rng.random(len(u)) < 0.10
    tp = trader_pos[u]
    k = s_ptr[tp] + (rng.random(len(u)) * n_sup[tp]).astype(int)
    payee = np.where(is_new, suppliers[rng.integers(0, cfg.n_suppliers, len(u))], s_list[k])
    amt = np.clip(rng.lognormal(np.log(15000 * spend[u]), 1.0), 500, 2_000_000)
    add_user_payments(KIND["supplier"], u, payee, _times(rng, len(u), D), amt,
                      _channel_for(amt, True, rng, 0.3))

    # One-off payments to people they have never paid: second-hand purchase, plumber, new tutor...
    rate = np.array([0.030, 0.020, 0.020, 0.018, 0.012])[seg]
    cnt = rng.poisson(rate * D)
    u = np.repeat(np.arange(U), cnt)
    to_vendor = rng.random(len(u)) < 0.15
    ext_pick = externals[rng.choice(cfg.n_external, size=len(u), p=_zipf_p(cfg.n_external, 0.6))]
    payee = np.where(to_vendor, vendors[rng.choice(cfg.n_vendors, size=len(u), p=_zipf_p(cfg.n_vendors, 0.8))], ext_pick)
    amt = np.clip(rng.lognormal(np.log(2500 * spend[u]), 1.1), 50, 200_000)
    add_user_payments(KIND["new_individual"], u, payee, _times(rng, len(u), D), amt,
                      _channel_for(amt, True, rng, 0.05))

    # Deposit to a new landlord (2% of customers in 60 days)
    u = np.where(rng.random(U) < 0.02)[0]
    t = _times(rng, len(u), D)
    amt = np.clip(rng.lognormal(np.log(40000 * np.sqrt(spend[u])), 0.6), 5000, 400_000)
    add_user_payments(KIND["new_landlord"], u, externals[rng.integers(0, cfg.n_external, len(u))],
                      t, amt, _channel_for(amt, True, rng, 0.15))
    fd = (rng.random(len(u)) < 0.05) & users.has_fd.values[u]
    fd_u.append(u[fd]); fd_t.append(t[fd] - rng.uniform(30, 1440, fd.sum()))

    # Medical emergency transfer to a person or hospital account they have never paid
    u = np.where(rng.random(U) < np.where(age == 3, 0.012, 0.006))[0]
    t = _times(rng, len(u), D)
    amt = np.clip(rng.lognormal(np.log(30000), 0.9), 3000, 600_000)
    add_user_payments(KIND["medical"], u, externals[rng.integers(0, cfg.n_external, len(u))],
                      t, amt, _channel_for(amt, True, rng, 0.2))
    fd = (rng.random(len(u)) < 0.25) & users.has_fd.values[u]
    fd_u.append(u[fd]); fd_t.append(t[fd] - rng.uniform(30, 1440, fd.sum()))

    # Informal investment with a new person (chit fund, a friend's business)
    u = np.where(rng.random(U) < np.where(users.investor.values, 0.01, 0.002))[0]
    t = _times(rng, len(u), D)
    amt = np.clip(rng.lognormal(np.log(100000), 0.8), 10_000, 2_000_000)
    add_user_payments(KIND["gray_invest"], u, externals[rng.integers(0, cfg.n_external, len(u))],
                      t, amt, _channel_for(amt, True, rng, 0.3))
    fd = (rng.random(len(u)) < 0.30) & users.has_fd.values[u]
    fd_u.append(u[fd]); fd_t.append(t[fd] - rng.uniform(30, 1440, fd.sum()))

    # Fixed deposits broken for other reasons
    u = np.where((rng.random(U) < 0.01) & users.has_fd.values)[0]
    fd_u.append(u); fd_t.append(_times(rng, len(u), D))

    # Group collections: friends pay one person, who pays a caterer or venue within hours
    collectors = np.where(rng.random(U) < 0.002)[0]
    for cu in collectors:
        friends = c_list[c_ptr[cu]:c_ptr[cu + 1]]
        day = rng.integers(0, D - 1)
        t_in = day * MIN_PER_DAY + 9 * 60 + rng.uniform(0, 12 * 60, len(friends))
        amt = np.clip(rng.lognormal(np.log(1500), 0.5, len(friends)), 200, 10_000)
        add_user_payments(KIND["collector_in"], friends, np.full(len(friends), cu), t_in, amt,
                          np.full(len(friends), CH_UPI_P2P))
        total = amt.sum() * rng.uniform(0.85, 0.95)
        payee = externals[rng.integers(0, cfg.n_external)]
        add_user_payments(KIND["collector_out"], np.array([cu]), np.array([payee]),
                          np.array([t_in.max() + rng.uniform(60, 360)]), np.array([total]),
                          _channel_for(np.array([total]), True))
        pre.append(pair_key(friends, np.full(len(friends), cu)))

    # Money from strangers: selling something online, being paid back by a new acquaintance
    u = np.where(rng.random(U) < 0.04)[0]
    u = np.repeat(u, rng.integers(1, 3, len(u)))
    amt = np.clip(rng.lognormal(np.log(1500), 1.0), 100, 100_000)
    P.add(t=_times(rng, len(u), D), payer=externals[rng.integers(0, cfg.n_external, len(u))],
          payee=u, amount=np.round(amt), channel=_channel_for(amt, True), kind=KIND["stranger_credit"])

    # ---------------- scams ----------------
    victims_used = np.zeros(U, dtype=bool)
    mule_holder = np.zeros(U, dtype=bool)
    rent_pool = np.where(((age == 0) | (age == 1)) & ((seg == 0) | (spend < np.median(spend))))[0]
    rng.shuffle(rent_pool)
    rent_pool = list(rent_pool)

    journeys, campaigns_rows = [], []
    sims_count = 0
    sim_used_by_journey = {}
    mule_rows = []          # (account, campaign, category)
    acc_campaign = {}       # mule/collector account -> campaign
    camp_collectors = {}

    def new_mule_account(c_id, wf, start_day, allow_rented=True):
        r = rng.random()
        btype = T_BUSINESS if rng.random() < (0.40 if wf == "digital_arrest" else 0.30) else T_INDIVIDUAL
        if allow_rented and r < 0.15 and rent_pool:
            a = int(rent_pool.pop())
            mule_holder[a] = True
            cat = "rented"
        elif r < 0.45:
            a = int(acc.add(1, btype, "mule", -rng.uniform(400, 3000), rng.uniform(120, 900))[0])
            cat = "dormant"
        else:
            a = int(acc.add(1, btype, "mule", start_day - rng.uniform(3, 90), 0)[0])
            cat = "fresh"
        mule_rows.append((a, c_id, cat))
        acc_campaign[a] = c_id
        return a

    lang_p = np.asarray(LANG_P)
    camp_id = 0
    journey_id = 0
    for wf, (n_c, mean_v, smin, (dmin, dmax)) in cfg.campaigns.items():
        for _ in range(n_c):
            start = rng.uniform(smin, D - 4)
            dur = rng.uniform(dmin, dmax)
            ai = rng.random() < {"investment": 0.4, "voice_clone": 1.0, "task": 0.3, "loan_fee": 0.3}.get(wf, 0.2)
            mean = mean_v * (1.7 if (ai and wf == "investment") else 1.0)
            nv = max(2, int(rng.negative_binomial(4, 4 / (4 + mean))))
            n_lang = 1 + (2 if ai else 0) + int(rng.random() < 0.3)
            langs = rng.choice(len(LANGS), size=min(n_lang, len(LANGS)), replace=False, p=lang_p)
            strategy = "shared" if rng.random() < SHARED_POOL_P[wf] else "fresh"
            # victims
            w = np.asarray(TARGET_AGE[wf])[age].astype(float)
            if wf == "investment":
                w *= np.where(users.investor.values, 1.5, 1.0)
            if wf == "task":
                w *= np.where(seg == 3, 1.5, 1.0)
            if wf == "loan_fee":
                w *= np.where(spend < np.median(spend), 1.5, 0.7)
            w *= np.isin(users.lang.values, langs)
            w *= ~victims_used & ~mule_holder
            if w.sum() == 0:
                continue
            nv = min(nv, int((w > 0).sum()))
            vics = rng.choice(U, size=nv, replace=False, p=w / w.sum())
            victims_used[vics] = True
            # infrastructure
            n_coll = int(rng.integers(1, 4))
            colls = []
            for _c in range(n_coll):
                if rng.random() < 0.6:
                    a = int(acc.add(1, T_INDIVIDUAL, "collector", start - rng.uniform(3, 60), 0)[0])
                else:
                    a = int(acc.add(1, T_INDIVIDUAL, "collector", -rng.uniform(400, 3000), rng.uniform(120, 900))[0])
                colls.append(a)
                acc_campaign[a] = camp_id
                mule_rows.append((a, camp_id, "collector"))
            camp_collectors[camp_id] = colls
            pool = []
            if strategy == "shared":
                size = max(2, int(np.ceil(nv / (2 if wf == "digital_arrest" else 3))))
                pool = [new_mule_account(camp_id, wf, start) for _p in range(size)]
            n_sims = int(rng.integers(3, 11))
            sims = np.arange(sims_count, sims_count + n_sims)
            sims_count += n_sims
            campaigns_rows.append(dict(campaign=camp_id, workflow=wf, start_day=start, duration=dur,
                                       ai_enabled=ai, strategy=strategy, n_victims=nv,
                                       langs=",".join(LANGS[i] for i in langs)))
            spread = 0.7 if wf == "investment" else 1.0
            for v in vics:
                t0 = _daytime(rng, (start + rng.uniform(0, dur * spread)) * MIN_PER_DAY)
                if t0 > (D - 0.2) * MIN_PER_DAY:
                    continue
                steps, credits = _journey_steps(rng, wf, t0, float(spend[v]), bool(users.has_fd.values[v]))
                steps = [s for s in steps if s["t"] < D * MIN_PER_DAY]
                if not steps:
                    continue
                prev = None
                ctx_p = SCAM_CTX[wf]
                sims_j = []
                for i, s in enumerate(steps):
                    if prev is None or s["rotate"]:
                        if strategy == "shared":
                            choices = [m for m in pool if m != prev] or pool
                            payee = int(choices[rng.integers(len(choices))])
                        else:
                            payee = new_mule_account(camp_id, wf, s["t"] / MIN_PER_DAY)
                    else:
                        payee = prev
                    prev = payee
                    ctx = _context(rng, 1, ctx_p)
                    sim = int(rng.choice(sims)) if ctx["on_call"][0] else -1
                    if sim >= 0:
                        sims_j.append(sim)
                    ch = _channel_for(np.array([s["amount"]]), True, rng, 0.15 if s["amount"] > 20000 else 0.0)
                    P.add(t=np.array([s["t"]]), payer=v, payee=payee, amount=s["amount"], channel=ch,
                          kind=KIND["scam"], workflow=WORKFLOWS.index(wf), campaign=camp_id,
                          journey=journey_id, step=i, caller_sim=sim, **ctx)
                    if s["fd"]:
                        fd_u.append(np.array([v])); fd_t.append(np.array([s["t"] - rng.uniform(20, 600)]))
                for cr in credits:
                    src = int(pool[rng.integers(len(pool))]) if pool else int(colls[rng.integers(len(colls))])
                    P.add(t=np.array([cr["t"]]), payer=src, payee=v, amount=cr["amount"],
                          channel=_channel_for(np.array([cr["amount"]]), True), kind=KIND["mule_credit"],
                          workflow=WORKFLOWS.index(wf), campaign=camp_id, journey=journey_id)
                total = float(sum(s["amount"] for s in steps))
                reported = rng.random() < REPORT_P[wf]
                report_t = (steps[-1]["t"] + rng.lognormal(np.log(REPORT_DELAY_DAYS[wf] * MIN_PER_DAY), 0.8)
                            if reported else np.inf)
                journeys.append(dict(journey=journey_id, campaign=camp_id, workflow=wf, victim=int(v),
                                     start_t=steps[0]["t"], end_t=steps[-1]["t"], n_steps=len(steps),
                                     total_amount=total, reported=reported, report_t=report_t))
                sim_used_by_journey[journey_id] = sims_j
                journey_id += 1
            camp_id += 1

    # ---------------- money movement after the scam ----------------
    pay_scam = P.frame()   # temporary: everything so far, to find mule inflows
    scam_rows = pay_scam[pay_scam.kind == KIND["scam"]]
    mule_set = {a for a, _c, cat in mule_rows if cat != "collector"}
    # first layer forwards to a collector
    f_t, f_src, f_dst, f_amt, f_c = [], [], [], [], []
    for r in scam_rows.itertuples(index=False):
        colls = camp_collectors[r.campaign]
        n_split = 2 if rng.random() < 0.3 else 1
        total = r.amount * rng.uniform(0.85, 0.99)
        base_t = r.t + rng.lognormal(np.log(25), 0.7)
        for j in range(n_split):
            f_t.append(base_t + j * rng.uniform(1, 15))
            f_src.append(r.payee)
            f_dst.append(int(colls[rng.integers(len(colls))]))
            f_amt.append(round(total / n_split))
            f_c.append(r.campaign)
    f_t, f_amt = np.array(f_t), np.array(f_amt)
    P.add(t=f_t, payer=np.array(f_src), payee=np.array(f_dst), amount=f_amt,
          channel=_channel_for(f_amt, True, rng, 0.5), kind=KIND["mule_forward"], campaign=np.array(f_c))
    # collectors cash out: crypto exchange (70%) or cash withdrawal (30%)
    c_t = f_t + rng.lognormal(np.log(90), 0.7, len(f_t))
    to_crypto = rng.random(len(f_t)) < 0.7
    c_dst = np.where(to_crypto, crypto[rng.integers(0, cfg.n_crypto, len(f_t))], atm)
    c_amt = np.round(f_amt * rng.uniform(0.9, 0.99, len(f_t)))
    P.add(t=c_t, payer=np.array(f_dst), payee=c_dst, amount=c_amt,
          channel=np.where(to_crypto, CH_UPI_P2M, CH_IMPS).astype(np.int8),
          kind=KIND["collector_cashout"], campaign=np.array(f_c))
    # penny drops: Rs 1-10 from a collector to check a new mule account before first use
    first_use = scam_rows.groupby("payee").t.min()
    pd_t, pd_src, pd_dst, pd_c = [], [], [], []
    for a, t_use in first_use.items():
        if a not in mule_set:
            continue
        p = 0.2 if a < U else 0.4
        if rng.random() < p:
            c = acc_campaign[a]
            colls = camp_collectors[c]
            pd_t.append(max(t_use - rng.uniform(60, 72 * 60), 0.0))
            pd_src.append(int(colls[rng.integers(len(colls))]))
            pd_dst.append(int(a))
            pd_c.append(c)
    P.add(t=np.array(pd_t), payer=np.array(pd_src), payee=np.array(pd_dst),
          amount=np.round(rng.uniform(1, 10, len(pd_t))), channel=CH_UPI_P2P,
          kind=KIND["penny_drop"], campaign=np.array(pd_c))

    # ---------------- spending by vendors and other people ----------------
    tmp = P.frame()
    acc_frame = acc.frame()
    role = acc_frame.role.astype(str).values
    to_vendor = tmp[np.isin(role[tmp.payee.values], ["vendor"])]
    vday = to_vendor.assign(day=(to_vendor.t // MIN_PER_DAY).astype(int)).groupby(["payee", "day"]).amount.sum()
    vv = vday.reset_index()
    n_out = rng.integers(1, 3, len(vv))
    rows = np.repeat(np.arange(len(vv)), n_out)
    out_amt = (vv.amount.values[rows] * rng.uniform(0.3, 0.7, len(rows)) / n_out[rows])
    out_t = (vv.day.values[rows] + rng.uniform(0.6, 1.5, len(rows))) * MIN_PER_DAY
    out_dst = np.where(rng.random(len(rows)) < 0.6, merchants[rng.integers(0, cfg.n_merchants, len(rows))],
                       suppliers[rng.integers(0, cfg.n_suppliers, len(rows))])
    P.add(t=out_t, payer=vv.payee.values[rows], payee=out_dst, amount=np.round(out_amt),
          channel=_channel_for(out_amt, True), kind=KIND["vendor_out"])
    to_ext = tmp[np.isin(role[tmp.payee.values], ["external"])]
    r = rng.random(len(to_ext))
    slow = r < 0.5
    fast = (r >= 0.5) & (r < 0.55)
    e = to_ext[slow]
    amt = np.round(e.amount.values * rng.uniform(0.2, 0.8, len(e)))
    P.add(t=e.t.values + rng.lognormal(np.log(1.5 * MIN_PER_DAY), 0.7, len(e)), payer=e.payee.values,
          payee=merchants[rng.integers(0, cfg.n_merchants, len(e))], amount=amt,
          channel=CH_UPI_P2M, kind=KIND["external_out"])
    e = to_ext[fast]
    amt = np.round(e.amount.values * rng.uniform(0.8, 0.95, len(e)))
    P.add(t=e.t.values + rng.uniform(30, 180, len(e)), payer=e.payee.values,
          payee=np.where(rng.random(len(e)) < 0.5, externals[rng.integers(0, cfg.n_external, len(e))],
                         suppliers[rng.integers(0, cfg.n_suppliers, len(e))]),
          amount=amt, channel=_channel_for(amt, True), kind=KIND["external_out"])

    payments = P.frame()
    payments = payments[payments.t < D * MIN_PER_DAY].reset_index(drop=True)
    payments["pid"] = np.arange(len(payments), dtype=np.int64)

    # ---------------- accounts table ----------------
    accounts = acc.frame()
    n_acc = len(accounts)
    accounts["is_sink"] = accounts.type.isin([T_MERCHANT, T_BROKER]).values
    accounts["is_valid_handle"] = (accounts.type == T_BROKER).values
    accounts["mule_category"] = ""
    accounts["campaign"] = -1
    for a, c, cat in mule_rows:
        accounts.at[a, "mule_category"] = cat
        accounts.at[a, "campaign"] = c
    users["mule_holder"] = mule_holder
    journeys = pd.DataFrame(journeys)
    users["victim"] = np.isin(users.user.values, journeys.victim.values)
    campaigns = pd.DataFrame(campaigns_rows)

    # ---------------- registry and FRI flags (point-in-time) ----------------
    registry_t = np.full(n_acc, np.inf)
    fri_t = np.full(n_acc, np.inf)
    fri_band = np.zeros(n_acc, dtype=np.int8)
    is_mule = accounts.mule_category.values != ""
    # some mule SIMs were already flagged from earlier frauds
    pre_flag = is_mule & (rng.random(n_acc) < 0.15)
    fri_t[pre_flag] = -np.inf
    fri_band[pre_flag] = 2
    # a few genuine accounts carry a false flag
    false_flag = ~is_mule & ~accounts.is_sink.values & (rng.random(n_acc) < 0.0005)
    fri_t[false_flag] = -np.inf
    fri_band[false_flag] = rng.choice([1, 2, 3], size=false_flag.sum(), p=[0.7, 0.25, 0.05])
    sim_fri_t = np.full(sims_count, np.inf)
    sim_fri_band = np.zeros(sims_count, dtype=np.int8)
    pre_sim = rng.random(sims_count) < 0.10
    sim_fri_t[pre_sim] = -np.inf
    sim_fri_band[pre_sim] = 2

    scam_pay = payments[payments.kind == KIND["scam"]]
    payees_by_journey = scam_pay.groupby("journey").payee.unique()
    for j in journeys[journeys.reported].itertuples(index=False):
        for a in payees_by_journey.get(j.journey, []):
            if rng.random() < 0.9:
                registry_t[a] = min(registry_t[a], j.report_t + MIN_PER_DAY)
            if fri_t[a] != -np.inf and rng.random() < 0.6:
                t_flag = j.report_t + 2 * MIN_PER_DAY
                if t_flag < fri_t[a]:
                    fri_t[a] = t_flag
                    fri_band[a] = rng.choice([2, 3], p=[0.6, 0.4])
        for c in camp_collectors[j.campaign]:
            if rng.random() < 0.5:
                registry_t[c] = min(registry_t[c], j.report_t + 3 * MIN_PER_DAY)
        for s in set(sim_used_by_journey.get(j.journey, [])):
            if rng.random() < 0.7 and sim_fri_t[s] != -np.inf:
                sim_fri_t[s] = min(sim_fri_t[s], j.report_t + 2 * MIN_PER_DAY)
                sim_fri_band[s] = max(sim_fri_band[s], rng.choice([2, 3], p=[0.6, 0.4]))

    pre_pairs = np.unique(np.concatenate(pre))
    fd_events = pd.DataFrame({"user": np.concatenate(fd_u).astype(np.int32),
                              "t": np.concatenate(fd_t)}).sort_values("t").reset_index(drop=True)

    world = World(cfg=cfg, users=users, accounts=accounts, payments=payments, fd_events=fd_events,
                  journeys=journeys, campaigns=campaigns, pre_pairs=pre_pairs, registry_t=registry_t,
                  fri_t=fri_t, fri_band=fri_band, sim_fri_t=sim_fri_t, sim_fri_band=sim_fri_band)
    if verbose:
        _summary(world)
    return world


def _journey_steps(rng, wf, t0, spend, has_fd):
    """Payment steps (and credits sent to the victim) for one scam journey."""
    steps, credits = [], []
    s05 = np.sqrt(max(spend, 0.05))

    def step(t, a, rotate, fd_p=0.0):
        a = float(np.clip(a, 1, 5_000_000))
        steps.append(dict(t=t, amount=round(a), rotate=rotate,
                          fd=bool(has_fd and a >= 50_000 and rng.random() < fd_p)))

    if wf == "investment":
        n = min(10, 3 + rng.poisson(2.5))
        tax = rng.random() < 0.6
        t, prev, cum = t0, 0.0, 0.0
        for i in range(n):
            if i:
                t = _daytime(rng, t + rng.lognormal(np.log(1.5 * MIN_PER_DAY), 0.6))
            if i == 0:
                a = np.clip(rng.lognormal(np.log(12000 * s05), 0.6), 2000, 60_000)
            elif tax and i == n - 1 and i >= 2:
                a = cum * rng.uniform(0.15, 0.30)
            else:
                a = prev * rng.lognormal(np.log(1.8), 0.35)
            step(t, a, i == 0 or rng.random() < 0.6, 0.45)
            prev, cum = steps[-1]["amount"], cum + steps[-1]["amount"]
            if i <= 1 and not credits and rng.random() < 0.55:
                credits.append(dict(t=t + rng.uniform(0.5, 2) * MIN_PER_DAY,
                                    amount=round(prev * rng.uniform(0.1, 0.3))))
    elif wf == "digital_arrest":
        n = min(5, 1 + rng.poisson(1.2))
        t = t0
        for i in range(n):
            if i:
                t = t + rng.uniform(1, 20) * 60
            a = np.clip(rng.lognormal(np.log(70_000 * s05), 0.9), 15_000, 5_000_000)
            step(t, a, rng.random() < 0.5, 0.6)
    elif wf == "task":
        t = t0
        for _ in range(int(rng.integers(2, 4))):
            t += rng.uniform(20, 240)
            credits.append(dict(t=t, amount=round(rng.uniform(100, 800))))
        t += rng.uniform(60, 360)
        n = min(7, 2 + rng.poisson(2))
        a = rng.uniform(1000, 3000)
        for i in range(n):
            if i:
                t += rng.lognormal(np.log(8 * 60), 0.6)
                a *= rng.lognormal(np.log(2.2), 0.3)
            step(t, a, i == 0 or rng.random() < 0.7)
    elif wf == "customer_care":
        n = min(4, 1 + rng.poisson(1.0))
        t = t0
        for i in range(n):
            if i:
                t += rng.uniform(2, 15)
            step(t, np.clip(rng.lognormal(np.log(25000 * s05), 0.8), 2000, 200_000), rng.random() < 0.6)
    elif wf == "marketplace":
        n = min(5, 1 + rng.poisson(1.3))
        t = t0
        a = np.clip(rng.lognormal(np.log(4000), 0.7), 500, 50_000)
        for i in range(n):
            if i:
                t += rng.uniform(2, 10)
                a *= rng.uniform(1.8, 2.2)
            step(t, a, rng.random() < 0.3)
    elif wf == "voice_clone":
        if rng.random() < 0.5:
            for _ in range(int(rng.integers(1, 3))):
                credits.append(dict(t=t0 - rng.uniform(2, 20), amount=round(rng.uniform(1, 1000))))
        n = min(3, 1 + rng.poisson(0.6))
        t = t0
        for i in range(n):
            if i:
                t += rng.uniform(5, 30)
            step(t, np.clip(rng.lognormal(np.log(30000 * s05), 0.7), 5000, 200_000), rng.random() < 0.4)
    elif wf == "loan_fee":
        n = min(6, 2 + rng.poisson(1.5))
        t = t0
        a = rng.uniform(999, 4999)
        for i in range(n):
            if i:
                t = _daytime(rng, t + rng.lognormal(np.log(5 * 60), 0.6))
                a *= rng.lognormal(np.log(1.6), 0.3)
            step(t, a, i == 0 or rng.random() < 0.6)
    return steps, credits


def _summary(w: World):
    p = w.payments
    users = p.payer < w.cfg.n_users
    print(f"payments: {len(p):,}  by customers: {users.sum():,}  "
          f"scam payments: {(p.kind == KIND['scam']).sum():,}")
    print(f"journeys: {len(w.journeys):,}  reported: {w.journeys.reported.mean():.0%}  "
          f"campaigns: {len(w.campaigns)}  mule/collector accounts: {(w.accounts.mule_category != '').sum():,}")
    print("journeys by workflow:", w.journeys.workflow.value_counts().to_dict())
    print("median loss by workflow:", w.journeys.groupby("workflow").total_amount.median().round(-2).to_dict())
