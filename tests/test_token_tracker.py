"""Tests hors-ligne du comptage de tokens."""
import importlib
import token_tracker as tt


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "STORE", tmp_path / "usage.json")
    tt.reset()


def test_mask_hides_middle():
    assert tt.mask("sk-abcd1234efgh") == "sk-a…efgh"
    assert tt.mask("short") == "short"
    assert tt.mask("") == "—"


def test_normalize_usage_openai_and_gemini():
    o = tt.normalize_usage("openrouter", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    assert o == {"prompt": 10, "completion": 5, "total": 15}
    g = tt.normalize_usage("gemini", {"promptTokenCount": 8, "candidatesTokenCount": 4})
    assert g == {"prompt": 8, "completion": 4, "total": 12}   # total déduit


def test_record_accumulates(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = {"prompt": 10, "completion": 5, "total": 15}
    tt.record("gemini", "gemini-2.0-flash", "KEYAAAA1111", u)
    tt.record("gemini", "gemini-2.0-flash", "KEYAAAA1111", u)
    snap = tt.snapshot()
    (k, e), = snap["entries"].items()
    assert e["appels"] == 2 and e["total"] == 30
    assert snap["total_tokens"] == 30
    assert "gemini-2.0-flash" in k


def test_record_error_counts(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    tt.record_error("openrouter", "m", "KEYBBBB2222", "429 quota")
    e = list(tt.snapshot()["entries"].values())[0]
    assert e["erreurs"] == 1 and e["appels"] == 0


def test_report_nonempty(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    tt.record("openrouter", "m", "KEYCCCC3333", {"prompt": 1, "completion": 1, "total": 2})
    r = tt.report()
    assert "TOTAL tokens" in r and "openrouter" in r
