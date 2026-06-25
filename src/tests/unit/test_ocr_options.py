"""pdf_digital `_resolve_ocr_options` 단위 테스트 — 한국어 OCR 엔진 선택.

docling 기본 RapidOCR(중국어)은 한글을 한자로 깨뜨린다. 설정(parse_ocr_engine/lang)에
따라 한국어 가능 엔진(ocrmac/easyocr)으로 교체하고, 미가용 시 안전 폴백(None=기본)한다.
"""

from __future__ import annotations

from types import SimpleNamespace


from kms.adapters.ingestion.extract import pdf_digital


def _settings(engine: str, lang: str = "ko,en") -> SimpleNamespace:
    return SimpleNamespace(parse_ocr_engine=engine, parse_ocr_lang=lang)


def _patch(monkeypatch, engine: str, lang: str = "ko,en") -> None:
    monkeypatch.setattr(
        "kms.config.settings.get_settings", lambda: _settings(engine, lang)
    )


def test_rapidocr_returns_none_default(monkeypatch) -> None:
    _patch(monkeypatch, "rapidocr")
    assert pdf_digital._resolve_ocr_options() is None


def test_unknown_engine_falls_back_to_none(monkeypatch) -> None:
    _patch(monkeypatch, "nope")
    assert pdf_digital._resolve_ocr_options() is None


def test_easyocr_returns_easyocr_options_with_langs(monkeypatch) -> None:
    _patch(monkeypatch, "easyocr", "ko,en")
    opts = pdf_digital._resolve_ocr_options()
    assert type(opts).__name__ == "EasyOcrOptions"
    assert opts.lang == ["ko", "en"]


def test_easyocr_baked_path_disables_download(monkeypatch) -> None:
    # 폐쇄망(EASYOCR_MODULE_PATH 설정) — 구운 경로 명시 + 다운로드 차단(런타임 권한오류 방지).
    monkeypatch.setenv("EASYOCR_MODULE_PATH", "/opt/easyocr")
    _patch(monkeypatch, "easyocr", "ko,en")
    opts = pdf_digital._resolve_ocr_options()
    assert opts.model_storage_directory == "/opt/easyocr/model"
    assert opts.download_enabled is False


def test_easyocr_no_env_keeps_default_download(monkeypatch) -> None:
    # 로컬(env 미설정) — 기존 동작(download_enabled 기본 True) 유지(무회귀).
    monkeypatch.delenv("EASYOCR_MODULE_PATH", raising=False)
    _patch(monkeypatch, "easyocr", "ko,en")
    opts = pdf_digital._resolve_ocr_options()
    assert opts.model_storage_directory is None
    assert opts.download_enabled is True


def test_ocrmac_maps_lang_codes(monkeypatch) -> None:
    _patch(monkeypatch, "ocrmac", "ko,en")
    opts = pdf_digital._resolve_ocr_options()
    assert type(opts).__name__ == "OcrMacOptions"
    # ko→ko-KR, en→en-US 로 매핑.
    assert opts.lang == ["ko-KR", "en-US"]


def test_auto_prefers_available_engine(monkeypatch) -> None:
    _patch(monkeypatch, "auto")
    # 가용 모듈을 강제로 지정해 자동선택 분기를 결정론적으로 검증.
    import importlib.util

    real = importlib.util.find_spec

    def fake_find_spec(name: str):
        if name == "ocrmac":
            return None  # ocrmac 없음 → easyocr로.
        if name == "easyocr":
            return object()  # 가용으로 가장.
        return real(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    opts = pdf_digital._resolve_ocr_options()
    assert type(opts).__name__ == "EasyOcrOptions"


def test_auto_none_when_no_korean_engine(monkeypatch) -> None:
    _patch(monkeypatch, "auto")
    import importlib.util

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name in {"ocrmac", "easyocr"} else object()
    )
    assert pdf_digital._resolve_ocr_options() is None
