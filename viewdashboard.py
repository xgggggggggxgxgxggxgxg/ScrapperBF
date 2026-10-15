# -*- coding: utf-8 -*-
import json
import webbrowser
from pathlib import Path

def launch_ui(json_path: Path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = []
    
    for sec_name, label, color in [("AVIS", "Avis", "#34d399")]:
        for idx, u in enumerate(data.get(sec_name, [])):
            titre = u.get("entite_heritee")
            if not titre and u.get("blocks"):
                titre = next((b["text"] for b in u["blocks"] if b.get("type") == "title"), "Marché Public")
            elif not titre:
                titre = "Marché Public"
            
            ref = u.get("reference", "Réf non spécifiée")
            content = u.get("flat_text", "").replace("\n", "<br>")

            pages_str = f"Pages {u.get('page_debut')} - {u.get('page_fin')}"
            if sec_name == "AVIS":
                # Clés telles qu'écrites par la segmentation ; les anciens noms
                # (`avis_category`/`avis_type`) n'étaient jamais produits, donc
                # l'affichage retombait toujours sur les valeurs par défaut.
                cat = u.get("category") or u.get("categorie") or "MINISTERES"
                typ = u.get("marche_type") or u.get("type_marche") or "Fournitures et Services courants"
                pages_str += f" | {cat} | {typ}"

            items.append({
                "id": f"{sec_name}_{idx}",
                "type": label,
                "badgeColor": color,
                "titre": titre,
                "reference": ref,
                "pages": pages_str,
                "content": content
            })

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Explorateur des Marchés Publics - Avis</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        body {{ background: #0f172a; color: #e2e8f0; display: flex; height: 100vh; overflow: hidden; }}
        #sidebar {{ width: 420px; background: #1e293b; border-right: 1px solid #334155; display: flex; flex-direction: column; }}
        .header {{ padding: 20px; border-bottom: 1px solid #334155; }}
        .header h1 {{ font-size: 1.15rem; color: #38bdf8; font-weight: 700; }}
        .stats {{ display: flex; gap: 10px; margin-top: 10px; }}
        .badge {{ padding: 4px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }}
        .search-box {{ padding: 12px 20px; }}
        .search-box input {{ width: 100%; padding: 10px; border-radius: 8px; border: 1px solid #475569; background: #0f172a; color: #fff; outline: none; }}
        #list {{ overflow-y: auto; flex: 1; padding: 10px; }}
        .item-card {{ padding: 14px; border-radius: 8px; background: #0f172a; margin-bottom: 8px; cursor: pointer; border: 1px solid transparent; transition: all 0.2s; }}
        .item-card:hover {{ border-color: #38bdf8; }}
        .item-card.active {{ border-color: #38bdf8; background: #1e293b; }}
        .item-card h3 {{ font-size: 0.88rem; margin-bottom: 6px; color: #f8fafc; line-height: 1.35; }}
        .item-card .meta {{ font-size: 0.75rem; color: #94a3b8; display: flex; justify-content: space-between; }}
        #main {{ flex: 1; padding: 40px; overflow-y: auto; display: flex; justify-content: center; }}
        .content-card {{ max-width: 950px; width: 100%; background: #1e293b; padding: 30px; border-radius: 12px; border: 1px solid #334155; }}
        .content-card h2 {{ color: #38bdf8; margin-bottom: 15px; border-bottom: 1px solid #334155; padding-bottom: 10px; font-size: 1.2rem; }}
        .detail-box {{ white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; background: #0f172a; padding: 20px; border-radius: 8px; border: 1px solid #334155; font-size: 0.85rem; line-height: 1.6; color: #cbd5e1; }}
    </style>
</head>
<body>
    <div id="sidebar">
        <div class="header">
            <h1>Appels d'offres (Avis)</h1>
            <div class="stats">
                <span class="badge" style="background:#047857; color:#d1fae5;">{len(data.get('AVIS', []))} Avis</span>
            </div>
        </div>
        <div class="search-box">
            <input type="text" id="search" placeholder="Rechercher entité, réf, mot-clé...">
        </div>
        <div id="list"></div>
    </div>
    <div id="main">
        <div class="content-card" id="detail">
            <h2>Sélectionnez une entrée</h2>
        </div>
    </div>

    <script>
        const data = {json.dumps(items, ensure_ascii=False)};
        const listEl = document.getElementById('list');
        const detailEl = document.getElementById('detail');
        const searchEl = document.getElementById('search');

        function renderList(filter = '') {{
            listEl.innerHTML = '';
            const filtered = data.filter(d => 
                d.titre.toLowerCase().includes(filter.toLowerCase()) || 
                d.reference.toLowerCase().includes(filter.toLowerCase()) ||
                d.content.toLowerCase().includes(filter.toLowerCase())
            );

            filtered.forEach((item, index) => {{
                const el = document.createElement('div');
                el.className = 'item-card';
                el.innerHTML = `
                    <div class="meta" style="margin-bottom: 4px;">
                        <span style="color: ${{item.badgeColor}}; font-weight: 600;">${{item.type}}</span>
                        <span>${{item.pages}}</span>
                    </div>
                    <h3>${{item.titre}}</h3>
                    <div class="meta"><span style="color:#38bdf8;">${{item.reference}}</span></div>
                `;
                el.onclick = () => {{
                    document.querySelectorAll('.item-card').forEach(c => c.classList.remove('active'));
                    el.classList.add('active');
                    showDetail(item);
                }};
                listEl.appendChild(el);
                if(index === 0 && !filter) el.click();
            }});
        }}

        function showDetail(item) {{
            detailEl.innerHTML = `
                <div class="meta" style="margin-bottom: 8px; font-size: 0.85rem; color: #94a3b8;">${{item.type}} &bull; ${{item.pages}}</div>
                <h2>${{item.titre}}</h2>
                <div style="margin-bottom: 15px; color: #38bdf8; font-weight: 600; font-size: 0.95rem;">${{item.reference}}</div>
                <div class="detail-box">${{item.content}}</div>
            `;
        }}

        searchEl.oninput = (e) => renderList(e.target.value);
        renderList();
    </script>
</body>
</html>"""

    out_html = json_path.parent / "dashboard.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"Tableau de bord actualise -> {out_html}")
    webbrowser.open(str(out_html.resolve()))

if __name__ == "__main__":
    base_dir = Path(__file__).parent
    launch_ui(base_dir / "layer2_output.json")