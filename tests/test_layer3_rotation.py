"""Tests hors-ligne de la rotation de providers (Couche 3)."""
import layer3_extract_llm as l3
from layer3_extract_llm import call_with_rotation, extract_unit, ProviderError


def ok_provider(response, name="ok"):
    def fn(prompt, schema, system_prompt, strict):
        return dict(response)
    return {"name": name, "fn": fn}


def fail_provider(exc, name="fail"):
    def fn(prompt, schema, system_prompt, strict):
        raise exc
    return {"name": name, "fn": fn}


def test_rotation_falls_back_to_next_provider(monkeypatch):
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)
    providers = [
        fail_provider(ProviderError("down"), "p1"),
        ok_provider({"objet": "X"}, "p2"),
    ]
    result = call_with_rotation("prompt", {}, max_retries=1, providers=providers)
    assert result["objet"] == "X"
    assert result["_provider"] == "p2"  # trace du provider ayant répondu


def test_rotation_returns_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)
    providers = [
        fail_provider(ProviderError("a"), "p1"),
        fail_provider(RuntimeError("b"), "p2"),
    ]
    assert call_with_rotation("prompt", {}, max_retries=1, providers=providers) is None


def test_rotation_rejects_non_dict(monkeypatch):
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)

    def bad(prompt, schema, system_prompt, strict):
        return "oops"

    providers = [{"name": "bad", "fn": bad}]
    assert call_with_rotation("prompt", {}, max_retries=1, providers=providers) is None


def test_extract_unit_offline(monkeypatch):
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)
    providers = [ok_provider({"autorite_emettrice": "Ministère", "objet": "Achat"}, "p1")]
    result = extract_unit("texte annonce", "AVIS", max_retries=1, providers=providers)
    assert result["autorite_emettrice"] == "Ministère"
    assert result["_provider"] == "p1"


def test_retries_then_success(monkeypatch):
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky(prompt, schema, system_prompt, strict):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ProviderError("temporaire")
        return {"objet": "ok"}

    providers = [{"name": "flaky", "fn": flaky}]
    result = call_with_rotation("prompt", {}, max_retries=3, providers=providers)
    assert result["objet"] == "ok"
    assert calls["n"] == 2
