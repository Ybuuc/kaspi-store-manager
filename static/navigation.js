(() => {
  const toggle=document.getElementById('menuToggle');
  const panel=document.getElementById('appSidebar');
  const backdrop=document.getElementById('sidebarBackdrop');
  const closeButton=document.getElementById('closeSidebar');
  const resizer=document.getElementById('sidebarResizer');
  if(!toggle || !panel) return;
  const isMobile=()=>window.matchMedia('(max-width:1199px)').matches;
  const minWidth=240,maxWidth=480,defaultWidth=272;
  let width=defaultWidth,drag=null,frame=0;
  function setWidth(value,save=false) {
    width=Math.round(Math.max(minWidth,Math.min(maxWidth,value)));
    document.documentElement.style.setProperty('--sidebar-width',width+'px');
    resizer?.setAttribute('aria-valuenow',String(width));
    if(save) { try { localStorage.setItem('ksm.navWidth',String(width)); } catch(_) {} }
  }
  function finishResize() {
    if(!drag) return;
    cancelAnimationFrame(frame);
    setWidth(drag.next,true);
    const pointer=drag.pointer;
    drag=null;
    document.body.classList.remove('nav-resizing');
    if(resizer.hasPointerCapture(pointer)) resizer.releasePointerCapture(pointer);
  }
  function setOpen(open,focus=true) {
    if(!open) finishResize();
    panel.hidden=!open;
    backdrop.hidden=!open;
    document.body.classList.toggle('nav-open',open);
    toggle.setAttribute('aria-expanded',String(open));
    toggle.setAttribute('aria-label',open?'Закрыть меню':'Открыть меню');
    if(focus) (open?closeButton:toggle).focus();
    if(!isMobile()) { try { localStorage.setItem('ksm.navOpen',String(open)); } catch(_) {} }
  }
  try {
    const saved=localStorage.getItem('ksm.navWidth');
    if(saved && Number.isFinite(Number(saved))) width=Number(saved);
  } catch(_) {}
  setWidth(width);
  resizer?.addEventListener('pointerdown',event=>{
    if(isMobile() || event.button!==0) return;
    event.preventDefault();
    resizer.focus();
    drag={pointer:event.pointerId,start:event.clientX,width,next:width};
    resizer.setPointerCapture(event.pointerId);
    document.body.classList.add('nav-resizing');
  });
  resizer?.addEventListener('pointermove',event=>{
    if(!drag || drag.pointer!==event.pointerId) return;
    drag.next=drag.width+event.clientX-drag.start;
    cancelAnimationFrame(frame);
    frame=requestAnimationFrame(()=>{if(drag) setWidth(drag.next);});
  });
  for(const name of ['pointerup','pointercancel','lostpointercapture']) resizer?.addEventListener(name,finishResize);
  resizer?.addEventListener('dblclick',()=>{finishResize();setWidth(defaultWidth,true);});
  resizer?.addEventListener('keydown',event=>{
    if(isMobile() || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const step=event.shiftKey?32:16;
    const value=event.key==='Home'?minWidth:event.key==='End'?maxWidth:width+(event.key==='ArrowRight'?step:-step);
    setWidth(value,true);
  });
  window.addEventListener('resize',finishResize);
  toggle.addEventListener('click',()=>setOpen(panel.hidden));
  closeButton.addEventListener('click',()=>setOpen(false));
  backdrop.addEventListener('click',()=>setOpen(false));
  panel.addEventListener('click',event=>{if(isMobile() && event.target.closest('a')) setOpen(false,false);});
  document.addEventListener('keydown',event=>{
    if(panel.hidden) return;
    if(event.key==='Escape') {event.preventDefault();setOpen(false);}
    if(event.key==='Tab' && isMobile()) {
      const items=[...panel.querySelectorAll('a[href],button,input:not([type=hidden])')].filter(el=>!el.disabled);
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey && document.activeElement===first) {event.preventDefault();last.focus();}
      else if(!event.shiftKey && document.activeElement===last) {event.preventDefault();first.focus();}
    }
  });
  if(!isMobile()) {try {if(localStorage.getItem('ksm.navOpen')==='true') setOpen(true,false);} catch(_) {}}
})();
