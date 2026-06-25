"""dev: data/ 파일을 OpenSearch에 적재 — 어휘(kms-files) + 벡터 k-NN(kms-chunks).

외부 소스(slack/onedrive/googledrive) 실시간 fetch는 쓰지 않는다 — `data/` 폴더에
미리 받아둔 파일만 적재 소스로 쓴다(외부 미연결). graph는 throwaway(InMemory).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kms.config.settings import get_settings
from kms.adapters.searchindex.opensearch_store import OpenSearchStore
from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.vectorstore.opensearch_store import OpenSearchVectorStore
from kms.adapters.vectorstore.embedder import FakeEmbedder
from kms.services.ingestion_service import IngestionService, IngestItem
from kms.domain.access import AccessLevel
from kms.domain.models import SourceType

EXTS={".pdf",".docx",".pptx",".xlsx",".xlsm",".html",".txt",".md"}
settings=get_settings()
search=OpenSearchStore.from_settings(settings)
emb=FakeEmbedder()
vec=OpenSearchVectorStore.from_settings(settings, emb)   # k-NN index kms-chunks
svc=IngestionService(vec, InMemoryGraphStore(), emb, search)

root=Path("data")
items=[]
for src in ("googledrive","onedrive","slack"):
    base=root/src
    if not base.exists():
        continue
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXTS:
            items.append(IngestItem(
                file_path=str(p), source=SourceType(src),
                access=AccessLevel.임직원, author_department="영업",
                source_url=p.name))
print(f"[ingest] 대상 {len(items)}건 → OpenSearch "
      f"어휘={settings.opensearch_index} 벡터={settings.opensearch_chunk_index}")
ok=fail=0
for it in items:
    name=Path(it.file_path).name
    try:
        r=svc.ingest_item(it)
        if r.ok:
            ok+=1
            print(f"  OK   {name[:50]}")
        else:
            fail+=1
            print(f"  FAIL {name[:40]} :: {r.error}")
    except Exception as e:  # noqa: BLE001 — dev 스크립트: 항목 실패를 보고만 하고 계속.
        fail+=1
        print(f"  ERR  {name[:40]} :: {type(e).__name__}: {str(e)[:80]}")
print(f"[done] ok={ok} fail={fail}")
