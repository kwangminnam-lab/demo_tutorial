"""로컬 LLM(양자화) OpenAI 호환 서버 런처 — 플랫폼 교차(mac MLX / Win·Linux llama.cpp).

앱(`LLM_BACKEND=openai_compat`)이 `LLM_BASE_URL`로 호출하는 OpenAI 호환 `/v1` 서버를
:8001에 띄운다. **양자화 모델이 기본 베이스**다(ADR — full-precision 로더 제거). 두
backend 모두 같은 `/v1`을 노출하므로 앱 설정은 동일하다.

backend:
  - ``mlx``      : Apple Silicon(Metal) — ``python -m mlx_lm.server`` (MLX Q4)
  - ``llamacpp`` : Windows/Linux + NVIDIA GPU — ``llama-server`` (GGUF Q4, GPU 오프로딩)
  - ``auto``(기본): macOS arm64 → mlx, 그 외(Windows/Linux) → llamacpp

**Windows에서**: make 없이도 동일하게 동작한다 —
    python scripts\\serve_llm.py --backend llamacpp --model .\\local_models\\gemma-e4b-Q4_K_M.gguf
(`llama-server.exe`가 PATH에 있어야 한다. llama.cpp를 CUDA로 빌드(`-DGGML_CUDA=on`)
하거나 릴리스 바이너리를 PATH에 둔다.) 모델·포트·GPU 레이어는 인자/env로 조정한다.

조용한 실패 금지(CONVENTIONS): 실행 파일·모델이 없으면 설치/경로 안내를 담아 명확히
종료한다(SystemExit). 본 스크립트는 진입점이라 비즈니스 로직은 두지 않는다.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# 기본 모델 경로/파라미터 — Makefile과 일치. env로 덮어쓴다.
_DEFAULT_MLX_MODEL = os.environ.get("MLX_MODEL", "./local_models/gemma_model_e4b_mlx_q4")
_DEFAULT_GGUF_MODEL = os.environ.get("GGUF_MODEL", "./local_models/gemma-e4b-Q4_K_M.gguf")
_DEFAULT_NGL = int(os.environ.get("GGUF_NGL", "-1"))

_BACKENDS = ("auto", "mlx", "llamacpp")
# llama.cpp 서버 실행 파일 후보(Windows는 shutil.which가 .exe를 자동 매칭).
_LLAMA_SERVER = "llama-server"


def resolve_backend(choice: str) -> str:
    """`auto`면 플랫폼으로 결정한다 — macOS Apple Silicon→mlx, 그 외→llamacpp."""
    if choice != "auto":
        return choice
    is_mac_arm = platform.system() == "Darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }
    return "mlx" if is_mac_arm else "llamacpp"


def build_command(args: argparse.Namespace) -> list[str]:
    """선택된 backend의 서버 실행 커맨드(list[str])를 만든다(순수 — 부수효과 없음)."""
    backend = resolve_backend(args.backend)
    if backend == "mlx":
        model = args.model or _DEFAULT_MLX_MODEL
        return [
            sys.executable, "-m", "mlx_lm.server",
            "--model", model,
            "--host", args.host,
            "--port", str(args.port),
        ]
    if backend == "llamacpp":
        model = args.model or _DEFAULT_GGUF_MODEL
        return [
            _LLAMA_SERVER,
            "--model", model,
            "--host", args.host,
            "--port", str(args.port),
            "--n-gpu-layers", str(args.n_gpu_layers),
            "--ctx-size", str(args.ctx_size),
            "--jinja",
        ]
    raise ValueError(f"알 수 없는 backend: {backend!r} (허용: mlx, llamacpp)")


def _looks_local(model: str) -> bool:
    """모델 식별자가 로컬 경로처럼 보이는지 — HF 저장소 ID(org/name)는 제외.

    로컬 경로면 존재를 검증한다. HF ID(`org/model`)는 서버가 직접 받으므로 검증 생략.
    """
    if model.startswith((".", "~", os.sep)) or os.path.isabs(model):
        return True
    # Windows 드라이브 경로(C:\...) 또는 .gguf 파일명도 로컬로 본다.
    if len(model) >= 2 and model[1] == ":":
        return True
    return model.lower().endswith(".gguf")


def _preflight(backend: str, model: str) -> None:
    """실행 파일·모델 존재를 사전 점검한다. 미충족이면 안내와 함께 종료(조용한 실패 금지)."""
    if backend == "mlx":
        if importlib.util.find_spec("mlx_lm") is None:
            raise SystemExit(
                "mlx_lm 미설치 — Apple Silicon에서 `pip install mlx-lm`로 설치하세요. "
                "(mlx는 macOS 전용입니다. Windows/Linux GPU면 `--backend llamacpp`를 쓰세요.)"
            )
    elif backend == "llamacpp":
        if shutil.which(_LLAMA_SERVER) is None:
            raise SystemExit(
                f"`{_LLAMA_SERVER}`를 PATH에서 찾지 못했습니다 — llama.cpp를 CUDA로 빌드"
                "(`-DGGML_CUDA=on`)하거나 릴리스 바이너리를 PATH에 두세요"
                "(Windows는 llama-server.exe)."
            )

    # 로컬 경로 모델이면 존재를 검증한다(HF 저장소 ID는 서버가 직접 받으므로 생략).
    if _looks_local(model) and not Path(model).expanduser().exists():
        raise SystemExit(
            f"모델을 찾을 수 없습니다: {model} — 경로를 확인하거나 "
            f"{'GGUF_MODEL' if backend == 'llamacpp' else 'MLX_MODEL'} env/`--model`로 지정하세요."
        )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="로컬 양자화 LLM OpenAI 호환 서버 런처 (mlx | llamacpp)"
    )
    p.add_argument(
        "--backend", choices=_BACKENDS, default="auto",
        help="서버 backend. auto=플랫폼 자동(mac arm→mlx, 그 외→llamacpp).",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8001)
    p.add_argument(
        "--model", default=None,
        help="모델 경로(GGUF 파일 또는 MLX 디렉토리) 또는 HF 저장소 ID. "
        "미지정 시 backend별 기본값(env MLX_MODEL/GGUF_MODEL).",
    )
    p.add_argument(
        "--n-gpu-layers", type=int, default=_DEFAULT_NGL,
        help="[llamacpp] GPU에 올릴 레이어 수. -1=전부(VRAM 충분 시), 부족하면 낮춘다.",
    )
    p.add_argument("--ctx-size", type=int, default=8192, help="[llamacpp] 컨텍스트 길이.")
    p.add_argument(
        "--dry-run", action="store_true",
        help="실행 없이 결정된 커맨드만 출력하고 종료(점검용).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    backend = resolve_backend(args.backend)
    cmd = build_command(args)

    if args.dry_run:
        print(" ".join(cmd))
        return 0

    model = args.model or (_DEFAULT_MLX_MODEL if backend == "mlx" else _DEFAULT_GGUF_MODEL)
    _preflight(backend, model)

    print(f"[serve-llm] backend={backend} host={args.host} port={args.port} model={model}")
    print(f"[serve-llm] exec: {' '.join(cmd)}")
    # 서버는 포그라운드로 돈다(Ctrl-C로 종료). subprocess라 신호가 그대로 전파된다.
    return subprocess.run(cmd, check=False).returncode  # noqa: S603 — 인자 리스트, shell=False.


if __name__ == "__main__":
    raise SystemExit(main())
