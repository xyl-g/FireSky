import json
import os

import pytest

from firesky import desktop_credentials as credentials
from firesky.desktop_rpc import DesktopService

TOKEN = "fixture-token-not-a-real-key"


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    path = tmp_path / ".cdsapirc"
    monkeypatch.setenv("CDSAPI_RC", str(path))
    return path


def test_missing_and_empty_do_not_create_files(isolated_credentials):
    path = isolated_credentials
    assert credentials.cams_status()["state"] == "missing"
    assert not path.exists()
    path.write_text("")
    assert not credentials.cams_status()["configured"]


def test_configuration_is_local_only_and_never_returns_secret(isolated_credentials, monkeypatch):
    import requests
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("network used"))
    isolated_credentials.write_text(f"url: {credentials.ADS_URL}\nkey: {TOKEN}\nverify: 1\n")
    status = credentials.cams_status()
    assert status["configured"] and status["state"] == "configured"
    assert TOKEN not in json.dumps(status)
    response = DesktopService().dispatch("hello", {})
    assert response["cams_credentials"]["configured"]
    assert TOKEN not in json.dumps(response)


@pytest.mark.parametrize("content,state", [
    (f"url: https://cds.climate.copernicus.eu/api\nkey: {TOKEN}", "wrong_endpoint"),
    (f"url: {credentials.ADS_URL}\nkey:", "missing"),
    (f"url: {credentials.ADS_URL}\nkey: {TOKEN}\nverify: 0", "tls_disabled"),
    (f"broken configuration with secret {TOKEN}", "unreadable"),
    (f"key: {TOKEN}\nkey: duplicate", "unreadable"),
    ("x" * (credentials.MAX_CONFIG_BYTES + 1), "unreadable"),
])
def test_bad_configuration_has_sanitized_status(isolated_credentials, content, state):
    isolated_credentials.write_text(content)
    status = credentials.cams_status()
    assert status["state"] == state and not status["configured"]
    assert TOKEN not in json.dumps(status)


def test_environment_overrides_file(isolated_credentials, monkeypatch):
    isolated_credentials.write_text("url: wrong\nkey: old\n")
    monkeypatch.setenv("CDSAPI_KEY", TOKEN)
    monkeypatch.setenv("CDSAPI_URL", credentials.ADS_URL)
    status = credentials.cams_status()
    assert status["configured"] and status["environment_key"] and not status["can_save"]
    with pytest.raises(ValueError, match="环境变量"):
        credentials.save_cams_key("replacement")
    assert isolated_credentials.read_text() == "url: wrong\nkey: old\n"


def test_environment_and_file_can_supply_different_fields(isolated_credentials, monkeypatch):
    isolated_credentials.write_text(f"key: {TOKEN}\n")
    monkeypatch.setenv("CDSAPI_URL", credentials.ADS_URL)
    assert credentials.cams_status()["configured"]
    monkeypatch.setenv("CDSAPI_KEY", "")
    assert not credentials.cams_status()["configured"]


@pytest.mark.parametrize("key", [TOKEN, "123:legacy-token", "123456"])
def test_save_is_atomic_private_and_compatible_with_ads(isolated_credentials, key):
    from cdsapi.api import read_config
    isolated_credentials.write_text("verify: 0\nextra: retained\n")
    service = DesktopService()
    result = service.dispatch("save_cams_key", {"key": f" {key}\n"})
    assert result["configured"]
    assert key not in json.dumps(result)
    assert isolated_credentials.stat().st_mode & 0o777 == 0o600
    saved = read_config(str(isolated_credentials))
    assert saved["key"] == key and saved["url"] == credentials.ADS_URL and saved["verify"] == "1"
    assert "extra: retained" in isolated_credentials.read_text()
    assert not list(isolated_credentials.parent.glob(".firesky-ads-*"))
    assert service.dispatch("cams_credentials", {})["configured"]


@pytest.mark.parametrize("key", ["", None, "key: pasted configuration", "abc\nkey: injection", "'quoted'", "中"])
def test_invalid_key_is_not_written_or_echoed(isolated_credentials, key):
    with pytest.raises(ValueError) as exc:
        credentials.save_cams_key(key)
    assert not isolated_credentials.exists()
    if key:
        assert key not in str(exc.value)


def test_unreadable_file_is_not_overwritten_or_leaked(isolated_credentials):
    original = f"broken file containing {TOKEN}"
    isolated_credentials.write_text(original)
    with pytest.raises(ValueError) as exc:
        credentials.save_cams_key("replacement")
    assert TOKEN not in str(exc.value)
    assert isolated_credentials.read_text() == original


def test_symlink_is_not_replaced(isolated_credentials, tmp_path):
    target = tmp_path / "original"
    target.write_text(f"url: {credentials.ADS_URL}\nkey: {TOKEN}\n")
    isolated_credentials.symlink_to(target)
    with pytest.raises(ValueError):
        credentials.save_cams_key("replacement")
    assert isolated_credentials.is_symlink() and TOKEN in target.read_text()


def test_failed_replace_preserves_original_and_cleans_temporary(isolated_credentials, monkeypatch):
    original = f"url: {credentials.ADS_URL}\nkey: {TOKEN}\n"
    isolated_credentials.write_text(original)
    def fail(*args):
        raise OSError("simulated failure")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(ValueError):
        credentials.save_cams_key("replacement")
    assert isolated_credentials.read_text() == original
    assert not list(isolated_credentials.parent.glob(".firesky-ads-*"))
