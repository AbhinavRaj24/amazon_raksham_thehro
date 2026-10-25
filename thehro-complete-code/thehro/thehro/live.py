"""Live features: turn one payment case into the same 39 features the models were trained on.

A case says who pays, which account, how much, what is happening on the phone, and anything that happened
earlier. live.py turns it into payment events, takes every event in the synthetic world that involves this
customer or this payee, and runs the training feature code (features.build_features) on that slice.
Two things need the whole world, so they are looked up instead:
  * each customer's usual amounts (from the warm-up weeks), and
  * the mule-network detector's state, replayed in time order up to the moment of the payment.
So live and training features agree by construction; tests/test_live.py checks it on random payments.

In production the slice comes from the bank's ledger and the network flag from NPCI; here both come from
the simulator.
"""
from __future__ import annotations

import dataclasses
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from .config import MIN_PER_DAY
from .features import build_features
from .simulate import CHANNELS, KIND, PAIR_BASE, T_MERCHANT, SimConfig, World, pair_key, simulate

MULE_KINDS = ("rented", "dormant", "fresh")          # first-layer mules (collectors are the second layer)
CONTEXT_KEYS = ("on_call", "caller_unknown", "call_ott", "call_min", "screen_share", "remote_app", "app_switches")
class NoPayeeAvailable(LookupError):
    """No account of the requested type is available for this customer at this moment."""


PAYEE_TYPES = ("saved_contact", "shop", "regular_vendor", "new_individual", "new_landlord", "new_account",
               "dormant", "registry", "fri_high", "flagged_genuine", "mule_network", "mule_unflagged")


class NetworkState:
    """The label-free mule-network detector (campaign.detect_campaigns), replayed up to a moment.

    Only the link events change the groups, so replaying them in time order gives the groups exactly as the
    streaming detector saw them just before any later payment.
    """

    def __init__(self, t, payer, payee, union, size_threshold: int = 3):
        idx = np.flatnonzero(union)
        self.t, self.a, self.b = t[idx], payer[idx].tolist(), payee[idx].tolist()
        self.pos = 0
        self.until = -np.inf
        self.parent: dict[int, int] = {}
        self.size: dict[int, int] = {}
        self.flagged: dict[int, float] = {}
        self.threshold = size_threshold

    def find(self, a: int) -> int:
        root = a
        while (p := self.parent.get(root, root)) != root:
            root = p
        while a != root:
            nxt = self.parent.get(a, a)
            self.parent[a] = root
            a = nxt
        return root

    def advance(self, t_until: float) -> None:
        """Apply every link made strictly before t_until. Time only moves forward."""
        if t_until < self.until:
            raise ValueError("NetworkState can only move forward in time")
        self.until = t_until
        while self.pos < len(self.t) and self.t[self.pos] < t_until:
            k, t_link = self.pos, float(self.t[self.pos])
            self.pos += 1
            ra, rb = self.find(self.a[k]), self.find(self.b[k])
            if ra == rb:
                continue
            sa, sb = self.size.get(ra, 1), self.size.get(rb, 1)
            if sa < sb:
                ra, rb, sa, sb = rb, ra, sb, sa
            self.parent[rb] = ra
            self.size[ra] = sa + sb
            fa, fb = self.flagged.get(ra), self.flagged.pop(rb, None)
            if fb is not None:
                self.flagged[ra] = fb if fa is None else min(fa, fb)
            if ra not in self.flagged and self.size[ra] >= self.threshold:
                self.flagged[ra] = t_link

    def lookup(self, accounts) -> tuple[np.ndarray, np.ndarray]:
        roots = [self.find(int(a)) for a in np.atleast_1d(accounts)]
        return (np.array([r in self.flagged for r in roots], dtype=bool),
                np.array([self.size.get(r, 1) for r in roots], dtype=np.int32))

    def group(self, account: int) -> dict:
        """The account's linked group: members, size, and when it was flagged (None if not flagged)."""
        root = self.find(int(account))
        members = sorted(a for a in set(self.parent) | set(self.size) if self.find(a) == root)
        return {"size": self.size.get(root, 1), "flagged_t": self.flagged.get(root), "members": members or [account]}


