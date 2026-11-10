"""Explicit, bounded download checks. Samples never enter forecast caches."""

from __future__ import annotations

import json
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urljoin, urlsplit

import requests

from firesky.desktop_credentials import ADS_URL, _path, _read, cams_status

DATASET = "cams-global-atmospheric-composition-forecasts"
EC_BASE = "https://data.ecmwf.int/forecasts"
EC_SOURCES = {
    "ecmwf": ("ECMWF 官方", "data.ecmwf.int"),
    "aws": ("AWS 欧洲镜像", "ecmwf-forecasts.s3.eu-central-1.amazonaws.com"),
    "google": ("Google Cloud 镜像", "storage.googleapis.com"),
}
MAX_SAMPLE = 4 * 1024 * 1024
MAX_JSON = 1024 * 1024
TITLES = {"gfs": "GFS · NOAA NOMADS", "ec": "EC · ECMWF Open Data",
          "cams": "CAMS · Copernicus ADS"}
HOSTS = {"gfs": "nomads.ncep.noaa.gov", "ec": "data.ecmwf.int",
         "cams": "ads.atmosphere.copernicus.eu"}


class ProbeFailure(Exception):
    def __init__(self, state, message):
        self.state = state
        super().__init__(message)


def _http_error(status):
    state, detail = {
        401: ("unauthorized", "身份验证失败，请检查 ADS Token。"),
        403: ("forbidden", "服务拒绝访问；请检查网络限制及 CAMS 数据集条款授权。"),
        404: ("unavailable", "测试时次或数据资源暂不可用，请稍后重试。"),
        429: ("rate_limited", "上游限流，请稍后重试。"),
    }.get(status, ("http_error", "上游服务返回错误，请稍后重试。"))
    return ProbeFailure(state, f"HTTP {status} · {detail}")


