import logging
from typing import Any, Optional

from app.tools.models import (
    ChangeImpactResponse,
    ImpactReviewersResponse,
    VerifiedReviewersResponse,
)
from app.evidence.models import RefusalCode

logger = logging.getLogger(__name__)


class TemplateNarrator:
    """
    Deterministic template narrator.
    Generates factual, evidence-backed prose exclusively from verified claims.
    Never invents subjective rankings, scores, or guesses.
    Preserves refusal codes without speculative language.
    """

    def narrate(self, question: str, response: Any) -> str:
        if getattr(response, "refused", False):
            reason_code = getattr(response, "reason_code", None)
            code_str = (
                reason_code.value
                if hasattr(reason_code, "value")
                else str(reason_code)
            )
            return f"[Refusal: {code_str}] {getattr(response, 'reason', '')}"

        if isinstance(response, ImpactReviewersResponse):
            lines = [
                f"Based on verified repository history and co-change analysis in '{response.repo_name}' for '{response.target_file}':"
            ]
            if response.impacted_files:
                lines.append(f"\nHistorical blast radius ({len(response.impacted_files)} co-changing files):")
                for f in response.impacted_files[:5]:
                    lines.append(f"- {f.path}: {f.co_change_count} co-changes ({f.shared_commit_count} shared commits)")

            if response.candidates:
                lines.append("\nRecommended candidate reviewers:")
                for c in response.candidates:
                    lines.append(
                        f"- {c.person_name} ({c.person_email}): {c.direct_commits} direct commits, "
                        f"{c.co_change_commits} co-change commits across {len(c.relevant_files)} relevant files."
                    )

            if response.excluded_candidates:
                excluded_names = ", ".join(e.person for e in response.excluded_candidates)
                lines.append(f"\nExcluded by active graph memory constraint: {excluded_names}.")

            if response.sync_info and "synced_at" in response.sync_info:
                lines.append(f"\nGraph snapshot timestamp: {response.sync_info['synced_at']}.")

            return "\n".join(lines)

        if isinstance(response, ChangeImpactResponse):
            lines = [
                f"Historical change impact analysis for '{response.target_file}' in '{response.repo_name}':"
            ]
            if response.impacted_files:
                lines.append(f"\nCo-changing files ({len(response.impacted_files)} files):")
                for f in response.impacted_files[:5]:
                    lines.append(f"- {f.path}: {f.co_change_count} co-changes")
            if response.related_people:
                lines.append(f"\nRelated historical contributors:")
                for p in response.related_people[:5]:
                    lines.append(f"- {p.person}: {p.shared_commit_count} shared commits")
            return "\n".join(lines)

        if isinstance(response, VerifiedReviewersResponse):
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

            if response.excluded_candidates:
                excluded_names = ", ".join(e.person for e in response.excluded_candidates)
                lines.append(f"\nExcluded by active graph memory constraint: {excluded_names}.")

            if response.sync_info and "synced_at" in response.sync_info:
                lines.append(f"\nGraph snapshot timestamp: {response.sync_info['synced_at']}.")

            return "\n".join(lines)

        return str(response)

