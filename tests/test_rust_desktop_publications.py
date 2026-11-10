"""Rust-owned desktop publication probing and service routing contracts."""

from firesky import desktop_rpc, rust_desktop_data


def test_publication_bridge_validates_native_contract(monkeypatch):
    expected = {
        "gfs": {
            "cycle": "2026-11-10T06:00:00+00:00",
            "label": "2026-11-10 06Z",
            "verified": False,
        },
        "cams": {
            "cycle": "2026-11-10T00:00:00+00:00",
            "label": "2026-11-10 00Z",
            "verified": False,
        },
    }
    monkeypatch.setattr(
        rust_desktop_data,
        "call_engine",
        lambda method, params, **kwargs: {
            "contract": "rust-desktop-publications-v1",
            "runtime_python_used": False,
            "publications": expected,
        },
    )
    assert rust_desktop_data.publications(["gfs", "cams", "gfs"]) == expected


def test_publication_bridge_rejects_unknown_or_malformed_sources(monkeypatch):
    monkeypatch.setattr(
        rust_desktop_data,
        "call_engine",
        lambda *args, **kwargs: {
            "contract": "rust-desktop-publications-v1",
            "runtime_python_used": False,
            "publications": {"gfs": {"cycle": "", "label": "未知", "verified": False}},
        },
    )
    import pytest

    with pytest.raises(RuntimeError, match="source response"):
        rust_desktop_data.publications(["gfs", "cams"])


def test_native_service_publications_do_not_call_python_probe(monkeypatch):
    service = desktop_rpc.DesktopService()
    report = {
        "ready": True,
        "datasets": [
            {"source": "gfs", "ready": True, "request_cycle": "2026-11-10 00Z"},
            {"source": "cams", "ready": True, "request_cycle": "2026-11-10 00Z"},
        ],
    }
    session = desktop_rpc.Session({"model_source": "both"}, "nowcast", report)
    service.sessions["native-publication-test"] = session
    monkeypatch.setattr(service, "_native_sessions_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "_native_session_call",
        lambda action, **kwargs: {"kind": "nowcast"},
    )
    native_calls = []
    monkeypatch.setattr(
        rust_desktop_data,
        "publications",
        lambda sources: native_calls.append(list(sources))
        or {
            source: {
                "cycle": "2026-11-10T00:00:00+00:00",
                "label": "2026-11-10 00Z",
                "verified": False,
            }
            for source in sources
        },
    )
    from firesky import desktop_data

    monkeypatch.setattr(
        desktop_data,
        "published_cycles",
        lambda sources: (_ for _ in ()).throw(
            AssertionError("Python publication probe was reached")
        ),
    )
    result = service.dispatch(
        "publications", {"session": "native-publication-test"}
    )
    assert native_calls == [["gfs", "cams"]]
    assert result["session"] == "native-publication-test"
