"""Tests hors-ligne de la configuration des providers LLM."""
import os
import config
import layer3_extract_llm as l3


def _clear(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEYS", "OPENROUTER_MODEL_KEYS", "OPENROUTER_MODELS",
              "OPENROUTER_XIAOMI_MODEL", "OPENROUTER_MISTRAL_MODEL",
              "GEMINI_API_KEY", "GEMINI_API_KEYS", "GEMINI_MODEL",
              "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_API_KEYS",
              "OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_MODEL"):
        monkeypatch.delenv(k, raising=False)


def test_multiple_keys_per_provider(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEYS", "k1, k2 ,k3")
    monkeypatch.setenv("OPENROUTER_MODELS", "a/b")
    s = config.provider_specs()[0]
    assert s["keys"] == ["k1", "k2", "k3"]   # plusieurs clés pour la rotation
    assert s["key"] == "k1"                   # la 1re (compat)


def test_model_key_map_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_KEYS", "a/b=k_ab, c/d=k_cd")
    m = config.model_key_map()
    assert m["a/b"] == "k_ab" and m["c/d"] == "k_cd"


def test_per_model_key_takes_priority(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEYS", "pool1, pool2")       # pool commun
    monkeypatch.setenv("OPENROUTER_MODELS", "x/y, z/w")
    monkeypatch.setenv("OPENROUTER_MODEL_KEYS", "x/y=dedie_xy")     # clé dédiée pour x/y
    specs = {s["model"]: s for s in config.provider_specs()}
    # x/y : sa clé dédiée en 1re, puis le pool en repli (rotation)
    assert specs["x/y"]["key"] == "dedie_xy"
    assert specs["x/y"]["keys"] == ["dedie_xy", "pool1", "pool2"]
    # z/w : pas de clé dédiée -> pool commun
    assert specs["z/w"]["key"] == "pool1"


def test_openrouter_active_with_only_model_keys(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_MODELS", "m/1")
    monkeypatch.setenv("OPENROUTER_MODEL_KEYS", "m/1=kperso")       # aucune clé pool
    specs = config.provider_specs()
    assert len(specs) == 1 and specs[0]["key"] == "kperso"


def test_keys_plural_and_singular_merge_dedup(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEYS", "g1,g2")
    monkeypatch.setenv("GEMINI_API_KEY", "g2")   # doublon -> ignoré
    s = config.provider_specs()[0]
    assert s["keys"] == ["g1", "g2"]


def test_no_provider_when_unconfigured(monkeypatch):
    _clear(monkeypatch)
    assert config.provider_specs() == []
    assert config.is_configured() is False
    assert "Aucun provider" in config.summary()


def test_openrouter_multiple_models(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_MODELS", "a/b, c/d")
    specs = config.provider_specs()
    assert [s["model"] for s in specs] == ["a/b", "c/d"]
    assert all(s["kind"] == "openrouter" and s["key"] == "k" for s in specs)


def test_gemini_and_compat(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "llama3")
    kinds = [s["kind"] for s in config.provider_specs()]
    assert kinds == ["gemini", "openai_compat"]


def test_load_dotenv_tmp(tmp_path, monkeypatch):
    monkeypatch.delenv("FOO_KEY", raising=False)
    monkeypatch.delenv("BAZ_KEY", raising=False)
    p = tmp_path / ".env"
    p.write_text('FOO_KEY=bar\n# un commentaire\nBAZ_KEY="q q"\n\n', encoding="utf-8")
    config.load_dotenv(p)
    assert os.environ["FOO_KEY"] == "bar"
    assert os.environ["BAZ_KEY"] == "q q"


def test_build_providers_from_specs():
    specs = [
        {"name": "or", "kind": "openrouter", "key": "k", "model": "m"},
        {"name": "gem", "kind": "gemini", "key": "g", "model": "gemini-2.0-flash"},
        {"name": "loc", "kind": "openai_compat", "key": "x", "model": "llama3",
         "base_url": "http://localhost:11434/v1"},
    ]
    provs = l3.build_providers(specs)
    assert [p["name"] for p in provs] == ["or", "gem", "loc"]
    assert all(callable(p["fn"]) for p in provs)


def test_openai_compatible_posts_to_chat_completions(monkeypatch):
    captured = {}

    class Resp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        return Resp()

    monkeypatch.setattr(l3.requests, "post", fake_post)
    out = l3.call_openai_compatible("prompt", {}, "key", "http://localhost:11434/v1", "llama3")
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["json"]["model"] == "llama3"
    assert out["ok"] is True
    assert "_usage" in out   # usage (tokens) attaché pour le comptage


def test_loads_lenient_strips_fences():
    assert l3._loads_lenient('{"a": 1}') == {"a": 1}
    assert l3._loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}
    assert l3._loads_lenient('Voici le JSON : {"a": 1} merci') == {"a": 1}


def test_openrouter_falls_back_when_format_rejected(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, code, body): self.status_code = code; self._b = body
        def json(self): return self._b
        @property
        def text(self): return str(self._b)

    def fake_post(url, **kw):
        calls.append(kw["json"].get("response_format"))
        # 1er appel (json_schema) -> 400 rejet de format ; 2e (json_object) -> 200
        if len(calls) == 1:
            return Resp(400, "response_format json_schema not supported")
        return Resp(200, {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}})

    monkeypatch.setattr(l3.requests, "post", fake_post)
    out = l3.call_openrouter("p", {}, "k", "m/free")
    assert out["ok"] is True
    assert len(calls) == 2                       # a basculé json_schema -> json_object
    assert calls[0]["type"] == "json_schema" and calls[1]["type"] == "json_object"


def test_openrouter_stops_on_auth_error(monkeypatch):
    class Resp:
        status_code = 401
        def json(self): return {}
        text = "401 No auth credentials found"
    monkeypatch.setattr(l3.requests, "post", lambda url, **kw: Resp())
    import pytest
    with pytest.raises(l3.ProviderError) as e:
        l3.call_openrouter("p", {}, "badkey", "m/free")
    assert "401" in str(e.value)                 # pas de retry inutile sur une erreur d'auth
