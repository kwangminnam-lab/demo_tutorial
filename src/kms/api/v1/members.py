"""멤버 관리 라우트 (/v1/members) — **마스터 전용** (ADR-017).

마스터가 멤버 계정을 추가(이메일+비밀번호)·조회·삭제한다. `require_master`로 멤버
요청은 403, 미인증은 401. 비밀번호는 요청 본문으로만 받고 응답·로그에 싣지 않으며,
서비스가 bcrypt 해시로만 저장한다. 도메인 거부(중복·약한 비번·마스터 삭제)는 400.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from kms.api.deps import get_account_service, require_master
from kms.domain.errors import MemberValidationError
from kms.domain.models import UserContext
from kms.services.account_service import AccountService, MemberView

router = APIRouter(prefix="/v1", tags=["members"])


class AddMemberRequest(BaseModel):
    """멤버 추가 요청 — 마스터가 생성하는 이메일 아이디 + 비밀번호 + 부서."""

    email: str
    password: str
    department: str = "default"


@router.get("/members", response_model=list[MemberView])
def list_members(
    _master: UserContext = Depends(require_master),
    service: AccountService = Depends(get_account_service),
) -> list[MemberView]:
    """모든 계정 목록(마스터 포함, 비밀번호 해시 제외)을 반환한다."""
    return service.list_members()


@router.post("/members", response_model=MemberView, status_code=status.HTTP_201_CREATED)
def add_member(
    request: AddMemberRequest,
    _master: UserContext = Depends(require_master),
    service: AccountService = Depends(get_account_service),
) -> MemberView:
    """멤버 계정을 추가한다(access=멤버). 형식·중복·약한 비번은 400."""
    try:
        return service.add_member(request.email, request.password, request.department)
    except MemberValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/members/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_member(
    account_id: int,
    _master: UserContext = Depends(require_master),
    service: AccountService = Depends(get_account_service),
) -> None:
    """멤버 계정을 삭제한다. 미존재·마스터 삭제 시도는 400."""
    try:
        service.delete_member(account_id)
    except MemberValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
