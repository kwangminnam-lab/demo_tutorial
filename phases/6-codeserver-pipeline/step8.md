# Step 8: helm-pvc

## 읽어야 할 파일

먼저 아래를 읽고 헬름 차트 구조와 code-server PVC 패턴을 파악하라:

- `docs/ARCHITECTURE.md`, `docs/INFRA.md`, `docs/ADR.md`
- `helm/docux-api/values.yaml` (image, config(ConfigMap), secret, resources, gpu, httpRoute)
- `helm/docux-api/templates/deployment.yaml` (envFrom: configMapRef docux-config + secretRef; volumeMounts 유무)
- `helm/docux-api/templates/configmap.yaml` (`range .Values.config`)
- `helm/docux-api/Chart.yaml` (version/appVersion)
- `../example/pvc.yaml` (RWX 예시: `accessModes: [ReadWriteMany]`, `storageClassName: local-path-shared`)
- `../reference/tutorials/wind-power-prediction-with-xgboost/README.md` (PVC 마운트·`~/workspace` = `/home/coder/workspace` 패턴)
- 백엔드 step 0/5 결과(`settings.export_root`, env `DOCUX_EXPORT_ROOT`)

## 의존 / 노출

- **의존**: step 0 `export_root`(env `DOCUX_EXPORT_ROOT`), step 5 export 쓰기 로직.
- **노출**: docux-api 파드에 **RWX 공유 PVC**(code-server workspace와 공유)를 마운트하고, `DOCUX_EXPORT_ROOT`를 그 마운트 경로로 배선한 헬름 차트.

## 작업

F3·F5 배포: 산출물이 code-server 노드 폴더(공유 PVC)에 보이도록 docux-api가 같은 RWX PVC를 마운트한다. 코드는 step 0/5에서 경로 설정·쓰기를 완료 — 이 step은 **헬름 배선만**.

1. `helm/docux-api/values.yaml`에 export PVC 설정 블록 추가(주석으로 의도·기본값 설명):
   ```yaml
   # 산출물(.jsonl) 내보내기 공유 볼륨 — code-server workspace PVC 공유(RWX).
   # code-server가 같은 PVC를 ~/workspace 에 마운트하면 docux 산출물이 IDE에 즉시 보인다.
   export:
     enabled: false                 # true면 PVC를 마운트하고 DOCUX_EXPORT_ROOT 주입.
     existingClaim: ""              # 기존 code-server PVC 이름(권장). 비우고 create=true면 차트가 생성.
     create: false                  # true면 아래 스펙으로 RWX PVC 생성(보통 code-server가 이미 소유 → false).
     storageClassName: local-path-shared
     size: 10Gi
     mountPath: /home/coder/workspace/docux-export   # 컨테이너 내 마운트 경로 = export_root
   ```
2. `helm/docux-api/templates/deployment.yaml`: `export.enabled`일 때
   - `volumes`에 PVC(`persistentVolumeClaim.claimName` = existingClaim 또는 생성한 이름),
   - 컨테이너 `volumeMounts`에 `mountPath: {{ .Values.export.mountPath }}`,
   - env `DOCUX_EXPORT_ROOT` = `mountPath`를 주입(configMap에 넣거나 deployment env로 직접).
   기존 `envFrom`(docux-config/docux-secret) 구조를 깨지 마라 — 추가만.
3. `export.create`가 true면 `helm/docux-api/templates/pvc-export.yaml`(신규) 템플릿으로 RWX PVC 생성(`accessModes: [ReadWriteMany]`, `storageClassName`, `size`). `../example/pvc.yaml` 형식 참고. 기본은 create=false(code-server가 이미 PVC 소유, 공유만).
4. `values.yaml` `config`에 `DOCUX_DATA_ROOT`/`DOCUX_EXPORT_ROOT` 관련 주석을 정리(step 0 env명과 일치). export.enabled=false면 코드 기본값(`data`,`data/export`)으로 동작 — 무회귀.
5. `Chart.yaml`: version·appVersion bump(SemVer patch/minor — 기존 컨벤션 따라). `helm lint helm/docux-api` 통과, `helm template`로 export.enabled=true 시 volume/volumeMount/env가 렌더되는지 확인.

핵심 규칙:
- 시크릿 금지: PVC·경로는 비민감. values에 토큰/비번 넣지 마라.
- 하위 호환: `export.enabled` 기본 false → 기존 배포 무영향(opt-in). breaking 아님.
- 조용한 실패 금지: RWX 미지원 환경 위험을 values 주석에 명시(`local-path-shared`가 RWX 지원해야 함; code-server와 동일 노드/스토리지클래스 전제).

## Acceptance Criteria

```bash
cd da_h
helm lint helm/docux-api
helm template t helm/docux-api --set export.enabled=true --set export.existingClaim=ws-pvc \
  | grep -E "DOCUX_EXPORT_ROOT|/home/coder/workspace/docux-export|persistentVolumeClaim|claimName: ws-pvc"
helm template t helm/docux-api | grep -c "persistentVolumeClaim" || true   # 기본(off)은 0이어야
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트:
   - `export.enabled=true`: deployment에 volume + volumeMount(mountPath) + env `DOCUX_EXPORT_ROOT`(=mountPath)가 렌더.
   - `export.enabled=false`(기본): PVC/볼륨 미렌더 — 기존 배포 무영향.
   - `export.create=true`: RWX PVC 템플릿 렌더(accessModes ReadWriteMany).
   - `helm lint` 통과.
   - 시크릿이 values/템플릿에 없는가?
3. 결과를 `phases/6-codeserver-pipeline/step8.result.json`에 기록:
   - 성공 → `{"status": "completed", "summary": "helm/docux-api: values.export(enabled/existingClaim/create/storageClassName/size/mountPath). deployment.yaml export.enabled시 volume+volumeMount+DOCUX_EXPORT_ROOT 주입. pvc-export.yaml(create시 RWX). Chart.yaml bump. helm lint/template 검증. 기본 off=무회귀."}`
   - 실패/차단 → 해당 스키마.

## 금지사항

- `export.enabled` 기본값을 true로 두지 마라. 이유: RWX PVC 없는 환경에서 배포가 막힌다(opt-in이어야 안전).
- 기존 `envFrom`(docux-config/docux-secret) 배선을 바꾸지 마라. 이유: 전체 env 주입 회귀.
- 차트를 패키지(.tgz)하거나 레지스트리에 push하지 마라. 이유: 범위 밖(자격증명 필요 — 사용자가 수행).
- 시크릿을 values/템플릿/커밋에 넣지 마라.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 결과는 `step8.result.json`에만.
