"""필드추출 API 통합 테스트 (ADR-024) — 서버 없이 TestClient + 인메모리 더블.

검증: 스키마 생성=마스터 전용(멤버 403), 추출 실행=인증 사용자, 디지털 PDF 라인
추출 → gpt-oss(카나리) → 값+bbox 근거. 미인증 401. 미지원 형식 422.

DB·실 LLM 없이: 인메모리 repo + 결정론 LLM + 실제 LineProviderRegistry(pymupdf).
"""

from __future__ import annotations

from collections.abc import Iterator

import pymupdf
import pytest
from fastapi.testclient import TestClient

from kms.adapters.auth.passwords import hash_password
from kms.adapters.auth.tokens import JwtCodec
from kms.adapters.extraction.llm_extractor import LlmFieldExtractor
from kms.adapters.ingestion.lines.registry import LineProviderRegistry
from kms.api.app import create_app
from kms.api.deps import get_account_service, get_auth_service, get_extraction_service
from kms.domain.access import AccessLevel
from kms.services.account_service import AccountService
from kms.services.auth_service import AuthService, JwtIdentityProvider
from kms.services.extraction_service import ExtractionService
from tests._fake_account_store import FakeAccountStore
from tests._fake_extraction_repo import FakeExtractionRepository

_MASTER = ("master@docux.local", "ChangeMe!1234")
_MEMBER = ("member@docux.local", "member-pw-8")

# 카나리 LLM — 첫 라인(line_id 0)을 근거로 금액 필드를 반환.
_CANNED = (
    '{"fields":[{"key":"amount","value":"1,200,000,000",'
    '"evidence_line_ids":[0],"confidence":0.95}]}'
)


class _CannedLLM:
    def generate(self, prompt, *, system=None, response_format=None):  # type: ignore[no-untyped-def]
        return _CANNED

    def stream(self, prompt, *, system=None):  # type: ignore[no-untyped-def]
        yield _CANNED


class _FakeVlm:
    """이미지/손글씨 경로 더블 — 네트워크 없이 카나리 필드 반환."""

    def is_available(self) -> bool:
        return True

    def extract(self, path, schema):  # type: ignore[no-untyped-def]
        from kms.domain.extraction import ExtractedField

        return [
            ExtractedField(
                key="amount",
                value="9,999",
                page=1,
                bbox=(1, 2, 3, 4),
                source="handwriting",
                confidence=0.85,
                needs_review=False,
            )
        ]

    def propose_schema(self, path, *, doc_type=None):  # type: ignore[no-untyped-def]
        return []


def _png_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72.0, 72.0), "scan", fontsize=12)
    data = page.get_pixmap().tobytes("png")
    doc.close()
    return bytes(data)


def _make_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72.0, 72.0), "Amount 1,200,000,000", fontsize=12)
    page.insert_text((72.0, 96.0), "Date 2026-03-01", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return bytes(data)


@pytest.fixture
def client() -> Iterator[TestClient]:
    store = FakeAccountStore()
    store.create(
        _MASTER[0],
        department="관리",
        access_level=int(AccessLevel.마스터),
        password_hash=hash_password(_MASTER[1]),
    )
    store.create(
        _MEMBER[0],
        department="영업",
        access_level=int(AccessLevel.멤버),
        password_hash=hash_password(_MEMBER[1]),
    )
    codec = JwtCodec("test-secret")
    # 추출 서비스: 인메모리 repo + 실 라인 추출 + 카나리 LLM 추출기.
    repo = FakeExtractionRepository()
    extractor = LlmFieldExtractor(_CannedLLM())  # type: ignore[arg-type]
    registry = LineProviderRegistry()

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: AuthService(
        JwtIdentityProvider(codec), store, codec=codec
    )
    app.dependency_overrides[get_account_service] = lambda: AccountService(store)
    app.dependency_overrides[get_extraction_service] = lambda: ExtractionService(
        repo,  # type: ignore[arg-type]
        registry,
        extractor,
        _FakeVlm(),  # type: ignore[arg-type]
    )
    with TestClient(app) as test_client:
        yield test_client


def test_schema_create_no_master_gate_in_demo(client: TestClient) -> None:
    # 데모 모드(ADR-026): 마스터 게이트 무력화(고정 마스터) — 인증 없이 스키마 생성 201.
    body = {"name": "계약서", "fields": [{"key": "amount", "type": "money"}]}
    created = client.post("/v1/extract/schemas", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["id"] is not None


def test_schema_update_overwrites_in_demo(client: TestClient) -> None:
    # 데모 모드(ADR-026): 인증 없이 업데이트(덮어쓰기·id 보존·404)만 검증한다.
    created = client.post(
        "/v1/extract/schemas",
        json={"name": "계약서", "fields": [{"key": "amount", "type": "money"}]},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]

    updated_body = {"name": "계약서v2", "doc_type": "contract", "fields": [{"key": "date", "type": "date"}]}

    # 마스터 게이트 무력화 → 200 + 덮어쓰기 + id 보존.
    ok = client.put(f"/v1/extract/schemas/{sid}", json=updated_body)
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["id"] == sid
    assert payload["name"] == "계약서v2"
    assert [f["key"] for f in payload["fields"]] == ["date"]

    # 없는 id 는 404.
    missing = client.put("/v1/extract/schemas/999999", json=updated_body)
    assert missing.status_code == 404


def test_run_extraction_returns_value_and_bbox(client: TestClient) -> None:
    schema = client.post(
        "/v1/extract/schemas",
        json={"name": "s", "fields": [{"key": "amount", "type": "money"}]},
    ).json()

    pdf = _make_pdf_bytes()
    resp = client.post(
        "/v1/extract/run",
        data={"schema_id": str(schema["id"])},
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    field = body["result"]["fields"][0]
    assert field["key"] == "amount"
    assert field["value"] == "1,200,000,000"
    assert field["page"] == 1
    assert field["bbox"] is not None
    assert field["needs_review"] is False
    # 근거 PNG 미리보기(페이지 1)가 생성됨.
    assert "1" in body["evidence_previews"]


def test_run_extraction_on_image_scan(client: TestClient) -> None:
    # 스캔 이미지(PNG)도 추출 가능 — VLM 경로(손글씨/스캔)로 값 반환.
    schema = client.post(
        "/v1/extract/schemas",
        json={"name": "s", "fields": [{"key": "amount"}]},
    ).json()
    resp = client.post(
        "/v1/extract/run",
        data={"schema_id": str(schema["id"]), "vlm": "true"},
        files={"file": ("scan.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 200, resp.text
    field = resp.json()["result"]["fields"][0]
    assert field["value"] == "9,999"
    assert field["source"] == "handwriting"


def test_run_extraction_unsupported_format_422(client: TestClient) -> None:
    schema = client.post(
        "/v1/extract/schemas",
        json={"name": "s", "fields": [{"key": "amount"}]},
    ).json()
    # 스키마는 존재 → 파일 형식(txt) 검증에서 422.
    resp = client.post(
        "/v1/extract/run",
        data={"schema_id": str(schema["id"])},
        files={"file": ("doc.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422


def test_results_history(client: TestClient) -> None:
    schema = client.post(
        "/v1/extract/schemas",
        json={"name": "s", "fields": [{"key": "amount"}]},
    ).json()
    pdf = _make_pdf_bytes()
    run = client.post(
        "/v1/extract/run",
        data={"schema_id": str(schema["id"])},
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    ).json()
    doc_id = run["result"]["doc_id"]
    hist = client.get(f"/v1/extract/results/{doc_id}")
    assert hist.status_code == 200
    assert len(hist.json()) == 1
