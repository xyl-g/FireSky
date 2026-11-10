"""Local-only ADS configuration checks; credentials never leave this module."""

from __future__ import annotations

import os
import re
import tempfile
from configparser import ConfigParser, Error as ConfigError
from pathlib import Path

ADS_URL = "https://ads.atmosphere.copernicus.eu/api"
MAX_CONFIG_BYTES = 64 * 1024


def _path():
    return Path(os.environ.get("CDSAPI_RC") or Path.home() / ".cdsapirc")


def _read(path):
    if not path.exists():
        return {}
    if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("invalid configuration")
    # ADS uses a flat "name: value" format, not YAML quoting or type coercion.
    parser = ConfigParser(interpolation=None, delimiters=(":",), empty_lines_in_values=False)
    parser.optionxform = str
    parser.read_string("[ads]\n" + path.read_text("utf-8"))
    config = dict(parser["ads"])
    if any("\n" in value for value in config.values()):
        raise ValueError("multiline configuration")
    return config


def _valid_key(key):
    return (
        isinstance(key, str) and 0 < len(key) <= 16384
        and re.fullmatch(r"[A-Za-z0-9._:+/=-]+", key) is not None
    )


def cams_status():
    """Match ADS environment precedence, without contacting ADS or returning a key."""
    path = _path()
    env_key = os.environ.get("CDSAPI_KEY")
    env_url = os.environ.get("CDSAPI_URL")
    state = {
        "configured": False, "state": "missing",
        "path": str(path), "environment_key": env_key is not None,
        "can_save": env_key is None and (env_url is None or env_url.rstrip("/") == ADS_URL),
    }
    try:
        config = _read(path) if env_key is None or env_url is None else {}
        key = env_key if env_key is not None else config.get("key")
        url = env_url if env_url is not None else config.get("url")
        if not _valid_key(key):
            return state
        if not isinstance(url, str) or url.rstrip("/") != ADS_URL:
            return {**state, "state": "wrong_endpoint"}
        if config.get("verify", 1) not in (1, "1", True):
            return {**state, "state": "tls_disabled"}
        return {**state, "configured": True, "state": "configured"}
    except (OSError, ValueError, UnicodeError, ConfigError):
        return {**state, "state": "unreadable"}


def save_cams_key(key):
    """Atomically save the ADS key with owner-only permissions, preserving other fields."""
    if not isinstance(key, str) or not _valid_key(key.strip()):
        raise ValueError("请输入有效的 ADS Personal Access Token，不要粘贴整段配置。")
    if not cams_status()["can_save"]:
        raise ValueError("环境变量正在覆盖 ADS 配置，请先修改 CDSAPI_KEY / CDSAPI_URL，再重新启动。")
    path = _path()
    temporary = None
    try:
        if path.is_symlink():
            raise ValueError("symlink")
        config = _read(path)
        config.update(url=ADS_URL, key=key.strip(), verify=1)
        content = "".join(f"{name}: {value}\n" for name, value in config.items())
        if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ValueError("configuration too large")
        descriptor, temporary = tempfile.mkstemp(prefix=".firesky-ads-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, UnicodeError, ConfigError):
        # Parser and filesystem exceptions may include secrets from the source file.
        raise ValueError("无法安全保存 CAMS 配置，请检查配置文件格式、位置和写入权限。") from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return cams_status()
