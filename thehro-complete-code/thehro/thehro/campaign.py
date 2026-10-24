"""Label-free campaign detector: finds live mule networks as money moves.

An account looks "mule-like" when it passes on at least 80% of the last day's
inflow, AND either received Rs 5,000+ from 2+ new payers that day, or was
opened in the last 6 months or woke up after 3+ months idle. A transfer out of
a mule-like account links it to the receiving account when the receiver is
also young or newly woken, or itself passes money on and this transfer moves
on most of what just came in. Shops, brokers and exchanges are never linked.
Linked groups of 3+ accounts are treated as a live scam network, and any
payment into one carries the flag `payee_in_campaign` (the rules live in
features.build_features; this module only keeps the groups).

Needs no complaints and no labels, so it can flag a brand-new scam type within
hours of its first victims. In production this view sits with NPCI / IDPIC,
who see both ends of every payment; the bank receives the flag.
"""
from __future__ import annotations

import numpy as np


def detect_campaigns(t, payer, payee, union_mask, query_mask, size_threshold=3):
    """Streaming union-find over money movement, in time order.

    union_mask: transfers that link payer and payee. query_mask: payments to score;
    each is checked against the groups as they stood just before it.
    """
    n = len(t)
    in_campaign = np.zeros(n, dtype=bool)
    comp_size = np.ones(n, dtype=np.int32)
    parent: dict[int, int] = {}
    size: dict[int, int] = {}
    flagged: dict[int, float] = {}

    def find(a):
        root = a
        while True:
            p = parent.get(root, root)
            if p == root:
                break
            root = p
        while a != root:
            nxt = parent.get(a, a)
            parent[a] = root
            a = nxt
        return root

    idx = np.where(union_mask | query_mask)[0]
    payer_l = payer[idx].tolist()
    payee_l = payee[idx].tolist()
    t_l = t[idx].tolist()
    q_l = query_mask[idx].tolist()
    u_l = union_mask[idx].tolist()
    for k, i in enumerate(idx.tolist()):
        if q_l[k]:
            r = find(payee_l[k])
            if r in flagged:
                in_campaign[i] = True
            comp_size[i] = size.get(r, 1)
        if u_l[k]:
            ra, rb = find(payer_l[k]), find(payee_l[k])
            if ra == rb:
                continue
            sa, sb = size.get(ra, 1), size.get(rb, 1)
            if sa < sb:
                ra, rb, sa, sb = rb, ra, sb, sa
            parent[rb] = ra
            size[ra] = sa + sb
            fa, fb = flagged.get(ra), flagged.pop(rb, None)
            if fb is not None:
                flagged[ra] = fb if fa is None else min(fa, fb)
            if ra not in flagged and size[ra] >= size_threshold:
                flagged[ra] = t_l[k]

    groups: dict[int, set] = {}
    for a in set(parent) | set(size):
        groups.setdefault(find(a), set()).add(a)
    components = [{"root": r, "flag_t": flagged.get(r), "members": np.array(sorted(m))}
                  for r, m in groups.items() if r in flagged]
    return {"in_campaign": in_campaign, "size": comp_size, "components": components}
