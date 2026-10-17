import logging
from typing import Optional

from app.tools.models import VerifiedReviewersResponse
from app.evidence.models import RefusalCode

logger = logging.getLogger(__name__)


class TemplateNarrator:
    """
    Deterministic template narrator.
    Generates factual, evidence-backed prose exclusively from verified claims.
    Never invents subjective rankings, scores, or guesses.
    Preserves refusal codes without speculative language.
    """

    def narrate(self, question: str, response: VerifiedReviewersResponse) -> str:
        if response.refused:
            code_str = (
                response.reason_code.value
                if hasattr(response.reason_code, "value")
                else str(response.reason_code)
            )
            return f"[Refusal: {code_str}] {response.reason}"

        lines = [
            f"Based on verified repository history in '{response.repo_name}', "
            f"candidate reviewers for {response.target_type} '{response.target}':"
        ]

        for cand in response.candidates:
            recency_str = (
                f", last commit {cand.last_commit_sha[:10]} at {cand.last_commit_at}"
                if cand.last_commit_at
                else ""
            )
            lines.append(
                f"- {cand.person_name} ({cand.person_email}): "
                f"{cand.commit_count} commits, {cand.lines_added} lines added, "
                f"{cand.lines_deleted} lines deleted{recency_str}."
            )

        if response.sync_info and "synced_at" in response.sync_info:
            lines.append(f"\nGraph snapshot timestamp: {response.sync_info['synced_at']}.")

        return "\n".join(lines)
