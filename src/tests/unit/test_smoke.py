import kms


def test_package_imports() -> None:
    assert kms is not None


def test_version_is_string() -> None:
    assert isinstance(kms.__version__, str)
