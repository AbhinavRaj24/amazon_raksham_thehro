# Mock responses for the bank server

These let P3 (judge page) and P4 (officer tab) build pages without running the live server. The live
`bank/server.py` (`bash scripts/demo_up.sh`) serves the same paths and shapes, plus `payee.truth` and
`dropped_from_phone`. Every text, reason, score, evidence record and complaint draft comes from the
Round 1 pipeline outputs. Only session-dependent values (decision IDs, timings, SLA clocks) are placeholders.
Each file is marked `"_mock": true`, and `"_source"` says where it came from.

## Run the mock server
```bash
cd thehro-complete-code/thehro
source ../../.venv/bin/activate
uvicorn bank.mock_server:app --host 0.0.0.0 --port 8080
```
- Try every endpoint at http://127.0.0.1:8080/docs.
- `GET /mock/list` lists the canned screens.
- Pages in `bank/static/` are served at `/`, so put `index.html` there.

**Force a screen:** add `"mock": "<name>"` to the request body, or `?mock=<name>` to the URL. Without it, `/judge/case` picks one from the payee type and the amount (rules in `/mock/list`). The "what left the phone" pane always echoes your actual request, so consent off really shows no phone flags.

## Endpoints and files

| Endpoint | File(s) | Notes |
|---|---|---|
| `GET /judge/options` | `judge_options.json` | customers (real ones from the synthetic world), payee types, channels, history kinds, context fields |
| `POST /judge/session`, `POST /judge/reset` | `judge_session.json` | |
| `POST /judge/case` | `judge_case_<name>.<en/hi>.json` | the language follows `"lang"` in the request |
| `POST /judge/action` | `judge_action_<action>.json` | actions: continue, confirm_pin, cancel, call_bank, this_was_me, trusted, wait (live server also: trusted_ok, trusted_declined) |
| `GET /judge/decisions/{id}` | `judge_decision_<held/released/cancelled>.json` (`?mock=`) | a decision's current state and the SMS the bank sent; poll it while a hold is reviewed |
| `GET /officer/cases` | `officer_cases.json` | 3 open holds with minutes left |
| `GET /officer/cases/{id}` | `officer_case_detail.json` | the digital-arrest hold in full |
| `POST /officer/cases/{id}/action` | `officer_action_<action>.json` | actions: release_confirm, cancel_scam, no_answer |
| `GET /officer/audit` | `officer_audit.json` | who read what |
| `GET /kit/cases` | `kit_cases.json` | 3 **format examples**; P4 writes the real cases in `test_kit/cases/` |
| `POST /kit/run` | `kit_run_k1.json` (body `{"case_id": …}`), `kit_run_k2.json` (body `{"seed": …}`) | |
| `GET /device/flags`, `POST /device/flags` | `device_flags.json` | the phone's automation app posts yes/no flags here; the mock keeps the last post |

## Case screens (`judge_case_<name>`)

| Name | Tier | Source payment | What it's for |
|---|---|---|---|
| `t0_allow` | 0 | genuine: new landlord, ₹44,300 | customer has **not opted in**: no phone flags sent |
| `t0_allow_on_call` | 0 | genuine: hospital bill during a call, ₹64,759 | a risky-looking genuine payment that goes through |
| `t0_merchant` | 0 | hand-built shop payment | the merchant fast path (not scored) |
| `t0_missed_scam` | 0 | marketplace scam, 1st payment, ₹2,728 | a **named failure case**: we miss it |
| `t1_note` | 1 | marketplace scam, 2nd payment | one-line note under Pay |
| `t2_pause` | 2 | loan-fee scam, 1st payment | pause: 30-s wait before Continue |
| `t2_false_alarm` | 2 | genuine payment, ₹14,002 | a **false alarm**: the way out for a genuine customer |
| `t3_trusted` | 3 | digital arrest, 3rd payment, ₹97,208 | trusted check or a 1-hour wait |
| `t4_hold` | 4 | digital arrest, 1st payment, ₹15,693 | hold for officer review |
| `t4_escalated` | 4 | task scam, 2nd payment | memory raised it from tier 2 |
| `fallback` | 0 | hand-built | decision service down: bank rules, nothing held |

## Response shape for `/judge/case`
- `phone`: exactly what the phone shows. It's the same as `explain.build_message()`: empty at tier 0, `headline` and `note` at tier 1, the full screen at tier 2+ (`headline`, `pattern`, `advice`, `why`, `actions`, and `hold_note` at tier 4).
- `phone_meta`: `continue_after_s`, and `case_id` for holds.
- `phone_by_lang`: the same screen in English and Hindi, so the language toggle needs no new request.
- `bank_view`: bank-only data (tier, scores, lines, families, reasons with weights, the key features behind the family cards, memory note, model guess, fallback, fast path, timings). **Never show it on the phone pane.**
- `sent_from_phone`: what the phone sent.
- `stored`: the evidence record, or `"kind": "none"` for tiers 0–1, plus the list of what is never kept.
- `payee`: masked reference, type, and plain-language facts.

## Live-server extras worth knowing
- **`same_payee_as`:** `POST /judge/case` accepts `"same_payee_as": "<decision_id>"` to pay that same account again in the session.
- **New payee type:** `flagged_genuine`, a genuine account DoT wrongly flagged (the false-positive demo).
- **Paying:** `confirm_pin` returns `alert_tier` and `time_to_get_past_s`.
- **Officer release:** confirms the payee for that customer (`note` in the response).
- **Reasons with `weight: null`:** either memory (`RECENT_WARNING`) or a fallback fact (`FIRST_PAYMENT`, `ABOVE_USUAL`, `UNUSUAL_PATTERN`).

## Regenerate
```bash
python bank/mock/make_mocks.py
```
Only needed if the Round 1 outputs or the shapes change.
