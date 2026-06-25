"""scripts/serve_llm.py (로컬 LLM 서버 런처) 단위 테스트.

서버 자체(subprocess)는 띄우지 않고, backend 해소·커맨드 조립·사전 점검(존재/실행
파일)만 결정론적으로 검증한다. scripts/는 pythonpath(src) 밖이라 sys.path에 직접
단다(test_serve_api와 동일).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import serve_llm  # noqa: E402


def _args(**overrides: object) -> SimpleNamespace:
    base = {
        "backend": "auto",
        "host": "127.0.0.1",
        "port": 8001,
        "model": None,
        "n_gpu_layers": -1,
        "ctx_size": 8192,
        "dry_run": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_backend_passes_explicit_choice() -> None:
    assert serve_llm.resolve_backend("mlx") == "mlx"
    assert serve_llm.resolve_backend("llamacpp") == "llamacpp"


def test_resolve_backend_auto_mac_arm_is_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_llm.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(serve_llm.platform, "machine", lambda: "arm64")
    assert serve_llm.resolve_backend("auto") == "mlx"


def test_resolve_backend_auto_windows_is_llamacpp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Windows/Linux(비 mac-arm) → GGUF llama.cpp.
    monkeypatch.setattr(serve_llm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(serve_llm.platform, "machine", lambda: "AMD64")
    assert serve_llm.resolve_backend("auto") == "llamacpp"


def test_build_command_mlx() -> None:
    cmd = serve_llm.build_command(_args(backend="mlx", model="/m/mlx", port=8001))
    assert "mlx_lm.server" in cmd
    assert cmd[cmd.index("--model") + 1] == "/m/mlx"
    assert cmd[cmd.index("--port") + 1] == "8001"


def test_build_command_llamacpp_has_gpu_and_jinja() -> None:
    cmd = serve_llm.build_command(
        _args(backend="llamacpp", model="/m/x.gguf", n_gpu_layers=32, ctx_size=4096)
    )
    assert cmd[0] == "llama-server"
    assert cmd[cmd.index("--n-gpu-layers") + 1] == "32"
    assert cmd[cmd.index("--ctx-size") + 1] == "4096"
    assert "--jinja" in cmd


def test_build_command_uses_backend_default_model_when_unset() -> None:
    cmd = serve_llm.build_command(_args(backend="llamacpp", model=None))
    assert cmd[cmd.index("--model") + 1] == serve_llm._DEFAULT_GGUF_MODEL


def test_preflight_llamacpp_missing_binary_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # llama-server가 PATH에 없으면 안내와 함께 종료(조용한 실패 금지).
    monkeypatch.setattr(serve_llm.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit):
        serve_llm._preflight("llamacpp", "org/model")  # HF ID라 존재검사는 생략


def test_preflight_local_model_missing_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 실행 파일은 있다고 가정하되, 로컬 GGUF 경로가 없으면 종료.
    monkeypatch.setattr(serve_llm.shutil, "which", lambda _name: "/usr/bin/llama-server")
    missing = tmp_path / "nope.gguf"
    with pytest.raises(SystemExit):
        serve_llm._preflight("llamacpp", str(missing))


def test_preflight_hf_repo_id_skips_existence_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HF 저장소 ID(org/name)는 로컬 존재검사를 건너뛴다(서버가 직접 받음).
    monkeypatch.setattr(serve_llm.shutil, "which", lambda _name: "/usr/bin/llama-server")
    serve_llm._preflight("llamacpp", "google/gemma-gguf")  # 예외 없이 통과


def test_main_dry_run_prints_command_without_exec(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = serve_llm.main(
        ["--backend", "llamacpp", "--model", "/m/x.gguf", "--dry-run"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "llama-server" in out and "/m/x.gguf" in out


def test_arg_parser_defaults() -> None:
    args = serve_llm.build_arg_parser().parse_args([])
    assert args.backend == "auto"
    assert args.port == 8001
    assert args.host == "127.0.0.1"
