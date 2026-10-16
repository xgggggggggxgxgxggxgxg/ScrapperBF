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


def test_quota_on_first_key_switches_to_second(monkeypatch):
    # Une clé en quota (429) -> bascule IMMÉDIATE sur la clé suivante, sans
    # épuiser les retries sur la clé à sec.
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)
    monkeypatch.setattr(l3.token_tracker, "record", lambda *a, **k: None)
    monkeypatch.setattr(l3.token_tracker, "record_error", lambda *a, **k: None)
    seen = []

    def build(spec, key):
        def fn(p, sc, sy, st):
            seen.append(key)
            if key == "k1":
                raise ProviderError("OpenRouter 429: rate limit exceeded")
            return {"objet": "OK", "_usage": {"prompt": 3, "completion": 2, "total": 5}}
        return fn
    monkeypatch.setattr(l3, "_build_call", build)

    prov = {"name": "or", "keys": ["k1", "k2"], "_spec": {"kind": "openrouter", "model": "m"}}
    res = call_with_rotation("p", {}, max_retries=3, providers=[prov])
    assert res["objet"] == "OK"
    assert seen == ["k1", "k2"]          # k1 essayée 1 fois (quota), puis k2
    assert "_usage" not in res           # usage consommé par le comptage, pas renvoyé


def test_quota_exhausts_all_keys_returns_none(monkeypatch):
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)
    monkeypatch.setattr(l3.token_tracker, "record_error", lambda *a, **k: None)

    def build(spec, key):
        def fn(p, sc, sy, st):
            raise ProviderError("quota exhausted")
        return fn
    monkeypatch.setattr(l3, "_build_call", build)
    prov = {"name": "or", "keys": ["k1", "k2"], "_spec": {"kind": "openrouter", "model": "m"}}
    assert call_with_rotation("p", {}, max_retries=2, providers=[prov]) is None
