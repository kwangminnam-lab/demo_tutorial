"""POST /v1/extract/results/export 단위 테스트 — 보정된 추출 결과 영속(F5).

AI OCR 결과의 잘못된 값을 화면에서 고친 뒤 저장하는 경로. 추출 재실행 없이 전달된
ExtractionResult를 그대로 export_root/extract/<doc_id>.jsonl로 덮어쓴다. 실 서버·DB
불요 — get_settings만 tmp 디렉토리로 바꿔 결정론적으로 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from kms.api.v1 import extract as extract_api
from kms.domain.extraction import ExtractedField, ExtractionResult


def _result() -> ExtractionResult:
    return ExtractionResult(
        doc_id="deadbeef",
        schema_id=1,
        fields=[ExtractedField(key="amount", value="1,200,000", page=1)],
    )


def test_export_corrected_writes_jsonl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        extract_api, "get_settings", lambda: SimpleNamespace(export_root=str(tmp_path))
    )
    # 사용자가 값을 보정(1,200,000 → 1,250,000)한 결과를 그대로 저장.
    result = _result()
    result.fields[0].value = "1,250,000"

    resp = extract_api.export_corrected_result(result, _user=None)  # type: ignore[arg-type]

    assert resp.export_path == "extract/deadbeef.jsonl"
    assert resp.export_error is None
    written = tmp_path / "extract" / "deadbeef.jsonl"
    assert written.exists()
    rows = [json.loads(line) for line in written.read_text().splitlines() if line]
    assert rows[0]["key"] == "amount"
    assert rows[0]["value"] == "1,250,000"  # 보정값이 반영됨


def test_export_corrected_reports_error_on_failure(tmp_path: Path, monkeypatch) -> None:
    # export_root를 파일로 만들어 디렉토리 생성 실패 유발 → 조용히 삼키지 않고 error 노출.
    bad = tmp_path / "notadir"
    bad.write_text("x")
    monkeypatch.setattr(
        extract_api, "get_settings", lambda: SimpleNamespace(export_root=str(bad))
    )

    resp = extract_api.export_corrected_result(_result(), _user=None)  # type: ignore[arg-type]

    assert resp.export_path is None
    assert resp.export_error is not None
