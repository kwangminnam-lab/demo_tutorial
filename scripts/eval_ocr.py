"""AI OCR(Qwen3-VL) 성능 평가 진입점 — 보유 OCR 정답(단어+박스)으로 두 축 측정.

Track 1 (OCR 읽기): 이미지를 VLM에 풀 전사시켜 정답 전문과 CER/WER 비교.
Track 2 (추출 faithfulness): 우리 필드추출을 돌려 추출 값이 정답 텍스트에 실재하는지
         (환각 아님) + 추출 bbox가 정답 단어 박스와 겹치는지(IoU) 측정.

순수 계산은 `kms.eval.ocr_eval`에 있고, 여기선 I/O·VLM 호출·MLflow 기록만 한다
(CLAUDE.md: 진입점에 비즈니스 로직 금지). 결과는 콘솔 + (설정 시) MLflow run 1건.

사용:
    VLM_BASE_URL=http://qwen3-vl-4b:8000/v1 VLM_MODEL=Qwen3-VL-4B-Instruct \\
    MLFLOW_ENABLED=true MLFLOW_TRACKING_URI=https://mlflow.try.mrxrunway.ai \\
    MLFLOW_EXPERIMENT=001-doxdemo.qwen3-4b-vlm \\
    python scripts/eval_ocr.py \\
        --images-dir samlple_data/images --labels-dir samlple_data/annotations_label \\
        --limit 20
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import httpx

# 메타 도구 — 레포에서 바로 실행 가능하도록 src를 경로에 둔다(설치 불요).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kms.adapters.extraction.qwen_vl import QwenVlmExtractor  # noqa: E402
from kms.adapters.ingestion.image_pdf import open_as_pdf  # noqa: E402
from kms.adapters.observability.mlflow_tracker import MlflowTracker  # noqa: E402
from kms.config.settings import get_settings  # noqa: E402
from kms.domain.extraction import ExtractionSchema, SchemaField  # noqa: E402
from kms.eval.ocr_eval import (  # noqa: E402
    BBox,
    cer,
    faithfulness,
    grounding,
    parse_label,
    wer,
)

# 전사용 system 프롬프트 — 설명·요약 없이 본문 텍스트만(읽기순). Track 1.
_TRANSCRIBE_SYSTEM = (
    "너는 이미지의 모든 텍스트를 그대로 읽어 전사하는 OCR 도구다. "
    "문서에 보이는 글자를 읽기순(위→아래, 좌→우)으로 옮긴다. "
    "설명·요약·해석을 덧붙이지 말고 본문 텍스트만 출력한다. 표는 셀 값을 공백으로 구분해 옮긴다."
)

# 정보보안동의서(보유 데이터 단일 양식) 기본 추출 스키마 — Track 2 faithfulness/IoU용.
# (필드 정답 라벨이 아니라 "추출 값이 진짜 페이지 글자인가"를 보는 용도라 값 정확도와 무관.)
_CONSENT_FIELDS = [
    SchemaField(key="이름", type="String"),
    SchemaField(key="생년월일", type="date"),
    SchemaField(key="연락처", type="String"),
    SchemaField(key="주소", type="String"),
    SchemaField(key="동의일자", type="date"),
    SchemaField(key="서명", type="String"),
]


def _b64_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _transcribe(settings: object, image_path: Path, timeout: float) -> str:
    """VLM에 이미지를 보내 풀 전사 텍스트를 받는다(Track 1). 빈 응답이면 빈 문자열."""
    base_url = settings.vlm_base_url.rstrip("/")  # type: ignore[attr-defined]
    content = [
        {"type": "text", "text": "이 이미지의 모든 텍스트를 전사하라."},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_b64_image(image_path)}"},
        },
    ]
    body = {
        "model": settings.vlm_model,  # type: ignore[attr-defined]
        "messages": [
            {"role": "system", "content": _TRANSCRIBE_SYSTEM},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "max_tokens": 4096,
    }
    headers = {"Content-Type": "application/json"}
    api_key = settings.vlm_api_key  # type: ignore[attr-defined]
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.post(
        f"{base_url}/chat/completions", json=body, headers=headers, timeout=timeout
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"VLM 전사 오류 {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("message", {}).get("content") or "")


def _page_size(image_path: Path) -> tuple[float, float]:
    """이미지를 PDF로 열어 첫 페이지 rect(pt) 크기 — 추출 bbox 정규화 기준."""
    doc = open_as_pdf(image_path)
    try:
        rect = doc[0].rect
        return float(rect.width), float(rect.height)
    finally:
        doc.close()


def _extracted_norm_boxes(
    fields: object, page_w: float, page_h: float
) -> list[BBox]:
    """추출 필드의 bbox를 페이지 크기로 [0,1] 정규화(정답과 같은 좌표계로)."""
    out: list[BBox] = []
    for f in fields:  # type: ignore[attr-defined]
        if f.bbox is None or page_w <= 0 or page_h <= 0:
            continue
        x0, y0, x1, y1 = f.bbox
        out.append((x0 / page_w, y0 / page_h, x1 / page_w, y1 / page_h))
    return out


def _pairs(images_dir: Path, labels_dir: Path, limit: int) -> list[tuple[Path, Path]]:
    """라벨 json ↔ 이미지 jpg를 Identifier 기준으로 매칭(둘 다 있는 것만)."""
    pairs: list[tuple[Path, Path]] = []
    for label in sorted(labels_dir.glob("*.json")):
        image = images_dir / f"{label.stem}.jpg"
        if image.exists():
            pairs.append((image, label))
        if limit and len(pairs) >= limit:
            break
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen3-VL AI OCR 성능 평가(eval).")
    parser.add_argument("--images-dir", required=True, type=Path)
    parser.add_argument("--labels-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=20, help="평가 문서 수 상한(0=전체).")
    parser.add_argument("--iou-threshold", type=float, default=0.3)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--no-mlflow", action="store_true", help="MLflow 기록 생략(콘솔만).")
    parser.add_argument("--run-name", default="ocr-eval")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.vlm_base_url:
        print("VLM_BASE_URL 미설정 — VLM 엔드포인트 필요(전사·추출 둘 다).", file=sys.stderr)
        return 2

    pairs = _pairs(args.images_dir, args.labels_dir, args.limit)
    if not pairs:
        print("평가할 (이미지,라벨) 쌍이 없음 — 경로 확인.", file=sys.stderr)
        return 2

    extractor = QwenVlmExtractor(
        settings.vlm_base_url, settings.vlm_model, api_key=settings.vlm_api_key
    )
    schema = ExtractionSchema(name="정보보안동의서", fields=_CONSENT_FIELDS)

    cers: list[float] = []
    wers: list[float] = []
    faithful_hits = 0
    faithful_total = 0
    iou_sum = 0.0
    iou_count = 0
    grounded_sum = 0.0

    for idx, (image, label) in enumerate(pairs, start=1):
        gt = parse_label(json.loads(label.read_text(encoding="utf-8")))
        gt_text = gt.full_text()

        # Track 1 — 전사 → CER/WER.
        hyp = _transcribe(settings, image, args.timeout)
        doc_cer = cer(gt_text, hyp)
        doc_wer = wer(gt_text, hyp)
        cers.append(doc_cer)
        wers.append(doc_wer)

        # Track 2 — 추출 faithfulness + grounding IoU.
        fields = extractor.extract(image, schema)
        rate, n = faithfulness([f.value for f in fields], gt_text)
        faithful_hits += round(rate * n)
        faithful_total += n
        page_w, page_h = _page_size(image)
        pred_boxes = _extracted_norm_boxes(fields, page_w, page_h)
        mean_iou, grounded_rate = grounding(
            pred_boxes, gt.normalized_boxes(), iou_threshold=args.iou_threshold
        )
        if pred_boxes:
            iou_sum += mean_iou * len(pred_boxes)
            grounded_sum += grounded_rate * len(pred_boxes)
            iou_count += len(pred_boxes)

        print(
            f"[{idx}/{len(pairs)}] {gt.identifier} "
            f"CER={doc_cer:.3f} WER={doc_wer:.3f} "
            f"faithful={rate:.2f}({n}) iou={mean_iou:.3f}"
        )

    n_docs = len(pairs)
    metrics = {
        "cer_mean": sum(cers) / n_docs,
        "wer_mean": sum(wers) / n_docs,
        "faithful_rate": (faithful_hits / faithful_total) if faithful_total else 0.0,
        "grounding_iou_mean": (iou_sum / iou_count) if iou_count else 0.0,
        "grounded_rate": (grounded_sum / iou_count) if iou_count else 0.0,
        "n_docs": float(n_docs),
        "n_fields_eval": float(faithful_total),
    }
    params = {
        "model": settings.vlm_model,
        "n_docs": n_docs,
        "schema": schema.name,
        "iou_threshold": args.iou_threshold,
        "track": "1+2",
    }

    print("\n=== OCR EVAL 요약 ===")
    for key, value in metrics.items():
        print(f"  {key:20s} {value:.4f}")

    if args.no_mlflow:
        print("(MLflow 기록 생략 — --no-mlflow)")
        return 0
    tracker = MlflowTracker.from_settings(settings)
    tracker.log_ocr_run(
        run_name=args.run_name,
        params=params,
        metrics=metrics,
        tags={"kind": "eval", "model": settings.vlm_model},
    )
    print(f"MLflow 기록 시도 완료(experiment={settings.mlflow_experiment}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
