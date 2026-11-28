(() => {
  const config=window.KSM_I18N||{language:'ru',locale:'ru-RU',messages:{}};
  function t(message,values={}) {
    if(message===null||message===undefined) return '';
    const text=String(message),key=text.trim().replace(/\s+/g,' ');
    const translated=config.messages[key];
    let result=translated===undefined?text:(text.match(/^\s*/)[0]+translated+text.match(/\s*$/)[0]);
    return result.replace(/%\(([^)]+)\)s/g,(match,name)=>Object.hasOwn(values,name)?String(values[name]):match);
  }
  window.KSMi18n={t,language:config.language,locale:config.locale};
})();
