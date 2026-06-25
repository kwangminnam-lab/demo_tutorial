"""OCR 성능 평가(eval) — 순수 로직(정답 파싱·CER/WER·IoU·faithfulness).

네트워크/VLM/MLflow 같은 외부 의존은 진입점(`scripts/eval_ocr.py`)에 두고, 여기엔
결정론적 순수 함수만 둔다(단위 테스트로 잠금 — CLAUDE.md: 외부 의존은 경계 격리).
"""
