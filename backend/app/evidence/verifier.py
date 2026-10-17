from typing import Any, Dict, List, Set
import re

from app.evidence.models import (
    Claim,
    ClaimRejection,
    Evidence,
    VerificationResult,
)

FORBIDDEN_TERMS = {
    "expert",
    "best",
    "owner",
    "master",
    "perfect",
    "guaranteed",
    "flawless",
    "risky",
}

CONFIDENCE_TERMS = {
    "probability",
    "confidence",
    "chance",
}


class EvidenceVerifier:
    """
    Deterministic, application-level verifier enforcing Anvaya's Trust Contract:
    1. Facts must come strictly from graph evidence records.
    2. No claim is accepted without valid evidence IDs.
    3. Factual values (commit counts, line churn) must match evidence aggregates.
    4. Subjective adjectives ('expert', 'best') and fake confidence percentages are rejected.
    """

    def __init__(self, allowed_forbidden_terms: Set[str] = None):
        self.forbidden_terms = FORBIDDEN_TERMS - (allowed_forbidden_terms or set())

    def verify(
        self,
        claims: List[Claim],
        evidence: List[Evidence],
    ) -> VerificationResult:
        ev_map: Dict[str, Evidence] = {e.id: e for e in evidence}

        verified_claims: List[Claim] = []
        rejected_claims: List[ClaimRejection] = []
        rejection_reasons: List[str] = []

        for claim in claims:
            rejection_reason = self._check_claim(claim, ev_map)
            if rejection_reason:
                rejected_claims.append(ClaimRejection(claim=claim, reason=rejection_reason))
                rejection_reasons.append(f"[{claim.claim_id}] {rejection_reason}")
            else:
                verified_claims.append(claim)

        is_verified = len(rejected_claims) == 0 and len(verified_claims) > 0

        return VerificationResult(
            verified=is_verified,
            verified_claims=verified_claims,
            rejected_claims=rejected_claims,
            rejection_reasons=rejection_reasons,
        )

    def _check_claim(self, claim: Claim, ev_map: Dict[str, Evidence]) -> str:
        # Rule 1: Every claim must reference evidence IDs
        if not claim.evidence_ids:
            return "Claim has no evidence IDs"

        # Rule 2: All referenced evidence IDs must exist in provided evidence
        missing_ids = [eid for eid in claim.evidence_ids if eid not in ev_map]
        if missing_ids:
            return f"Evidence ID(s) {missing_ids} not found in provided evidence"

        # Rule 3: Detect forbidden subjective words or fake confidence scores
        text_to_scan = f"{claim.claim_type} {claim.predicate} {str(claim.value)}".lower().replace("_", " ")

        # Check for fake confidence numbers or percentages
        if any(term in text_to_scan for term in CONFIDENCE_TERMS) or "%" in str(claim.value):
            return f"Forbidden confidence score or probability asserted: '{claim.value}'"

        # Check for subjective adjectives (expert, best reviewer, etc.)
        for term in self.forbidden_terms:
            if re.search(r"\b" + re.escape(term) + r"\b", text_to_scan):
                return f"Unsupported subjective claim: word '{term}' is not supported by graph evidence."

        # Rule 4: Validate quantitative claims against actual evidence values
        supporting_evs = [ev_map[eid] for eid in claim.evidence_ids]

        if claim.claim_type == "commit_count":
            # Count of distinct commit evidence records
            commit_evs = [e for e in supporting_evs if e.kind == "commit"]
            if len(commit_evs) != claim.value:
                return (
                    f"Mismatch: Claimed {claim.value} commits but only {len(commit_evs)} "
                    f"supporting evidence records provided"
                )

        elif claim.claim_type == "lines_added":
            total_added = sum(e.properties.get("added", 0) for e in supporting_evs)
            if total_added != claim.value:
                return f"Mismatch: Claimed {claim.value} lines added but evidence totals {total_added}"

        elif claim.claim_type == "lines_deleted":
            total_deleted = sum(e.properties.get("deleted", 0) for e in supporting_evs)
            if total_deleted != claim.value:
                return f"Mismatch: Claimed {claim.value} lines deleted but evidence totals {total_deleted}"

        elif claim.claim_type == "recency":
            # Verify timestamp matches one of the supporting evidence commit timestamps
            ev_timestamps = [e.properties.get("ts") for e in supporting_evs if "ts" in e.properties]
            if ev_timestamps and claim.value not in ev_timestamps:
                return f"Mismatch: Claimed recency {claim.value} not found among evidence commit timestamps"

        elif claim.claim_type == "co_change_count":
            commit_evs = [e for e in supporting_evs if e.kind == "commit"]
            if commit_evs and len(commit_evs) != claim.value:
                return (
                    f"Mismatch: Claimed {claim.value} co-changes but only {len(commit_evs)} "
                    f"supporting commit evidence records provided"
                )

        elif claim.claim_type == "shared_commit_count":
            commit_evs = [e for e in supporting_evs if e.kind == "commit"]
            if commit_evs and len(commit_evs) != claim.value:
                return (
                    f"Mismatch: Claimed {claim.value} shared commits but only {len(commit_evs)} "
                    f"supporting commit evidence records provided"
                )

        elif claim.claim_type == "last_co_change":
            ev_timestamps = [e.properties.get("ts") for e in supporting_evs if "ts" in e.properties]
            if ev_timestamps and claim.value not in ev_timestamps:
                return f"Mismatch: Claimed last co-change {claim.value} not found among evidence commit timestamps"

        return ""

