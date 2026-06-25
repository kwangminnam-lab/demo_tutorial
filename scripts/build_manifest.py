"""적재 매니페스트 생성기 — `data/` 트리를 훑어 ingest.py용 매니페스트(YAML)를 만든다.

사용법:
    python scripts/build_manifest.py [DATA_DIR] [-o OUT.yaml] [--access 1]

`data/<source>/**` 의 추출 가능한 파일을 모아 `{file_path, source, access}` 항목
리스트로 출력한다. `source`는 최상위 폴더명(googledrive·onedrive·slack)에서 추론하고
`SourceType` 으로 검증한다. 지원 안 하는 확장자·숨김 파일(.DS_Store 등)은 건너뛴다.

진입점은 얇게 — 파일 수집·메타 추론·직렬화만 한다. 적재 자체는 ingest.py가 한다:
    python scripts/build_manifest.py data -o manifest.yaml
    python scripts/ingest.py manifest.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from kms.domain.models import SourceType

# 추출 가능 확장자 — extract/registry.py `_SUPPORTED_EXTS` 와 일치(원본은 거기).
# 여기서 직접 import하면 무거운 추출 의존(pymupdf 등)을 끌어오므로 매니페스트
# 생성기는 디커플해 이 목록만 둔다. registry가 바뀌면 함께 갱신한다.
_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".txt", ".md",
}

# data/ 최상위 폴더명 → SourceType 값. 폴더명이 SourceType 값과 같아 그대로 매핑된다.
_VALID_SOURCES = {s.value for s in SourceType}


def _infer_source(rel: Path) -> str | None:
    """경로의 최상위 폴더명을 source로 추론한다. 알 수 없으면 None(스킵)."""
    if not rel.parts:
        return None
    top = rel.parts[0].lower()
    return top if top in _VALID_SOURCES else None


def collect_items(data_dir: Path, access: int) -> tuple[list[dict[str, object]], list[str]]:
    """data_dir 하위 추출 가능 파일을 매니페스트 항목으로 모은다.

    반환: (항목 리스트, 스킵 사유 목록). 숨김 파일·미지원 확장자·source 불명은 스킵하고
    사유를 남긴다(조용히 버리지 않는다 — 무엇이 빠졌는지 보이게).
    """
    items: list[dict[str, object]] = []
    skipped: list[str] = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_dir)
        if any(part.startswith(".") for part in rel.parts):
            skipped.append(f"숨김: {rel}")
            continue
        if path.suffix.lower() not in _SUPPORTED_EXTS:
            skipped.append(f"미지원({path.suffix}): {rel}")
            continue
        source = _infer_source(rel)
        if source is None:
            skipped.append(f"source 불명(최상위 폴더): {rel}")
            continue
        items.append(
            {"file_path": str(path.resolve()), "source": source, "access": access}
        )
    return items, skipped


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="data/ → 적재 매니페스트(YAML) 생성")
    parser.add_argument("data_dir", nargs="?", default="data", help="데이터 루트(기본 data)")
    parser.add_argument("-o", "--out", default="-", help="출력 파일(기본 stdout)")
    parser.add_argument(
        "--access", type=int, default=1,
        help="모든 항목의 접근 레벨(기본 1=멤버, 2=마스터 전용)",
    )
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"데이터 디렉토리를 찾을 수 없음: {data_dir}", file=sys.stderr)
        return 2

    items, skipped = collect_items(data_dir, args.access)
    if not items:
        print(f"적재할 파일이 없음: {data_dir}", file=sys.stderr)
        return 1

    dumped = yaml.safe_dump(items, allow_unicode=True, sort_keys=False)
    if args.out == "-":
        sys.stdout.write(dumped)
    else:
        Path(args.out).write_text(dumped, encoding="utf-8")
        print(f"매니페스트 작성: {args.out} — {len(items)}건", file=sys.stderr)

    # 무엇이 빠졌는지 stderr로 보고(조용한 누락 금지).
    for reason in skipped:
        print(f"  스킵 {reason}", file=sys.stderr)
    print(f"수집 {len(items)}건, 스킵 {len(skipped)}건", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
