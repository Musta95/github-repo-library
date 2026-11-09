'use strict';
const state = { categories:[], lists:[], listId:null, data:[], category:'All', favorites:false, search:'', sort:'stars', page:1, total:0, stats:{} };
const $ = id => document.getElementById(id);
const escapeHtml = v => String(v ?? '').replace(/[&<>"']/g, s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const formatNum = n => Number(n||0).toLocaleString('en-US');
let toastTimer = null;
let latestRefresh = 0;
function toast(message){ const el=$('toast');el.textContent=message;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),5000); }
async function api(path, options){
  let r;
  try { r=await fetch(path, {headers:{'Content-Type':'application/json'},cache:'no-store',...options}); }
  catch(e){throw Error('Unable to reach the server. Check the Docker container and network connection.');}
  const raw=await r.text();let data;
  try { data=raw?JSON.parse(raw):{}; }catch(e){throw Error(`Invalid server response (HTTP ${r.status}). Check the Docker logs.`);}
  if(!r.ok)throw Error(data.error||`HTTP ${r.status} for ${path.split('?')[0]}`);
  return data;
}
function showImported(category=null, listId=null){
  // Clear pre-existing search, favorite and list filters that hid imported results.
  state.search='';state.favorites=false;state.listId=listId;
  state.category=category || 'All';state.sort='synced';state.page=1;
  $('search').value='';$('sortSelect').value='synced';$('categorySelect').value=state.category;
}
async function refreshAutomation(){
  try{
    const a=await api('/api/auto-discovery');
    const run=a.last_run;
    const schedule=`${String(a.hour).padStart(2,'0')}:00 (${a.timezone})`;
    let summary=a.enabled?`Automatic discovery: daily from ${schedule}, ${a.limit_per_category} repositories per category.`:'Automatic discovery is disabled.';
    if(run){summary+=` Last run: ${run.last_day} · ${run.added} new / ${run.processed} processed · ${run.categories_done}/${a.categories} categories.`;
      if(run.error)summary+=` Error: ${run.error}`;
      else if(run.result==='running')summary+=' Discovery in progress…';
    }
    $('autoScanInfo').textContent=summary;
  }catch(e){$('autoScanInfo').textContent='Automatic discovery: status unavailable.';}
}
async function refreshStats(){
  const [statistics,lists]=await Promise.all([api('/api/stats'),api('/api/lists')]);
  state.stats=statistics;state.lists=lists;
  $('metricTotal').textContent=formatNum(state.stats.total);
  $('metricCategories').textContent=Object.keys(state.stats.categories).length;
  $('metricFavs').textContent=formatNum(state.stats.favorites);
  $('count-favorites').textContent=formatNum(state.stats.favorites);
  $('tokenInfo').textContent=state.stats.token_configured?'● API token configured':'○ Public GitHub API';
  buildNav();
}
function buildNav(){
  $('categoryNav').innerHTML=`<button class="nav-item ${!state.favorites&&state.category==='All'&&state.listId===null?'active':''}" data-category="All"><span>◈</span> All Repositories <span class="nav-count">${formatNum(state.stats.total||0)}</span></button>`+state.categories.map((c,i)=>`<button class="nav-item ${!state.favorites&&state.category===c.name&&state.listId===null?'active':''}" data-category="${escapeHtml(c.name)}"><span>${(['◫','◇','⬡','✧','↯','☁','⌂','▣','⌘','▤','♧','⊞','⌘','◧'][i]||'◆')}</span> ${escapeHtml(c.name)} <span class="nav-count">${formatNum(state.stats.categories?.[c.name]||0)}</span></button>`).join('');
  $('listsNav').innerHTML=state.lists.length?state.lists.map(l=>`<button class="nav-item ${state.listId===l.id?'active':''}" data-list="${l.id}" title="${escapeHtml(l.description)}"><span>▤</span><span class="nav-list-title">${escapeHtml(l.name)}</span><span class="nav-count">${formatNum(l.repo_count)}</span></button>`).join(''):'<div class="empty-lists">No personal lists yet</div>';
  $('favoritesNav').classList.toggle('active',state.favorites);
}
async function refresh(){
  const requestId=++latestRefresh;
  const qs=new URLSearchParams({q:state.search,category:state.category,sort:state.sort,page:state.page,favorite:state.favorites?'1':'0'});
  if(state.listId!==null)qs.set('list_id',String(state.listId));
  try {
    const [data, statistics, lists] = await Promise.all([
      api('/api/repos?'+qs), api('/api/stats'), api('/api/lists')
    ]);
    // A previous search response must not overwrite a newer page or query.
    if(requestId!==latestRefresh)return;
    state.data=data.items;state.total=data.total;state.page=data.page;
    state.stats=statistics;state.lists=lists;
    $('metricTotal').textContent=formatNum(statistics.total);
    $('metricCategories').textContent=Object.keys(statistics.categories).length;
    $('metricFavs').textContent=formatNum(statistics.favorites);
    $('count-favorites').textContent=formatNum(statistics.favorites);
    $('tokenInfo').textContent=statistics.token_configured?'● API token configured':'○ Public GitHub API';
    buildNav();render();refreshAutomation();
  } catch(e) { if(requestId===latestRefresh)toast(`Unable to load results: ${e.message}`); }
}
function render(){
  const list=state.lists.find(l=>l.id===state.listId);
  let title=state.favorites?'Favoriten':list?list.name:state.category==='All'?'All Repositories':state.category;
  $('viewTitle').textContent=title;
  $('breadcrumb').textContent=title.toUpperCase();
  $('resultCount').textContent=`${formatNum(state.total)} ${state.total===1?'project':'projects'} found`;
  $('categorySelect').value=state.category;
  $('editListBtn').hidden=!list;
  const grid=$('repoGrid');
  if(!state.data.length){grid.innerHTML=`<div class="empty"><div class="empty-icon">⌘</div><strong>${state.search?'No matching repositories':list?'Your list is still empty':'Your library is still empty'}</strong><p>${state.search?'Try a different keyword or select another category.':list?'Add a GitHub link and select this list.':'Discover repositories by category, add a GitHub link, or import your GitHub stars.'}</p><button class="btn btn-primary" data-action="add">＋ Add GitHub Link</button></div>`;}
  else{grid.innerHTML=state.data.map(repoCard).join('');}
  $('pageLabel').textContent=state.total>40?`Page ${state.page} of ${Math.ceil(state.total/40)}`:'';
  $('prevBtn').hidden=state.page<=1;$('nextBtn').hidden=state.page*40>=state.total;
}
function repoCard(r){let [owner,name]=r.full_name.split('/');return `<article class="repo-card" data-id="${r.id}"><div class="repo-top"><div class="repo-icon">⌘</div><div class="repo-name-wrap"><a class="repo-name" href="${escapeHtml(r.html_url)}" target="_blank" rel="noreferrer">${escapeHtml(name)}</a><div class="repo-owner">${escapeHtml(owner)}</div></div><button class="star-btn ${r.favorite?'on':''}" data-action="favorite" data-id="${r.id}" aria-label="Toggle favorite" title="Favorit">${r.favorite?'★':'☆'}</button></div><p class="repo-desc">${escapeHtml(r.description||'No description available on GitHub.')}</p><div class="chips">${(r.categories?.length?r.categories:[r.category]).slice(0,4).map(c=>`<span class="chip">${escapeHtml(c)}</span>`).join('')}${r.topics.slice(0,2).map(t=>`<span class="chip topic">${escapeHtml(t)}</span>`).join('')}${(r.list_ids||[]).slice(0,2).map(id=>`<span class="chip custom">▤ ${escapeHtml(state.lists.find(l=>l.id===id)?.name||'List')}</span>`).join('')}</div><div class="repo-bottom"><div class="repo-meta"><span>★ ${formatNum(r.stars)}</span><span>${r.language?`<i class="lang-dot"></i>${escapeHtml(r.language)}`:'—'}</span></div><div class="card-actions"><button class="text-btn" data-action="category" data-id="${r.id}" aria-label="Choose categories for ${escapeHtml(r.full_name)}">✎ Categories</button><button class="text-btn" data-action="detail" data-id="${r.id}">Details / Notes →</button></div></div></article>`;}
function showModal(title,html){$('dialogTitle').textContent=title;$('dialogContent').innerHTML=html;$('overlay').hidden=false;}
function closeModal(){$('overlay').hidden=true;}
function setBusy(busy){$('dialogContent').classList.toggle('busy',busy);}
async function runImport(endpoint, body, success){setBusy(true);try{let r=await api(endpoint,{method:'POST',body:JSON.stringify(body)});closeModal();
  if(endpoint==='/api/discover')showImported(body.category);
  else if(endpoint==='/api/import-url')showImported(null,body.list_id??null);
  else showImported();
  await refresh();toast(`${r.count} repositories ${success}. The corresponding view is now open.`);
}catch(e){toast(e.message);}finally{setBusy(false);}}
function categoryOptions(){
  $('categorySelect').innerHTML='<option value="All">All Categories</option>'+state.categories.map(c=>`<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('');
  $('categorySelect').value=state.category;
}
async function loadCategories(){state.categories=await api('/api/categories');categoryOptions();}
function newCategoryDialog(){
  showModal('Create Custom Category',`<p class="section-caption">Custom categories are stored permanently. You can then assign repositories manually. Recognized topics such as Robotics do not require a custom category.</p><form id="newCategoryForm"><div class="field"><label for="newCategoryName">Category Name</label><input id="newCategoryName" maxlength="60" required placeholder="e.g., 3D Printing, Smart Home, Hardware"></div><div class="dialog-actions"><button type="button" class="btn btn-outline" data-action="close">Cancel</button><button type="submit" class="btn btn-primary">Create Category</button></div></form>`);
  $('newCategoryForm').addEventListener('submit',async e=>{e.preventDefault();setBusy(true);try{
    const newCat=await api('/api/categories',{method:'POST',body:JSON.stringify({name:$('newCategoryName').value})});
    await loadCategories();state.category=newCat.name;state.listId=null;state.favorites=false;state.page=1;
    closeModal();await refresh();toast(`Category “${newCat.name}” created. You can now assign repositories to it.`);
  }catch(err){toast(err.message);}finally{setBusy(false);}});
}
function quickCategoriesDialog(id){
  const r=state.data.find(x=>x.id===Number(id));if(!r)return;
  const chosen=new Set(r.categories?.length?r.categories:[r.category]);
  const boxes=state.categories.map(c=>`<label class="list-check"><input type="checkbox" data-quick-category="${escapeHtml(c.name)}" ${chosen.has(c.name)?'checked':''}><span>${escapeHtml(c.name)}</span></label>`).join('');
  showModal('Select Categories',`<div class="detail-head"><strong>${escapeHtml(r.full_name)}</strong></div><p class="section-caption">Select one or more categories. Your choices are preserved during later GitHub imports.</p><form id="quickCategoryForm"><div class="list-checks category-checks">${boxes}</div><div class="dialog-actions"><button type="button" class="btn btn-outline" data-action="close">Cancel</button><button type="submit" class="btn btn-primary">Save Categories</button></div></form>`);
  $('quickCategoryForm').addEventListener('submit',async e=>{e.preventDefault();const selected=[...document.querySelectorAll('[data-quick-category]:checked')].map(el=>el.dataset.quickCategory);
    if(!selected.length){toast('Select at least one category.');return;}
    const primary=selected.includes(r.category)?r.category:selected[0];setBusy(true);
    try{await api('/api/repos/'+id,{method:'PATCH',body:JSON.stringify({category:primary,categories:selected})});closeModal();await refresh();toast('Categories saved.');}
    catch(err){toast(err.message);}finally{setBusy(false);}
  });
}
function addDialog(){
  const opts=state.lists.map(l=>`<option value="${l.id}" ${state.listId===l.id?'selected':''}>${escapeHtml(l.name)}</option>`).join('');
  showModal('Save GitHub Link',`<p class="section-caption">Found a useful tool on Instagram, YouTube, or another website? Paste its <b>GitHub repository URL</b> here. The description, language, topics, and categories are retrieved from GitHub.</p><form id="addForm"><div class="field"><label for="repoUrl">GitHub-URL</label><input id="repoUrl" type="url" required placeholder="https://github.com/owner/repository"><div class="help">Links containing /tree/main or URL query parameters are supported. Direct Instagram URL scraping is not provided.</div></div><div class="field"><label for="addToList">Add to a personal list (optional)</label><select id="addToList"><option value="">Library only — no list</option>${opts}</select></div><div class="dialog-actions"><button type="button" class="btn btn-outline" data-action="close">Cancel</button><button type="submit" class="btn btn-primary">Save Repository</button></div></form>`);
  $('addForm').addEventListener('submit',e=>{e.preventDefault();const listId=$('addToList').value;runImport('/api/import-url',{url:$('repoUrl').value,...(listId?{list_id:Number(listId)}:{})},'saved');});
}
function listDialog(list=null){
  const editing=Boolean(list);
  showModal(editing?'Edit Personal List':'Create Personal List',`<p class="section-caption">Personal lists are separate from automatic categories. A repository can belong to multiple lists.</p><form id="listForm"><div class="field"><label for="listName">List Name</label><input id="listName" required maxlength="80" placeholder="e.g., Tools for my next web project" value="${escapeHtml(list?.name||'')}"></div><div class="field"><label for="listDesc">Description (optional)</label><textarea id="listDesc" maxlength="500" placeholder="What is this list for?">${escapeHtml(list?.description||'')}</textarea></div><div class="dialog-actions">${editing?'<button type="button" class="btn btn-outline danger" id="deleteList">Delete List</button>':''}<button type="button" class="btn btn-outline" data-action="close">Cancel</button><button type="submit" class="btn btn-primary">${editing?'Save Changes':'Create List'}</button></div></form>`);
  $('listForm').addEventListener('submit',async e=>{e.preventDefault();setBusy(true);try{const data=await api(editing?'/api/lists/'+list.id:'/api/lists',{method:editing?'PATCH':'POST',body:JSON.stringify({name:$('listName').value,description:$('listDesc').value})});state.listId=data.id;state.category='All';state.favorites=false;state.page=1;closeModal();await refresh();toast(editing?'List updated.':'Your list was created.');}catch(e){toast(e.message);}finally{setBusy(false);}});
  if(editing)$('deleteList').addEventListener('click',async()=>{if(!confirm('Delete this list? All repositories in it will remain in your library.'))return;try{await api('/api/lists/'+list.id,{method:'DELETE'});state.listId=null;closeModal();await refresh();toast('List deleted. Repositories were kept.');}catch(e){toast(e.message);}});
}
function discoverDialog(){let opts=state.categories.filter(c=>c.query).map(c=>`<option value="${escapeHtml(c.name)}" ${state.category===c.name?'selected':''}>${escapeHtml(c.name)}</option>`).join('');showModal('Discover GitHub Repositories',`<p class="section-caption">Search popular public projects through the GitHub API. Choose a category and import up to 100 results. Existing repositories are updated rather than duplicated.</p><form id="discoverForm"><div class="field"><label for="discoverCat">Category</label><select id="discoverCat">${opts}</select></div><div class="field"><label for="discoverAmount">Results (up to 100)</label><input id="discoverAmount" type="number" min="1" max="100" value="30"></div><div class="dialog-actions"><button type="button" class="btn btn-outline" data-action="close">Cancel</button><button type="submit" class="btn btn-primary">✦ Discover Now</button></div></form><div class="dialog-actions"><button id="discoverAll" class="btn btn-outline" type="button">Discover All Categories</button></div><p id="scanProgress" class="help" aria-live="polite"></p><hr class="muted-divider"><p class="section-caption">Stars indicate popularity, not security or quality. GitHub limits API requests.</p>`);$('discoverForm').addEventListener('submit',e=>{e.preventDefault();runImport('/api/discover',{category:$('discoverCat').value,limit:Number($('discoverAmount').value)},'imported');});$('discoverAll').addEventListener('click',async()=>{let total=0;setBusy(true);try{let categories=state.categories.filter(c=>c.query);for(let i=0;i<categories.length;i++){let cat=categories[i];$('scanProgress').textContent=`Searching category ${i+1}/${categories.length}: ${cat.name}…`;let result=await api('/api/discover',{method:'POST',body:JSON.stringify({category:cat.name,limit:Number($('discoverAmount').value)})});total+=result.count;$('scanProgress').textContent=`${i+1}/${categories.length} categories completed · ${total} results imported`;if(i<categories.length-1)await sleep(state.stats.token_configured?2300:7000);}closeModal();showImported();await refresh();toast(`${total} results imported. Showing All Repositories sorted by Recently Imported.`);}catch(e){showImported();await refresh();toast(`Discovery stopped after ${total} imported results: ${e.message}`);}finally{setBusy(false);}});}
function starsDialog(){showModal('Import GitHub Stars',`<p class="section-caption">Import starred repositories from a public GitHub profile or, with a configured token, your own GitHub stars.</p><form id="starsForm"><div class="field"><label for="starsUser">GitHub Username</label><input id="starsUser" type="text" placeholder="e.g., octocat"><div class="help">Leave this blank to import your own stars when using a token. Up to 100 repositories per page.</div></div><div class="field"><label for="starsPages">Pages (1–5)</label><input id="starsPages" type="number" min="1" max="5" value="2"></div><div class="dialog-actions"><button type="button" class="btn btn-outline" data-action="close">Cancel</button><button type="submit" class="btn btn-primary">★ Import Stars</button></div></form>`);$('starsForm').addEventListener('submit',e=>{e.preventDefault();runImport('/api/import-stars',{username:$('starsUser').value,pages:Number($('starsPages').value)},'imported');});}
function detailDialog(id){
  const r=state.data.find(x=>x.id===Number(id));if(!r)return;
  const cats=state.categories.map(c=>c.name);
  const selectedCategories=new Set(r.categories||[r.category]);
  const checks=cats.map(cat=>`<label class="list-check"><input type="checkbox" data-category-tag="${escapeHtml(cat)}" ${selectedCategories.has(cat)?'checked':''}><span>${escapeHtml(cat)}</span></label>`).join('');
  const membership=state.lists.length?state.lists.map(l=>`<label class="list-check"><input type="checkbox" data-member-list="${l.id}" ${(r.list_ids||[]).includes(l.id)?'checked':''}><span>${escapeHtml(l.name)}</span></label>`).join(''):'<p class="help">Create a personal list first using Create List.</p>';
  showModal('Edit Repository',`<div class="detail-head"><strong>${escapeHtml(r.full_name)}</strong><div>${escapeHtml(r.description||'No description')}</div><a href="${escapeHtml(r.html_url)}" target="_blank" rel="noreferrer">Open Repository on GitHub ↗</a><div>★ ${formatNum(r.stars)} · ${escapeHtml(r.language||'Unknown language')} · License: ${escapeHtml(r.license||'unknown')}</div></div><hr class="muted-divider"><form id="editForm"><div class="field"><label for="editCategory">Primary Category (manual selection)</label><select id="editCategory">${cats.map(c=>`<option ${r.category===c?'selected':''}>${escapeHtml(c)}</option>`).join('')}</select><div class="help">Your primary category is preserved when the repository is imported again.</div></div><div class="field"><label>Additional Categories (multiple selections)</label><div class="list-checks category-checks">${checks}</div><div class="help">For cross-platform projects, select Windows Tools, macOS Tools, and Linux Tools as appropriate.</div></div><div class="field"><label>Add to My Personal Lists</label><div class="list-checks">${membership}</div></div><div class="field"><label for="editNotes">My Notes / Use Case</label><textarea id="editNotes" maxlength="3000" placeholder="How will I use this project? Notes, links, ideas…">${escapeHtml(r.notes)}</textarea></div><label class="section-caption"><input type="checkbox" id="editFavorite" ${r.favorite?'checked':''}> ★ Mark as Favorite</label><div class="dialog-actions"><button type="button" class="btn btn-outline danger" id="deleteRepo">Delete</button><button type="submit" class="btn btn-primary">Save Changes</button></div></form>`);
  $('editForm').addEventListener('submit',async e=>{e.preventDefault();setBusy(true);try{
    await api('/api/repos/'+id,{method:'PATCH',body:JSON.stringify({
      category:$('editCategory').value,
      categories:[...document.querySelectorAll('[data-category-tag]:checked')].map(el=>el.dataset.categoryTag),
      notes:$('editNotes').value,favorite:$('editFavorite').checked
    })});
    for(const el of document.querySelectorAll('[data-member-list]')){
      const listId=Number(el.dataset.memberList),had=(r.list_ids||[]).includes(listId);
      if(el.checked!==had)await api(`/api/lists/${listId}/repos`+(el.checked?'':`/${id}`),{method:el.checked?'POST':'DELETE',...(el.checked?{body:JSON.stringify({repo_id:Number(id)})}:{})});
    }
    closeModal();await refresh();toast('Categories, notes, and lists saved.');
  }catch(e){toast(e.message);}finally{setBusy(false);}});
  $('deleteRepo').addEventListener('click',async()=>{if(!confirm('Remove this repository from your entire local library and all lists?'))return;try{await api('/api/repos/'+id,{method:'DELETE'});closeModal();await refresh();toast('Repository removed.');}catch(e){toast(e.message);}});
}
async function flipFav(id){let r=state.data.find(x=>x.id===Number(id));if(!r)return;try{await api('/api/repos/'+id,{method:'PATCH',body:JSON.stringify({favorite:!r.favorite})});await refresh();}catch(e){toast(e.message);}}
function pickCategory(cat, favorites=false){state.category=cat;state.listId=null;state.favorites=favorites;state.page=1;refresh();}
function pickList(id){state.listId=Number(id);state.category='All';state.favorites=false;state.page=1;refresh();}
async function boot(){try{await loadCategories();await refresh();}catch(e){toast(e.message);}}
$('categoryNav').addEventListener('click',e=>{let b=e.target.closest('[data-category]');if(b)pickCategory(b.dataset.category);});
$('listsNav').addEventListener('click',e=>{let b=e.target.closest('[data-list]');if(b)pickList(b.dataset.list);});
$('favoritesNav').addEventListener('click',()=>pickCategory('All',true));
$('categorySelect').addEventListener('change',e=>{state.category=e.target.value;state.page=1;refresh();});
$('sortSelect').addEventListener('change',e=>{state.sort=e.target.value;state.page=1;refresh();});
let timer;$('search').addEventListener('input',e=>{clearTimeout(timer);timer=setTimeout(()=>{state.search=e.target.value;state.page=1;refresh();},220);});
$('addBtn').addEventListener('click',addDialog);
$('newListBtn').addEventListener('click',()=>listDialog());
$('newCategoryBtn').addEventListener('click',newCategoryDialog);
$('newListTopBtn').addEventListener('click',()=>listDialog());
$('editListBtn').addEventListener('click',()=>{const list=state.lists.find(l=>l.id===state.listId);if(list)listDialog(list);});
$('discoverBtn').addEventListener('click',discoverDialog);
$('starsBtn').addEventListener('click',starsDialog);
$('closeModal').addEventListener('click',closeModal);
$('overlay').addEventListener('click',e=>{if(e.target===$('overlay'))closeModal();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});
$('dialogContent').addEventListener('click',e=>{if(e.target.closest('[data-action="close"]'))closeModal();});
$('repoGrid').addEventListener('click',e=>{let b=e.target.closest('[data-action]');if(!b)return;if(b.dataset.action==='favorite')flipFav(b.dataset.id);if(b.dataset.action==='detail')detailDialog(b.dataset.id);if(b.dataset.action==='category')quickCategoriesDialog(b.dataset.id);if(b.dataset.action==='discover')discoverDialog();if(b.dataset.action==='add')addDialog();});
$('prevBtn').addEventListener('click',()=>{if(state.page>1){state.page--;refresh();$('viewTitle').scrollIntoView({behavior:'smooth',block:'start'});}});
$('nextBtn').addEventListener('click',()=>{if(state.page*40<state.total){state.page++;refresh();$('viewTitle').scrollIntoView({behavior:'smooth',block:'start'});}});
boot();
