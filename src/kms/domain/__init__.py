"""핵심 도메인 모델·규칙 (외부 의존 없음)."""

from kms.domain.access import AccessLevel
from kms.domain.errors import AuthenticationError, AuthorizationError
from kms.domain.models import (
    Answer,
    Citation,
    DiffOp,
    DiffResult,
    Document,
    DocumentMetadata,
    FileDoc,
    FileHit,
    SearchQuery,
    SearchResult,
    SourceType,
    UserContext,
    WordSpan,
    department_boost,
    is_visible_to,
)

__all__ = [
    "AccessLevel",
    "AuthenticationError",
    "AuthorizationError",
    "SourceType",
    "DocumentMetadata",
    "Document",
    "FileDoc",
    "FileHit",
    "UserContext",
    "SearchQuery",
    "SearchResult",
    "Citation",
    "Answer",
    "WordSpan",
    "DiffOp",
    "DiffResult",
    "is_visible_to",
    "department_boost",
]