class LiveWorld:
    """The synthetic world plus everything needed to compute features for new payments."""

    def __init__(self, world: World, state: dict, size_threshold: int = 3):
        self.world = world
        p = world.payments
        self.pay_cols = list(p.columns)
        self.pay_dtypes = p.dtypes.to_dict()
        self.t = p.t.values
        self.payer = p.payer.values.astype(np.int64)
        self.payee = p.payee.values.astype(np.int64)
        self.amount = p.amount.values
        self.kind = p.kind.values
        self.by_payer = np.argsort(self.payer, kind="stable")
        self.by_payee = np.argsort(self.payee, kind="stable")
        self.payer_sorted = self.payer[self.by_payer]
        self.payee_sorted = self.payee[self.by_payee]
        self.baselines = (state["usual_amt"], state["usual_daily"])
        self.network = NetworkState(state["t"], state["payer"], state["payee"], state["union"], size_threshold)
        self.pre_pairs = np.sort(world.pre_pairs)
        self.next_pid = int(p.pid.max()) + 1
        self.now: float | None = None
        self.pools: dict = {}

    # ------------------------------------------------------------------ building
    @classmethod
    def build(cls, cfg: SimConfig | None = None, cache: str | Path | None = None, verbose: bool = True):
        """Simulate the world and the detector state, or load them from a cache made by an earlier build."""
        if cache and Path(cache).exists():
            with open(cache, "rb") as fh:
                world, state = pickle.load(fh)
        else:
            world = simulate(cfg or SimConfig(), verbose=verbose)
            _, denom = build_features(world, verbose=verbose, keep_state=True)
            state = denom["state"]
            if cache:
                with open(cache, "wb") as fh:
                    pickle.dump((world, state), fh, protocol=pickle.HIGHEST_PROTOCOL)
        return cls(world, state)

    # ------------------------------------------------------------------ slices and features
    def event_index(self, entities, before_t: float) -> np.ndarray:
        """Rows of world.payments that involve any of the entities and happened before before_t, in order."""
        parts = []
        for e in set(int(x) for x in entities):
            for order, keys in ((self.by_payer, self.payer_sorted), (self.by_payee, self.payee_sorted)):
                lo, hi = np.searchsorted(keys, e, "left"), np.searchsorted(keys, e, "right")
                parts.append(order[lo:hi])
        idx = np.unique(np.concatenate(parts)) if parts else np.array([], dtype=np.int64)
        return idx[self.t[idx] < before_t]

    def payment_rows(self, rows: list[dict]) -> pd.DataFrame:
        """New payment events in the world's payment schema (defaults: no scam labels, no phone context)."""
        defaults = dict(workflow=-1, campaign=-1, journey=-1, step=-1, on_call=0, caller_unknown=0, call_ott=0,
                        call_min=0.0, screen_share=0, remote_app=0, app_switches=0, caller_sim=-1, caller_fri_static=0)
        out = []
        for r in rows:
            row = {**defaults, **r}
            row.setdefault("pid", self._new_pid())
            out.append({c: row[c] for c in self.pay_cols})
        return pd.DataFrame(out, columns=self.pay_cols).astype(self.pay_dtypes)

    def _new_pid(self) -> int:
        self.next_pid += 1
        return self.next_pid

    def slice_features(self, customer: int, payee: int, cut_t: float, new_rows: pd.DataFrame | None = None,
                       new_fd_t: list[float] = (), consent: bool | None = None,
                       exclude_pids=()) -> pd.DataFrame:
        """Features for every scored payment in the slice: world events before cut_t that involve the customer
        or the payee, plus new_rows, minus exclude_pids (payments that, in this what-if, never happened, such
        as an attempt the customer cancelled). The network flag comes from the detector's current state."""
        w = self.world
        hist = w.payments.iloc[self.event_index([customer, payee], cut_t)]
        if len(exclude_pids):
            hist = hist[~np.isin(hist.pid.values, np.asarray(list(exclude_pids)))]
        pay = hist if new_rows is None or new_rows.empty else pd.concat([hist, new_rows], ignore_index=True)
        pay = pay.sort_values("t", kind="mergesort").reset_index(drop=True)
        fd = w.fd_events[(w.fd_events.user.values == customer) & (w.fd_events.t.values < cut_t)]
        if len(new_fd_t):
            fd = pd.concat([fd, pd.DataFrame({"user": np.int32(customer), "t": list(new_fd_t)})], ignore_index=True)
            fd = fd.sort_values("t", kind="mergesort").reset_index(drop=True)
        users = w.users
        if consent is not None and bool(users.consent.values[customer]) != consent:
            users = users.copy()
            users.loc[customer, "consent"] = consent
        # only the known-before-day-0 pairs that occur in this slice: same answers, no search of all 1.2 M pairs
        keys = pair_key(pay.payer.values, pay.payee.values)
        pos = np.minimum(np.searchsorted(self.pre_pairs, keys), len(self.pre_pairs) - 1)
        pre = np.unique(keys[self.pre_pairs[pos] == keys])
        sub = dataclasses.replace(w, payments=pay, fd_events=fd, users=users, pre_pairs=pre)
        f, _ = build_features(sub, verbose=False, baselines=self.baselines,
                              network=lambda q_payee, q_t: self.network.lookup(q_payee))
        return f

    def historical_features(self, pid: int) -> pd.Series:
        """Recompute one past payment's features from its slice (used by the parity test)."""
        row = self.world.payments.iloc[pid]
        t = float(row.t)
        self.network.advance(t)
        f = self.slice_features(int(row.payer), int(row.payee), t, self.world.payments.iloc[[pid]])
        return f.loc[f.pid.values == pid].iloc[0]

    # ------------------------------------------------------------------ the demo moment
    def set_now(self, now_t: float) -> None:
        """Fix the demo moment: the network detector and the payee pools are computed as of now_t."""
        self.now = now_t
        self.network.advance(now_t)
        self.pools = self._pools(now_t)

    def _pools(self, now_t: float) -> dict:
        w, acc = self.world, self.world.accounts
        n = len(acc)
        before = self.t < now_t
        last = np.full(n, -np.inf)
        np.maximum.at(last, self.payer[before], self.t[before])
        np.maximum.at(last, self.payee[before], self.t[before])
        recent = before & (self.t >= now_t - 7 * MIN_PER_DAY)
        inflow7 = np.bincount(self.payee[recent], weights=self.amount[recent], minlength=n)
        scam = self.kind == KIND["scam"]
        first_scam = np.full(n, np.inf)
        np.minimum.at(first_scam, self.payee[scam], self.t[scam])
        registry = w.registry_t <= now_t
        fri = (w.fri_t <= now_t) & (w.fri_band >= 2)
        in_net = np.zeros(n, dtype=bool)
        for a in set(self.network.parent) | set(self.network.size):
            if self.network.find(a) in self.network.flagged:
                in_net[a] = True
        role = acc.role.astype(str).values
        mule = np.isin(acc.mule_category.values, MULE_KINDS)
        age = now_t / MIN_PER_DAY - acc.open_day.values
        clean = ~registry & ~fri & ~in_net
        external = (role == "external") & (acc.mule_category.values == "")

        def order(mask, key):
            idx = np.flatnonzero(mask)
            return idx[np.argsort(key[idx], kind="stable")].tolist()

        live_soon = np.abs(np.where(np.isfinite(first_scam), first_scam - now_t, 1e9))
        return {
            "mule_network": order(in_net & mule & (inflow7 > 0), -inflow7) or order(in_net, -inflow7),
            "mule_unflagged": order(mule & clean, live_soon),
            "registry": order(registry & ~in_net, -w.registry_t) or order(registry, -w.registry_t),
            "fri_high": order(fri & ~registry & ~in_net, -w.fri_t),
            # genuine accounts whose linked number DoT wrongly rates high risk: the realistic false hold
            "flagged_genuine": order(fri & ~registry & (acc.mule_category.values == "")
                                     & ~np.isin(role, ["merchant", "broker"]), np.arange(n, dtype=float)),
            "new_account": order(clean & (age < 30) & ~np.isin(role, ["customer", "merchant", "broker"]), age),
            "dormant": order(external & clean & ~np.isfinite(last) & (acc.last_active_before.values >= 90),
                             -acc.last_active_before.values),
            "new_individual": order(external & clean & (age > 180) & (last > now_t - 30 * MIN_PER_DAY), -last),
            "new_landlord": order(external & clean & (age > 180) & (last > now_t - 30 * MIN_PER_DAY), last),
            "_last": last, "_role": role,
        }

    def known_payees(self, customer: int) -> np.ndarray:
        lo = np.searchsorted(self.pre_pairs, np.int64(customer) * PAIR_BASE, "left")
        hi = np.searchsorted(self.pre_pairs, (np.int64(customer) + 1) * PAIR_BASE, "left")
        return (self.pre_pairs[lo:hi] - np.int64(customer) * PAIR_BASE).astype(np.int64)

    def paid_before(self, customer: int, payee: int) -> bool:
        lo, hi = np.searchsorted(self.payer_sorted, customer, "left"), np.searchsorted(self.payer_sorted, customer, "right")
        rows = self.by_payer[lo:hi]
        return bool(np.any((self.payee[rows] == payee) & (self.t[rows] < self.now)))

    def pick_payee(self, customer: int, payee_type: str, skip: int = 0, exclude=()) -> int:
        """A real account of the requested type, as of now. skip=1, 2... gives the next candidates."""
        if payee_type not in PAYEE_TYPES:
            raise ValueError(f"payee_type must be one of {PAYEE_TYPES}")
        acc = self.world.accounts
        if payee_type in ("saved_contact", "regular_vendor", "shop"):
            known = self.known_payees(customer)
            role = acc.role.astype(str).values[known]
            want = {"saved_contact": role == "customer", "regular_vendor": role == "vendor",
                    "shop": acc.type.values[known] == T_MERCHANT}[payee_type]
            cands = known[want].tolist()
        else:
            cands = list(self.real_scam_payees(customer, payee_type)) + self.pools[payee_type]
            known = set(self.known_payees(customer).tolist())
            cands = [a for a in dict.fromkeys(cands) if a not in known and a != customer]
            if payee_type not in ("mule_network", "registry", "fri_high", "mule_unflagged"):
                cands = [a for a in cands[:200] if not self.paid_before(customer, a)]
        cands = [a for a in cands if a not in set(exclude)]
        if not cands:
            raise NoPayeeAvailable(f"no {payee_type} account available for customer {customer}")
        return int(cands[skip % len(cands)])

    def real_scam_payees(self, customer: int, payee_type: str) -> list[int]:
        """The accounts this customer really pays in their simulated scam journey (from now on), if they fit."""
        lo, hi = np.searchsorted(self.payer_sorted, customer, "left"), np.searchsorted(self.payer_sorted, customer, "right")
        rows = self.by_payer[lo:hi]
        rows = rows[(self.kind[rows] == KIND["scam"]) & (self.t[rows] >= self.now)]
        pool = set(self.pools.get(payee_type, [])[:5000])
        return [int(a) for a in self.payee[rows] if int(a) in pool]

    # ------------------------------------------------------------------ describing people and accounts
    def customer_profile(self, customer: int) -> dict:
        u = self.world.users.iloc[customer]
        from .simulate import AGE_BANDS, LANGS, REGIONS, SEGMENTS
        return {"id": f"C{customer}", "customer": customer, "age_band": AGE_BANDS[int(u.age_band)],
                "segment": SEGMENTS[int(u.segment)], "region": REGIONS[int(u.region)], "lang": LANGS[int(u.lang)],
                "consent": bool(u.consent), "trusted_contact": bool(u.trusted_contact), "has_fd": bool(u.has_fd),
                "usual_payment": float(self.baselines[0][customer]), "usual_daily": float(self.baselines[1][customer])}

    def account_truth(self, account: int) -> dict:
        """What the simulator knows about an account. The models never see this."""
        a = self.world.accounts.iloc[account]
        cat = str(a.mule_category)
        return {"role": str(a.role), "mule": cat or None,
                "campaign": int(a.campaign) if int(a.campaign) >= 0 else None}

    @staticmethod
    def channel_code(name: str) -> int:
        return CHANNELS.index(name) if name in CHANNELS else 0


