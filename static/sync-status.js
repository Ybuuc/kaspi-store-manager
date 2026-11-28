(() => {
  const xml=document.getElementById('syncXmlChip'),api=document.getElementById('syncApiChip');
  const notice=document.getElementById('syncNotice'),details=document.getElementById('syncDetails');
  const errors=document.getElementById('syncErrorDetails');
  if(!xml) return;
  let timer,controller,generation=0,channels;
  const labels={imported:'успешно',unchanged:'без изменений',failed:'ошибка',not_configured:'не настроено',queued:'в очереди',running:'выполняется',disabled:'отключено',waiting:'ожидание',needs_login:'нужна авторизация',success:'успешно',partial:'частично успешно',interrupted:'прервано',temporary_error:'временная ошибка',rate_limited:'лимит Kaspi (429)'};
  const label=value=>labels[value]||'ожидание';
  function date(value) {
    if(!value) return 'не выполнено';
    const parsed=new Date(/[zZ]|[+-]\d{2}:\d{2}$/.test(value)?value:value+'Z');
    return Number.isNaN(parsed.getTime())?'не выполнено':new Intl.DateTimeFormat('ru-RU',{timeZone:'Asia/Qyzylorda',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(parsed);
  }
  function countdown(value) {
    const seconds=value?Math.max(0,Math.ceil((new Date(value)-Date.now())/1000)):0;
    return seconds?Math.floor(seconds/60)+' мин '+seconds%60+' сек':'по расписанию';
  }
  function chip(element,title,channel) {
    const state=channel.state;
    element.textContent=title+': '+label(state);
    element.className='status-chip '+(['success','imported','unchanged'].includes(state)?'good':['partial','rate_limited','needs_login'].includes(state)?'warn':['failed','temporary_error'].includes(state)?'bad':['running','queued'].includes(state)?'busy':'');
  }
  function render(auto,run) {
    channels=auto.channels||{};
    const rows=[];
    for(const [source,title] of [['api','API заказов'],['xml','XML-каталог']]) {
      const channel=channels[source]||{state:'waiting'};
      chip(source==='api'?api:xml,title,channel);
      rows.push([title,label(channel.state)],['Интервал · '+title,Math.round(channel.interval_seconds/60)+' мин'],['Последняя попытка · '+title,date(channel.last_attempt_at)],['Полностью успешно · '+title,date(channel.last_success_at)],['Последние полученные данные · '+title,date(channel.last_data_at)],['Следующая попытка · '+title,date(channel.next_attempt_at)],['Обработано / ошибок · '+title,(channel.records||0)+' / '+(channel.errors||0)],['Причина задержки · '+title,channel.message||'Нет задержки'],['До повторной попытки · '+title,countdown(channel.next_attempt_at)],['Сохранённые данные · '+title,channel.using_saved_data?'Используется последняя сохранённая копия':'Состояние — '+label(channel.state)]);
    }
    rows.push(['Дата внутри XML',auto.catalog_source_date||'не получен'],['Позиций в XML',auto.catalog_offers||0],['Часовой пояс проверок','Кызылорда · UTC+5; дата XML показана как в файле']);
    details.replaceChildren(...rows.flatMap(([title,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=title;dd.textContent=String(value);return [dt,dd];}));
    const rate=Object.entries(channels).filter(([,channel])=>channel.state==='rate_limited').map(([source,channel])=>(source==='api'?'API':'XML')+': Kaspi ограничил частоту. Повтор '+date(channel.next_attempt_at)+' (через '+countdown(channel.next_attempt_at)+').');
    notice.textContent=(rate.length?rate.join(' ')+' ':'')+'Сохранённый XML от '+(auto.catalog_source_date||'не получен')+'. API и XML обновляются независимо.';
    const items=(run?.error_details||[]).map(error=>{const li=document.createElement('li');li.textContent=error.code+' · '+error.message+(error.http_status?' · HTTP '+error.http_status:'')+(error.reference?' · ref '+error.reference:'');return li;});
    if(!items.length) {const li=document.createElement('li');li.textContent='Ошибок последнего API-запуска нет.';items.push(li);}
    errors?.replaceChildren(...items);
  }
  async function refresh() {
    const current=++generation;
    clearTimeout(timer);controller?.abort();controller=new AbortController();
    try {
      const response=await fetch('/api/kaspi/status',{signal:controller.signal,cache:'no-store'});
      if(!response.ok||response.redirected) throw new Error();
      const data=await response.json();render(data.automatic,data.last_run);
    } catch(error) {if(error.name!=='AbortError') notice.textContent='Не удалось проверить состояние. Сохранённые данные доступны.';}
    finally {if(current===generation&&!document.hidden) timer=setTimeout(refresh,15000);}
  }
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(document.hidden) controller?.abort();else refresh();});
  refresh();
})();
