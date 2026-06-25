"""산출물 영속 — `export_root`(공유 PVC) 아래에 파일을 원자적으로 쓴다(F3/F5).

파싱/추출이 만든 .jsonl(및 업로드 원본)을 code-server 노드 폴더(클러스터에선 RWX PVC
마운트)로 흘려보내는 헬퍼. 경로는 설정(`settings.export_root`)에서 주입한다 — 코드에
하드코딩하지 않는다(step 0 설정·step 8 PVC 마운트).

동시 접근(RWX PVC) 안전을 위해 임시 파일에 쓴 뒤 같은 디렉토리 안에서 rename(원자적
교체)한다. `..`/절대경로 주입은 차단한다(filename은 basename만, 최종 경로는 루트 하위로 강제).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_export(
    rel_dir: str, filename: str, content: str | bytes, export_root: str
) -> Path:
    """`export_root/rel_dir/<basename(filename)>`에 `content`를 원자적으로 쓴다.

    - 디렉토리는 자동 생성(`parents=True, exist_ok=True`).
    - `filename`은 basename만 사용해 `..`/절대경로 주입을 차단한다. `rel_dir`도 정규화
      후 루트를 벗어나면 거부한다(traversal 방어).
    - 임시 파일 작성 후 같은 디렉토리에서 `os.replace`로 교체 — 동시 접근(RWX PVC) 안전.

    반환: 쓰여진 절대 경로.
    예외: 빈/위험 파일명, 루트 이탈 경로는 `ValueError`. I/O 실패는 OSError로 전파.
    """
    root = Path(export_root).resolve()
    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError(f"안전하지 않은 파일명: {filename!r}")

    target_dir = (root / rel_dir).resolve()
    # rel_dir에 `..` 등이 섞여 루트를 벗어나면 거부(traversal 방어).
    if root != target_dir and root not in target_dir.parents:
        raise ValueError(f"export_root를 벗어난 대상 디렉토리: {rel_dir!r}")

    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name

    mode = "wb" if isinstance(content, bytes) else "w"
    encoding = None if isinstance(content, bytes) else "utf-8"
    # 같은 디렉토리에 임시 파일 → flush+fsync → os.replace(원자적). 부분 쓰기 노출 방지.
    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, mode, encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target_path)
    except BaseException:
        # 실패 시 임시 파일을 남기지 않는다(조용한 잔존 방지). 원인은 호출자에 전파.
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target_path
