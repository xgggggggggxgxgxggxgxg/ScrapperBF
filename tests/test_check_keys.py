"""Tests hors-ligne du vérificateur de clés (poster injecté)."""
from check_keys import check_one, check_all, diagnose, is_placeholder

GOOD = "sk-or-v1-" + "a" * 40      # clé réaliste (assez longue, sans « ... »)
BAD = "sk-or-v1-" + "b" * 40


class _Resp:
    def __init__(self, code, text=""):
        self.status_code = code
        self.text = text


def test_is_placeholder():
    assert is_placeholder("sk-or-v1-...604") is True
    assert is_placeholder("") is True
    assert is_placeholder("trop court") is True
    assert is_placeholder(GOOD) is False


def test_diagnose_messages():
    assert "INVALIDE" in diagnose(401, '{"message":"User not found."}')
    assert "SLUG" in diagnose(404, "No endpoints found for x")
    assert "QUOTA" in diagnose(429, "rate limited")
    assert "PLACEHOLDER" in diagnose("placeholder", "")
    assert diagnose(200, "") == "OK"


def test_check_one_placeholder_short_circuits():
    called = []
    r = check_one("m/free", "sk-or-v1-...604", poster=lambda *a, **k: called.append(1))
    assert r["status"] == "placeholder" and not called   # aucun appel réseau


def test_check_one_ok():
    r = check_one("m/free", GOOD, poster=lambda *a, **k: _Resp(200))
    assert r["ok"] is True and r["status"] == 200


def test_check_one_401():
    r = check_one("m/free", BAD, poster=lambda *a, **k: _Resp(401, '{"message":"User not found."}'))
    assert r["ok"] is False and r["status"] == 401


def test_check_all_adds_diagnostic():
    def poster(url, **k):
        key = k["headers"]["Authorization"]
        return _Resp(200) if "aaa" in key else _Resp(401, "User not found.")
    res = check_all([("m1/free", GOOD), ("m2/free", BAD)], poster=poster)
    assert res[0]["ok"] is True and res[0]["diagnostic"] == "OK"
    assert res[1]["ok"] is False and "INVALIDE" in res[1]["diagnostic"]


def test_check_one_network_error():
    def boom(*a, **k):
        raise RuntimeError("timeout")
    r = check_one("m/free", GOOD, poster=boom)
    assert r["ok"] is False and r["status"] == "réseau"
