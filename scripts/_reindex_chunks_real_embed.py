"""dev 1회성: kms-chunks 를 실 임베딩(sentence_transformers, dim 384)으로 재색인.

기존 인덱스의 청크 텍스트·메타를 스크롤로 읽어 재임베딩만 한다(파일 재추출 회피 →
빠름). dim 이 바뀌므로(64→384) 인덱스를 지우고 OpenSearchVectorStore 가 새 매핑으로
재생성한다. 어휘 인덱스(kms-files)·neo4j 는 건드리지 않는다.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opensearchpy import OpenSearch
from opensearchpy.helpers import scan

from kms.config.settings import get_settings
from kms.adapters.vectorstore.sentence_transformer import SentenceTransformerEmbedder
from kms.adapters.vectorstore.opensearch_store import OpenSearchVectorStore
from kms.adapters.ingestion.chunk.models import Chunk, ChunkMetadata

s = get_settings()
index = s.opensearch_chunk_index
client = OpenSearch(hosts=[s.opensearch_url])

# 1) 기존 청크(텍스트+메타) 스크롤 — embedding 필드는 제외(곧 재생성).
print(f"[reindex] scrolling {index} …")
rows = []
for hit in scan(client, index=index, _source_excludes=["embedding"], size=500):
    src = hit["_source"]
    text = src.pop("text", "")
    rows.append((hit["_id"], text, src))
print(f"[reindex] {len(rows)} chunks 읽음")

# 2) 인덱스 삭제 → OpenSearchVectorStore 가 실 임베더 차원(384)으로 재생성.
client.indices.delete(index=index, ignore=[404])
emb = SentenceTransformerEmbedder(s.embedding_model_name)
vs = OpenSearchVectorStore(s.opensearch_url, index, emb)
print(f"[reindex] dim={vs._dim} 새 인덱스 생성 (model={s.embedding_model_name})")

# 3) Chunk 재구성 → 배치 재임베딩·색인.
BATCH = 256
done = 0
for i in range(0, len(rows), BATCH):
    batch = rows[i : i + BATCH]
    chunks = [
        Chunk(chunk_id=cid, text=text, metadata=ChunkMetadata.model_validate(meta))
        for cid, text, meta in batch
    ]
    vs.index(chunks)
    done += len(chunks)
    print(f"  {done}/{len(rows)}")
print(f"[done] 재색인 완료: {done} chunks (dim {vs._dim})")
