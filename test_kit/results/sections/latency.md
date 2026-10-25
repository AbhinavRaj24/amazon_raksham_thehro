**Measured** on this laptop (16 CPUs, Python 3.13.7), client side, HTTP included, one server process each (as in the demo). Decision-service requests are real test-day payments (features from the kit cache); judge cases are random judge-mode inputs, and each one builds the 39 features live from events before deciding.

| What | Requests | p50 ms | p95 ms | p99 ms | Per second | Errors |
|---|---|---|---|---|---|---|
| Decision service, 1 at a time | 2,000 | 12.6 | 15.1 | 18.2 | 77 | 0 |
| Decision service, 8 at a time | 2,000 | 136.1 | 151.2 | 200.7 | 57 | 0 |
| Decision service, 32 at a time | 2,000 | 560.8 | 626.2 | 681.6 | 54 | 0 |
| Bank server, judge case end to end, 1 at a time | 400 | 26.6 | 40.2 | 44.4 | 33 | 0 (+10 random inputs refused: no account of that type left) |
| Bank server, judge case end to end, 8 at a time | 400 | 337.0 | 437.8 | 457.0 | 23 | 0 (+10 random inputs refused: no account of that type left) |

Inside the decision service a decision takes 10.8 ms at the median (15.8 ms p99): models, policy, reasons, message and evidence record. With more requests at once the service queues them (one Python process); a bank would run several processes behind a load balancer, so the per-request time, not this laptop's throughput, is the number that carries over. The bank server handles one judge case at a time (it keeps every session's history in memory).

Failure paths are tested in `tests/test_failures.py`: the payee feeds time out (decided without them), the decision service is down (the bank's own rules apply after a 0.5 s timeout, nothing held), no officer acts within 30 minutes (the hold is cancelled and the money stays), a call-back can't reach the customer (nothing is cancelled), and no consent (the phone sends nothing). Not measured here: the demo phone's round trip over Wi-Fi and 4G.
