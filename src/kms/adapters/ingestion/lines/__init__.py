"""라인 추출(line provider) — 문서를 텍스트+좌표(`TextLine`) 목록으로 만든다.

필드추출(IDP, ADR-024)의 grounding 입력. 디지털 PDF는 `PymupdfLineProvider`가
텍스트 레이어에서 라인+bbox를 무손실로 뽑는다. 스캔-인쇄 페이지(텍스트 레이어
없음)는 OCR 기반 provider가 필요하나 phase 1 범위 밖이다(registry 주석 참조).
"""
