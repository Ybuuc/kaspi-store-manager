(() => {
  const message=document.getElementById('catalogLiveMessage');
  const button=document.getElementById('catalogRefresh');
  if(!document.getElementById('productResults')) return;
  let timer, busy=false;
  async function refresh(force=false) {
    clearTimeout(timer);
    if(document.hidden || busy) return;
    busy=true;
    try {
      const version=await fetch('/api/products/catalog-version',{cache:'no-store'});
      if(!version.ok || version.redirected) throw new Error();
      const data=await version.json();
      const current=document.getElementById('productResults');
      if(force || String(data.revision || 0)!==current.dataset.catalogVersion) {
        const selection=window.getSelection();
        if(selection && !selection.isCollapsed) return;
        const response=await fetch(location.href,{cache:'no-store'});
        if(!response.ok || response.redirected) throw new Error();
        const doc=new DOMParser().parseFromString(await response.text(),'text/html');
        const next=doc.getElementById('productResults');
        if(!next) throw new Error();
        const expanded=new Set([...current.querySelectorAll('article')].filter(el=>el.querySelector('details[open]')).map(el=>el.dataset.productId));
        next.querySelectorAll('article').forEach(el=>{if(expanded.has(el.dataset.productId)) el.querySelector('details').open=true;});
        current.replaceWith(next);
        document.dispatchEvent(new Event('catalog:updated'));
        message.textContent=next.dataset.sourceDate?window.KSMi18n.t('Показан XML от ')+next.dataset.sourceDate+'.':window.KSMi18n.t('XML ещё не импортирован.');
      } else if(force) { message.textContent=window.KSMi18n.t('Нового снимка XML пока нет.'); }
    } catch(_) {
      message.textContent=window.KSMi18n.t('Не удалось проверить новые данные. Показан последний сохранённый снимок.');
    } finally {
      busy=false;
      if(!document.hidden) timer=setTimeout(refresh,30000);
    }
  }
  button?.addEventListener('click',()=>refresh(true));
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden) refresh();});
  refresh();
})();
