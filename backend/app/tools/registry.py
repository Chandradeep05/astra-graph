from typing import Any, Callable, Dict, List, Optional, Type
import inspect
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.db.falkor import FalkorDBManager
from app.tools.models import VerifiedReviewersResponse, ChangeImpactResponse, ImpactReviewersResponse
from app.tools.reviewers import get_verified_reviewers
from app.tools.impact import change_impact
from app.tools.impact_reviewers import impact_reviewers
from app.context.models import ContextPackage
from app.context.service import ContextPackageService




class UnknownToolError(Exception):
    """Raised when an unrecognized tool name is requested."""
    pass


class InvalidToolArgumentsError(Exception):
    """Raised when tool arguments do not satisfy the tool's input schema."""
    pass


class ReviewersToolInput(BaseModel):
    """Input schema for the reviewers_for tool."""
    repo: str = Field(default="arth", description="Target repository name")
    file: Optional[str] = Field(default=None, description="Path to target file")
    module: Optional[str] = Field(default=None, description="Path to target module")
    exclude_emails: Optional[List[str]] = Field(default=None, description="Contributor emails to exclude")
    limit: int = Field(default=10, description="Maximum candidate reviewers to return")
    max_evidence: int = Field(default=20, description="Maximum evidence commits to return")

    @model_validator(mode="before")
    @classmethod
    def validate_target(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        file = data.get("file") or data.get("target_file")
        module = data.get("module") or data.get("target_module")
        if not file and not module:
            raise ValueError("Must provide either 'file' or 'module'")
        if file and module:
            raise ValueError("Cannot provide both 'file' and 'module'")
        return data


def _handle_reviewers_for(
    params: ReviewersToolInput,
    db_manager: Optional[FalkorDBManager] = None,
) -> VerifiedReviewersResponse:
    return get_verified_reviewers(
        target_file=params.file,
        target_module=params.module,
        repo_name=params.repo,
        exclude_emails=params.exclude_emails,
        limit=params.limit,
        max_evidence=params.max_evidence,
        db_manager=db_manager,
    )


class ToolDefinition(BaseModel):
    """
    Metadata and execution contract for an application-owned deterministic tool.
    The LLM receives schemas, never raw database execution primitives.
    """
    name: str
    description: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]
    is_read_only: bool = True
    handler: Callable[..., Any] = Field(exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ToolRegistry:
    """
    Deterministic registry of application-owned tools.
    Rejects unknown tools and malformed arguments before any database or graph access.
    """

    def __init__(self) -> None:
        self.tools: Dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        self.tools[tool_def.name] = tool_def

    def get_tool(self, name: str) -> ToolDefinition:
        if name not in self.tools:
            raise UnknownToolError(
                f"Unknown tool '{name}'. Available tools: {list(self.tools.keys())}"
            )
        return self.tools[name]

    def execute_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        db_manager: Optional[FalkorDBManager] = None,
    ) -> Any:
        tool_def = self.get_tool(name)

        try:
            validated_args = tool_def.input_schema.model_validate(arguments)
        except (ValidationError, ValueError, TypeError) as exc:
            raise InvalidToolArgumentsError(
                f"Invalid arguments for tool '{name}': {str(exc)}"
            ) from exc

        # Inspect handler signature to optionally pass db_manager if supported
        sig = inspect.signature(tool_def.handler)
        kwargs = {}
        if "db_manager" in sig.parameters:
            kwargs["db_manager"] = db_manager

        return tool_def.handler(validated_args, **kwargs)

    def get_tools_prompt_description(self) -> str:
        """Returns JSON-compatible descriptions of available tools for LLM prompts."""
        descriptions = []
        for name, tool in self.tools.items():
            schema_json = tool.input_schema.model_json_schema()
            descriptions.append({
                "name": name,
                "description": tool.description,
                "parameters": schema_json,
                "read_only": tool.is_read_only,
            })
        return str(descriptions)


