(() => {
  const root=document.documentElement,compact=document.getElementById('compactMode');
  let hidden=[];
  try {root.classList.toggle('compact-workspace',localStorage.getItem('ksm.compact')==='true');hidden=JSON.parse(localStorage.getItem('ksm.catalogColumns')||'[]');} catch(_) {}
  if(!Array.isArray(hidden)) hidden=[];
  if(compact) {compact.checked=root.classList.contains('compact-workspace');compact.addEventListener('change',()=>{root.classList.toggle('compact-workspace',compact.checked);try {localStorage.setItem('ksm.compact',String(compact.checked));} catch(_) {}});}
  function columns() {
    const table=document.getElementById('catalogTable');
    const sizes={photo:72,price:140,declared:170,physical:150,preorder:150};
    document.querySelectorAll('[data-column-toggle]').forEach(input=>{
      input.checked=!hidden.includes(input.dataset.columnToggle);
      table?.querySelectorAll('[data-column="'+input.dataset.columnToggle+'"]').forEach(cell=>{cell.hidden=!input.checked;});
    });
    if(table) table.style.minWidth=(280+Object.entries(sizes).reduce((sum,[key,width])=>sum+(hidden.includes(key)?0:width),0))+'px';
  }
  columns();
  document.querySelectorAll('[data-column-toggle]').forEach(input=>input.addEventListener('change',()=>{
    hidden=[...document.querySelectorAll('[data-column-toggle]')].filter(item=>!item.checked).map(item=>item.dataset.columnToggle);
    try {localStorage.setItem('ksm.catalogColumns',JSON.stringify(hidden));} catch(_) {}
    columns();
  }));
  document.addEventListener('catalog:updated',columns);
})();
