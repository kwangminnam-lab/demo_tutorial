"""OCR 평가 순수 로직 — 정답(단어+박스) 파싱, 읽기순 전사, CER/WER, IoU, faithfulness.

보유 라벨은 **단어 단위 OCR 정답**(폴리곤+text)이라 key-value 필드 라벨이 아니다.
그래서 이 모듈은 라벨로 즉시 계산 가능한 두 축을 제공한다:

- Track 1 (OCR 읽기): 정답 단어를 읽기순으로 이어 **정답 전문**을 만들고, VLM 전사와
  CER/WER(문자/단어 오류율)로 비교한다.
- Track 2 (추출 faithfulness): 추출한 필드 값이 정답 페이지 텍스트에 실재하는지
  (환각 아님)와, 추출 bbox가 정답 단어 박스와 겹치는지(IoU)로 본다.

좌표는 모두 axis-aligned `(x0,y0,x1,y1)`. IoU는 호출자가 [0,1] 정규화해 넘겨 스케일을
맞춘다(정답=라벨 px, 추출=페이지 pt → 서로 다른 단위라 정규화 필수).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class LabelWord:
    """정답 단어 1개 — 텍스트 + axis-aligned 박스(라벨 픽셀 좌표)."""

    text: str
    bbox: BBox


@dataclass(frozen=True)
class LabelDoc:
    """한 문서의 OCR 정답 — 단어 목록 + 이미지 크기 + 메타."""

    identifier: str
    width: float
    height: float
    document_name: str | None
    words: list[LabelWord]

    def full_text(self) -> str:
        """정답 단어를 읽기순으로 이어 붙인 전문(전사 비교 기준)."""
        return reading_order_text(self.words)

    def normalized_boxes(self) -> list[BBox]:
        """단어 박스를 이미지 크기로 [0,1] 정규화(IoU 비교용)."""
        if self.width <= 0 or self.height <= 0:
            return [w.bbox for w in self.words]
        return [
            (
                w.bbox[0] / self.width,
                w.bbox[1] / self.height,
                w.bbox[2] / self.width,
                w.bbox[3] / self.height,
            )
            for w in self.words
        ]


def parse_label(data: dict[str, Any]) -> LabelDoc:
    """어노테이션 JSON(dict) → LabelDoc. 폴리곤 quad를 axis-aligned 박스로 환산한다."""
    images = data.get("images") or [{}]
    img = images[0] if isinstance(images[0], dict) else {}
    width = float(img.get("width") or 0)
    height = float(img.get("height") or 0)
    document_name = img.get("document_name")
    words: list[LabelWord] = []
    for ann in data.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        for poly in ann.get("polygons") or []:
            if not isinstance(poly, dict):
                continue
            text = poly.get("text")
            points = poly.get("points")
            if not text or not isinstance(points, list) or not points:
                continue
            xs = [float(p[0]) for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]
            ys = [float(p[1]) for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]
            if not xs or not ys:
                continue
            words.append(
                LabelWord(str(text), (min(xs), min(ys), max(xs), max(ys)))
            )
    return LabelDoc(
        identifier=str(data.get("Identifier") or ""),
        width=width,
        height=height,
        document_name=document_name,
        words=words,
    )


def reading_order_text(words: list[LabelWord], *, line_tol_ratio: float = 0.6) -> str:
    """단어를 읽기순(위→아래 행, 행 내 좌→우)으로 정렬해 전문 문자열로.

    행 클러스터링: 단어 y중심이 직전 행 중심에서 (중앙값 높이 × `line_tol_ratio`)
    이내면 같은 행으로 묶는다(스캔 기울기·줄간격 변동 흡수). 행은 줄바꿈, 단어는 공백.
    """
    if not words:
        return ""
    heights = sorted(w.bbox[3] - w.bbox[1] for w in words)
    med_h = heights[len(heights) // 2] or 1.0
    tol = med_h * line_tol_ratio
    ordered = sorted(words, key=lambda w: (w.bbox[1] + w.bbox[3]) / 2)
    lines: list[list[LabelWord]] = []
    cur: list[LabelWord] = []
    cur_y: float | None = None
    for w in ordered:
        yc = (w.bbox[1] + w.bbox[3]) / 2
        if cur_y is None or abs(yc - cur_y) <= tol:
            cur.append(w)
            cur_y = yc if cur_y is None else (cur_y * (len(cur) - 1) + yc) / len(cur)
        else:
            lines.append(cur)
            cur = [w]
            cur_y = yc
    if cur:
        lines.append(cur)
    rows = [
        " ".join(w.text for w in sorted(ln, key=lambda w: w.bbox[0])) for ln in lines
    ]
    return "\n".join(rows)


def _levenshtein(a: list[Any] | str, b: list[Any] | str) -> int:
    """편집 거리(문자열=문자단위, 리스트=토큰단위). O(len(a)·len(b))."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i]
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[lb]


