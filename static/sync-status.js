(() => {
  const xml=document.getElementById('syncXmlChip'),api=document.getElementById('syncApiChip');
  const notice=document.getElementById('syncNotice'),details=document.getElementById('syncDetails');
  if(!xml) return;
  let timer,controller,generation=0;
  const labels={imported:'обновлён',unchanged:'без изменений',failed:'ошибка обновления',not_configured:'не настроен',queued:'в очереди',running:'обновление…',available:'доступен, TLS проверен',not_checked:'не проверялся',invalid_url:'неверный URL',tls_error:'ошибка TLS',unavailable:'недоступен',response_error:'ошибка ответа',disabled:'выключен',waiting:'ожидает проверки',needs_login:'нужен вход',exported:'получен из кабинета',wrong_merchant:'другой магазин',stale:'устаревший файл',success:'завершён',partial:'есть предупреждения',interrupted:'прерван',timeout:'превышено время ожидания',network_error:'ошибка сети',rate_limited:'лимит экспорта Kaspi'};
  const tone=value=>['imported','unchanged','exported','success','available'].includes(value)?'good':['failed','tls_error','unavailable','wrong_merchant','invalid_url','timeout','network_error'].includes(value)?'bad':['partial','needs_login','stale','interrupted','rate_limited'].includes(value)?'warn':['queued','running'].includes(value)?'busy':'';
  const label=value=>labels[value] || value || 'нет данных';
  function date(value) {
    if(!value) return 'не выполнено';
    const parsed=new Date(/[zZ]|[+-]\d{2}:\d{2}$/.test(value)?value:value+'Z');
    return Number.isNaN(parsed.getTime())?'не выполнено':new Intl.DateTimeFormat('ru-RU',{timeZone:'Asia/Qyzylorda',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(parsed);
  }
  function chip(el,title,state) { el.textContent=title+': '+label(state);el.className='status-chip '+tone(state); }
  async function refresh() {
    const current=++generation;
    clearTimeout(timer);controller?.abort();controller=new AbortController();
    try {
      const response=await fetch('/api/kaspi/status',{signal:controller.signal,cache:'no-store'});
      if(!response.ok || response.redirected) throw new Error();
      const data=await response.json(),auto=data.automatic,source=auto.source || {};
      const state=!auto.enabled?'disabled':source.state==='running'?'running':auto.catalog_status;
      chip(xml,'XML',source.state==='needs_login'?'needs_login':state);chip(api,'Заказы API',auto.orders_status);
      const failed=['failed','needs_login','wrong_merchant','stale','timeout','network_error','rate_limited','response_error'].includes(source.state) || auto.catalog_status==='failed';
      notice.textContent=(failed?'Обновление XML не завершено. Показан сохранённый снимок':'Снимок XML')+' от '+(auto.catalog_source_date || 'не получен')+'. Следующая проверка: '+date(auto.next_attempt_at)+'.';
      const rows=[['Автообновление',auto.enabled?'Каждые '+auto.interval_seconds+' секунд':'Выключено'],['Источник XML',label(source.state)+' · '+(source.message || '')],['HTTPS — последняя проверка',label(auto.https_status)],['XML — импорт',label(auto.catalog_status)+' · '+(auto.catalog_message || '')],['Позиций в XML',auto.catalog_offers ?? 0],['Дата внутри XML',auto.catalog_source_date || 'не получен'],['Последний экспорт из Kaspi',date(source.exported_at)],['Последний импорт',date(auto.catalog_imported_at)],['Успешная HTTPS-проверка',date(auto.catalog_checked_at)],['Последняя попытка',date(auto.last_attempt_at)],['Следующая попытка',date(auto.next_attempt_at)],['Заказы API',label(auto.orders_status)+' · '+(auto.orders_message || '')],['Часовой пояс времени проверок','Кызылорда · UTC+5; дата XML показана как в файле']];
      if(auto.startup_error) rows.push(['Ошибка запуска',auto.startup_error]);
      details.replaceChildren(...rows.flatMap(([title,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=title;dd.textContent=String(value);return [dt,dd];}));
    } catch(error) { if(error.name!=='AbortError') { chip(xml,'XML','failed');notice.textContent='Не удалось проверить синхронизацию. Сохранённые данные доступны.'; } }
    finally { if(current===generation && !document.hidden) timer=setTimeout(refresh,15000); }
  }
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(document.hidden) controller?.abort();else refresh();});
  refresh();
})();
