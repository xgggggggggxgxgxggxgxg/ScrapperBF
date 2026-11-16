// Bascule Email / WhatsApp + soumission simulée (front seul, aucun appel réseau)
(function(){
  const tabs=document.querySelectorAll('.auth-tab');
  const panes={email:document.querySelector('[data-mode="email"]'),whatsapp:document.querySelector('[data-mode="whatsapp"]')};
  tabs.forEach(t=>t.addEventListener('click',()=>{
    tabs.forEach(x=>x.setAttribute('aria-selected',x===t));
    const m=t.dataset.mode;
    Object.entries(panes).forEach(([k,p])=>{ if(!p) return; p.hidden=k!==m; p.querySelectorAll('input').forEach(i=>i.disabled=k!==m); });
  }));
  document.querySelectorAll('form[data-demo]').forEach(f=>f.addEventListener('submit',e=>{
    e.preventDefault();
    const btn=f.querySelector('button[type=submit]');
    const label=btn.innerHTML; btn.disabled=true; btn.innerHTML='<span class="spin"></span> Veuillez patienter…';
    setTimeout(()=>{ btn.disabled=false; btn.innerHTML=label; const to=f.dataset.demo; if(to) location.href=to; },700);
  }));
})();