_WS_RE = re.compile(r"\s+")


def _strip_ws(text: str) -> str:
    return _WS_RE.sub("", text)


def cer(ref: str, hyp: str) -> float:
    """문자 오류율 — 공백 제거 후 문자 편집거리 / 정답 문자 수. 정답 빈 문자열 방어."""
    ref_c = _strip_ws(ref)
    hyp_c = _strip_ws(hyp)
    if not ref_c:
        return 0.0 if not hyp_c else 1.0
    return _levenshtein(ref_c, hyp_c) / len(ref_c)


def wer(ref: str, hyp: str) -> float:
    """단어 오류율 — 공백 토큰 편집거리 / 정답 단어 수. 정답 빈 문자열 방어."""
    ref_w = ref.split()
    hyp_w = hyp.split()
    if not ref_w:
        return 0.0 if not hyp_w else 1.0
    return _levenshtein(ref_w, hyp_w) / len(ref_w)


def _norm(text: str) -> str:
    """매칭용 정규화 — 공백 제거 + 소문자."""
    return _strip_ws(text).lower()


def value_in_text(value: str, gt_text: str) -> bool:
    """추출 값이 정답 텍스트에 (공백무시) 부분 문자열로 실재하는가(환각 판별)."""
    needle = _norm(value)
    return bool(needle) and needle in _norm(gt_text)


def faithfulness(values: list[str | None], gt_text: str) -> tuple[float, int]:
    """추출 값들 중 정답 텍스트에 실재하는 비율 + 평가 대상(비어있지 않은 값) 수.

    값이 정답 페이지 텍스트에 없으면 환각(또는 오독)으로 본다. 필드 정답 라벨이
    없어도 "진짜 페이지 글자를 읽었나"를 잰다.
    """
    present = [v for v in values if v]
    if not present:
        return 0.0, 0
    hit = sum(1 for v in present if value_in_text(v, gt_text))
    return hit / len(present), len(present)


def iou(box_a: BBox, box_b: BBox) -> float:
    """두 axis-aligned 박스의 IoU(0~1). 같은 좌표계(정규화)여야 의미 있음."""
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    denom = area_a + area_b - inter
    return inter / denom if denom > 0 else 0.0


def max_iou(box: BBox, gt_boxes: list[BBox]) -> float:
    """박스 1개와 정답 박스들 중 최대 IoU(겹치는 단어가 없으면 0)."""
    return max((iou(box, g) for g in gt_boxes), default=0.0)


def grounding(
    pred_boxes: list[BBox], gt_boxes: list[BBox], *, iou_threshold: float = 0.3
) -> tuple[float, float]:
    """추출 박스들의 (평균 최대IoU, grounded 비율). grounded = max IoU ≥ 임계값.

    필드 위치가 정답 단어 어딘가와 겹치는지를 본다(필드 정답 라벨 불요).
    """
    if not pred_boxes:
        return 0.0, 0.0
    maxes = [max_iou(b, gt_boxes) for b in pred_boxes]
    grounded = sum(1 for m in maxes if m >= iou_threshold)
    return sum(maxes) / len(maxes), grounded / len(maxes)
