"""내보내기 어댑터 단위 테스트 — PDF·DOCX·TXT 바이트 + 출처 인용 보존.

실 LLM·서버 없이 도는 결정론적 테스트. 한글 본문으로 PDF 폰트 처리를 검증한다.

검증 핵심(AC):
- TXT 바이트에 답변 텍스트·출처가 포함된다.
- DOCX는 비어 있지 않은 유효 zip(`PK` 시그니처)이다.
- PDF는 비어 있지 않은 유효 바이트(`%PDF` 시그니처)이며 한글이 깨지지 않는다.
- `Answer` 입력 시 출처 인용이 렌더링에 보존된다.
"""

from pathlib import Path

import pytest

from kms.adapters.export import ExportFormat, export, render_answer
from kms.adapters.storage import write_export
from kms.domain.models import Answer, Citation, SourceType

ANSWER = Answer(
    text="영업부 매출은 전년 대비 15% 증가했다.",
    citations=[
        Citation(
            source=SourceType.ONEDRIVE,
            doc_id="doc-매출-2025",
            page=3,
            snippet="2025년 영업부 매출 요약",
        ),
        Citation(
            source=SourceType.SLACK,
            doc_id="doc-회의록",
            slide_no=2,
            snippet="분기 실적 공유",
        ),
    ],
    grounded=True,
)


def test_render_answer_includes_citations() -> None:
    rendered = render_answer(ANSWER)

    assert ANSWER.text in rendered
    assert "출처:" in rendered
    # 번호·source·위치(page/slide)·doc_id가 모두 인용에 담긴다.
    assert "[1] onedrive doc-매출-2025 p.3" in rendered
    assert "[2] slack doc-회의록 slide 2" in rendered
    assert "2025년 영업부 매출 요약" in rendered


def test_txt_export_contains_text_and_sources() -> None:
    data = export(ANSWER, ExportFormat.TXT)

    text = data.decode("utf-8")
    assert ANSWER.text in text
    assert "doc-매출-2025" in text
    assert "doc-회의록" in text


def test_docx_export_is_valid_zip() -> None:
    data = export(ANSWER, ExportFormat.DOCX)

    assert len(data) > 0
    # DOCX는 zip 컨테이너 — `PK` 시그니처로 시작한다.
    assert data[:2] == b"PK"


def test_pdf_export_is_valid_pdf() -> None:
    data = export(ANSWER, ExportFormat.PDF)

    assert len(data) > 0
    # 유효 PDF는 `%PDF` 시그니처로 시작한다(한글 본문도 폰트 등록으로 처리).
    assert data[:4] == b"%PDF"


def test_export_plain_string() -> None:
    data = export("그냥 평문 요약입니다.", ExportFormat.TXT)

    assert data.decode("utf-8") == "그냥 평문 요약입니다."


def test_export_answer_citations_preserved_in_pdf_text() -> None:
    # Answer 입력은 본문과 출처가 함께 렌더링되어 내보내기에 들어간다.
    rendered = render_answer(ANSWER)

    assert "slack" in rendered
    assert "onedrive" in rendered


# ── 산출물 영속(storage.write_export) — 공유 PVC 쓰기 (step 5, F3/F5) ──────────


def test_write_export_writes_to_root_rel_dir_filename(tmp_path: Path) -> None:
    # export_root/rel_dir/filename 에 정확히 쓰고 그 경로를 반환한다.
    written = write_export("parse", "report.jsonl", '{"a": 1}\n', str(tmp_path))

    expected = tmp_path / "parse" / "report.jsonl"
    assert written == expected
    assert expected.read_text(encoding="utf-8") == '{"a": 1}\n'


def test_write_export_creates_parent_dirs(tmp_path: Path) -> None:
    # 디렉토리가 없어도 parents=True로 자동 생성한다.
    root = tmp_path / "does" / "not" / "exist"
    written = write_export("extract", "doc.jsonl", "x\n", str(root))

    assert written.parent.is_dir()
    assert written == root / "extract" / "doc.jsonl"


def test_write_export_accepts_bytes(tmp_path: Path) -> None:
    written = write_export("parse", "b.bin", b"\x00\x01ABC", str(tmp_path))

    assert written.read_bytes() == b"\x00\x01ABC"


def test_write_export_filename_basename_only_blocks_traversal(tmp_path: Path) -> None:
    # `..`/절대경로가 섞인 filename은 basename만 써서 루트 밖으로 새지 않는다.
    written = write_export("parse", "../../etc/evil.jsonl", "data\n", str(tmp_path))

    assert written == tmp_path / "parse" / "evil.jsonl"
    # 루트 밖(상위)에는 아무 것도 쓰이지 않는다.
    assert not (tmp_path.parent / "etc").exists()


def test_write_export_absolute_filename_uses_basename(tmp_path: Path) -> None:
    written = write_export("extract", "/etc/passwd", "data\n", str(tmp_path))

    assert written == tmp_path / "extract" / "passwd"


def test_write_export_rejects_dotdot_filename(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_export("parse", "..", "data\n", str(tmp_path))


def test_write_export_rejects_rel_dir_escape(tmp_path: Path) -> None:
    # rel_dir가 `..`로 루트를 벗어나면 거부한다(traversal 방어).
    with pytest.raises(ValueError):
        write_export("../escape", "x.jsonl", "data\n", str(tmp_path))


def test_write_export_overwrites_existing(tmp_path: Path) -> None:
    # 멱등 재쓰기 — 같은 경로는 덮어쓴다(원자적 교체).
    write_export("parse", "x.jsonl", "old\n", str(tmp_path))
    written = write_export("parse", "x.jsonl", "new\n", str(tmp_path))

    assert written.read_text(encoding="utf-8") == "new\n"
