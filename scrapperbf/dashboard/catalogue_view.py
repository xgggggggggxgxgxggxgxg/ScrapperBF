"""Dashboard de recherche : trouver les deux annonces qui comptent.

Le problème que cette page résout est celui d'origine : un bulletin fait
soixante pages, le lecteur en cherche deux annonces, et les trouver à l'œil
prend une heure. La page est donc organisée autour du filtrage, pas de la
lecture :

* les filtres sont sur **une seule rangée au-dessus** du tableau, et se
  combinent par ET — secteur, région, catégorie, portée, type d'émetteur,
  bulletin, fiabilité minimale, recherche plein texte ;
* les filtres actifs s'affichent en pastilles retirables d'un clic, pour qu'on
  ne cherche jamais pourquoi la liste est vide ;
* le tableau **est** la vue de données (il n'y a pas de graphique à déchiffrer),
  triable sur chaque colonne ;
* la sélection courante s'exporte en CSV, pour continuer dans un tableur.

Choix de couleur assumé : les secteurs ne sont **pas** colorés. Dix-huit
secteurs demanderaient de recycler les teintes, ce qui rend l'identité
illisible ; ils sont donc rendus en pastilles de texte. Les seules couleurs
porteuses de sens sont celles de la fiabilité, et elles accompagnent toujours
le pourcentage chiffré — jamais la couleur seule.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..catalogue import Catalogue

_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marchés publics — recherche d'annonces</title>
<style>
:root {
  --bg:#0f172a; --panel:#1e293b; --panel2:#172033; --line:#334155;
  --ink:#e2e8f0; --ink2:#94a3b8; --ink3:#64748b; --accent:#38bdf8;
  --ok:#34d399; --warn:#fbbf24; --err:#f87171; --crit:#e879f9;
}
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:var(--bg); color:var(--ink); font-size:14px; }
header { padding:16px 22px 12px; border-bottom:1px solid var(--line); background:var(--panel); }
h1 { font-size:1.05rem; color:var(--accent); }
.sub { color:var(--ink2); font-size:.78rem; margin-top:3px; }
.tiles { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
.tile { background:var(--panel2); border:1px solid var(--line); border-radius:8px;
  padding:7px 13px; min-width:92px; }
.tile .v { font-size:1.25rem; font-weight:700; line-height:1.15; }
.tile .k { font-size:.66rem; color:var(--ink2); text-transform:uppercase; letter-spacing:.05em; }
.filters { display:flex; gap:7px; padding:11px 22px; flex-wrap:wrap; align-items:center;
  border-bottom:1px solid var(--line); background:var(--panel2); }
input,select,button { background:var(--bg); color:var(--ink); border:1px solid var(--line);
  border-radius:6px; padding:6px 9px; font-size:.8rem; font-family:inherit; }
input[type=search] { min-width:230px; }
input[type=range] { padding:0; min-width:120px; }
button { cursor:pointer; }
button.primary { background:var(--accent); color:#06283d; border-color:var(--accent); font-weight:700; }
label.inline { font-size:.76rem; color:var(--ink2); display:flex; align-items:center; gap:5px; }
.chips { display:flex; gap:6px; flex-wrap:wrap; padding:8px 22px; min-height:14px;
  border-bottom:1px solid var(--line); align-items:center; }
.chip { background:#1d3a52; color:var(--ink); border:1px solid var(--accent); border-radius:9999px;
  padding:2px 9px; font-size:.72rem; cursor:pointer; }
.chip::after { content:" ✕"; color:var(--ink2); }
.count { font-size:.76rem; color:var(--ink2); }
main { display:flex; align-items:flex-start; }
#tablewrap { flex:1; min-width:0; overflow:auto; max-height:calc(100vh - 215px); }
/* Largeurs fixes : sans elles, la colonne « objet » mange la place et pousse
   la fiabilité hors de l'écran — or c'est la colonne qui décide si l'annonce
   est exploitable. */
table { width:100%; border-collapse:collapse; table-layout:fixed; }
th,td { text-align:left; padding:5px 9px; border-bottom:1px solid var(--line);
  font-size:.78rem; vertical-align:middle; }
/* Une ligne par annonce : on en voit trois fois plus d'un coup d'œil, et le
   texte complet reste disponible dans le panneau de droite. */
td.cut { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
/* L'objet est ce qu'on lit pour reconnaître une annonce : il prend la place
   restante, et la catégorie quitte le tableau — elle reste un filtre et figure
   dans le panneau, alors que tronquée en « Fournitures et… » elle ne distingue
   rien. */
col.c-doc { width:62px; } col.c-pag { width:54px; } col.c-sec { width:168px; }
col.c-reg { width:80px; } col.c-eme { width:126px; }
col.c-obj { width:auto; } col.c-dep { width:104px; } col.c-fia { width:152px; }
th { position:sticky; top:0; background:var(--panel); cursor:pointer; white-space:nowrap;
  font-size:.68rem; text-transform:uppercase; letter-spacing:.05em; color:var(--ink2); }
th.sorted { color:var(--accent); }
tbody tr { cursor:pointer; }
tbody tr:hover { background:var(--panel2); }
tbody tr.sel { background:#1d3a52; }
.pill { display:inline-block; padding:2px 7px; border-radius:9999px; font-size:.68rem;
  background:#0b1220; color:var(--ink2); border:1px solid var(--line); white-space:nowrap; }
.meterwrap { display:flex; align-items:center; gap:7px; }
.meter { height:6px; width:46px; background:#0b1220; border-radius:3px; overflow:hidden;
  flex:none; }
.meter i { display:block; height:100%; border-radius:3px; }
.pct { font-variant-numeric:tabular-nums; font-size:.76rem; white-space:nowrap; }
.meterwrap .pill { padding:1px 5px; font-size:.63rem; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.73rem; color:var(--ink2); }
#panel { width:36%; min-width:330px; flex:none; border-left:1px solid var(--line); background:var(--panel);
  max-height:calc(100vh - 215px); overflow:auto; padding:16px; }
#panel h2 { font-size:.95rem; color:var(--accent); margin-bottom:3px; line-height:1.35; }
.sec { margin-top:15px; }
.sec > h3 { font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; color:var(--ink2);
  margin-bottom:7px; border-bottom:1px solid var(--line); padding-bottom:3px; }
.kv { display:grid; grid-template-columns:148px 1fr; gap:7px; padding:5px 0;
  border-bottom:1px dotted #26344a; }
.kv .k { font-size:.73rem; color:var(--ink2); }
.kv .v { font-size:.79rem; word-break:break-word; }
.src { font-size:.66rem; color:var(--ink3); margin-top:2px; }
.alert { border-left:3px solid var(--line); padding:7px 9px; margin-bottom:7px;
  background:var(--panel2); border-radius:0 6px 6px 0; }
.alert.sev-4 { border-color:var(--crit); } .alert.sev-3 { border-color:var(--err); }
.alert.sev-2 { border-color:var(--warn); } .alert.sev-1 { border-color:var(--ink3); }
.alert .msg { font-size:.77rem; } .alert .hint { font-size:.7rem; color:var(--ink2); margin-top:3px; }
pre.body { background:#0b1220; padding:9px; border-radius:6px; font-size:.71rem; line-height:1.45;
  white-space:pre-wrap; max-height:280px; overflow:auto; color:#cbd5e1; }
.empty { padding:34px; text-align:center; color:var(--ink2); font-size:.82rem; }
</style>
</head>
<body>
<header>
  <h1>Marchés publics du Burkina Faso — recherche d'annonces</h1>
  <div class="sub" id="sub"></div>
  <div class="tiles" id="tiles"></div>
</header>

<div class="filters">
  <input type="search" id="q" placeholder="Rechercher dans l'objet, l'émetteur, le texte…">
  <select id="f-secteur"><option value="">Tous les secteurs</option></select>
  <select id="f-region"><option value="">Toutes les régions</option></select>
  <select id="f-categorie"><option value="">Toutes les catégories</option></select>
  <select id="f-portee"><option value="">Nationales et régionales</option></select>
  <select id="f-emetteur"><option value="">Tous les émetteurs</option></select>
  <select id="f-document"><option value="">Tous les bulletins</option></select>
  <label class="inline">fiabilité ≥ <input type="range" id="f-fiab" min="0" max="100" step="5" value="0">
    <span class="pct" id="f-fiab-v">0 %</span></label>
  <label class="inline"><input type="checkbox" id="f-relire" style="width:auto"> à relire</label>
  <span style="flex:1"></span>
  <button id="csv" class="primary">Exporter la sélection (CSV)</button>
  <button id="reset">Réinitialiser</button>
</div>

<div class="chips" id="chips"><span class="count" id="count"></span></div>

<main>
  <div id="tablewrap">
    <table>
      <colgroup>
        <col class="c-doc"><col class="c-pag"><col class="c-sec"><col class="c-reg">
        <col class="c-eme"><col class="c-obj"><col class="c-dep"><col class="c-fia">
      </colgroup>
      <thead><tr>
        <th data-sort="document">Bulletin</th>
        <th data-sort="pages">Pages</th>
        <th data-sort="secteur">Secteur</th>
        <th data-sort="region">Région</th>
        <th data-sort="emetteur">Émetteur</th>
        <th data-sort="objet">Objet</th>
        <th data-sort="echeance">Dépôt</th>
        <th data-sort="fiabilite">Fiabilité</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
  <div id="panel"><div class="empty">Sélectionnez une annonce pour la consulter.</div></div>
</main>

<script id="catalogue-data" type="application/json">__DATA__</script>
<script>
const CAT = JSON.parse(document.getElementById('catalogue-data').textContent);
const A = CAT.annonces;
let sortKey = 'fiabilite', sortDir = 1, selected = null;

const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const norm = s => String(s == null ? '' : s).normalize('NFD')
  .replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
const pct = v => v == null ? '—' : Math.round(v * 100) + ' %';
// Couleur de STATUT : elle double le pourcentage affiché, elle ne le remplace pas.
const band = v => v == null ? 'var(--ink3)'
  : v >= .85 ? 'var(--ok)' : v >= .75 ? 'var(--warn)' : v >= .5 ? 'var(--err)' : 'var(--crit)';
const F = () => ({
  q: document.getElementById('q').value.trim(),
  secteur: document.getElementById('f-secteur').value,
  region: document.getElementById('f-region').value,
  categorie: document.getElementById('f-categorie').value,
  portee: document.getElementById('f-portee').value,
  emetteur: document.getElementById('f-emetteur').value,
  document: document.getElementById('f-document').value,
  fiab: Number(document.getElementById('f-fiab').value) / 100,
  relire: document.getElementById('f-relire').checked,
});

function options(id, valeurs, formate) {
  const sel = document.getElementById(id);
  const garde = sel.value;
  sel.length = 1;
  valeurs.forEach(([v, n]) => sel.insertAdjacentHTML('beforeend',
    `<option value="${esc(v)}">${esc(formate ? formate(v) : v)} (${n})</option>`));
  sel.value = garde;
}

function tally(cle) {
  const m = new Map();
  A.forEach(a => { const v = cle(a); if (v) m.set(v, (m.get(v) || 0) + 1); });
  return [...m.entries()].sort((x, y) => y[1] - x[1] || String(x[0]).localeCompare(String(y[0])));
}

function header() {
  const s = CAT.stats;
  document.getElementById('sub').textContent =
    `${s.documents.join(' · ')} — généré le ${CAT.generated_at}`;
  const t = [
    ['Annonces', s.annonces, 'var(--ink)'],
    ['Fiabilité moy.', pct(s.fiabilite_moyenne), band(s.fiabilite_moyenne)],
    ['À relire', s.a_relire, s.a_relire ? 'var(--warn)' : 'var(--ok)'],
    ['Secteurs', Object.keys(s.secteurs).length, 'var(--ink)'],
    ['Régions', Object.keys(s.regions).filter(r => r !== '—').length, 'var(--ink)'],
  ];
  document.getElementById('tiles').innerHTML = t.map(([k, v, c]) =>
    `<div class="tile"><div class="v" style="color:${c}">${esc(v)}</div>
     <div class="k">${esc(k)}</div></div>`).join('');

  // Le filtre « secteur » retient aussi les secteurs secondaires : le compte
  // affiché doit les inclure, sinon il annonce 4 et en rend 9.
  const parSecteur = new Map();
  A.forEach(a => [a.sector, ...(a.secondary_sectors || [])].forEach(s => {
    if (s) parSecteur.set(s, (parSecteur.get(s) || 0) + 1);
  }));
  const libelle = v => (A.find(a => a.sector === v) || {}).sector_label || v;
  options('f-secteur',
    [...parSecteur.entries()].sort((x, y) => y[1] - x[1] || libelle(x[0]).localeCompare(libelle(y[0]))),
    libelle);
  options('f-region', tally(a => a.region));
  options('f-categorie', tally(a => a.categorie));
  options('f-portee', tally(a => a.portee));
  options('f-emetteur', tally(a => a.emitter_type),
    v => (A.find(a => a.emitter_type === v) || {}).emitter_label || v);
  options('f-document', tally(a => a.document));
}

function secondaireRetenu(a) {
  // Une annonce peut sortir sous un filtre de secteur SECONDAIRE : le dire
  // évite de se demander ce que fait une annonce « Transport » sous « Santé ».
  const f = document.getElementById('f-secteur').value;
  if (!f || a.sector === f) return null;
  return (a.secondary_sectors || []).includes(f)
    ? (A.find(x => x.sector === f) || {}).sector_label || f : null;
}

function echeance(a) {
  const v = (a.record || {}).date_limite_depot;
  return v == null ? '' : String(v);
}

function visible() {
  const f = F(), q = norm(f.q);
  return A.filter(a => {
    if (f.secteur && a.sector !== f.secteur && !(a.secondary_sectors || []).includes(f.secteur)) return false;
    if (f.region && a.region !== f.region) return false;
    if (f.categorie && a.categorie !== f.categorie) return false;
    if (f.portee && a.portee !== f.portee) return false;
    if (f.emetteur && a.emitter_type !== f.emetteur) return false;
    if (f.document && a.document !== f.document) return false;
    if (f.fiab > 0 && (a.reliability == null || a.reliability < f.fiab)) return false;
    if (f.relire && !a.needs_admin) return false;
    if (q && !norm([a.objet, a.emetteur, a.body, a.id, a.pages.join(' '),
                    JSON.stringify(a.record || {})].join(' ')).includes(q)) return false;
    return true;
  });
}

function chips() {
  const f = F(), zone = document.getElementById('chips');
  const actifs = [];
  const pousse = (id, texte) => actifs.push(`<span class="chip" data-clear="${id}">${esc(texte)}</span>`);
  if (f.q) pousse('q', `« ${f.q} »`);
  if (f.secteur) pousse('f-secteur', document.getElementById('f-secteur').selectedOptions[0].text);
  if (f.region) pousse('f-region', 'Région : ' + f.region);
  if (f.categorie) pousse('f-categorie', f.categorie);
  if (f.portee) pousse('f-portee', f.portee);
  if (f.emetteur) pousse('f-emetteur', document.getElementById('f-emetteur').selectedOptions[0].text);
  if (f.document) pousse('f-document', 'Bulletin ' + f.document);
  if (f.fiab > 0) pousse('f-fiab', 'fiabilité ≥ ' + Math.round(f.fiab * 100) + ' %');
  if (f.relire) pousse('f-relire', 'à relire');
  const n = visible().length;
  zone.innerHTML = actifs.join('') +
    `<span class="count" id="count">${n} annonce${n > 1 ? 's' : ''} sur ${A.length}</span>`;
  zone.querySelectorAll('[data-clear]').forEach(c => c.addEventListener('click', () => {
    const el = document.getElementById(c.dataset.clear);
    if (el.type === 'checkbox') el.checked = false;
    else if (el.type === 'range') { el.value = 0; document.getElementById('f-fiab-v').textContent = '0 %'; }
    else el.value = '';
    render();
  }));
}

function tri(rows) {
  const cle = a => ({
    document: a.document, pages: a.page_debut, secteur: a.sector_label || '',
    region: a.region || '\\uffff', categorie: a.categorie || '', emetteur: a.emetteur || '',
    objet: (a.objet || '').toLowerCase(), echeance: echeance(a),
    fiabilite: a.reliability == null ? -1 : a.reliability,
  })[sortKey];
  return rows.sort((x, y) => { const a = cle(x), b = cle(y);
    return (a < b ? -1 : a > b ? 1 : 0) * sortDir; });
}

function render() {
  chips();
  document.querySelectorAll('th[data-sort]').forEach(th =>
    th.classList.toggle('sorted', th.dataset.sort === sortKey));
  const rows = tri(visible());
  document.getElementById('rows').innerHTML = rows.map(a => `
    <tr data-id="${esc(a.id)}" class="${selected === a.id ? 'sel' : ''}">
      <td class="mono">${esc(a.document)}</td>
      <td class="mono">${esc(a.page_debut === a.page_fin ? a.page_debut : a.page_debut + '–' + a.page_fin)}</td>
      <td class="cut" title="${esc([a.sector_label, ...(a.secondary_sectors || [])].join(' · '))}">${
        secondaireRetenu(a)
          /* Quand c'est un secteur SECONDAIRE qui retient l'annonce, c'est lui
             qu'on montre en premier : placé après le principal, il se faisait
             rogner par les libellés longs, et la ligne semblait hors sujet. */
          ? `<span class="pill" style="border-color:var(--accent);color:var(--accent)">${
              esc(secondaireRetenu(a))}</span> <span class="src">${esc(a.sector_label)}</span>`
          : `<span class="pill">${esc(a.sector_label || '—')}</span>`}</td>
      <td class="cut">${esc(a.region || '—')}</td>
      <td class="cut" title="${esc(a.emetteur || '')}">${esc(a.emetteur || '')}</td>
      <td class="cut" title="${esc(a.objet || '')}">${esc(a.objet || a.preview || '')}</td>
      <td class="cut mono" title="${esc(echeance(a))}">${esc(echeance(a) || '—')}</td>
      <td><div class="meterwrap">
        <span class="meter"><i style="width:${Math.round((a.reliability || 0) * 100)}%;
          background:${band(a.reliability)}"></i></span>
        <span class="pct">${pct(a.reliability)}</span>
        ${a.needs_admin ? '<span class="pill" style="border-color:var(--warn);color:var(--warn)">à relire</span>' : ''}
      </div></td>
    </tr>`).join('') ||
    '<tr><td colspan="8" class="empty">Aucune annonce ne correspond à ces filtres. ' +
    'Retirez une pastille ci-dessus pour élargir.</td></tr>';
  document.querySelectorAll('#rows tr[data-id]').forEach(tr =>
    tr.addEventListener('click', () => { selected = tr.dataset.id; render(); panel(); }));
}

const ORDRE_CHAMPS = ['type_avis','numero_reference','date_limite_depot','date_ouverture_plis',
  'montant_previsionnel_global','garantie_soumission','financement','delai_execution',
  'delai_validite_offres','acquisition_dossier','conditions_eligibilite_generales',
  'renseignements_et_contact','lots','rectificatif_de'];

function valeur(v) {
  if (v == null) return '<span style="color:var(--ink3)">— absent —</span>';
  if (typeof v === 'object') return esc(JSON.stringify(v, null, 1));
  return esc(v);
}

function panel() {
  const a = A.find(x => x.id === selected);
  const el = document.getElementById('panel');
  if (!a) { el.innerHTML = '<div class="empty">Sélectionnez une annonce pour la consulter.</div>'; return; }
  const prov = a.provenance || {}, rec = a.record || {};
  const champs = ORDRE_CHAMPS.filter(c => c in rec || c in prov);
  el.innerHTML = `
    <h2>${esc(a.objet || a.emetteur)}</h2>
    <div class="mono">${esc(a.emetteur)} — ${esc(a.document)} p.${esc(a.page_debut)}${
      a.page_fin !== a.page_debut ? '–' + esc(a.page_fin) : ''}</div>
    <div class="sec"><h3>Classement</h3>
      <div class="kv"><span class="k">secteur</span><span class="v">${esc(a.sector_label)}
        ${(a.secondary_sectors || []).length ? '<span class="src">aussi : ' +
          esc(a.secondary_sectors.join(', ')) + '</span>' : ''}
        <div class="src">${esc((a.sector_evidence || []).join(' · '))}</div></span></div>
      <div class="kv"><span class="k">lieu</span><span class="v">${esc(a.region || '—')}${
        a.commune ? ' / commune de ' + esc(a.commune) : ''}${
        a.province ? ' / province du ' + esc(a.province) : ''}</span></div>
      <div class="kv"><span class="k">portée</span><span class="v">${esc(a.portee)} —
        ${esc(a.emitter_label || '')}</span></div>
      <div class="kv"><span class="k">catégorie / nature</span><span class="v">${esc(a.categorie || '—')}
        — ${esc(a.nature || '')}</span></div>
      <div class="kv"><span class="k">fiabilité</span><span class="v">
        <span class="pct" style="color:${band(a.reliability)}">${pct(a.reliability)}</span>
        <span class="src">couverture des informations essentielles : ${esc(a.coverage_percent)} %</span>
      </span></div>
    </div>
    <div class="sec"><h3>Valeurs clés</h3>
      ${champs.map(c => `<div class="kv"><span class="k">${esc(c)}</span><span class="v">
        ${valeur(rec[c])}
        ${prov[c] ? `<div class="src">source : ${esc(prov[c].source)} · confiance ${
          Math.round((prov[c].confidence || 0) * 100)} %${
          prov[c].evidence ? ' · « ' + esc(String(prov[c].evidence).slice(0, 150)) + ' »' : ''}</div>` : ''}
      </span></div>`).join('') || '<span class="mono">Aucune valeur extraite.</span>'}
    </div>
    <div class="sec"><h3>Alertes (${(a.alerts || []).length})</h3>
      ${(a.alerts || []).map(al => `<div class="alert sev-${al.severity}">
        <div class="msg"><strong>${esc(al.pages_label)}</strong> — ${esc(al.message)}</div>
        ${al.hint ? `<div class="hint">${esc(al.hint)}</div>` : ''}</div>`).join('') ||
        '<span class="mono">Aucune alerte.</span>'}
    </div>
    <div class="sec"><h3>Texte de l'annonce</h3><pre class="body">${esc(a.body || '')}</pre></div>`;
}

function csv() {
  const rows = tri(visible());
  if (!rows.length) { alert('Aucune annonce à exporter.'); return; }
  const colonnes = ['id','document','page_debut','page_fin','sector_label','secondary_sectors',
    'region','commune','categorie','portee','emitter_label','emetteur','objet','nature',
    'date_limite_depot','date_ouverture_plis','montant_previsionnel_global','numero_reference',
    'financement','reliability_percent','coverage_percent','needs_admin'];
  const cellule = v => {
    const s = v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
    return /[",;\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lignes = [colonnes.join(';')];
  rows.forEach(a => lignes.push(colonnes.map(c =>
    cellule(c in a ? a[c] : (a.record || {})[c])).join(';')));
  // Point-virgule et BOM : c'est ce qu'attend un tableur configuré en français.
  const blob = new Blob(['\\ufeff' + lignes.join('\\r\\n')], {type:'text/csv;charset=utf-8'});
  const lien = document.createElement('a');
  lien.href = URL.createObjectURL(blob);
  lien.download = 'annonces-' + (F().secteur || 'selection') + '.csv';
  lien.click(); URL.revokeObjectURL(lien.href);
}

['q','f-secteur','f-region','f-categorie','f-portee','f-emetteur','f-document','f-relire']
  .forEach(id => document.getElementById(id).addEventListener('input', render));
document.getElementById('f-fiab').addEventListener('input', e => {
  document.getElementById('f-fiab-v').textContent = e.target.value + ' %';
  render();
});
document.querySelectorAll('th[data-sort]').forEach(th => th.addEventListener('click', () => {
  if (sortKey === th.dataset.sort) sortDir *= -1; else { sortKey = th.dataset.sort; sortDir = 1; }
  render();
}));
document.getElementById('csv').addEventListener('click', csv);
document.getElementById('reset').addEventListener('click', () => {
  ['q','f-secteur','f-region','f-categorie','f-portee','f-emetteur','f-document']
    .forEach(id => document.getElementById(id).value = '');
  document.getElementById('f-fiab').value = 0;
  document.getElementById('f-fiab-v').textContent = '0 %';
  document.getElementById('f-relire').checked = false;
  render();
});

header(); render();
</script>
</body>
</html>
"""


def render_catalogue(catalogue: Catalogue) -> str:
    """Produit le dashboard HTML autonome du catalogue."""
    data = json.dumps(catalogue.to_dict(), ensure_ascii=False)
    # `</script>` dans une donnée fermerait la balise qui la porte.
    data = data.replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", data)


def write_catalogue(catalogue: Catalogue, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_catalogue(catalogue), encoding="utf-8")
    return path
