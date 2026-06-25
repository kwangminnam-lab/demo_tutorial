"""비동기 적재 API 통합 테스트 — 202 접수 + 백그라운드 처리 + 상태 폴링.

TestClient는 BackgroundTasks를 요청 내에서 동기 실행하므로, 업로드 직후 상태
조회가 결정론적으로 종료 상태(done/error)를 본다 — 실 서버의 비동기 흐름을
유실 없이 검증한다(인메모리 더블, 서버 불요).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from kms.api.app import create_app
from kms.api.deps import (
    get_auth_service,
    get_ingest_job_store,
    get_ingestion_service,
)
from kms.services.auth_service import AuthService, JwtIdentityProvider
from kms.services.ingest_jobs import IngestJobStore
from kms.services.ingestion_service import IngestItem, IngestReport
from tests._auth_tokens import TEST_CODEC, issue_token
from tests.integration._fake_accounts import FakeAccountRepository

EMP_TOKEN = issue_token("emp@corp.com")


class _FakeIngestionService:
    """ingest_item만 흉내내는 더블 — 실 추출/임베딩 없이 호출과 결과를 통제한다."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[IngestItem] = []

    def ingest_item(self, item: IngestItem) -> IngestReport:
        self.calls.append(item)
        if self.fail:
            raise RuntimeError("boom")  # 비밀이 섞일 수 있는 원문 — 응답엔 안 나와야 함
        return IngestReport(
            file_path=item.file_path, ok=True, doc_id="doc-1", chunk_count=4
        )


def _build(service: _FakeIngestionService) -> tuple[TestClient, IngestJobStore]:
    accounts = FakeAccountRepository()
    accounts.upsert(email="emp@corp.com", department="영업부", access_level=1)
    auth_service = AuthService(
        JwtIdentityProvider(TEST_CODEC), accounts, codec=TEST_CODEC
    )
    store = IngestJobStore()
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_ingestion_service] = lambda: service
    app.dependency_overrides[get_ingest_job_store] = lambda: store
    return TestClient(app), store


@pytest.fixture
def client() -> Iterator[TestClient]:
    c, _ = _build(_FakeIngestionService())
    with c:
        yield c


def _upload(client: TestClient, token: str | None) -> Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/v1/ingest/upload",
        headers=headers,
        files={"file": ("note.txt", b"hello world", "text/plain")},
        data={"source": "slack"},
    )


def test_upload_no_auth_required_in_demo(client: TestClient) -> None:
    # 데모 모드(ADR-026): 인증 비활성 — 토큰 없이도 업로드가 접수된다(202).
    resp = _upload(client, None)
    assert resp.status_code == 202


def test_upload_accepted_then_status_done() -> None:
    service = _FakeIngestionService()
    c, _ = _build(service)
    with c as client:
        resp = _upload(client, EMP_TOKEN)
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] in {"pending", "running", "done"}
        job_id = body["job_id"]
        assert body["filename"] == "note.txt"

        # TestClient가 BackgroundTask를 동기 실행 → 상태는 이미 done.
        status_resp = client.get(f"/v1/ingest/jobs/{job_id}", headers={
            "Authorization": f"Bearer {EMP_TOKEN}"
        })
        assert status_resp.status_code == 200
        sbody = status_resp.json()
        assert sbody["status"] == "done"
        assert sbody["report"]["ok"] is True
        assert sbody["report"]["doc_id"] == "doc-1"
        assert sbody["error"] is None
    assert len(service.calls) == 1  # 백그라운드에서 실제 적재가 1회 일어났다


def test_upload_failure_reported_as_error_without_leaking_detail() -> None:
    service = _FakeIngestionService(fail=True)
    c, _ = _build(service)
    with c as client:
        resp = _upload(client, EMP_TOKEN)
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]
        status_resp = client.get(f"/v1/ingest/jobs/{job_id}", headers={
            "Authorization": f"Bearer {EMP_TOKEN}"
        })
        sbody = status_resp.json()
        assert sbody["status"] == "error"
        # 일반 메시지만 — 원문("boom")이 새지 않는다.
        assert "boom" not in (sbody["error"] or "")
        assert sbody["report"] is None


def test_unknown_job_returns_404(client: TestClient) -> None:
    resp = client.get(
        "/v1/ingest/jobs/nonexistent",
        headers={"Authorization": f"Bearer {EMP_TOKEN}"},
    )
    assert resp.status_code == 404
