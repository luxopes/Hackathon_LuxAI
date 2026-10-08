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
| P1 | ~~Signed receipts + offline verifier~~ **DONE** (Ed25519, key published, `tools/verify_receipt.py`, tamper test passes) | “Practically 1:1 with real payments”: cryptographic proof, not just our word | 1–1.5 h |
| P2 | ~~Ledger export (JSONL/CSV)~~ **DONE** (+ append-only hash chain + `/api/ledger/check`) | Auditable data out of the system | 30 min |
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

Measured on the live system: the whole auto demo (catalog question + audit,
refund, re-purchase, receipt) runs in **20–30 seconds**, so the video has room to
walk through the receipt. Keep the narration tight at the start.

| t | Screen / action | Talk track |
|---|---|---|
| 0:00–0:10 | Console (fresh state) | “Every agent deal still ends at a human’s credit card. We removed the human.” |
| 0:10–0:20 | Click **▶ Auto demo** | “One click. From here nobody touches anything: discovery, purchase, dispute resolution.” |
| 0:20–0:40 | Tools panel fills with Flash tool + HTTP calls; escrow pill appears, turns into a refusal, refund, re-purchase | “The agent calls `fetch_offers`, locks the cheapest audit in escrow with an idempotency key, refuses the incomplete delivery, gets refunded and buys elsewhere — nothing pays twice.” |
| 0:40–0:55 | Paid delivery with the **CART BUG** line; wallet numbers; open the **Payments** tab, click 🧾 | “Only the verified delivery is paid. Here is the receipt.” |
| 0:55–1:35 | Receipt page: money movement table with buyer balances, lifecycle stepper, contract + delivery hashes, **SIGNED RECEIPT** badge | “Double-entry ledger movements with reconstructed balances, the escrow lifecycle, contract and delivery SHA-256 — and an Ed25519-signed receipt.” |
| 1:35–1:50 | Run the verifier, or show `/docs` + the ledger chain badge | “Anyone can verify it offline with the published key, the ledger is an append-only hash chain, and the market invariant issued == accounted holds.” |
| 1:50–2:00 | Payments list (totals, success rate) | “Caps hold, nothing pays twice — and every payment is labelled simulated.” |

---

## Pre-record checklist

- [x] `POST /api/new` (fresh conversation, wallet appears with budget 30 on first purchase)
- [x] Quotas checked: ElevenLabs 46/131000 chars, Apify credit almost unused, Flash OK
- [x] Rehearsal run measured: refund + re-purchase + receipt in ~20–30 s
- [x] Signed receipt verified offline against the production key (tamper test fails)
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
