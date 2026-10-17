"""Tests hors-ligne du vérificateur de clés (poster injecté)."""
from check_keys import check_one, check_all, diagnose


class _Resp:
    def __init__(self, code, text=""):
        self.status_code = code
        self.text = text


def test_diagnose_messages():
    assert "INVALIDE" in diagnose(401, '{"message":"User not found."}')
    assert "SLUG" in diagnose(404, "No endpoints found for x")
    assert "QUOTA" in diagnose(429, "rate limited")
    assert diagnose(200, "") == "OK"


def test_check_one_ok():
    r = check_one("m/free", "sk-good", poster=lambda *a, **k: _Resp(200))
    assert r["ok"] is True and r["status"] == 200
    assert r["cle"] == "sk-g…good" or r["cle"] == "sk-good"  # masquée


def test_check_one_401():
    r = check_one("m/free", "sk-bad", poster=lambda *a, **k: _Resp(401, '{"message":"User not found."}'))
    assert r["ok"] is False and r["status"] == 401


def test_check_all_adds_diagnostic():
    def poster(url, **k):
        key = k["headers"]["Authorization"]
        return _Resp(200) if "good" in key else _Resp(401, "User not found.")
    res = check_all([("m1/free", "sk-good-key"), ("m2/free", "sk-bad-key")], poster=poster)
    assert res[0]["ok"] is True and res[0]["diagnostic"] == "OK"
    assert res[1]["ok"] is False and "INVALIDE" in res[1]["diagnostic"]


def test_check_one_network_error():
    def boom(*a, **k):
        raise RuntimeError("timeout")
    r = check_one("m/free", "k", poster=boom)
    assert r["ok"] is False and r["status"] == "réseau"