def _safe_https(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ProbeFailure("invalid_response", "服务返回了不安全的下载地址。")
    return url


class Transfer:
    def __init__(self, source, progress):
        self.source = source
        self.progress = progress
        self.started = time.monotonic()
        self.deadline = self.started + (90 if source == "cams" else 40)
        self.bytes = 0
        self.cams_waiting = False
        self.session = requests.Session()
        # Match the production ADS client, which deliberately bypasses proxies.
        self.session.trust_env = source != "cams"

    def tick(self, detail="正在下载并校验数据样本"):
        self.progress({"source": self.source, "stage": "network_probe",
                       "probe": self.row("testing", detail), "percent": 0})
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeFailure("timeout", "探测超时，尚未证明数据可下载。")
        return remaining

    def row(self, state, detail):
        return {"source": self.source, "title": TITLES[self.source],
                "host": HOSTS[self.source], "state": state, "detail": detail,
                "bytes_received": self.bytes,
                "elapsed_ms": round((time.monotonic() - self.started) * 1000)}

    def read(self, method, url, *, headers=None, body=None, limit=MAX_JSON,
             expected_range=None):
        remaining = self.tick()
        # Redirect manually: never forward ADS tokens, and never downgrade TLS.
        for _ in range(6):
            with self.session.request(
                method, _safe_https(url), headers={"Accept-Encoding": "identity", **(headers or {})},
                json=body, timeout=(min(8, remaining), min(8, remaining)),
                stream=True, allow_redirects=False,
            ) as response:
                if 300 <= response.status_code < 400:
                    if headers and "PRIVATE-TOKEN" in headers:
                        raise ProbeFailure("invalid_response", "ADS API 发生重定向，已停止以保护 Token。")
                    location = response.headers.get("Location")
                    if not location:
                        raise ProbeFailure("invalid_response", "下载重定向缺少目标地址。")
                    url = _safe_https(urljoin(url, location))
                    remaining = self.tick()
                    continue
                if response.status_code not in (200, 201, 202, 206):
                    raise _http_error(response.status_code)
                if expected_range:
                    start, length = expected_range
                    expected = f"bytes {start}-{start + length - 1}/"
                    if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(expected):
                        raise ProbeFailure("invalid_response", "服务器未按 Range 返回数据，已停止以避免整文件下载。")
                payload = bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    self.bytes += len(chunk)
                    if len(payload) + len(chunk) > limit:
                        raise ProbeFailure("too_large", "响应超出小样本上限，已停止下载。")
                    payload.extend(chunk)
                    self.tick()
                if expected_range and len(payload) != expected_range[1]:
                    raise ProbeFailure("invalid_response", "数据样本传输不完整。")
                return bytes(payload)
        raise ProbeFailure("invalid_response", "下载重定向次数过多。")

    def json(self, method, url, **kwargs):
        try:
            return json.loads(self.read(method, url, **kwargs))
        except (ValueError, UnicodeError):
            raise ProbeFailure("invalid_response", "服务返回的不是有效 JSON 数据。") from None


def _validate_grib(payload):
    """Require a complete message and successful ecCodes decoding, not HTTP 200."""
    import eccodes

    if len(payload) < 20 or payload[:4] != b"GRIB" or payload[-4:] != b"7777":
        raise ProbeFailure("invalid_data", "下载内容不是完整 GRIB 数据，可能被代理或登录页面替换。")
    edition = payload[7]
    size = int.from_bytes(payload[8:16] if edition == 2 else payload[4:7], "big")
    if edition not in (1, 2) or size != len(payload):
        raise ProbeFailure("invalid_data", "GRIB 数据长度不匹配。")
    handle = None
    try:
        handle = eccodes.codes_new_from_message(payload)
        if not handle or eccodes.codes_get_size(handle, "values") <= 0:
            raise ValueError("empty GRIB")
        # Decode values too: metadata alone does not validate a truncated field.
        eccodes.codes_get_values(handle)
    except Exception:
        raise ProbeFailure("invalid_data", "GRIB 样本无法解码。") from None
    finally:
        if handle:
            eccodes.codes_release(handle)


def _gfs(transfer, now):
    adjusted = now - timedelta(hours=6)
    cycle = adjusted.replace(hour=adjusted.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for _ in range(3):
        query = urlencode({
            "file": f"gfs.t{cycle:%H}z.pgrb2.0p25.f000",
            "dir": f"/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos",
            "var_TMP": "on", "lev_850_mb": "on", "subregion": "",
            "leftlon": "120", "rightlon": "121", "toplat": "31", "bottomlat": "30",
        })
        try:
            payload = transfer.read("GET", f"https://{HOSTS['gfs']}/cgi-bin/filter_gfs_0p25.pl?{query}", limit=MAX_SAMPLE)
            _validate_grib(payload)
            return f"小区域 GRIB 已下载并解码 · {cycle:%m-%d %HZ}"
        except ProbeFailure as exc:
            if exc.state != "unavailable":
                raise
        cycle -= timedelta(hours=6)
    raise ProbeFailure("unavailable", "最近三个 GFS 测试时次均不可用，请稍后重试。")


def _ec(transfer, now, source):
    if source not in EC_SOURCES:
        raise ProbeFailure("invalid_source", "EC 下载源设置无效。")
    transfer.host = EC_SOURCES[source][1]
    adjusted = now - timedelta(hours=12)
    cycle = adjusted.replace(hour=adjusted.hour // 12 * 12, minute=0, second=0, microsecond=0)
    for _ in range(2):
        base_root = {
            "ecmwf": EC_BASE,
            "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
            "google": "https://storage.googleapis.com/ecmwf-open-data",
        }[source]
        base = f"{base_root}/{cycle:%Y%m%d}/{cycle:%H}z/ifs/0p25/oper/{cycle:%Y%m%d%H}0000-0h-oper-fc"
        try:
            index = transfer.read("GET", base + ".index")
        except ProbeFailure as exc:
            if exc.state != "unavailable":
                raise
            cycle -= timedelta(hours=12)
            continue
        try:
            record = json.loads(index.splitlines()[0])
            offset, length = int(record["_offset"]), int(record["_length"])
            if offset < 0 or not 20 <= length <= MAX_SAMPLE:
                raise ValueError("invalid range")
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProbeFailure("invalid_response", "EC 索引格式或样本大小不符合预期。") from None
        payload = transfer.read("GET", base + ".grib2", limit=length,
                                headers={"Range": f"bytes={offset}-{offset + length - 1}"},
                                expected_range=(offset, length))
        _validate_grib(payload)
        return f"索引及 Range GRIB 下载、解码通过 · {cycle:%m-%d %HZ}"
    raise ProbeFailure("unavailable", "最近两个 EC 测试时次均不可用，请稍后重试。")


def _api_link(base, href):
    url = _safe_https(urljoin(base, href))
    parsed, origin = urlsplit(url), urlsplit(ADS_URL)
    if (parsed.netloc != origin.netloc or not parsed.path.startswith(origin.path + "/retrieve/v1/")
            or parsed.query):
        raise ProbeFailure("invalid_response", "ADS API 链接超出安全范围。")
    return url


def _link(document, relation):
    for link in document.get("links", []):
        if link.get("rel") == relation and isinstance(link.get("href"), str):
            return link["href"]
    raise ProbeFailure("invalid_response", "ADS 响应缺少任务链接。")


def _cams(transfer, now):
    import os

    if not cams_status()["configured"]:
        raise ProbeFailure("not_configured", "CAMS 凭据未配置或配置无效，请先在设置中保存 ADS Token。")
    config = _read(_path()) if "CDSAPI_KEY" not in os.environ else {}
    key = os.environ.get("CDSAPI_KEY", config.get("key"))
    headers = {"PRIVATE-TOKEN": key}
    process = f"{ADS_URL}/retrieve/v1/processes/{DATASET}"
    # One variable, one time, one tiny area. Never retry ambiguous submissions.
    sample_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    inputs = {"variable": ["total_aerosol_optical_depth_550nm"], "type": ["forecast"],
              "date": [sample_date], "time": ["00:00"], "leadtime_hour": ["0"],
              "data_format": "grib", "area": [31, 120, 30, 121]}
    transfer.json("GET", process, headers=headers)
    job = transfer.json("POST", process + "/execution", headers=headers, body={"inputs": inputs})
    monitor = _api_link(process + "/execution", _link(job, "monitor"))
    completed = False
    try:
        while True:
            state = transfer.json("GET", monitor, headers=headers)
            status = state.get("status")
            if status == "successful":
                transfer.cams_waiting = False
                completed = True
                break
            if status not in ("accepted", "running"):
                raise ProbeFailure("job_failed", "CAMS 测试任务被拒绝或失败，请检查 Token、数据集条款及服务状态。")
            transfer.cams_waiting = True
            transfer.tick("CAMS 测试任务排队 / 处理中，尚未验证下载")
            for _ in range(2):
                time.sleep(min(1, transfer.tick("CAMS 测试任务排队 / 处理中，尚未验证下载")))
    finally:
        if not completed:
            # Best effort only. A timed-out/cancelled check must not keep polling a queued job.
            try:
                with transfer.session.delete(monitor, headers=headers, timeout=(2, 2),
                                             allow_redirects=False):
                    pass
            except requests.RequestException:
                pass
    try:
        href = _link(state, "results")
    except ProbeFailure:
        href = monitor.rstrip("/") + "/results"
    result_url = _api_link(monitor, href)
    result = transfer.json("GET", result_url, headers=headers)
    try:
        asset = result["asset"]["value"]
        url = _safe_https(urljoin(result_url, asset["href"]))
        size = int(asset["file:size"])
        if not 20 <= size <= MAX_SAMPLE:
            raise ValueError("invalid size")
    except (KeyError, ValueError, TypeError):
        raise ProbeFailure("invalid_response", "CAMS 返回的样本下载信息无效或超出大小上限。") from None
    # Asset hosts never receive the ADS PRIVATE-TOKEN, including redirects.
    payload = transfer.read("GET", url, limit=size)
    if len(payload) != size:
        raise ProbeFailure("invalid_data", "CAMS 样本下载不完整。")
    _validate_grib(payload)
    return "最小 CAMS 任务完成，GRIB 已下载并解码"


def _failure(exc):
    if isinstance(exc, ProbeFailure):
        return exc.state, str(exc)
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error", "TLS 证书验证失败，请检查系统时间、证书及代理。"
    if isinstance(exc, (requests.exceptions.Timeout, TimeoutError)):
        return "timeout", "连接或读取超时，尚未证明数据可下载。"
    if isinstance(exc, requests.exceptions.ConnectionError):
        # Traverse typed causes without ever displaying URLs, tokens or proxy credentials.
        seen, pending = set(), [exc]
        while pending:
            value = pending.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, socket.gaierror):
                return "dns_error", "域名解析失败，请检查 DNS 或网络。"
            pending.extend(item for item in (value.__cause__, value.__context__, *value.args)
                           if isinstance(item, BaseException))
        return "connection_error", "连接失败，请检查防火墙、代理或网络限制。"
    if isinstance(exc, ssl.SSLError):
        return "tls_error", "TLS 证书验证失败。"
    return "error", "探测未完成，请检查本地依赖、配置或稍后重试。"


def probe_downloads(progress=lambda event: None, *, now=None, ecmwf_source="ecmwf"):
    """Only a fully received, decoded GRIB is reported as a download success."""
    now = now or datetime.now(timezone.utc)

    def run(source):
        transfer = Transfer(source, progress)
        transfer.host = HOSTS[source]
        try:
            detail = (
                _gfs(transfer, now) if source == "gfs"
                else _cams(transfer, now)
                if source == "cams"
                else _ec(transfer, now, ecmwf_source)
            )
            return transfer.row("passed", detail)
        except Exception as exc:
            # Cancellation raised by the owner must propagate instead of becoming a failed row.
            from firesky.web_server import ForecastCancelled
            if isinstance(exc, ForecastCancelled):
                raise
            state, detail = _failure(exc)
            if transfer.cams_waiting and state == "timeout":
                detail = "CAMS 任务仍在排队 / 处理中，等待超时；尚未验证实际下载，可稍后重试。"
            return transfer.row(state, detail)
        finally:
            transfer.session.close()

    rows = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="download-probe") as pool:
        futures = [pool.submit(run, source) for source in TITLES]
        for future in as_completed(futures):
            row = future.result()
            rows[row["source"]] = row
            progress({"source": row["source"], "stage": "network_probe", "probe": row,
                      "percent": len(rows) * 100 // len(TITLES)})
    return {"checked_at": now.isoformat(), "datasets": [rows[source] for source in TITLES],
            "all_passed": all(row["state"] == "passed" for row in rows.values())}
