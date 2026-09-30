# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# DeliveryClaims - evidence-based APPROVED/REJECTED claim verification.
#
# A claimant submits a claim (what was delivered/completed), the
# criteria it should be judged against, and one or more public
# evidence URLs. Any account can then trigger evaluation.
#
# Consensus design (this is the part earlier drafts got wrong):
# validators don't just check that the leader's response *starts with*
# an allowed label - that only verifies formatting, so two validators
# could reach opposite substantive conclusions and still both "pass"
# as long as each independently produced *some* allowed label. Instead
# this contract uses gl.eq_principle.prompt_comparative, which has
# every validator independently re-run the exact same pipeline (fetch
# the same evidence sources fresh, format them the same way, ask the
# same question) and then has an LLM comparator judge whether the
# validator's own output and the leader's output reach the *same*
# APPROVED/REJECTED verdict. Only genuine agreement on the substantive
# outcome clears consensus; differing wording is fine, an opposite
# verdict is not.

from genlayer import *

import json
import datetime


MAX_EVIDENCE_SOURCES = 5
ALLOWED_LABELS = ("APPROVED", "REJECTED")


class DeliveryClaims(gl.Contract):
    counter: u256
    claims: TreeMap[str, str]

    def __init__(self) -> None:
        self.counter = u256(0)

    # =================================================================
    # HELPERS
    #
    # Every method defined in this class - public, view, or private -
    # takes self as its first parameter. GenVM's lint (E022) requires
    # this for any function defined in a contract's class body, whether
    # or not you think of it as "just a helper".
    # =================================================================

    def _claim_key(self, claim_id: str) -> str:
        return "claim:" + str(claim_id)

    def _now(self) -> int:
        # Confirmed deterministic against this repo's pinned runtime
        # with a standalone time-probe contract before shipping. If
        # you're on a different py-genlayer pin, re-verify.
        return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    def _sender(self) -> str:
        return gl.message.sender_address.as_hex.lower()

    def fetch_evidence(self, url: str) -> str:
        # One evidence source, fetched fresh every time this is
        # called. Both the leader's run and each validator's own
        # independent run call this themselves - nobody trusts a
        # fetch somebody else did.
        try:
            return gl.nondet.web.render(url, mode="text")
        except Exception as e:
            return "[FETCH_FAILED] " + str(e)

    def format_evidence(self, sources: list, texts: list) -> str:
        # Purely a function of (sources, texts) - deterministic given
        # the same inputs, so two validators who each independently
        # fetched matching live content end up handing the LLM
        # identically-shaped evidence.
        blocks = []

        for i in range(len(sources)):
            blocks.append(
                "Source "
                + str(i + 1)
                + " ("
                + str(sources[i])
                + "):\n"
                + str(texts[i]).strip()[:4000]
            )

        return "\n\n---\n\n".join(blocks)

    def evaluate_delivery(self, claim_text: str, criteria: str, evidence: str) -> str:
        prompt = (
            "You are verifying a delivery/completion claim against "
            "independent evidence. Respond with exactly one word on "
            "the first line: APPROVED or REJECTED. Follow it with a "
            "one-sentence reason on the next line. Base your verdict "
            "only on whether the evidence supports the claim under "
            "the given criteria.\n\n"
            "CLAIM:\n" + claim_text + "\n\n"
            "APPROVAL CRITERIA:\n" + criteria + "\n\n"
            "EVIDENCE:\n" + evidence
        )

        return gl.nondet.exec_prompt(prompt)

    def extract_label(self, response: str) -> str:
        text = str(response).strip()

        if text == "":
            return "UNKNOWN"

        first_line = text.splitlines()[0].strip().upper()

        for label in ALLOWED_LABELS:
            if first_line.startswith(label):
                return label

        return "UNKNOWN"

    def _read_claim(self, claim_id: str):
        key = self._claim_key(claim_id)

        if key not in self.claims:
            raise gl.vm.UserError("[EXPECTED] CLAIM_NOT_FOUND")

        return json.loads(self.claims[key])

    def _save_claim(self, claim) -> None:
        key = self._claim_key(claim["id"])
        self.claims[key] = json.dumps(claim, sort_keys=True, separators=(",", ":"))

    # =================================================================
    # SUBMIT
    # =================================================================

    @gl.public.write
    def submit_claim(
        self,
        description: str,
        criteria: str,
        evidence_sources_json: str,
    ) -> str:
        text = str(description).strip()

        if len(text) < 5:
            raise gl.vm.UserError("[EXPECTED] DESCRIPTION_TOO_SHORT")

        crit = str(criteria).strip()

        if len(crit) < 5:
            raise gl.vm.UserError("[EXPECTED] CRITERIA_TOO_SHORT")

        try:
            sources = json.loads(evidence_sources_json)
        except Exception:
            raise gl.vm.UserError("[EXPECTED] BAD_EVIDENCE_JSON")

        if not isinstance(sources, list) or not (1 <= len(sources) <= MAX_EVIDENCE_SOURCES):
            raise gl.vm.UserError("[EXPECTED] BAD_EVIDENCE_COUNT")

        clean_sources = []

        for s in sources:
            url = str(s).strip()

            if not (url.startswith("https://") or url.startswith("http://")):
                raise gl.vm.UserError("[EXPECTED] BAD_EVIDENCE_URL")

            clean_sources.append(url)

        self.counter = u256(int(self.counter) + 1)
        claim_id = str(int(self.counter))

        claim = {
            "id": claim_id,
            "claimant": self._sender(),
            "description": text,
            "criteria": crit,
            "evidence_sources": clean_sources,
            "status": "pending",
            "decision": "",
            "decided_at": "",
            "submitted_at": str(self._now()),
        }

        self._save_claim(claim)
        return json.dumps(claim, sort_keys=True, separators=(",", ":"))

    # =================================================================
    # EVALUATE  (the consensus step)
    # =================================================================

    @gl.public.write
    def evaluate_claim(self, claim_id: str) -> str:
        claim = self._read_claim(claim_id)

        if claim["status"] != "pending":
            return json.dumps(claim, sort_keys=True, separators=(",", ":"))

        description = claim["description"]
        criteria = claim["criteria"]
        sources = claim["evidence_sources"]

        def run_evaluation() -> str:
            # Every validator - leader included - runs this exact
            # closure itself: its own fresh fetch of every source, its
            # own formatting, its own LLM call. Nothing here is passed
            # in from outside; it is fully independently reproducible.
            texts = [self.fetch_evidence(url) for url in sources]
            evidence = self.format_evidence(sources, texts)
            return self.evaluate_delivery(description, criteria, evidence)

        # This is the actual fix: agreement is judged by an LLM
        # comparator against explicit comparison criteria, on the
        # *substance* of each validator's independently-produced
        # verdict - not by every validator separately checking that
        # its own text merely starts with a recognized word. Two
        # validators concluding opposite verdicts can no longer both
        # "pass"; that is precisely what this call is for.
        comparison_criteria = (
            "Both responses are verdicts on the same delivery claim, "
            "evaluated against the same approval criteria and "
            "equivalent evidence. They are equivalent if and only if "
            "they reach the same final verdict - APPROVED or REJECTED. "
            "Different wording, phrasing, or reasoning in the "
            "explanation does not matter. A different verdict word "
            "means they are NOT equivalent, even if the reasoning "
            "given is similar."
        )

        raw_response = gl.eq_principle.prompt_comparative(
            run_evaluation, comparison_criteria
        )

        label = self.extract_label(raw_response)

        if label not in ALLOWED_LABELS:
            raise gl.vm.UserError("[EXTERNAL] UNRECOGNIZED_VERDICT")

        claim["status"] = label.lower()
        claim["decision"] = str(raw_response)
        claim["decided_at"] = str(self._now())

        self._save_claim(claim)
        return json.dumps(claim, sort_keys=True, separators=(",", ":"))

    # =================================================================
    # VIEWS
    # =================================================================

    @gl.public.view
    def get_claim(self, claim_id: str) -> str:
        return json.dumps(
            self._read_claim(claim_id), sort_keys=True, separators=(",", ":")
        )

    @gl.public.view
    def get_counter(self) -> str:
        return str(int(self.counter))
