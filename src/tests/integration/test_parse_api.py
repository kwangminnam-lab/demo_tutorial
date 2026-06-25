"""/v1/parse/upload 통합 테스트 — 인증·파싱·HTML/JSON 응답·크기 제한."""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from kms.api.app import create_app
from kms.api.deps import get_auth_service
from kms.domain.access import AccessLevel
from kms.domain.errors import AuthenticationError
from kms.domain.models import UserContext


class _FakeAuthService:
    def authenticate(self, token: str) -> UserContext:
        if token == "valid":
            return UserContext(user_id="u@x", department="기획", access_level=AccessLevel.임직원)
        raise AuthenticationError("invalid token")


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeAuthService()
    return TestClient(app)


def _headers(token: str = "valid") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_parse_upload_returns_html_and_json(client: TestClient) -> None:
    file_data = ("줄1\n줄2".encode("utf-8"))
    response = client.post(
        "/v1/parse/upload",
        headers=_headers(),
        files={"file": ("note.txt", BytesIO(file_data), "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "note.txt"
    assert body["doc_type"] == "TXT"
    assert "줄1" in body["html"]
    # type = 원본 문서 형식(TXT), ir_kind = IR 표현 종류(MarkdownDoc).
    assert body["json_data"]["type"] == "TXT"
    assert body["json_data"]["ir_kind"] == "MarkdownDoc"
    # 본문은 통짜 markdown이 아니라 단락/섹션 단위로 분할돼 내려온다.
    sections = body["json_data"]["sections"]
    assert isinstance(sections, list) and len(sections) >= 1
    assert any("줄1" in s["text"] for s in sections)
    assert "markdown" not in body["json_data"]  # 통짜 markdown 필드 제거


def test_parse_upload_returns_html_for_pptx_like_ir(client: TestClient) -> None:
    """Markdown 헤더가 살아있는지(HTML <h2>) 확인."""
    file_data = "# 큰 제목\n## 작은 제목\n본문 단락".encode("utf-8")
    response = client.post(
        "/v1/parse/upload",
        headers=_headers(),
        files={"file": ("note.md", BytesIO(file_data), "text/markdown")},
    )

    assert response.status_code == 200
    html = response.json()["html"]
    assert "<h1>큰 제목</h1>" in html
    assert "<h2>작은 제목</h2>" in html
    assert "본문 단락" in html


def test_parse_upload_no_auth_required_in_demo(client: TestClient) -> None:
    # 데모 모드(ADR-026): 인증 비활성 — 토큰 없이도 파싱된다.
    response = client.post(
        "/v1/parse/upload",
        files={"file": ("a.txt", BytesIO(b"x"), "text/plain")},
    )
    assert response.status_code == 200


def test_parse_upload_rejects_unsupported_format(client: TestClient) -> None:
    # 이미지(PNG/JPG)는 이제 지원(ADR-025) — 실제 미지원 확장자로 422를 검증한다.
    response = client.post(
        "/v1/parse/upload",
        headers=_headers(),
        files={"file": ("a.exe", BytesIO(b"MZ\x90\x00"), "application/octet-stream")},
    )
    assert response.status_code == 422


def test_parse_upload_includes_image_blobs_field_in_json(client: TestClient) -> None:
    response = client.post(
        "/v1/parse/upload",
        headers=_headers(),
        files={"file": ("a.txt", BytesIO(b"hello"), "text/plain")},
    )
    assert response.status_code == 200
    assert "image_blobs" in response.json()["json_data"]