class ChangeImpactToolInput(BaseModel):
    """Input schema for the change_impact tool."""
    file_path: Optional[str] = Field(default=None, description="Path to target file")
    file: Optional[str] = Field(default=None, description="Path to target file (alias)")
    repo: str = Field(default="arth", description="Target repository name")
    limit: int = Field(default=10, description="Maximum impacted files to return")
    max_evidence: int = Field(default=30, description="Maximum evidence commit records to return")

    @model_validator(mode="before")
    @classmethod
    def validate_file(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        target = data.get("file_path") or data.get("file") or data.get("target_file")
        if not target:
            raise ValueError("Must provide 'file_path' or 'file'")
        data["file_path"] = target
        return data


def _handle_change_impact(
    params: ChangeImpactToolInput,
    db_manager: Optional[FalkorDBManager] = None,
) -> ChangeImpactResponse:
    return change_impact(
        target_file=params.file_path,
        repo_name=params.repo,
        limit=params.limit,
        max_evidence=params.max_evidence,
        db_manager=db_manager,
    )


# Application-wide singleton registry
tool_registry = ToolRegistry()

# Register core reviewers_for tool
tool_registry.register(
    ToolDefinition(
        name="reviewers_for",
        description=(
            "Retrieves evidence-backed candidate code reviewers for a given file or module "
            "based exclusively on FalkorDB commit authorship, line churn, and recency."
        ),
        input_schema=ReviewersToolInput,
        output_schema=VerifiedReviewersResponse,
        is_read_only=True,
        handler=_handle_reviewers_for,
    )
)

# Register change_impact blast-radius tool
tool_registry.register(
    ToolDefinition(
        name="change_impact",
        description=(
            "Calculates deterministic, evidence-backed blast radius and historical co-change "
            "impact for a repository file based on FalkorDB CO_CHANGES and commit history."
        ),
        input_schema=ChangeImpactToolInput,
        output_schema=ChangeImpactResponse,
        is_read_only=True,
        handler=_handle_change_impact,
    )
)


class ImpactReviewersToolInput(BaseModel):
    """Input schema for the impact_reviewers tool."""
    file_path: Optional[str] = Field(default=None, description="Path to target file")
    file: Optional[str] = Field(default=None, description="Path to target file (alias)")
    repo: str = Field(default="databases", description="Target repository name")
    limit: int = Field(default=5, description="Maximum reviewer candidates to return")
    max_evidence: int = Field(default=30, description="Maximum evidence commit records to return")

    @model_validator(mode="before")
    @classmethod
    def validate_file(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        target = data.get("file_path") or data.get("file") or data.get("target_file")
        if not target:
            raise ValueError("Must provide 'file_path' or 'file'")
        data["file_path"] = target
        return data


def _handle_impact_reviewers(
    params: ImpactReviewersToolInput,
    db_manager: Optional[FalkorDBManager] = None,
) -> ImpactReviewersResponse:
    return impact_reviewers(
        target_file=params.file_path,
        repo_name=params.repo,
        limit=params.limit,
        max_evidence=params.max_evidence,
        db_manager=db_manager,
    )


# Register impact_reviewers tool
tool_registry.register(
    ToolDefinition(
        name="impact_reviewers",
        description=(
            "Synthesizes historical co-change blast radius with author lineage to determine "
            "ranked, evidence-backed candidate code reviewers respecting persistent graph memory."
        ),
        input_schema=ImpactReviewersToolInput,
        output_schema=ImpactReviewersResponse,
        is_read_only=True,
        handler=_handle_impact_reviewers,
    )
)


class ContextPackageToolInput(BaseModel):
    """Input schema for the context_package tool."""
    repository: str = Field(default="arth", description="Target repository name")
    subject: Optional[str] = Field(default=None, description="Optional target file or module path")
    subject_type: Optional[str] = Field(default=None, description="Type of subject: 'repository', 'file', or 'module'")
    depth: int = Field(default=3, description="Graph traversal depth (1-5)")
    max_files: int = Field(default=20, description="Max files to include in context")
    max_contributors: int = Field(default=10, description="Max contributors to include in context")
    max_evidence: int = Field(default=30, description="Max evidence records to attach")


def _handle_context_package(
    params: ContextPackageToolInput,
    db_manager: Optional[FalkorDBManager] = None,
) -> ContextPackage:
    service = ContextPackageService(db_manager=db_manager)
    return service.get_context_package(
        repository=params.repository,
        subject=params.subject,
        subject_type=params.subject_type,
        depth=params.depth,
        max_files=params.max_files,
        max_contributors=params.max_contributors,
        max_evidence=params.max_evidence,
    )


# Register context_package tool
tool_registry.register(
    ToolDefinition(
        name="context_package",
        description=(
            "Generates a bounded, evidence-backed repository, file, or module Context Package "
            "derived exclusively from FalkorDB knowledge graph relationships."
        ),
        input_schema=ContextPackageToolInput,
        output_schema=ContextPackage,
        is_read_only=True,
        handler=_handle_context_package,
    )
)



