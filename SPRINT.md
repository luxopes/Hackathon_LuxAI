# Sprint: the last 7 hours (code freeze 07:14)

**Scope rule: only what improves the submission.** Everything that cannot be
finished, verified and shown in a 2-minute video before the freeze is out of
scope (see `ROADMAP.md` for the post-hackathon plan).

Current clock: freeze is at 07:14; plan 7 hours of work, then keep ~2 hours of
safety buffer. Submission = **codebase + 2-minute demo video** on the HQ; the
video is mandatory, so it is scheduled first and nothing may delay it.

---

## Priority order (by judging impact)

| # | Item | Why | Time |
|---|---|---|---|
| P0 | Demo video (script + record + upload) | Mandatory for submission; 35 % value + 20 % end-to-end are shown here | 1.5–2 h |
| P1 | Signed receipts + offline verifier | “Practically 1:1 with real payments”: cryptographic proof, not just our word | 1–1.5 h |
| P2 | Ledger export (JSONL/CSV) | Auditable data out of the system | 30 min |
| P3 | Demo hardening: reset flow, quota check, rehearsal | One failed take costs 20 minutes | 30 min |
| P4 | Submission packaging: repo state, ZIP, HQ upload | Nobody should scramble at 07:00 | 30 min |
| P5 | Stretch — only if everything above is done | e.g. epoch hash chain, seller page | rest |

**Out of scope tonight (deliberately):** SSE, console auth, Postgres, x402/AP2
rails, seller onboarding, arbitration jury, semantic verification.

---

## Timeline (local time)

| Time | Work | Owner |
|---|---|---|
| now → +1:15 | P1 signed receipts: key, `signatures` table, signing on settle/refund, badge on the receipt, `tools/verify_receipt.py`, tamper test | agent |
| +1:15 → +1:45 | P2 ledger export endpoint + link on the payments page + docs | agent |
| +1:45 → +2:15 | P3 demo hardening: one-click reset, all pages checked, auto-demo timing measured, rehearsal | agent |
| +2:15 → +2:45 | P0a storyboard is written (below); reset the demo, put the console on the projector | both |
| +2:45 → +4:15 | P0b record the 2-minute video (2–3 takes), upload | user |
| +4:15 → +5:00 | P0c cut/trim + publish link, HQ submission started | user |
| +5:00 → +6:00 | P5/ fixes found while recording (worst case: revert to the recorded take) | agent |
| +6:00 → +7:00 | P4 final: freeze commit, ZIP, submission confirmed, services left healthy | both |

---

## Demo video — 2 minutes, exact beats

Record the browser at 1366×768 or 1920×1080, console full screen. Voice-over in
English, calm pace. One take = ok, do not chase perfection.

| t | Screen / action | Talk track |
|---|---|---|
| 0:00–0:10 | Console (fresh state) | “Every agent deal still ends at a human’s credit card. We removed the human.” |
| 0:10–0:25 | Click **▶ Auto demo** | “One click. From here nothing is touched by a human: discovery, purchase, dispute resolution.” |
| 0:25–0:45 | Tools panel fills with Flash tool + HTTP calls; escrow pill appears | “The agent calls `fetch_offers`, picks the cheapest auditor and locks 3 Lux Coins in escrow with an idempotency key.” |
| 0:45–1:05 | Chat shows: preview refused → refund → re-purchase; wallet numbers change | “The delivery breaks the contract, the preview is refused, the escrow is refunded automatically, and the agent buys elsewhere — nothing pays twice.” |
| 1:05–1:25 | Paid delivery with **CART BUG** line, then open **Payments** tab and click 🧾 | “Only the verified delivery is paid. Here is the receipt.” |
| 1:25–1:50 | Receipt page: money movement table, buyer balances, lifecycle stepper, hashes, **signature badge**, reconciliation + invariant | “Double-entry ledger movements, reconstructed balances, contract and delivery hashes, a signed receipt you can verify offline, and the market invariant issued == accounted.” |
| 1:50–2:00 | Payments list (totals, success rate) | “Caps hold, nothing pays twice — and every payment is labelled simulated.” |

---

## Pre-record checklist

- [ ] `POST /api/new` (fresh conversation), wallet visible with budget 30
- [ ] Browser zoom 125 %, no bookmarks bar, notifications off
- [ ] Services: market, web, tts, all five sellers active
- [ ] Flash + Apify + ElevenLabs quota checked (one full rehearsal run)
- [ ] Auto demo timed once: expected 60–90 s to the paid receipt
- [ ] Fallback: if a live turn misbehaves on camera, cut to the receipts of the
      rehearsal run (they are on the payments page)

## Freeze checklist (07:14)

- [ ] `git push` — repo contains backend, frontend, docs, roadmap, sprint
- [ ] Video uploaded, link pasted into the HQ submission form
- [ ] Codebase submitted (repo link + ZIP)
- [ ] Services left running; public pages reachable
