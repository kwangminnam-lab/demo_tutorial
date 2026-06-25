# DocuX 앱 배포 (백엔드 API + 프론트엔드)

임베딩 서버와 같은 패턴 — 각각 독립 Helm 차트로 패키징해 **Gitea Helm 레지스트리**에
push하고, 플랫폼 "Helm repository URL"로 가져와 앱 배포한다.

토폴로지: PG·Neo4j·liteLLM·임베딩은 이미 따로 떠 있고, 여기선 **API와 프론트만** 올린다.

```
da_h/helm/
├── docux-api/        # 백엔드 FastAPI 차트 (Deployment+Service+ConfigMap, alembic migrate Job)
└── docux-frontend/   # nginx 정적 SPA 차트 (Deployment+Service)
```

연결 그림:
```
브라우저 → docux-frontend(nginx:8080) ──/v1 프록시(BACKEND_URL)──> docux-api:8000
                                                                      │
                              외부 앱 ── PG · Neo4j · liteLLM · docux-embed
```
프론트가 nginx로 `/v1`을 백엔드에 프록시 → **동일 오리진 → CORS 불요.**

---

## A. 이미지 빌드 & push (2개)

```bash
# 백엔드 (컨텍스트 = da_h/)
cd da_h
docker build -t gitea.try.mrxrunway.ai/platform/demo/docux-api:1.0 .
docker push gitea.try.mrxrunway.ai/platform/demo/docux-api:1.0

# 프론트 (컨텍스트 = da_h/frontend/)
docker build -t gitea.try.mrxrunway.ai/platform/demo/docux-frontend:1.0 frontend/
docker push gitea.try.mrxrunway.ai/platform/demo/docux-frontend:1.0
```

## B. 시크릿 생성 (git 금지 — kubectl로 직접)

백엔드가 envFrom으로 읽는 민감값. **배포 namespace에** 미리 만든다:
```bash
kubectl -n <namespace> create secret generic docux-secret \
  --from-literal=DATABASE_URL='postgresql+psycopg://USER:PW@postgresql-db.<ns>.svc.cluster.local:5432/DB' \
  --from-literal=NEO4J_PASSWORD='...' \
  --from-literal=JWT_SECRET='...' \
  --from-literal=GEMINI_API_KEY='...' \
  --from-literal=LLM_API_KEY='sk-...' \
  --from-literal=EMBED_API_KEY='<플랫폼 API키>'
```
- `LLM_API_KEY` = LLM 게이트웨이 전용 모델 호출 키.
- `EMBED_API_KEY` = 플랫폼 API키(Inference Endpoint·MLflow·Airflow 공용) — 임베딩 인증.
- 두 키 모두 백엔드가 `Authorization: Bearer <키>` 로 전달한다.

## C. 차트 패키징 + Gitea Helm 레지스트리 push (단일 차트)

API와 프론트는 **단일 차트 `docux`** 로 한 번에 배포한다(`api`·`frontend` 두 섹션).
```bash
cd da_h/helm
helm package docux
curl --user platform:<토큰> -X POST --upload-file docux-0.1.0.tgz \
  https://gitea.try.mrxrunway.ai/api/packages/platform/helm/api/charts
```

## D. 플랫폼 UI 입력 (앱 1개)

| 칸 | 값 |
|---|---|
| Helm repository URL | `https://gitea.try.mrxrunway.ai/api/packages/platform/helm` |
| Chart name | `docux` |
| Chart version | `0.1.0` |

### values 덮어쓰기 (UI values 칸)

이미지 태그 + 프론트 외부 호스트만 맞추면 된다(엔드포인트·키는 values/secret에 이미 반영):
```yaml
api:
  image: { repository: gitea.try.mrxrunway.ai/platform/demo/docux-api, tag: "1.0" }
  secret: { existingSecret: docux-secret }
frontend:
  image: { repository: gitea.try.mrxrunway.ai/platform/demo/docux-frontend, tag: "1.0" }
  httpRoute: { enabled: true, hostname: "docux.try.mrxrunway.ai" }
```

## E. 배포 후 확인

```bash
kubectl -n <ns> get pods | grep -E "docux-api|docux-frontend"
kubectl -n <ns> port-forward svc/docux-api 8000:8000   # curl localhost:8000/healthz
kubectl -n <ns> port-forward svc/docux-frontend 8080:8080  # 브라우저로 SPA 확인
```
외부 접속은 플랫폼 ingress/route로 `docux-frontend:8080` 노출.

---

## 주의

- **마이그레이션 Job**: API 차트는 기동 전 `alembic upgrade head` Job을 돌린다(스키마 생성).
  PG에 `CREATE EXTENSION vector` 권한이 미리 있어야 pgvector 테이블이 생성된다.
- **데이터 재적재**: 스키마만 만들어지고 문서는 비어있다 — 별도 적재 단계 필요(다음 작업).
- **이미지 레지스트리 인증**: 노드가 gitea 레지스트리에서 pull하려면 pull secret 필요할 수 있다
  → `imagePullSecrets: [{ name: <reg-cred> }]` values에 추가.
