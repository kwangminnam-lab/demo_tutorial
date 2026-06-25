"""scripts/serve_api.py (API composition root) 단위 테스트.

서빙 자체(uvicorn.run)는 돌리지 않고, arg 파서 기본값·인자 파싱만 검증한다.
scripts/는 pythonpath(src) 밖이라 sys.path에 직접 단다(test_serve_llm과 동일).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import serve_api  # noqa: E402


def test_arg_parser_defaults() -> None:
    args = serve_api.build_arg_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.data_root is None


def test_arg_parser_accepts_host_port_data_root() -> None:
    args = serve_api.build_arg_parser().parse_args(
        ["--host", "0.0.0.0", "--port", "9000", "--data-root", "data"]
    )
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.data_root == "data"
