// Shell applicatif commun : sidebar, topbar, nav mobile, thème, palette de commandes.
(function(){
  var D = window.DATA;
  var page = (document.getElementById('app')||{}).dataset ? document.getElementById('app').dataset.page : '';
  var NAV = [
    {href:'dashboard.html', key:'dashboard', icon:'layout-dashboard', label:'Tableau de bord'},
    {href:'markets.html', key:'markets', icon:'briefcase-business', label:'Marchés'},
    {href:'companies.html', key:'companies', icon:'building2', label:'Entreprises'},
    {href:'authorities.html', key:'authorities', icon:'landmark', label:'Autorités'},
    {href:'experts.html', key:'experts', icon:'user-check', label:'Experts agréés'},
    {href:'directory.html', key:'directory', icon:'archive', label:'Archives 2022', tag:'ENT'},
    {sep:true},
    {href:'settings.html', key:'settings', icon:'settings', label:'Paramètres'},
    {href:'capability-profile.html', key:'profile', icon:'clipboard-check', label:'Profil de candidature'},
    {href:'support.html', key:'support', icon:'circle-help', label:'Support'}
  ];
  var MOBILE = [
    {href:'dashboard.html', key:'dashboard', icon:'house', label:'Accueil'},
    {href:'markets.html', key:'markets', icon:'briefcase-business', label:'Marchés'},
    {href:'recommendations.html', key:'recommendations', icon:'sparkles', label:'Pour vous'},
    {href:'applications.html', key:'applications', icon:'folder-lock', label:'Candidatures'},
    {href:'assistant.html', key:'assistant', icon:'bot', label:'Assistant'}
  ];

  function el(html){ var t=document.createElement('template'); t.innerHTML=html.trim(); return t.content.firstChild; }

  var navHtml = NAV.map(function(n){
    if(n.sep) return '<div class="nav-separator"></div>';
    var active = n.key===page ? ' active':'';
    return '<a class="nav-link'+active+'" href="'+n.href+'"'+(active?' aria-current="page"':'')+'><i data-icon="'+n.icon+'" data-size="18"></i><span>'+n.label+'</span>'+(n.tag?'<em class="nav-tag">'+n.tag+'</em>':'')+'</a>';
  }).join('');

  var sidebar = el(
    '<aside class="sidebar" id="app-sidebar" aria-label="Menu">'
    +'<a class="sidebar-brand" href="dashboard.html"><img class="sidebar-brand-logo" src="assets/logo.svg" alt=""><div class="sidebar-brand-text"><strong>VEILLE BF</strong><span>Marchés publics du Burkina Faso</span></div></a>'
    +'<nav class="sidebar-nav">'+navHtml+'</nav>'
    +'<a class="sidebar-upsell" href="upgrade.html"><small>COMPTE '+D.user.plan.toUpperCase()+'</small><strong>✦ Passer à un plan payant</strong><span>Orange Money · Moov · Coris</span></a>'
    +'<div class="sidebar-footer"><div class="sidebar-footer-brand"><img class="mini-logo" src="assets/logo.svg" alt=""><div><strong style="display:block;font-size:12px;color:var(--ink)">Marchés publics</strong><span style="font-size:10px">Burkina Faso</span></div></div>'
    +'<button class="icon-text" type="button" data-toast="Déconnexion simulée (démo front)"><i data-icon="log-out" data-size="15"></i> Déconnexion</button></div>'
    +'</aside>'
  );

  var topbar = el(
    '<header class="topbar">'
    +'<div class="topbar-left">'
    +'<button class="menu-toggle" aria-label="Ouvrir le menu" type="button"><i data-icon="menu" data-size="22"></i></button>'
    +'<button class="kbar-trigger" type="button" id="kbar-open"><i data-icon="search" data-size="16"></i> Rechercher un marché… <kbd>Ctrl K</kbd></button>'
    +'</div>'
    +'<button class="icon-btn" id="theme-toggle" title="Changer de thème" type="button"><i data-icon="moon" data-size="19"></i></button>'
    +'<a class="icon-btn" href="alerts.html" title="Notifications"><i data-icon="bell" data-size="20"></i><span class="dot"></span></a>'
    +'<a class="user-menu" href="settings.html"><div class="user-avatar">'+D.user.initials+'</div><div class="user-info"><strong>'+D.user.name+'</strong><span>'+D.user.role+'</span></div></a>'
    +'</header>'
  );

  var overlay = el('<div class="sidebar-overlay" aria-hidden="true"></div>');

  var mobileNav = el('<nav class="mobile-bottom-nav" aria-label="Navigation mobile">'
    + MOBILE.map(function(n){ var a=n.key===page?' active':''; return '<a class="'+a+'" href="'+n.href+'"'+(a?' aria-current="page"':'')+'><i data-icon="'+n.icon+'" data-size="20"></i><span>'+n.label+'</span></a>'; }).join('')
    + '</nav>');

  // ---- Montage : enveloppe le <main> dans la grille shell ----
  var main = document.querySelector('main.main') || document.querySelector('main');
  var shell = el('<div class="shell"></div>');
  main.parentNode.insertBefore(shell, main);
  shell.appendChild(sidebar); shell.appendChild(topbar); shell.appendChild(main);
  document.body.appendChild(overlay); document.body.appendChild(mobileNav);

  // ---- Thème ----
  try{ var saved=localStorage.getItem('veillebf-theme'); if(saved) document.documentElement.setAttribute('data-theme',saved); }catch(e){}
  function syncThemeIcon(){ var dark=document.documentElement.getAttribute('data-theme')==='dark'; var b=document.getElementById('theme-toggle'); if(b) b.innerHTML=''; var i=document.createElement('i'); i.setAttribute('data-icon',dark?'sun':'moon'); i.setAttribute('data-size','19'); if(b){b.appendChild(i); window.renderIcons(b);} }

  window.renderIcons();
  syncThemeIcon();

  document.getElementById('theme-toggle').addEventListener('click',function(){
    var dark=document.documentElement.getAttribute('data-theme')==='dark';
    var next=dark?'light':'dark';
    document.documentElement.setAttribute('data-theme',next);
    try{ localStorage.setItem('veillebf-theme',next); }catch(e){}
    syncThemeIcon();
  });

  // ---- Sidebar mobile ----
  function closeSidebar(){ sidebar.classList.remove('open'); overlay.classList.remove('open'); }
  document.querySelector('.menu-toggle').addEventListener('click',function(){ sidebar.classList.toggle('open'); overlay.classList.toggle('open'); });
  overlay.addEventListener('click', closeSidebar);

  // ---- Toasts ----
  var toastEl;
  window.toast=function(msg){ if(!toastEl){ toastEl=el('<div class="toast"></div>'); document.body.appendChild(toastEl); } toastEl.textContent=msg; toastEl.classList.add('show'); clearTimeout(toastEl._t); toastEl._t=setTimeout(function(){ toastEl.classList.remove('show'); },2200); };
  document.body.addEventListener('click',function(e){ var t=e.target.closest('[data-toast]'); if(t){ e.preventDefault(); window.toast(t.getAttribute('data-toast')); } });

  // ---- Palette de commandes (Ctrl/Cmd + K) — touche d'originalité ----
  var kb = el(
    '<div class="kbar-overlay" id="kbar"><div class="kbar">'
    +'<div class="kbar-input"><i data-icon="search" data-size="18"></i><input id="kbar-q" placeholder="Rechercher un marché, une autorité, une page…" autocomplete="off"></div>'
    +'<div class="kbar-results" id="kbar-res"></div>'
    +'<div class="kbar-hint"><span>↑↓ naviguer</span><span>↵ ouvrir</span><span>Échap fermer</span></div>'
    +'</div></div>'
  );
  document.body.appendChild(kb);
  window.renderIcons(kb);
  var qEl=kb.querySelector('#kbar-q'), resEl=kb.querySelector('#kbar-res'), sel=0, items=[];

  function buildItems(q){
    q=(q||'').toLowerCase().trim();
    var out=[];
    NAV.concat([{href:'recommendations.html',label:'Pour vous',icon:'sparkles'},{href:'upgrade.html',label:'Passer à un plan payant',icon:'zap'}]).forEach(function(n){
      if(n.sep) return; if(!q||n.label.toLowerCase().indexOf(q)>-1) out.push({href:n.href,title:n.label,sub:'Page',icon:n.icon||'file-text'});
    });
    D.markets.forEach(function(m){
      if(!q || m.title.toLowerCase().indexOf(q)>-1 || m.authority.toLowerCase().indexOf(q)>-1 || String(m.ref).toLowerCase().indexOf(q)>-1)
        out.push({href:'market.html?id='+m.id,title:m.title,sub:m.authority+' · '+m.typeLabel,icon:'file-text'});
    });
    return out.slice(0,12);
  }
  function renderKbar(){
    items=buildItems(qEl.value); sel=0;
    if(!items.length){ resEl.innerHTML='<div class="kbar-empty">Aucun résultat.</div>'; return; }
    resEl.innerHTML=items.map(function(it,i){ return '<a class="kbar-item'+(i===0?' sel':'')+'" href="'+it.href+'"><i data-icon="'+it.icon+'" data-size="16"></i><div><div>'+it.title.slice(0,70)+'</div><div class="ki-sub">'+it.sub+'</div></div></a>'; }).join('');
    window.renderIcons(resEl);
  }
  function openKbar(){ kb.classList.add('open'); qEl.value=''; renderKbar(); setTimeout(function(){qEl.focus();},30); }
  function closeKbar(){ kb.classList.remove('open'); }
  function moveSel(d){ var links=resEl.querySelectorAll('.kbar-item'); if(!links.length) return; links[sel]&&links[sel].classList.remove('sel'); sel=(sel+d+links.length)%links.length; links[sel].classList.add('sel'); links[sel].scrollIntoView({block:'nearest'}); }

  var openBtn=document.getElementById('kbar-open'); if(openBtn) openBtn.addEventListener('click',openKbar);
  qEl.addEventListener('input',renderKbar);
  kb.addEventListener('click',function(e){ if(e.target===kb) closeKbar(); });
  document.addEventListener('keydown',function(e){
    if((e.ctrlKey||e.metaKey)&&(e.key==='k'||e.key==='K')){ e.preventDefault(); kb.classList.contains('open')?closeKbar():openKbar(); return; }
    if(!kb.classList.contains('open')) return;
    if(e.key==='Escape') closeKbar();
    else if(e.key==='ArrowDown'){ e.preventDefault(); moveSel(1); }
    else if(e.key==='ArrowUp'){ e.preventDefault(); moveSel(-1); }
    else if(e.key==='Enter'){ var links=resEl.querySelectorAll('.kbar-item'); if(links[sel]) location.href=links[sel].getAttribute('href'); }
  });

  // re-render des icônes posées par le contenu de page
  window.renderIcons();
})();
