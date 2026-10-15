"""Tests hors-ligne de la configuration des providers LLM."""
import os
import config
import layer3_extract_llm as l3


def _clear(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "OPENROUTER_MODELS", "OPENROUTER_XIAOMI_MODEL",
              "OPENROUTER_MISTRAL_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL",
              "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_MODEL"):
        monkeypatch.delenv(k, raising=False)


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
    assert out == {"ok": True}
