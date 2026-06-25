"""필드추출 어댑터 — 좌표 있는 라인 + 스키마 → 추출 필드(LLM 구조화 출력).

LLM은 텍스트(번호 매긴 라인)만 보고 `{key, value, evidence_line_ids, confidence}`를
반환한다. 좌표(bbox)는 라인에서 역참조하므로 LLM 비전이 불필요하다 — 인쇄/디지털
문서는 gpt-oss 같은 텍스트 LLM으로 충분하다(ADR-024 phase 1).
"""
