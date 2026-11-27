(() => {
  const toggle=document.getElementById('menuToggle');
  const panel=document.getElementById('appSidebar');
  const backdrop=document.getElementById('sidebarBackdrop');
  const closeButton=document.getElementById('closeSidebar');
  if(!toggle || !panel) return;
  const isMobile=()=>window.matchMedia('(max-width:1199px)').matches;
  function setOpen(open, focus=true) {
    panel.hidden=!open;
    backdrop.hidden=!open;
    document.body.classList.toggle('nav-open',open);
    toggle.setAttribute('aria-expanded',String(open));
    toggle.setAttribute('aria-label',open?'Закрыть меню':'Открыть меню');
    if(focus) (open?closeButton:toggle).focus();
    if(!isMobile()) { try { localStorage.setItem('ksm.navOpen',String(open)); } catch(_) {} }
  }
  toggle.addEventListener('click',()=>setOpen(panel.hidden));
  closeButton.addEventListener('click',()=>setOpen(false));
  backdrop.addEventListener('click',()=>setOpen(false));
  panel.addEventListener('click',event=>{ if(isMobile() && event.target.closest('a')) setOpen(false,false); });
  document.addEventListener('keydown',event=>{
    if(panel.hidden) return;
    if(event.key==='Escape') { event.preventDefault(); setOpen(false); }
    if(event.key==='Tab' && isMobile()) {
      const items=[...panel.querySelectorAll('a[href],button,input:not([type=hidden])')].filter(el=>!el.disabled);
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey && document.activeElement===first) { event.preventDefault(); last.focus(); }
      else if(!event.shiftKey && document.activeElement===last) { event.preventDefault(); first.focus(); }
    }
  });
  if(!isMobile()) { try { if(localStorage.getItem('ksm.navOpen')==='true') setOpen(true,false); } catch(_) {} }
})();
