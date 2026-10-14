"""Génération du dashboard de vérification humaine.

Un fichier HTML autonome (aucun serveur, aucune dépendance réseau) qui embarque
le rapport d'audit. Il sert trois usages, dans cet ordre de fréquence :

1. **trier** — la table se trie par confiance croissante par défaut : les
   annonces douteuses arrivent en haut, celles qui vont bien ne demandent rien ;
2. **vérifier** — pour une annonce, le panneau affiche les champs *par
   priorité*, le texte source de la page, et les alertes avec leurs références
   de page ;
3. **corriger** — les corrections de champs, les verdicts humains et les
   jugements d'alerte sont saisis dans la page, conservés dans le navigateur,
   puis exportés en JSON et réinjectés via ``scrapperbf feedback import``.

Le travail en cours est gardé en ``localStorage`` : une relecture de bulletin
dure plus longtemps qu'un onglet.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..qa.report import AuditReport

_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Contrôle qualité — __DOC__</title>
<style>
:root {
  --bg:#0f172a; --panel:#1e293b; --panel2:#172033; --line:#334155; --fg:#e2e8f0;
  --muted:#94a3b8; --accent:#38bdf8;
  --ok:#34d399; --warn:#fbbf24; --err:#f87171; --crit:#e879f9;
}
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:var(--bg); color:var(--fg); font-size:14px; }
header { padding:18px 24px; border-bottom:1px solid var(--line); background:var(--panel); }
h1 { font-size:1.1rem; color:var(--accent); }
.sub { color:var(--muted); font-size:.8rem; margin-top:4px; }
.tiles { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
.tile { background:var(--panel2); border:1px solid var(--line); border-radius:8px;
  padding:8px 14px; min-width:104px; }
.tile .v { font-size:1.3rem; font-weight:700; }
.tile .k { font-size:.7rem; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
.bar { display:flex; gap:12px; padding:12px 24px; flex-wrap:wrap; align-items:center;
  border-bottom:1px solid var(--line); background:var(--panel2); }
input,select,button { background:var(--bg); color:var(--fg); border:1px solid var(--line);
  border-radius:6px; padding:7px 10px; font-size:.82rem; font-family:inherit; }
input[type=search] { min-width:260px; }
button { cursor:pointer; }
button.primary { background:var(--accent); color:#06283d; border-color:var(--accent); font-weight:700; }
main { display:flex; align-items:flex-start; gap:0; }
#tablewrap { flex:1; overflow:auto; max-height:calc(100vh - 230px); }
table { width:100%; border-collapse:collapse; }
th,td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); font-size:.82rem;
  vertical-align:top; }
th { position:sticky; top:0; background:var(--panel); cursor:pointer; white-space:nowrap;
  font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
tbody tr { cursor:pointer; }
tbody tr:hover { background:var(--panel2); }
tbody tr.sel { background:#1d3a52; }
.pill { display:inline-block; padding:2px 8px; border-radius:9999px; font-size:.7rem; font-weight:700; }
.v-VALIDE { background:rgba(52,211,153,.18); color:var(--ok); }
.v-A_VERIFIER { background:rgba(251,191,36,.18); color:var(--warn); }
.v-SUSPECT { background:rgba(248,113,113,.18); color:var(--err); }
.v-REJETE { background:rgba(232,121,249,.18); color:var(--crit); }
.meter { height:6px; width:90px; background:#0b1220; border-radius:3px; overflow:hidden; }
.meter i { display:block; height:100%; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.75rem; color:var(--muted); }
#panel { width:46%; min-width:380px; border-left:1px solid var(--line); background:var(--panel);
  max-height:calc(100vh - 230px); overflow:auto; padding:18px; }
#panel h2 { font-size:.98rem; color:var(--accent); margin-bottom:4px; }
.sec { margin-top:18px; }
.sec > h3 { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
  margin-bottom:8px; border-bottom:1px solid var(--line); padding-bottom:4px; }
.field { display:grid; grid-template-columns:150px 1fr; gap:8px; padding:6px 0;
  border-bottom:1px dotted #26344a; }
.field label { font-size:.75rem; color:var(--muted); }
.field .val { font-size:.8rem; word-break:break-word; }
.field input { width:100%; }
.s-ok { color:var(--ok); } .s-suspect { color:var(--warn); }
.s-invalid,.s-ungrounded { color:var(--err); } .s-missing { color:var(--crit); }
.tag { font-size:.65rem; padding:1px 6px; border-radius:4px; background:#0b1220; color:var(--muted); }
.alert { border-left:3px solid var(--line); padding:8px 10px; margin-bottom:8px; background:var(--panel2);
  border-radius:0 6px 6px 0; }
.alert.sev-4 { border-color:var(--crit); } .alert.sev-3 { border-color:var(--err); }
.alert.sev-2 { border-color:var(--warn); } .alert.sev-1 { border-color:var(--muted); }
.alert .msg { font-size:.8rem; } .alert .hint { font-size:.72rem; color:var(--muted); margin-top:4px; }
.alert .acts { margin-top:6px; display:flex; gap:6px; }
.alert button { padding:3px 8px; font-size:.68rem; }
.alert button.on { background:var(--accent); color:#06283d; font-weight:700; }
pre.src { background:#0b1220; padding:10px; border-radius:6px; font-size:.72rem; line-height:1.45;
  white-space:pre-wrap; max-height:300px; overflow:auto; color:#cbd5e1; }
.empty { padding:40px; text-align:center; color:var(--muted); }
.note { width:100%; min-height:60px; background:var(--bg); color:var(--fg); border:1px solid var(--line);
  border-radius:6px; padding:8px; font-family:inherit; font-size:.8rem; }
.docalerts { padding:12px 24px; border-bottom:1px solid var(--line); max-height:190px; overflow:auto; }
</style>
</head>
<body>
<header>
  <h1>Contrôle qualité des annonces — __DOC__</h1>
  <div class="sub" id="sub"></div>
  <div class="tiles" id="tiles"></div>
</header>

<div class="docalerts" id="docalerts"></div>

<div class="bar">
  <input type="search" id="q" placeholder="Rechercher titre, référence, objet, page…">
  <select id="fverdict"><option value="">Tous les verdicts</option></select>
  <select id="fkind"><option value="">Toutes les natures</option></select>
  <select id="fsev">
    <option value="">Toutes les annonces</option>
    <option value="relire">À relire seulement</option>
    <option value="3">Avec erreur ou plus</option>
    <option value="4">Avec alerte critique</option>
  </select>
  <select id="freview">
    <option value="">Relecture : tout</option>
    <option value="todo">Non relues</option>
    <option value="done">Relues</option>
  </select>
  <span class="mono" id="count"></span>
  <span style="flex:1"></span>
  <button id="export" class="primary">Exporter les corrections</button>
  <button id="reset">Vider mes saisies</button>
</div>

<main>
  <div id="tablewrap">
    <table>
      <thead><tr>
        <th data-sort="id">Annonce</th>
        <th data-sort="pages">Pages</th>
        <th data-sort="kind">Nature</th>
        <th data-sort="title">Titre</th>
        <th data-sort="confidence">Confiance</th>
        <th data-sort="alerts">Alertes</th>
        <th data-sort="verdict">Verdict</th>
        <th data-sort="review">Relu</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
  <div id="panel"><div class="empty">Sélectionnez une annonce pour la vérifier.</div></div>
</main>

<script id="report-data" type="application/json">__DATA__</script>
<script>
const REPORT = JSON.parse(document.getElementById('report-data').textContent);
const STORE_KEY = 'scrapperbf-review-' + REPORT.document;
let work = {};
try { work = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (e) { work = {}; }
const save = () => { try { localStorage.setItem(STORE_KEY, JSON.stringify(work)); } catch (e) {} };
const entry = id => (work[id] = work[id] || { corrections: {}, alerts: {}, verdict: '', note: '' });
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pct = v => Math.round(v * 100) + '%';
const meterColor = v => v >= .85 ? 'var(--ok)' : v >= .65 ? 'var(--warn)' : v >= .4 ? 'var(--err)' : 'var(--crit)';
const fmt = v => v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));

let sortKey = 'confidence', sortDir = 1, selected = null;

function header() {
  const s = REPORT.stats;
  document.getElementById('sub').textContent =
    `${REPORT.source} · généré le ${REPORT.generated_at} · ${s.pages} pages`;
  const tiles = [
    ['Annonces', s.annonces, 'var(--fg)'],
    ['À relire', s.a_relire, s.a_relire ? 'var(--warn)' : 'var(--ok)'],
    ['Confiance moy.', pct(s.confiance_moyenne), meterColor(s.confiance_moyenne)],
    ['Critiques', s.alertes.critique, s.alertes.critique ? 'var(--crit)' : 'var(--ok)'],
    ['Erreurs', s.alertes.erreur, s.alertes.erreur ? 'var(--err)' : 'var(--ok)'],
    ['Avertis.', s.alertes.avertissement, 'var(--warn)'],
  ];
  document.getElementById('tiles').innerHTML = tiles.map(([k, v, c]) =>
    `<div class="tile"><div class="v" style="color:${c}">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join('');

  const docAlerts = REPORT.document_alerts || [];
  document.getElementById('docalerts').innerHTML = docAlerts.length
    ? '<h3 style="font-size:.72rem;text-transform:uppercase;color:var(--muted);margin-bottom:8px">'
      + `Problèmes de structure du document (${docAlerts.length})</h3>`
      + docAlerts.map(a => `<div class="alert sev-${a.severity}"><div class="msg">
         <strong>${esc(a.pages_label)}</strong> — ${esc(a.message)}</div>
         <div class="hint">${esc(a.hint)}</div></div>`).join('')
    : '<span class="mono">Aucun problème de structure détecté (pages, doublons, continuité).</span>';

  const uniq = (arr) => [...new Set(arr)].sort();
  const fv = document.getElementById('fverdict'), fk = document.getElementById('fkind');
  uniq(REPORT.notices.map(n => n.score.verdict)).forEach(v =>
    fv.insertAdjacentHTML('beforeend', `<option value="${esc(v)}">${esc(v)}</option>`));
  uniq(REPORT.notices.map(n => n.kind)).forEach(v =>
    fk.insertAdjacentHTML('beforeend', `<option value="${esc(v)}">${esc(v)}</option>`));
}

function maxSeverity(n) {
  return (n.alerts || []).reduce((m, a) => Math.max(m, a.severity), 0);
}

function visible() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const fv = document.getElementById('fverdict').value;
  const fk = document.getElementById('fkind').value;
  const fs = document.getElementById('fsev').value;
  const fr = document.getElementById('freview').value;
  return REPORT.notices.filter(n => {
    if (fv && n.score.verdict !== fv) return false;
    if (fk && n.kind !== fk) return false;
    if (fs === 'relire' && n.score.verdict === 'VALIDE') return false;
    if ((fs === '3' || fs === '4') && maxSeverity(n) < Number(fs)) return false;
    const reviewed = !!(work[n.id] && work[n.id].verdict);
    if (fr === 'todo' && reviewed) return false;
    if (fr === 'done' && !reviewed) return false;
    if (!q) return true;
    const hay = [n.id, n.title, n.kind, n.preview, n.pages.join(' '),
                 JSON.stringify(n.extracted || {})].join(' ').toLowerCase();
    return hay.includes(q);
  });
}

function sortRows(rows) {
  const key = n => ({
    id: n.id, pages: n.page_debut, kind: n.kind, title: (n.title || '').toLowerCase(),
    confidence: n.score.confidence, alerts: -maxSeverity(n) * 100 - (n.alerts || []).length,
    verdict: n.score.verdict, review: (work[n.id] && work[n.id].verdict) || '',
  })[sortKey];
  return rows.sort((a, b) => {
    const x = key(a), y = key(b);
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
  });
}

function renderTable() {
  const rows = sortRows(visible());
  document.getElementById('count').textContent =
    `${rows.length} / ${REPORT.notices.length} annonces affichées`;
  document.getElementById('rows').innerHTML = rows.map(n => {
    const c = n.score.confidence, w = work[n.id] || {};
    const sev = maxSeverity(n);
    return `<tr data-id="${esc(n.id)}" class="${selected === n.id ? 'sel' : ''}">
      <td class="mono">${esc(n.id)}</td>
      <td class="mono">${esc(n.page_debut === n.page_fin ? n.page_debut : n.page_debut + '–' + n.page_fin)}</td>
      <td><span class="tag">${esc(n.kind)}</span></td>
      <td>${esc((n.title || n.preview || '').slice(0, 90))}</td>
      <td><div class="meter"><i style="width:${Math.round(c * 100)}%;background:${meterColor(c)}"></i></div>
          <span class="mono">${pct(c)}</span></td>
      <td class="mono">${(n.alerts || []).length ? (n.alerts.length + (sev >= 3 ? ' ⚠' : '')) : '—'}</td>
      <td><span class="pill v-${esc(n.score.verdict)}">${esc(n.score.verdict_label)}</span></td>
      <td class="mono">${w.verdict ? esc(w.verdict) : '—'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="8" class="empty">Aucune annonce ne correspond aux filtres.</td></tr>';

  document.querySelectorAll('#rows tr[data-id]').forEach(tr =>
    tr.addEventListener('click', () => { selected = tr.dataset.id; renderTable(); renderPanel(); }));
}

function fieldRow(n, name, priority) {
  const check = (n.checks || {})[name] || { status: n.extracted ? 'missing' : 'n/a', reason: '' };
  const w = entry(n.id);
  const machine = fmt((n.extracted || {})[name]);
  const corrected = w.corrections[name];
  return `<div class="field">
    <label>${esc(name)}<br><span class="tag">${esc(priority)}</span>
      <span class="s-${esc(check.status)}">${esc(check.status)}</span></label>
    <div class="val">
      <div>${machine ? esc(machine) : '<span class="s-missing">— absent —</span>'}</div>
      ${check.reason ? `<div class="hint mono">${esc(check.reason)}</div>` : ''}
      <input data-field="${esc(name)}" placeholder="Valeur corrigée (laisser vide si correcte)"
             value="${esc(corrected == null ? '' : corrected)}">
    </div></div>`;
}

function renderPanel() {
  const n = REPORT.notices.find(x => x.id === selected);
  const panel = document.getElementById('panel');
  if (!n) { panel.innerHTML = '<div class="empty">Sélectionnez une annonce pour la vérifier.</div>'; return; }
  const w = entry(n.id), s = n.score;
  const pf = n.priority_fields || {};
  const others = Object.keys(n.checks || {}).filter(f =>
    !(pf.critique || []).includes(f) && !(pf.important || []).includes(f));

  panel.innerHTML = `
    <h2>${esc(n.id)} — ${esc(n.title || 'titre non identifié')}</h2>
    <div class="mono">pages ${esc(n.page_debut)}–${esc(n.page_fin)} ·
      ${esc(n.kind)} (${pct(n.kind_confidence)}) · ${esc(n.category || 'catégorie ?')} ·
      ${esc(n.marche_type || 'type ?')}</div>
    <div class="sec"><h3>Notes</h3>
      <div class="mono">confiance ${pct(s.confidence)} · complétude ${pct(s.completeness)} ·
        exactitude ${pct(s.accuracy)} · structure ${pct(s.structure)}</div>
      ${(s.reasons || []).map(r => `<div class="hint">• ${esc(r)}</div>`).join('')}
    </div>
    ${(pf.critique || []).length ? `<div class="sec"><h3>Champs critiques</h3>
      ${pf.critique.map(f => fieldRow(n, f, 'critique')).join('')}</div>` : ''}
    ${(pf.important || []).length ? `<div class="sec"><h3>Champs importants</h3>
      ${pf.important.map(f => fieldRow(n, f, 'important')).join('')}</div>` : ''}
    ${others.length ? `<div class="sec"><h3>Autres champs</h3>
      ${others.map(f => fieldRow(n, f, 'optionnel')).join('')}</div>` : ''}
    <div class="sec"><h3>Alertes (${(n.alerts || []).length})</h3>
      ${(n.alerts || []).map(a => `<div class="alert sev-${a.severity}">
        <div class="msg"><strong>${esc(a.pages_label)}</strong> — ${esc(a.message)}</div>
        ${a.hint ? `<div class="hint">${esc(a.hint)}</div>` : ''}
        <div class="acts">
          <button data-alert="${esc(a.code)}" data-j="confirme"
            class="${w.alerts[a.code] === 'confirme' ? 'on' : ''}">Problème confirmé</button>
          <button data-alert="${esc(a.code)}" data-j="faux_positif"
            class="${w.alerts[a.code] === 'faux_positif' ? 'on' : ''}">Faux positif</button>
        </div></div>`).join('') || '<span class="mono">Aucune alerte sur cette annonce.</span>'}
    </div>
    <div class="sec"><h3>Mon verdict</h3>
      <select id="hverdict">
        ${['', 'VALIDE', 'A_VERIFIER', 'SUSPECT', 'REJETE'].map(v =>
          `<option value="${v}" ${w.verdict === v ? 'selected' : ''}>${v || '— non relu —'}</option>`).join('')}
      </select>
      <textarea class="note" id="hnote" placeholder="Remarque libre pour l'équipe…">${esc(w.note || '')}</textarea>
    </div>
    <div class="sec"><h3>Texte source (pages ${esc(n.page_debut)}–${esc(n.page_fin)})</h3>
      <pre class="src">${esc(n.flat_text || '')}</pre></div>`;

  panel.querySelectorAll('input[data-field]').forEach(inp =>
    inp.addEventListener('change', () => {
      const v = inp.value.trim();
      if (v) w.corrections[inp.dataset.field] = v; else delete w.corrections[inp.dataset.field];
      save();
    }));
  panel.querySelectorAll('button[data-alert]').forEach(btn =>
    btn.addEventListener('click', () => {
      const code = btn.dataset.alert;
      w.alerts[code] = w.alerts[code] === btn.dataset.j ? undefined : btn.dataset.j;
      if (!w.alerts[code]) delete w.alerts[code];
      save(); renderPanel();
    }));
  panel.querySelector('#hverdict').addEventListener('change', e => {
    w.verdict = e.target.value; save(); renderTable();
  });
  panel.querySelector('#hnote').addEventListener('change', e => { w.note = e.target.value; save(); });
}

function exportReviews() {
  const reviews = REPORT.notices.filter(n => {
    const w = work[n.id];
    return w && (w.verdict || Object.keys(w.corrections).length || Object.keys(w.alerts).length);
  }).map(n => {
    const w = work[n.id];
    return {
      unit_id: n.id, kind: n.kind, pages: n.pages, verdict: w.verdict || '',
      verdict_machine: n.score.verdict, confidence_machine: n.score.confidence,
      note: w.note || '', corrections: w.corrections, extracted: n.extracted || {},
      alerts: Object.entries(w.alerts).map(([code, judgement]) => ({ code, judgement })),
    };
  });
  if (!reviews.length) { alert('Aucune saisie à exporter.'); return; }
  const blob = new Blob([JSON.stringify({ document: REPORT.document, reviews }, null, 2)],
    { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `revue-${REPORT.document}.json`;
  a.click(); URL.revokeObjectURL(a.href);
}

document.getElementById('export').addEventListener('click', exportReviews);
document.getElementById('reset').addEventListener('click', () => {
  if (confirm('Supprimer toutes vos saisies locales pour ce document ?')) {
    work = {}; save(); renderTable(); renderPanel();
  }
});
['q', 'fverdict', 'fkind', 'fsev', 'freview'].forEach(id =>
  document.getElementById(id).addEventListener('input', renderTable));
document.querySelectorAll('th[data-sort]').forEach(th =>
  th.addEventListener('click', () => {
    if (sortKey === th.dataset.sort) sortDir *= -1; else { sortKey = th.dataset.sort; sortDir = 1; }
    renderTable();
  }));

header(); renderTable();
</script>
</body>
</html>
"""


def render_dashboard(report: AuditReport) -> str:
    """Produit le HTML autonome du dashboard."""
    data = json.dumps(report.to_dict(), ensure_ascii=False)
    # `</script>` dans une donnée fermerait la balise qui la porte : seul
    # échappement nécessaire, le reste du JSON étant déjà sûr dans ce contexte.
    data = data.replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", data).replace("__DOC__", report.document)


def write_dashboard(report: AuditReport, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(report), encoding="utf-8")
    return path
