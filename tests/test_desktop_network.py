import json
import socket
from datetime import datetime, timezone
from types import SimpleNamespace

import eccodes
import pytest
import requests

from firesky import desktop_network as network
from firesky import desktop_rpc as rpc

NOW = datetime(2026, 11, 10, 10, tzinfo=timezone.utc)


@pytest.fixture
def grib():
    handle = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        return eccodes.codes_get_message(handle)
    finally:
        eccodes.codes_release(handle)


class FakeTransfer:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def read(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value

    def json(self, method, url, **kwargs):
        return json.loads(self.read(method, url, **kwargs))

    def tick(self, *args):
        return 10


def test_grib_requires_complete_decodable_data(grib):
    network._validate_grib(grib)
    for payload in (b"<html>login</html>", grib[:-1], grib + b"x",
                    b"GRIB" + b"\x00" * 16 + b"7777"):
        with pytest.raises(network.ProbeFailure):
            network._validate_grib(payload)


def test_gfs_uses_real_subset_download_and_missing_cycle_fallback(grib):
    transfer = FakeTransfer([network._http_error(404), grib])
    assert "GRIB" in network._gfs(transfer, NOW)
    assert len(transfer.calls) == 2
    assert "filter_gfs_0p25.pl" in transfer.calls[0][1]
    assert "lev_850_mb=on" in transfer.calls[0][1]
    assert "%2Fgfs.20261109%2F18%2Fatmos" in transfer.calls[1][1]
    assert transfer.calls[0][0] == "GET"


def test_gfs_never_passes_html_even_with_success_status():
    with pytest.raises(network.ProbeFailure, match="GRIB"):
        network._gfs(FakeTransfer([b"<html>captive portal</html>"]), NOW)


def test_ec_reads_index_and_complete_range_message(grib):
    transfer = FakeTransfer([json.dumps({"_offset": 42, "_length": len(grib)}).encode(), grib])
    assert "Range" in network._ec(transfer, NOW, "ecmwf")
    method, url, kwargs = transfer.calls[1]
    assert url.endswith(".grib2") and method == "GET"
    assert kwargs["expected_range"] == (42, len(grib))
    assert kwargs["headers"]["Range"] == f"bytes=42-{41 + len(grib)}"


@pytest.mark.parametrize("record", [{}, {"_offset": -1, "_length": 100},
                                     {"_offset": 0, "_length": network.MAX_SAMPLE + 1}])
def test_ec_rejects_invalid_index(record):
    with pytest.raises(network.ProbeFailure):
        network._ec(FakeTransfer([json.dumps(record).encode()]), NOW, "ecmwf")


def configure_cams(monkeypatch):
    monkeypatch.setattr(network, "cams_status", lambda: {"configured": True})
    monkeypatch.setenv("CDSAPI_KEY", "test-secret-never-display")


def test_cams_checks_actual_asset_without_forwarding_token(monkeypatch, grib):
    configure_cams(monkeypatch)
    monitor = network.ADS_URL + "/retrieve/v1/jobs/test"
    transfer = FakeTransfer([
        b"{}", json.dumps({"links": [{"rel": "monitor", "href": monitor}]}).encode(),
        b'{"status":"accepted"}', b'{"status":"successful"}',
        json.dumps({"asset": {"value": {"href": "https://asset.example/sample.grib",
                                      "file:size": len(grib)}}}).encode(), grib,
    ])
    monkeypatch.setattr(network.time, "sleep", lambda seconds: None)
    assert "GRIB" in network._cams(transfer, NOW)
    assert len(transfer.calls) == 6
    assert transfer.calls[1][0] == "POST"
    assert transfer.calls[1][2]["body"]["inputs"]["variable"] == ["total_aerosol_optical_depth_550nm"]
    assert transfer.calls[-1][1].startswith("https://asset.example/")
    assert "headers" not in transfer.calls[-1][2]
    assert transfer.calls[0][2]["headers"]["PRIVATE-TOKEN"] == "test-secret-never-display"


def test_cams_missing_key_does_not_access_network(monkeypatch):
    monkeypatch.setattr(network, "cams_status", lambda: {"configured": False})
    transfer = FakeTransfer([])
    with pytest.raises(network.ProbeFailure) as error:
        network._cams(transfer, NOW)
    assert error.value.state == "not_configured"
    assert not transfer.calls


@pytest.mark.parametrize("cancelled", [False, True])
def test_cams_wait_timeout_and_cancel_attempt_dismissal(monkeypatch, cancelled):
    from firesky.web_server import ForecastCancelled
    configure_cams(monkeypatch)
    monitor = network.ADS_URL + "/retrieve/v1/jobs/test"
    transfer = FakeTransfer([
        b"{}", json.dumps({"links": [{"rel": "monitor", "href": monitor}]}).encode(),
        b'{"status":"accepted"}',
    ])
    dismissals = []
    transfer.session = SimpleNamespace(delete=lambda url, **kwargs: (
        dismissals.append((url, kwargs)) or Response(status=200)
    ))
    def stop(*args):
        if cancelled:
            raise ForecastCancelled("cancel")
        raise network.ProbeFailure("timeout", "timeout")
    transfer.tick = stop
    with pytest.raises(ForecastCancelled if cancelled else network.ProbeFailure):
        network._cams(transfer, NOW)
    assert transfer.cams_waiting
    assert dismissals[0][0] == monitor
    assert dismissals[0][1]["allow_redirects"] is False
    assert [call[0] for call in transfer.calls].count("POST") == 1


@pytest.mark.parametrize("href", ["http://ads.atmosphere.copernicus.eu/api/retrieve/v1/jobs/1",
                                 "https://evil.example/api/retrieve/v1/jobs/1",
                                 "https://ads.atmosphere.copernicus.eu/other",
                                 "https://user:secret@ads.atmosphere.copernicus.eu/api/retrieve/v1/jobs/1"])
def test_cams_api_links_cannot_escape_or_leak_token(href):
    with pytest.raises(network.ProbeFailure):
        network._api_link(network.ADS_URL, href)


class Response:
    def __init__(self, payload=b"abc", status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.payload = payload
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        yield self.payload


def transfer_with_response(monkeypatch, response, source="gfs"):
    transfer = network.Transfer(source, lambda event: None)
    monkeypatch.setattr(transfer.session, "request", lambda *a, **kw: response)
    return transfer


def test_range_is_required_even_if_server_returns_200(monkeypatch):
    response = Response(b"abc", 200)
    transfer = transfer_with_response(monkeypatch, response)
    with pytest.raises(network.ProbeFailure, match="Range"):
        transfer.read("GET", "https://example.org/file", expected_range=(0, 3))
    assert response.closed


def test_range_length_and_response_size_are_checked(monkeypatch):
    response = Response(b"ab", 206, {"Content-Range": "bytes 0-2/300"})
    transfer = transfer_with_response(monkeypatch, response)
    with pytest.raises(network.ProbeFailure, match="不完整"):
        transfer.read("GET", "https://example.org/file", expected_range=(0, 3))
    response = Response(b"12345")
    transfer = transfer_with_response(monkeypatch, response)
    with pytest.raises(network.ProbeFailure, match="上限"):
        transfer.read("GET", "https://example.org/file", limit=4)


def test_api_redirect_is_not_followed_with_token(monkeypatch):
    transfer = transfer_with_response(monkeypatch, Response(status=302, headers={
        "Location": "https://evil.example/token"
    }), source="cams")
    with pytest.raises(network.ProbeFailure, match="Token"):
        transfer.read("GET", network.ADS_URL, headers={"PRIVATE-TOKEN": "test-secret"})
    assert not transfer.session.trust_env


@pytest.mark.parametrize("error,state", [
    (requests.Timeout("https://secret:key@proxy/secret"), "timeout"),
    (requests.exceptions.SSLError("test-secret"), "tls_error"),
    (requests.ConnectionError(socket.gaierror("test-secret")), "dns_error"),
    (requests.ConnectionError("test-secret"), "connection_error"),
    (RuntimeError("test-secret"), "error"),
    (network._http_error(403), "forbidden"),
])
def test_errors_are_classified_without_secret_details(error, state):
    code, message = network._failure(error)
    assert code == state
    assert "secret" not in message


def test_parallel_results_are_ordered_and_failures_independent(monkeypatch):
    monkeypatch.setattr(network, "_gfs", lambda *a: "real GFS sample")
    monkeypatch.setattr(network, "_ec", lambda *a: (_ for _ in ()).throw(requests.Timeout("secret")))
    monkeypatch.setattr(network, "_cams", lambda *a: (_ for _ in ()).throw(
        network.ProbeFailure("not_configured", "未配置")))
    events = []
    report = network.probe_downloads(events.append, now=NOW, ecmwf_source="ecmwf")
    assert [row["source"] for row in report["datasets"]] == ["gfs", "ec", "cams"]
    assert [row["state"] for row in report["datasets"]] == ["passed", "timeout", "not_configured"]
    assert not report["all_passed"]
    assert len(events) == 3 and events[-1]["percent"] == 100
    assert "secret" not in json.dumps(report)


def test_ec_mirror_selection_changes_host_and_base(monkeypatch, grib):
    transfer = FakeTransfer([json.dumps({"_offset": 0, "_length": len(grib)}).encode(), grib])
    assert "AWS" not in network._ec(transfer, NOW, "aws")
    assert transfer.calls[0][1].startswith("https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/")
    transfer = FakeTransfer([json.dumps({"_offset": 0, "_length": len(grib)}).encode(), grib])
    network._ec(transfer, NOW, "google")
    assert transfer.calls[0][1].startswith("https://storage.googleapis.com/ecmwf-open-data/")
    with pytest.raises(network.ProbeFailure, match="EC 下载源"):
        network._ec(FakeTransfer([]), NOW, "unknown")


def test_cancellation_propagates(monkeypatch):
    from firesky.web_server import ForecastCancelled
    monkeypatch.setattr(network, "_gfs", lambda transfer, now: transfer.tick())
    monkeypatch.setattr(network, "_ec", lambda transfer, now: transfer.tick())
    monkeypatch.setattr(network, "_cams", lambda transfer, now: transfer.tick())
    def cancelled(event):
        raise ForecastCancelled("cancel")
    with pytest.raises(ForecastCancelled):
        network.probe_downloads(cancelled, now=NOW)


def test_rpc_probe_needs_no_prediction_session_and_is_single_flight(monkeypatch):
    service = rpc.DesktopService()
    monkeypatch.setattr(network, "probe_downloads",
                        lambda progress, **kwargs: {"datasets": []})
    assert service.dispatch("network_probe", {}) == {"datasets": []}
    assert not service.sessions
    with service.network_probe_lock:
        with pytest.raises(rpc.RequestError, match="正在运行"):
            service.dispatch("network_probe", {})
    assert not service.compute_lock.locked()


def test_rpc_probe_releases_lock_after_failure(monkeypatch):
    service = rpc.DesktopService()
    def fail(progress, **kwargs):
        raise RuntimeError("test failure")
    monkeypatch.setattr(network, "probe_downloads", fail)
    with pytest.raises(RuntimeError):
        service.dispatch("network_probe", {})
    assert not service.network_probe_lock.locked()
