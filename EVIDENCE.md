# EVIDENCE

One pasted proof per requirement (test name + output, curl transcript, or log line). Claims without evidence score as not done. Each entry: **How** (command to reproduce), **Date**, **Output** (verbatim, secrets redacted), **Why this proves it** (one sentence).

## Metering
- [ ] **M1** A billable action creates exactly one usage event, even under retries.
- [ ] **M2** Proof that double-counting cannot happen.

## Quotas
- [ ] **Q1** Usage checked against the tenant's plan; over-limit requests rejected.
- [ ] **Q2** Correct 429 / 402 with an explanatory message.

## Cost calculation
- [ ] **C1** Monthly usage rolls up into a cost figure per tenant.
- [ ] **C2** Token pricing handles cached input, reasoning, and output correctly.
- [ ] **C3** Pricing constants pinned in config; totals proven.

## Stripe integration
- [ ] **S1** Subscription Checkout works end-to-end in test mode.
- [ ] **S2** Webhooks verify signatures, ignore duplicates, update plan/status.

## Data model, tests & documentation
- [ ] **D1** Tenants, plans, subscriptions, usage events; tenant isolation.
- [ ] **D2** README + architecture diagram + setup; required files present.

## Acceptance probes
- [ ] **P1** Same request twice → one event, mirrored response.
- [ ] **P2** Exact quota boundary → documented behaviour; next → 429/402.
- [ ] **P3** Test Checkout → Free → Pro; `/usage` shows new limits.
- [ ] **P4** Forged webhook → 400; replayed event processed once.
- [ ] **P5** Pricing rules → exact expected totals; `/usage` matches.
