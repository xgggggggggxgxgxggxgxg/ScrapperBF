"""
Vérifie la configuration des API LLM et, en option, teste la connexion.

    python check_llm.py          # liste les providers configurés (hors-ligne)
    python check_llm.py --ping   # fait un petit appel réel à chaque provider

« --ping » envoie un mini-prompt et affiche OK / l'erreur par provider. À lancer
chez toi une fois tes clés mises dans .env (nécessite un accès réseau sortant).
"""

import sys
import config
from layer3_extract_llm import build_providers, ProviderError


PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "ville": {"type": "string"}},
    "required": ["ok"],
}
PING_PROMPT = ('Réponds en JSON {"ok": true, "ville": "Ouagadougou"} '
               'exactement, sans rien d\'autre.')


def main(argv):
    print(config.summary())
    if "--ping" not in argv:
        print("\n(Ajoute --ping pour tester réellement la connexion.)")
        return 0

    specs = config.provider_specs()
    if not specs:
        return 1
    print("\nTest de connexion (--ping) :")
    providers = build_providers(specs)
    ok_any = False
    for prov in providers:
        name = prov["name"]
        try:
            res = prov["fn"](PING_PROMPT, PING_SCHEMA, "Tu réponds en JSON strict.", False)
            print(f"  ✅ {name} — réponse reçue ({str(res)[:60]})")
            ok_any = True
        except ProviderError as e:
            print(f"  ❌ {name} — {e}")
        except Exception as e:  # réseau, timeout, JSON…
            print(f"  ⚠️  {name} — {type(e).__name__}: {str(e)[:80]}")
    return 0 if ok_any else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
