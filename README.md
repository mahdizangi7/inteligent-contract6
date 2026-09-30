# DeliveryClaims

An evidence-based APPROVED/REJECTED claim verification contract for
GenLayer. A claimant submits a claim, the criteria it should be judged
against, and one or more public evidence URLs. Any account can then
trigger evaluation, and GenLayer's validators must reach genuine
substantive agreement — not just matching text formatting — before a
verdict is recorded.

```
contracts/delivery_claims.py   Intelligent Contract (Python / GenVM)
```

---

## Table of contents

1. [What this fixes](#what-this-fixes)
2. [How it works](#how-it-works)
3. [Contract reference](#contract-reference)
4. [Deploy](#deploy)
5. [Test checklist](#test-checklist)
6. [Troubleshooting](#troubleshooting)
7. [Known unverified assumption](#known-unverified-assumption)
8. [Design notes](#design-notes)

---

## What this fixes

This contract replaces an earlier draft that had three problems:

1. **Cosmetic validation, not substantive consensus.** The old
   validator only checked that the leader's response *started with* an
   allowed label (`"APPROVED"` / `"REJECTED"`). That only verifies
   formatting — two validators could reach opposite real conclusions
   and both still "pass" as long as each produced some recognized
   word. Fixed by routing the whole evaluation through
   [`gl.eq_principle.prompt_comparative`](#how-it-works), which has an
   LLM comparator judge whether the leader's and each validator's
   *independently produced* verdicts genuinely agree.
2. **E022 lint failures** — `fetch_evidence`, `format_evidence`, and
   `evaluate_delivery` were defined without `self` as their first
   parameter. GenVM's linter requires every method defined in a
   contract's class body to take `self`, helper or not. Fixed: all
   four flagged methods (the three above, plus `extract_label`) now
   take `self` and are called as `self.method(...)`.
3. **Missing return annotation on `get_claim`.** Fixed: `get_claim`
   now has an explicit `-> str` return type, matching every other
   `@gl.public.view`/`@gl.public.write` method in the contract.

## How it works

1. **`submit_claim(description, criteria, evidence_sources_json)`** —
   registers a claim. `evidence_sources_json` is a JSON array of 1–5
   `http(s)://` URLs (public pages a validator can fetch). Status
   starts as `"pending"`.
2. **`evaluate_claim(claim_id)`** — the consensus step:
   - Builds a closure, `run_evaluation`, that fetches every evidence
     source fresh (`self.fetch_evidence`), formats them into one
     evidence block (`self.format_evidence`), and asks an LLM to
     judge the claim against the criteria (`self.evaluate_delivery`),
     returning its raw text response (verdict word + one-sentence
     reason).
   - Passes that closure to
     `gl.eq_principle.prompt_comparative(run_evaluation, comparison_criteria)`.
     Every validator — leader included — runs `run_evaluation` **itself**,
     completely independently: its own fetch of the same URLs, its own
     formatting, its own LLM call. GenLayer then has an LLM comparator
     check the leader's output against each validator's own output
     using `comparison_criteria`, which explicitly says: *equivalent
     if and only if the final verdict word matches; wording doesn't
     matter, a different verdict does.*
   - Only once that consensus clears does the contract extract the
     verdict word (`self.extract_label`) and write `status`,
     `decision`, `decided_at` into the claim.
3. **`get_claim(claim_id)`** / **`get_counter()`** — views.

Because `run_evaluation` re-fetches evidence and re-runs the LLM from
scratch for every validator, a claim can only resolve to a verdict
that multiple independent validators, each looking at freshly-fetched
evidence themselves, actually agree on.

## Contract reference

| Method | Type | What it does |
|---|---|---|
| `submit_claim(description, criteria, evidence_sources_json)` | write | Creates a claim, `status = "pending"` |
| `evaluate_claim(claim_id)` | write | Runs the comparative-consensus evaluation, sets `status` to `"approved"` or `"rejected"` |
| `get_claim(claim_id)` | view | Full claim JSON |
| `get_counter()` | view | Total claims submitted |

### Claim JSON shape

```json
{
  "id": "1",
  "claimant": "0x...",
  "description": "Order #4821 was delivered to the customer",
  "criteria": "Evidence must show a completed delivery scan at the destination address",
  "evidence_sources": ["https://example.com/tracking/4821"],
  "status": "approved",
  "decision": "APPROVED\nThe tracking page shows a delivered scan at the destination on the claimed date.",
  "decided_at": "1790000000",
  "submitted_at": "1789999000"
}
```

## Deploy

1. Open GenLayer Studio.
2. Paste the **entire** contents of `delivery_claims.py`, keeping the
   first line — `# { "Depends": "py-genlayer:..." }` — exactly as-is,
   with nothing before it.
3. Deploy and copy the resulting contract address.

**Copy from the downloaded file, not from rendered chat/browser
text.** Rich-text sources sometimes convert straight quotes (`"`) into
curly ones, which breaks GenVM's runner-comment parser and produces
`invalid_contract absent_runner_comment` at deploy time even though
the logic is fine.

## Test checklist

Run these before trusting the contract with anything real:

1. **Clear-approve case** — submit a claim whose evidence source
   plainly supports the claim under the stated criteria. Call
   `evaluate_claim`. Expect `status: "approved"`.
2. **Clear-reject case** — submit a claim whose evidence source
   plainly contradicts it (or is unrelated). Expect
   `status: "rejected"`.
3. **Conflicting-evidence case** (the actual regression test for the
   bug this contract fixes) — submit a claim with two evidence sources
   that point opposite directions — one supporting the claim, one
   contradicting it. Confirm the contract reaches *one* coherent
   verdict via genuine consensus rather than silently succeeding with
   validators privately disagreeing. If `evaluate_claim` instead fails
   to reach consensus (reverts), that's also a correct outcome for
   contradictory evidence — the point is that it must not silently
   record a decision nobody actually agreed on.
4. **Bad input rejection** — confirm `submit_claim` rejects a
   description/criteria under 5 characters, more than 5 evidence
   sources, and a non-`http(s)://` URL, each with the corresponding
   `[EXPECTED]` error.
5. **Double-evaluate is a no-op** — call `evaluate_claim` again on an
   already-resolved claim; it should just return the existing claim
   unchanged rather than re-running evaluation.

## Troubleshooting

**`invalid_contract absent_runner_comment` at deploy** — see "Deploy"
above; almost always a copy/paste artifact, not a code problem.

**E022 reappears on a method you added** — any new helper you add
inside the `DeliveryClaims` class needs `self` as its first parameter,
even if you only ever call it internally.

**`evaluate_claim` fails to reach consensus / times out** — this can
be a genuine, correct outcome for a claim whose evidence doesn't
clearly point one way (see test 3 above), not necessarily a bug. If it
happens on unambiguous test claims, check the
[unverified assumption](#known-unverified-assumption) below first.

## Known unverified assumption

The exact call signature of `gl.eq_principle.prompt_comparative` is
inconsistently documented across public sources — some show it called
as `prompt_comparative(fn, criteria_string)`, others show it used as a
context manager (`with prompt_comparative(criteria_string): ...`).
This contract uses the function-call form, matching the pattern used
in several live example contracts. If deployment or evaluation fails
with a signature-related error, this is the one line in
`evaluate_claim` to check against your installed SDK version.

## Design notes

- All contract-body methods take `self` and have explicit return
  annotations, satisfying GenVM's E022 lint rule throughout, not just
  on the three/four methods that were originally flagged.
- `fetch_evidence` and `format_evidence` are pure functions of their
  arguments — no contract state is read or written inside them — so
  two validators fetching the same live URLs end up handing the LLM
  identically-shaped evidence, which is what makes genuine agreement
  possible in the first place.
- The comparison criteria string passed to `prompt_comparative` is
  deliberately explicit that *wording differences don't matter but a
  different verdict word does* — leaving this vague would reintroduce
  a softer version of the original bug, just moved into the LLM
  comparator's judgment instead of a Python string-prefix check.
- Verdict extraction (`extract_label`) only ever runs on the
  leader's already-validated response, after consensus has cleared —
  it's a deterministic parse of already-agreed-upon text, not part of
  the consensus decision itself.