def payee_facts(f) -> list[str]:
    """Plain-language facts about the receiving account, from its features (what the bank can say)."""
    from .explain import inr

    def v(name):
        x = f.get(name)
        try:
            x = float(x)
        except (TypeError, ValueError):
            return None
        return None if np.isnan(x) else x

    facts = []
    if v("is_new_payee"):
        facts.append("first payment from this customer")
    if v("payee_age_days") is not None:
        facts.append(f"opened {max(int(v('payee_age_days')), 1)} days ago")
    if (v("payee_idle_days") or 0) >= 90:
        facts.append(f"no activity for {int(v('payee_idle_days') // 30)} months before today")
    if v("payee_in_campaign"):
        facts.append(f"in a live network of {int(v('payee_campaign_size'))} accounts")
    if v("payee_passthrough_24h") is not None and (v("payee_inflow_24h_log") or 0) >= 3:
        inflow = 10 ** v("payee_inflow_24h_log") - 1
        facts.append(f"passed on {int(min(v('payee_passthrough_24h'), 1) * 100)}% of {inr(inflow)} received in 24 h")
    if v("payee_registry"):
        facts.append("reported on the national cybercrime portal")
    if (v("payee_fri_band") or 0) >= 2:
        facts.append("linked mobile rated high fraud risk by DoT")
    return facts
