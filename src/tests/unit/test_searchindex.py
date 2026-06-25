"""SearchIndex 단위 테스트 — InMemorySearchIndex (서버 없이 자동 실행).

검증 핵심:
- 권한 인지(하드 필터)를 검색 단계에서 강제 — 권한 밖 파일은 결과에 안 나온다.
- 부서 가중(soft)은 권한 통과분의 순위만 올린다 — 권한 밖 파일을 끌어올리지 않는다.
- source 필터·파일 단위(중복 없음)·오타 근접(difflib) 동작.
"""

from kms.adapters.searchindex import InMemorySearchIndex
from kms.domain.access import AccessLevel
from kms.domain.models import FileDoc, SourceType


def _file(
    doc_id: str,
    *,
    title: str = "기획안",
    description: str = "",
    tags: list[str] | None = None,
    author: str | None = None,
    author_department: str | None = None,
    source: SourceType = SourceType.ONEDRIVE,
    access: AccessLevel = AccessLevel.임직원,
) -> FileDoc:
    return FileDoc(
        doc_id=doc_id,
        title=title,
        description=description,
        tags=tags or [],
        author=author,
        author_department=author_department,
        source=source,
        access=access,
    )


def test_search_excludes_higher_access_files() -> None:
    # Arrange: 임직원 파일과 사장 파일, 둘 다 제목이 쿼리와 일치.
    index = InMemorySearchIndex()
    index.index_file(_file("emp", access=AccessLevel.임직원))
    index.index_file(_file("ceo", access=AccessLevel.사장))

    # Act: 임직원 권한으로 검색.
    hits = index.search("기획안", AccessLevel.임직원)

    # Assert: 사장 파일은 결과에 절대 없다(권한 하드 필터).
    doc_ids = {hit.file.doc_id for hit in hits}
    assert "ceo" not in doc_ids
    assert "emp" in doc_ids


def test_search_same_department_ranks_higher() -> None:
    # Arrange: 어휘 점수 동점인 두 파일, 하나만 요청자와 같은 부서.
    index = InMemorySearchIndex()
    index.index_file(_file("same_dept", author_department="AI플랫폼사업팀"))
    index.index_file(_file("other_dept", author_department="영업팀"))

    # Act: 같은 부서 가중을 켜고 검색.
    hits = index.search(
        "기획안",
        AccessLevel.임직원,
        department="AI플랫폼사업팀",
        department_boost_weight=1.0,
    )

    # Assert: 같은 부서 파일이 상위(부서 가중).
    assert hits[0].file.doc_id == "same_dept"


def test_search_does_not_surface_unauthorized_file_despite_boost() -> None:
    # Arrange: 권한 밖(사장) + 같은 부서 파일.
    index = InMemorySearchIndex()
    index.index_file(
        _file("ceo_same_dept", access=AccessLevel.사장, author_department="AI플랫폼사업팀")
    )

    # Act: 임직원 권한 + 강한 부서 가중으로 검색.
    hits = index.search(
        "기획안",
        AccessLevel.임직원,
        department="AI플랫폼사업팀",
        department_boost_weight=100.0,
    )

    # Assert: 가중이 아무리 커도 권한 밖 파일은 노출 안 됨.
    assert hits == []


def test_search_source_filter_returns_only_matching_source() -> None:
    # Arrange: onedrive·slack 파일.
    index = InMemorySearchIndex()
    index.index_file(_file("od", source=SourceType.ONEDRIVE))
    index.index_file(_file("sl", source=SourceType.SLACK))

    # Act: slack source 필터로 검색.
    hits = index.search("기획안", AccessLevel.임직원, source_filter=SourceType.SLACK)

    # Assert: slack 파일만.
    doc_ids = {hit.file.doc_id for hit in hits}
    assert doc_ids == {"sl"}


def test_search_returns_one_hit_per_file() -> None:
    # Arrange: 제목·요약·태그·작성자가 모두 쿼리와 맞는 파일(다중 필드 일치).
    index = InMemorySearchIndex()
    index.index_file(
        _file(
            "multi",
            title="기획안",
            description="기획안 본문",
            tags=["기획안"],
            author="기획안",
        )
    )

    # Act.
    hits = index.search("기획안", AccessLevel.임직원)

    # Assert: 여러 필드가 맞아도 파일당 1건(doc_id 유일).
    doc_ids = [hit.file.doc_id for hit in hits]
    assert doc_ids == ["multi"]


def test_search_tolerates_typo() -> None:
    # Arrange: 제목 "기획안".
    index = InMemorySearchIndex()
    index.index_file(_file("typo", title="기획안"))

    # Act: 오타 쿼리 "기혹안" (difflib 근접).
    hits = index.search("기혹안", AccessLevel.임직원)

    # Assert: 오타에도 결과에 포함(점수 > 0).
    doc_ids = {hit.file.doc_id for hit in hits}
    assert "typo" in doc_ids
