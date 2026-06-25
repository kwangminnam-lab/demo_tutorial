"""저장소(파일시스템) 어댑터 — 산출물을 공유 PVC 경로에 영속한다(F3/F5).

`write_export`가 파싱/추출 산출 JSONL(및 업로드 원본)을 `export_root`(클러스터=code-server
공유 PVC 마운트) 아래에 원자적으로 쓴다. 경로는 코드에 하드코딩하지 않고 설정
(`settings.export_root`)에서 주입한다(step 0 설정·step 8 PVC 마운트).
"""

from kms.adapters.storage.export import write_export

__all__ = ["write_export"]
