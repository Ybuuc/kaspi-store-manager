(() => {
  const xml=document.getElementById('syncXmlChip'),api=document.getElementById('syncApiChip');
  const notice=document.getElementById('syncNotice'),details=document.getElementById('syncDetails');
  const errors=document.getElementById('syncErrorDetails');
  if(!xml) return;
  let timer,controller,generation=0,channels;
  const labels={imported:window.KSMi18n.t('успешно'),unchanged:window.KSMi18n.t('без изменений'),failed:window.KSMi18n.t('ошибка'),not_configured:window.KSMi18n.t('не настроено'),queued:window.KSMi18n.t('в очереди'),running:window.KSMi18n.t('выполняется'),disabled:window.KSMi18n.t('отключено'),waiting:window.KSMi18n.t('ожидание'),needs_login:window.KSMi18n.t('нужна авторизация'),success:window.KSMi18n.t('успешно'),partial:window.KSMi18n.t('частично успешно'),interrupted:window.KSMi18n.t('прервано'),temporary_error:window.KSMi18n.t('временная ошибка'),rate_limited:window.KSMi18n.t('лимит Kaspi (429)')};
  const label=value=>labels[value]||window.KSMi18n.t('ожидание');
  function date(value) {
    if(!value) return window.KSMi18n.t('не выполнено');
    const parsed=new Date(/[zZ]|[+-]\d{2}:\d{2}$/.test(value)?value:value+'Z');
    return Number.isNaN(parsed.getTime())?window.KSMi18n.t('не выполнено'):new Intl.DateTimeFormat(window.KSMi18n.locale,{timeZone:'Asia/Qyzylorda',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(parsed);
  }
  function countdown(value) {
    const seconds=value?Math.max(0,Math.ceil((new Date(value)-Date.now())/1000)):0;
    return seconds?Math.floor(seconds/60)+window.KSMi18n.t(' мин ')+seconds%60+window.KSMi18n.t(' сек'):window.KSMi18n.t('по расписанию');
  }
  function chip(element,title,channel) {
    const state=channel.state;
    element.textContent=title+': '+label(state);
    element.className='status-chip '+(['success','imported','unchanged'].includes(state)?'good':['partial','rate_limited','needs_login'].includes(state)?'warn':['failed','temporary_error'].includes(state)?'bad':['running','queued'].includes(state)?'busy':'');
  }
  function render(auto,run) {
    channels=auto.channels||{};
    const rows=[];
    for(const [source,title] of [['api',window.KSMi18n.t('API заказов')],['xml',window.KSMi18n.t('XML-каталог')]]) {
      const channel=channels[source]||{state:'waiting'};
      chip(source==='api'?api:xml,title,channel);
      rows.push([title,label(channel.state)],[window.KSMi18n.t('Интервал · ')+title,Math.round(channel.interval_seconds/60)+window.KSMi18n.t(' мин')],[window.KSMi18n.t('Последняя попытка · ')+title,date(channel.last_attempt_at)],[window.KSMi18n.t('Полностью успешно · ')+title,date(channel.last_success_at)],[window.KSMi18n.t('Последние полученные данные · ')+title,date(channel.last_data_at)],[window.KSMi18n.t('Следующая попытка · ')+title,date(channel.next_attempt_at)],[window.KSMi18n.t('Обработано / ошибок · ')+title,(channel.records||0)+' / '+(channel.errors||0)],[window.KSMi18n.t('Причина задержки · ')+title,window.KSMi18n.t(channel.message)||window.KSMi18n.t('Нет задержки')],[window.KSMi18n.t('До повторной попытки · ')+title,countdown(channel.next_attempt_at)],[window.KSMi18n.t('Сохранённые данные · ')+title,channel.using_saved_data?window.KSMi18n.t('Используется последняя сохранённая копия'):window.KSMi18n.t('Состояние — ')+label(channel.state)]);
    }
    rows.push([window.KSMi18n.t('Дата внутри XML'),auto.catalog_source_date||window.KSMi18n.t('не получен')],[window.KSMi18n.t('Позиций в XML'),auto.catalog_offers||0],[window.KSMi18n.t('Часовой пояс проверок'),window.KSMi18n.t('Кызылорда · UTC+5; дата XML показана как в файле')]);
    details.replaceChildren(...rows.flatMap(([title,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=title;dd.textContent=String(value);return [dt,dd];}));
    const rate=Object.entries(channels).filter(([,channel])=>channel.state==='rate_limited').map(([source,channel])=>(source==='api'?'API':'XML')+window.KSMi18n.t(': Kaspi ограничил частоту. Повтор ')+date(channel.next_attempt_at)+window.KSMi18n.t(' (через ')+countdown(channel.next_attempt_at)+').');
    notice.textContent=(rate.length?rate.join(' ')+' ':'')+window.KSMi18n.t('Сохранённый XML от ')+(auto.catalog_source_date||window.KSMi18n.t('не получен'))+window.KSMi18n.t('. API и XML обновляются независимо.');
    const items=(run?.error_details||[]).map(error=>{const li=document.createElement('li');li.textContent=error.code+' · '+window.KSMi18n.t(error.message)+(error.http_status?' · HTTP '+error.http_status:'')+(error.reference?' · ref '+error.reference:'');return li;});
    if(!items.length) {const li=document.createElement('li');li.textContent=window.KSMi18n.t('Ошибок последнего API-запуска нет.');items.push(li);}
    errors?.replaceChildren(...items);
  }
  async function refresh() {
    const current=++generation;
    clearTimeout(timer);controller?.abort();controller=new AbortController();
    try {
      const response=await fetch('/api/kaspi/status',{signal:controller.signal,cache:'no-store'});
      if(!response.ok||response.redirected) throw new Error();
      const data=await response.json();render(data.automatic,data.last_run);
    } catch(error) {if(error.name!=='AbortError') notice.textContent=window.KSMi18n.t('Не удалось проверить состояние. Сохранённые данные доступны.');}
    finally {if(current===generation&&!document.hidden) timer=setTimeout(refresh,15000);}
  }
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(document.hidden) controller?.abort();else refresh();});
  refresh();
})();
