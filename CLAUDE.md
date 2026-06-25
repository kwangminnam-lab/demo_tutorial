# 프로젝트: {프로젝트명}

소프트웨어 개발 프로젝트. 정확성·유지보수성·테스트 가능성을 속도·편의보다 우선한다.

## 기술 스택
- 주 언어: Python {예: 3.12+}. 필요 시 다른 언어({예: TypeScript/Node, Go}) 혼용 가능.
- 패키지/의존성: {예: uv / poetry / pip-tools (Python), pnpm (Node), go mod (Go)} — lock 파일로 고정.
- 검증: {예: pytest, ruff, mypy (Python) / vitest, eslint, tsc (Node) / go test, golangci-lint (Go)}
- 런타임/프레임워크: {예: FastAPI / Django / CLI / 라이브러리}
- 데이터 저장소: {예: PostgreSQL, SQLite, 초}
- 설정 관리: {예: 환경 변수 + `.env`(gitignore), `config/` 디렉토리}

## 아키텍처 규칙

- **CRITICAL: 시크릿을 코드·git에 넣지 않는다.** API 키·토큰·비밀번호·인증서는 환경 변수 또는 시크릿 매니저로. `.env`·`credentials*`·`*.pem`·`*.key`는 `.gitignore` 처리하고 절대 커밋하지 않는다.
- **CRITICAL: 공개 인터페이스의 하위 호환을 깨지 않는다.** 공개 API·CLI 플래그·DB 스키마의 breaking change는 ADR에 기록하고 마이그레이션 경로를 함께 제시한다.
- **CRITICAL: 로직 변경에는 테스트가 따른다.** 동작을 바꾸는 변경은 그 동작을 잠그는 테스트를 동반한다. 버그 수정은 회귀 테스트를 먼저 추가한다.
- **CRITICAL: 조용한 실패 금지.** 에러를 삼키지 마라(`except: pass` 금지). 복구 못 하는 에러는 전파하고, 경계에서 로깅·핸들링한다. 빈 catch는 이유를 주석으로 남긴다.
- **레이어 분리.** 재사용 로직은 `src/`에, 진입점(CLI·서버·잡)은 `scripts/` 또는 `src/<pkg>/__main__.py`에, 테스트는 `tests/`에. 진입점에 비즈니스 로직을 두지 마라.
- **마이그레이션은 전진 전용·되돌릴 수 있게.** DB·데이터 스키마 변경은 버전 관리되는 마이그레이션 파일로. 적용된 마이그레이션을 편집하지 말고 새 마이그레이션을 추가한다.
- **의존성은 lock 파일로.** 직접 글로벌 설치 금지. 의존성 추가 시 lock 파일을 갱신해 커밋한다.
- **큰/생성 산출물은 Git에 넣지 않는다.** 빌드 출력(`dist/`, `build/`, `target/`), `node_modules/`, 가상환경, 캐시, 로그는 `.gitignore` 처리.

## 개발 프로세스

- **테스트 대상은 결정론적 동작이다.** 순수 함수·비즈니스 규칙·경계 조건을 단위 테스트로 잠근다. 외부 의존(네트워크·DB·시계)은 경계에서 격리하고 통합 테스트로 따로 검증한다.
- **설계 → 구현 → 테스트 → 리팩터링** 순서. 인터페이스(시그니처·계약)를 먼저 정하고, 작은 단위로 구현하며, 각 단계에서 게이트(lint·type·test)를 통과시킨다.
- **변경은 작게, 한 번에 하나.** 한 커밋/PR은 하나의 논리적 변경만. 무관한 리팩터링을 섞지 마라.
- **커밋 메시지는 conventional commits** (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `perf:`).

## 명령어

```bash
# 환경 (Python 기준; 타 언어는 해당 도구로 대체)
uv sync                            # 또는 poetry install / pip install -r requirements.txt

# 코드 품질
ruff check .                       # 린트
ruff format .                      # 포맷
mypy src                           # 타입

# 테스트
pytest -q                          # 전체
pytest -q tests/test_<module>.py   # 단일 파일
pytest -q -k <expr>                # 패턴 매칭

# 실행 (Makefile 진입점 예시)
make run                           # 앱/서버 실행
make build                         # 빌드
make migrate                       # DB 마이그레이션 적용
```
