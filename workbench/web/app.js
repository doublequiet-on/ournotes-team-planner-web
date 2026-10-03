"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = value => JSON.parse(JSON.stringify(value));
const fmt = value => Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2});
const types = {1:'紅赤 · 红',2:'紺碧 · 蓝',3:'翡翠 · 绿',4:'山吹 · 黄',5:'紫苑 · 紫'};
const typeShort = {1:'紅赤',2:'紺碧',3:'翡翠',4:'山吹',5:'紫苑'};
const rarity = {2:'R',3:'SR',4:'SSR',10:'EX'};
const fields = {level:'等级',training_count:'特训阶段',awakening_count:'觉醒次数',live_skill_level:'Live 技能等级',limit_break_count:'突破次数'};
const titles = {plan:'配队配置',inventory:'我的卡牌',growth:'道具与角色',output:'计算结果',archive:'档案与说明'};
const STORE = 'ournotes-team-lab-v1-profile', ARCHIVES = 'ournotes-team-lab-v2-archives', JOB = 'ournotes-team-lab-v1-job';
let catalog, defaults, token, sample, state, archives, signature, version, cardIndex;
let result=null, resultInput=null, jobId=null, stale=false, evaluating=false, starting=false, editor=null, picker=null, detailIndex=0;
let comparison=[], resultFilter='all', growthBand=0, currentPage='plan', issues=[];
let savedArchives=null, savedProfile=null;
const card = (kind,id) => cardIndex[kind].get(id);
const number = el => el.value === '' ? null : Number(el.value);
const activeProfile = () => archives.profiles.find(p=>p.id===archives.active_id);
const selectedKey = kind => kind==='members'?'candidate_member_ids':'candidate_snap_ids';
const requiredKey = kind => kind==='members'?'required_member_ids':'required_snap_ids';
function on(id,event,fn){$(id).addEventListener(event,e=>Promise.resolve().then(()=>fn(e)).catch(e=>notice(e.message,true)));}
function notice(text,error=false){$('message').hidden=!text;$('message').textContent=text;$('message').className='notice'+(error?' error':'');}
function toggle(list,id,on){const index=list.indexOf(id);if(on&&index<0)list.push(id);if(!on&&index>=0)list.splice(index,1);}
function options(map,blank='全部'){return `<option value="0">${blank}</option>`+Object.entries(map).map(([id,name])=>`<option value="${id}">${esc(name)}</option>`).join('');}
function save(){
  if(stale)throw new Error('另一标签页已修改档案，请刷新后继续。当前输入仍可导出。');
  activeProfile().input=state;
  try{
    if(localStorage.getItem(ARCHIVES)!==savedArchives||localStorage.getItem(STORE)!==savedProfile){stale=true;busy(false);throw new Error('另一标签页已修改档案，请先导出当前输入，再刷新。');}
    const next=JSON.stringify(archives);localStorage.setItem(ARCHIVES,next);savedArchives=next;
    const profile=JSON.stringify(state);localStorage.setItem(STORE,profile);savedProfile=profile;
  }
  catch(e){notice('本次输入未保存：'+e.message+'。请导出卡库或全部档案备份。',true);}
  if(stale)throw new Error('另一标签页已修改档案，请刷新后继续。');
}
function changed(){
  activeProfile().last_result=null;result=null;resultInput=null;comparison=[];issues=[];
  $('results').hidden=true;$('emptyResult').hidden=false;$('resultCount').textContent='';$('growthIssues').innerHTML='';
  save();overview();renderLocks();renderFixed();renderArchive();
}
function tab(name,updateHash=true){
  if(!titles[name])name='plan';currentPage=name;
  document.querySelectorAll('.tab-page').forEach(el=>el.hidden=el.id!==name);
  document.querySelectorAll('[data-tab]').forEach(el=>el.classList.toggle('active',el.dataset.tab===name));
  $('pageTitle').textContent=titles[name];
  if(updateHash)history.replaceState(null,'','#'+name);
  if(name==='archive')renderArchive();
}
function normalize(raw){
  const x=clone(raw.input || raw);
  if(x.schema_version!==1||!x.profile?.inventory)throw new Error('请导入 schema_version 1 的卡库或计算结果。');
  for(const kind of ['members','snaps']){
    const rows=x.profile.inventory[kind];if(!Array.isArray(rows))throw new Error('缺少成员或留影卡库。');
    const ids=new Set();
    for(const r of rows){if(!r||!Number.isInteger(r.id)||!card(kind,r.id)||ids.has(r.id))throw new Error('卡库包含未知或重复卡牌。');ids.add(r.id);}
    const key=selectedKey(kind);x[key]??=[];
    if(!Array.isArray(x[key])||new Set(x[key]).size!==x[key].length||x[key].some(id=>!ids.has(id)))throw new Error('候选卡与已拥有卡不一致。');
  }
  if(!Array.isArray(x.profile.character_ranks)||!Array.isArray(x.profile.facilities))throw new Error('卡库缺少角色或道具信息。');
  x.team_settings={...clone(defaults),...x.team_settings};
  for(const key of ['required_member_ids','required_snap_ids','allowed_band_ids','allowed_character_ids','required_bindings'])if(!Array.isArray(x.team_settings[key]))throw new Error('必带或筛选条件格式不正确。');
  for(const id of [x.team_settings.required_leader_id,...x.team_settings.required_bindings.map(p=>p.member_id)])if(id&&!x.team_settings.required_member_ids.includes(id))x.team_settings.required_member_ids.push(id);
  x.name ||= '我的卡库';x.profile_kind=x.profile_kind==='plan'?'plan':'actual';x.operation=x.operation==='evaluate'?'evaluate':'recommend';
  const fixed=x.fixed_team||{};
  x.fixed_team={member_ids:Array.from({length:5},(_,i)=>fixed.member_ids?.[i]??null),snap_ids:Array.from({length:5},(_,i)=>fixed.snap_ids?.[i]??null),leader_id:fixed.leader_id??0};
  if(x.fixed_team.member_ids.some(id=>id!==null&&!card('members',id))||x.fixed_team.snap_ids.some(id=>id!==null&&!card('snaps',id)))throw new Error('指定队伍包含未知卡牌。');
  return x;
}
function fresh(){
  const x=clone(sample);x.name='我的卡库';x.is_demo=false;x.profile_kind='actual';
  x.profile.inventory={members:[],snaps:[]};x.profile.character_ranks=[];x.profile.facilities=[];
  x.profile.character_total_rank=null;x.profile.tgw_card_rank=null;x.candidate_member_ids=[];x.candidate_snap_ids=[];x.team_settings=clone(defaults);
  return normalize(x);
}
function addProfile(value){
  if(jobId||evaluating||stale)return;
  const input=normalize(value),id=crypto.randomUUID();archives.profiles.push({id,input,last_result:null});archives.active_id=id;state=input;
  changed();renderAll();notice('已保存为独立档案：'+state.name);
}
function restoreResult(){
  const saved=activeProfile().last_result;
  if(saved&&saved.signature===signature&&JSON.stringify(saved.input)===JSON.stringify(state)&&saved.result?.version===version){
    result=clone(saved.result);resultInput=clone(saved.input);renderResult(false);
  }else{$('results').hidden=true;$('emptyResult').hidden=false;$('resultCount').textContent='';}
}
function switchProfile(id){
  if(jobId||evaluating||stale||id===archives.active_id)return;
  if(!archives.profiles.some(p=>p.id===id))return;
  archives.active_id=id;state=activeProfile().input;comparison=[];result=null;resultInput=null;save();renderAll();restoreResult();notice('已切换档案：'+state.name);
}
function filteredCandidates(){
  const s=state.team_settings;
  return {members:state.candidate_member_ids.filter(id=>{const c=card('members',id);return(!s.member_type||c.type===s.member_type)&&(!s.member_band||c.band_id===s.member_band)&&c.rarity>=s.member_min_rarity&&(!s.allowed_band_ids.length||s.allowed_band_ids.includes(c.band_id))&&(!s.allowed_character_ids.length||s.allowed_character_ids.includes(c.character_id));}),snaps:state.candidate_snap_ids.filter(id=>{const c=card('snaps',id);return(!s.snap_type||c.type===s.snap_type)&&c.rarity>=s.snap_min_rarity;})};
}
function overview(){
  $('profileName').textContent=state.name;
  const kind=state.is_demo?'合成示例':state.profile_kind==='plan'?'升级规划':'实际养成';
  $('profileBadge').textContent=kind;$('profileBadge').className='badge '+(state.is_demo?'demo':state.profile_kind==='plan'?'plan':'');
  const inv=state.profile.inventory;$('ownedCount').textContent=`${inv.members.length} + ${inv.snaps.length}`;
  $('profileSelect').innerHTML=archives.profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.input.name)}</option>`).join('');$('profileSelect').value=archives.active_id;
  const f=filteredCandidates(),chars=new Set(f.members.map(id=>card('members',id).character_id)),good=chars.size>=5&&f.snaps.length>=5;
  $('poolHint').innerHTML=`<span>参与配队 <strong>${f.members.length} 张成员 / ${f.snaps.length} 张留影</strong></span><span class="tiny ${good?'muted':'warning'}">${chars.size} 位角色 · ${good?'满足基础人数要求':'至少需要 5 位角色和 5 张留影'}</span>`;
  $('sidePool').textContent=`${f.members.length} 成员 / ${f.snaps.length} 留影 · ${chars.size} 位角色`;
  $('inventoryHint').textContent=`已拥有 ${inv.members.length} 张成员、${inv.snaps.length} 张留影。点开卡牌填写养成，勾选后参与配队。`;
  $('readyHint').textContent=state.operation==='evaluate'?'只检查指定队伍涉及的养成':'只检查筛选后的候选养成';
  renderRoleFilters();renderRequiredLeader();renderBindings();
}
function renderControls(){
  for(const [id,key]of Object.entries({memberType:'member_type',snapType:'snap_type',memberBand:'member_band',mode:'mode',strategy:'strategy',count:'count',powerAttribute:'music_attribute',memberRarity:'member_min_rarity',snapRarity:'snap_min_rarity'}))$(id).value=state.team_settings[key];
  $('minEventBonus').value=state.team_settings.min_event_bonus_10000?state.team_settings.min_event_bonus_10000/100:'';$('minShopBonus').value=state.team_settings.min_shop_bonus_10000?state.team_settings.min_shop_bonus_10000/100:'';
  $('pureType').checked=state.team_settings.pure_type;renderGoal();overview();renderLocks();renderFixed();
}
function renderGoal(){
  const fixed=state.operation==='evaluate',goal=fixed?'fixed':state.team_settings.strategy;
  document.querySelectorAll('[data-goal]').forEach(b=>{b.classList.toggle('active',b.dataset.goal===goal);b.setAttribute('aria-pressed',String(b.dataset.goal===goal));});
  $('recommendControls').hidden=fixed;$('fixedControls').hidden=!fixed;$('countLabel').hidden=fixed;$('advancedGoals').hidden=fixed;
  $('calculate').innerHTML=fixed?'验算指定队伍 <span>→</span>':'开始计算 <span>→</span>';$('calculateInline').textContent=fixed?'验算指定队伍 →':'开始计算 →';
  const hints={portfolio:'轮流寻找技能覆盖、综合潜力、基础力、活动加成与商店加成方向的不同卡组。',balanced:'综合力与增分技能联合比较；留影的适用条件和延长时间一起计算。',event:'优先最大化队伍活动 PT 加成，同加成再比较综合潜力；不预测最终活动收益。',shop:'优先最大化队伍商店 PT 加成，同加成再比较综合潜力；不预测最终商店收益。',fixed:'只验算指定队伍的综合力、技能与活动加成，修改绑定后可快速重算。'};
  $('goalHint').textContent=hints[goal]||'按所选方向寻找不同卡组；技能积分和综合力分别保留，便于对照。';
}
function lockButton(kind,id,index){
  const c=card(kind,id);
  return `<button data-lock-kind="${kind}" data-lock-index="${index}" aria-label="${kind==='members'?'必带成员':'必带留影'} ${index+1}${c?'：'+esc(c.name):'：自动'}">${c?`<img src="${esc(c.thumbnail)}" alt=""><span class="slot-name">${esc(c.name)}<br><span class="muted">#${c.id} · ${esc(typeShort[c.type])}</span></span>`:'<span class="slot-add">＋</span><span class="slot-auto">自动选择</span>'}</button>`;
}
function renderLocks(){
  for(const[kind,container,count]of [['members','memberLocks','memberLockCount'],['snaps','snapLocks','snapLockCount']]){
    const ids=state.team_settings[requiredKey(kind)],extra=kind==='snaps'?[...new Set(state.team_settings.required_bindings.map(p=>p.snap_id))].filter(id=>!ids.includes(id)):[],all=[...ids,...extra];
    $(container).innerHTML=Array.from({length:5},(_,i)=>i>=ids.length&&all[i]?`<button data-bound-snap="${all[i]}" aria-label="已固定绑定的留影"><img src="${esc(card(kind,all[i]).thumbnail)}" alt=""><span class="slot-name">${esc(card(kind,all[i]).name)}<br><span class="bound-note">已固定绑定</span></span></button>`:lockButton(kind,ids[i],i)).join('');$(count).textContent=all.length+' / 5';
  }
}
function renderRoleFilters(){
  const s=state.team_settings;$('roleFilterCount').textContent=s.allowed_character_ids.length?'· '+s.allowed_character_ids.length+' 位角色':s.allowed_band_ids.length?'· '+s.allowed_band_ids.length+' 支乐队':'· 不限制';
  $('allowedBands').innerHTML=`<button data-allowed-band="0" class="${!s.allowed_band_ids.length?'active':''}">全部乐队</button>`+catalog.bands.map(b=>`<button data-allowed-band="${b.id}" class="${s.allowed_band_ids.includes(b.id)?'active':''}" aria-pressed="${s.allowed_band_ids.includes(b.id)}">${esc(b.name)}</button>`).join('');
  $('allowedCharacters').innerHTML=`<div class="character-filter-band"><button data-allowed-character="0" class="${!s.allowed_character_ids.length?'active':''}">不限角色</button></div>`+catalog.bands.map(b=>`<div class="character-filter-band"><span>${esc(b.name)}</span>${catalog.characters.filter(c=>c.band_id===b.id).map(c=>`<button data-allowed-character="${c.id}" class="${s.allowed_character_ids.includes(c.id)?'active':''}" aria-pressed="${s.allowed_character_ids.includes(c.id)}">${esc(c.name)}</button>`).join('')}</div>`).join('');
}
function renderRequiredLeader(){
  const chosen=state.team_settings.required_leader_id,mids=filteredCandidates().members;
  $('requiredLeader').innerHTML='<option value="0">自动选择队长</option>'+mids.map(id=>`<option value="${id}">${esc(card('members',id).name)} · #${id}</option>`).join('')+(chosen&&!mids.includes(chosen)?`<option value="${chosen}">#${chosen}（不符合筛选）</option>`:'');$('requiredLeader').value=chosen;
}
function renderBindings(){
  const s=state.team_settings,sids=filteredCandidates().snaps;$('bindingCount').textContent=s.required_bindings.length?'· '+s.required_bindings.length+' 组':'';
  $('bindingRows').innerHTML=s.required_member_ids.map(mid=>{const c=card('members',mid),selected=s.required_bindings.find(p=>p.member_id===mid)?.snap_id||0;return `<div class="binding-row"><div class="binding-member"><img src="${esc(c.thumbnail)}" alt=""><span>${esc(c.name)} · #${mid}${s.required_leader_id===mid?' · 队长':''}</span></div><label>绑定留影<select data-bind-member="${mid}"><option value="0">自动搭配</option>${sids.map(id=>`<option value="${id}" ${id===selected?'selected':''}>${esc(card('snaps',id).title)} · #${id}</option>`).join('')}${selected&&!sids.includes(selected)?`<option value="${selected}" selected>#${selected}（不符合筛选）</option>`:''}</select></label></div>`;}).join('')||'<p class="tiny muted">先在上方指定必带成员或队长。</p>';
}
function renderFixed(){
  const f=state.fixed_team;$('autoLeader').checked=!f.leader_id;
  $('fixedSlots').innerHTML=Array.from({length:5},(_,i)=>{
    const m=card('members',f.member_ids[i]),s=card('snaps',f.snap_ids[i]);
    const button=(kind,c)=>`<button class="fixed-card ${kind==='snaps'?'snap ':''}${c?'':'empty'}" data-fixed-kind="${kind}" data-fixed-index="${i}">${c?`<img src="${esc(c.thumbnail)}" alt="${esc(c.title)}"><span>${esc(c.name)}<br>#${c.id} · ${esc(typeShort[c.type])}</span>`:`<span class="slot-add">＋</span><span>选择${kind==='members'?'成员':'留影'}</span>`}</button>`;
    return `<div class="fixed-slot">${button('members',m)}${button('snaps',s)}<label class="leader-choice"><input type="radio" name="fixed-leader" data-leader="${i}" ${m&&f.leader_id===m.id?'checked':''} ${!m?'disabled':''}>队长</label></div>`;
  }).join('');
}
function eventRuleTitle(rule){
  const c=rule.constraints;
  const id=rule.kind==='members'?c._memberCardId:c._supportCardId;
  if(id){const x=card(rule.kind,id);return `<img class="${rule.kind==='snaps'?'snap-thumb':''}" src="${esc(x.thumbnail)}" alt=""><span>${esc(x.title)}<br><span class="muted">${esc(x.name)} · #${id}</span></span>`;}
  const names=[];
  if(c._cardType)names.push(`<span class="type-dot t${c._cardType}"></span>${esc(typeShort[c._cardType])}属性`);
  if(c._bandId)names.push(esc(catalog.bands.find(b=>b.id===c._bandId)?.name||'#'+c._bandId));
  if(c._characterId)names.push(esc(catalog.characters.find(x=>x.id===c._characterId)?.name||'#'+c._characterId));
  if(c._tagId)names.push('标签 #'+c._tagId);
  return '<span>'+(names.join(' + ')||'全部卡牌')+'</span>';
}
function renderEvent(){
  $('eventName').textContent='#1 · '+catalog.event.name;$('eventDates').textContent=catalog.event.start_at.slice(0,10)+' — '+catalog.event.end_at.slice(0,10)+'（快照预设）';
  const currency={event_pt:'活动 PT',shop_pt:'商店 PT',parameter:'挑战参数'};
  $('eventRules').innerHTML=['members','snaps'].map(kind=>`<div class="event-group"><h3>${kind==='members'?'成员加成 · 按觉醒次数':'留影加成 · 按突破次数'}</h3>${catalog.event.rules.filter(r=>r.kind===kind).map(r=>`<div class="event-rule"><div class="event-rule-title">${eventRuleTitle(r)}</div><div class="event-rule-values">${Object.entries(r.rates).map(([name,values])=>`${currency[name]} +${fmt(values[0]/100)}～${fmt(values[4]/100)}%`).join('<br>')}</div></div>`).join('')}</div>`).join('');
}
function shownCards(){
  const kind=$('kind').value,owned=new Set(state.profile.inventory[kind].map(r=>r.id)),key=selectedKey(kind);
  const query=$('cardSearch').value.trim().toLowerCase().replace(/^#/,'');
  return catalog[kind].filter(c=>{
    if(number($('cardType'))&&c.type!==number($('cardType')))return false;
    if(number($('cardRarity'))&&c.rarity!==number($('cardRarity')))return false;
    if(number($('cardBand'))&&!c.band_ids.includes(number($('cardBand'))))return false;
    if(number($('cardCharacter'))&&!c.character_ids.includes(number($('cardCharacter'))))return false;
    if(query&&!`${c.id} ${c.title} ${c.name}`.toLowerCase().includes(query))return false;
    return $('cardStatus').value==='all'||($('cardStatus').value==='owned'&&owned.has(c.id))||($('cardStatus').value==='new'&&!owned.has(c.id))||($('cardStatus').value==='selected'&&state[key].includes(c.id));
  }).sort((a,b)=>b.id-a.id);
}
function rowFields(kind){return kind==='members'?['level','training_count','awakening_count','live_skill_level']:['level','limit_break_count'];}
function rowReady(kind,row,c){
  if(!row||rowFields(kind).some(f=>!Number.isInteger(row[f])))return false;
  const stage=row[kind==='members'?'training_count':'limit_break_count'];
  return stage>=0&&stage<=4&&row.level>=1&&row.level<=c.caps[stage]&&(kind!=='members'||(row.awakening_count>=0&&row.awakening_count<=4&&row.live_skill_level>=1&&row.live_skill_level<=5));
}
function cardBonus(c,row,kind){
  const stage=row?.[kind==='members'?'awakening_count':'limit_break_count'];if(!Number.isInteger(stage)||!c.event_bonuses_by_stage?.[stage])return '活动加成：按实际'+(kind==='members'?'觉醒':'突破')+'填写后显示';
  const b=c.event_bonuses_by_stage[stage];return `${kind==='members'?'活动':'商店'} +${fmt((kind==='members'?b.event_pt:b.shop_pt)/100)}% · 挑战参数 +${fmt(b.parameter/100)}%`;
}
function renderCards(){
  const kind=$('kind').value,rows=new Map(state.profile.inventory[kind].map(r=>[r.id,r])),shown=shownCards(),names=rowFields(kind);
  $('catalogCount').textContent=shown.length+' 张卡牌';
  document.querySelectorAll('[data-kind-tab]').forEach(b=>b.classList.toggle('active',b.dataset.kindTab===kind));
  $('cardTypeChips').innerHTML=`<button data-card-type="0" class="${!number($('cardType'))?'active':''}">全部属性</button>`+Object.entries(types).map(([id,name])=>`<button data-card-type="${id}" class="${number($('cardType'))===Number(id)?'active':''}"><span class="type-dot t${id}"></span>${esc(name.split(' · ')[0])}</button>`).join('');
  $('cardCatalog').innerHTML=shown.map(c=>{
    const row=rows.get(c.id),required=state.team_settings[requiredKey(kind)].includes(c.id),ready=rowReady(kind,row,c);
    return `<article class="catalog-card ${row?'owned':''} ${required?'required':''}"><button class="card-art ${kind}" data-edit="${kind}" data-id="${c.id}" aria-label="编辑${esc(c.title)}"><img src="${esc(c.thumbnail)}" loading="lazy" alt="${esc(c.title)}"><span class="art-tags"><span>${esc(typeShort[c.type])}</span><span class="${required?'tag-lock':''}">${required?'必带':esc(rarity[c.rarity]||c.rarity)}</span></span></button><div class="card-body"><strong>${esc(c.title)}</strong><span class="card-subtitle">${esc(c.name)} · #${c.id}</span><span class="card-status ${ready?'ready':''}">${row?(ready?`Lv.${row.level} · ${kind==='members'?'Live Lv.'+row.live_skill_level:'突破 '+row.limit_break_count}`:'养成待填写'):'未录入'}</span>${row?`<span class="card-bonus">${esc(cardBonus(c,row,kind))}</span><label class="check-label"><input type="checkbox" data-candidate="${kind}" data-id="${c.id}" ${state[selectedKey(kind)].includes(c.id)?'checked':''}>参与配队</label><div class="card-actions"><button class="secondary" data-edit="${kind}" data-id="${c.id}">编辑养成</button><label class="check-label"><input type="checkbox" data-required="${kind}" data-id="${c.id}" ${required?'checked':''}>必带</label><button class="text-button danger" data-remove="${kind}" data-id="${c.id}">移除</button></div>`:`<div class="card-actions"><button class="primary" data-add="${kind}" data-id="${c.id}">标为已拥有</button></div>`}</div></article>`;
  }).join('')||'<div class="catalog-empty">没有符合条件的卡牌。切换「全部图鉴」或重置筛选。</div>';
  $('batchFields').innerHTML=names.map(f=>`<label>${fields[f]}<input type="number" data-batch="${f}" min="${f==='level'||f==='live_skill_level'?1:0}" max="${f==='level'?90:f==='live_skill_level'?5:4}" placeholder="不修改"></label>`).join('');
}
function selectVisible(on,all=false){
  const kind=$('kind').value,visible=new Set((all?catalog[kind]:shownCards()).map(c=>c.id));
  for(const row of state.profile.inventory[kind])if(visible.has(row.id)){toggle(state[selectedKey(kind)],row.id,on);if(!on)toggle(state.team_settings[requiredKey(kind)],row.id,false);}
  if(!on){state.team_settings.required_bindings=state.team_settings.required_bindings.filter(p=>!visible.has(p[kind==='members'?'member_id':'snap_id']));if(kind==='members'&&visible.has(state.team_settings.required_leader_id))state.team_settings.required_leader_id=0;}
  changed();renderCards();
}
function ensureOwned(kind,id){
  let row=state.profile.inventory[kind].find(r=>r.id===id);
  if(!row){row=kind==='members'?{id,level:null,training_count:null,awakening_count:null,live_skill_level:null,gekisou_skill_level:null}:{id,level:null,limit_break_count:null};state.profile.inventory[kind].push(row);toggle(state[selectedKey(kind)],id,true);changed();renderCards();}
  return row;
}
function fieldControl(field,value){
  if(field==='level')return `<input type="number" min="1" max="90" data-editor-field="${field}" value="${value??''}" placeholder="实际等级">`;
  const values=field==='live_skill_level'?[1,2,3,4,5]:[0,1,2,3,4];
  return `<select data-editor-field="${field}"><option value="">未填写</option>${values.map(v=>`<option value="${v}" ${v===value?'selected':''}>${field==='training_count'?v+1+' 阶':v}</option>`).join('')}</select>`;
}
function openEditor(kind,id){
  if(jobId||evaluating||stale)return;
  const c=card(kind,id),row=ensureOwned(kind,id);editor={kind,id};$('editorTitle').textContent=kind==='members'?'成员卡养成':'留影养成';
  $('editorBody').innerHTML=`<div class="editor-card ${kind}"><img src="${esc(c.thumbnail)}" alt="${esc(c.title)}"><div><h3>${esc(c.title)}</h3><p>${esc(c.name)} · #${c.id}</p><p>${esc(types[c.type])}</p>${c.live_skill_name?`<p>Live 技能：${esc(c.live_skill_name)}</p>`:''}<p class="card-bonus">${esc(cardBonus(c,row,kind))}</p></div></div>`;
  $('editorFields').innerHTML=rowFields(kind).map(f=>`<label>${fields[f]}${fieldControl(f,row[f])}</label>`).join('');
  $('editorError').textContent='';editorHint();$('cardEditor').showModal();
}
function editorHint(){
  const kind=editor.kind,stageEl=$('editorFields').querySelector('[data-editor-field="'+(kind==='members'?'training_count':'limit_break_count')+'"]'),stage=number(stageEl),c=card(kind,editor.id);
  $('editorHint').textContent=stage===null?'请按实际养成填写，空白保留为未知。':'当前阶段等级上限 '+c.caps[stage]+'。'+(kind==='members'?'特训影响等级上限，觉醒影响队长技能及活动加成。':'突破影响等级上限、支援率及活动加成。');
  $('editorFields').querySelector('[data-editor-field="level"]').max=stage===null?90:c.caps[stage];
}
function validateRow(kind,row,c){
  for(const f of rowFields(kind)){const v=row[f],low=f==='level'||f==='live_skill_level'?1:0,high=f==='level'?90:f==='live_skill_level'?5:4;if(v!==null&&(!Number.isInteger(v)||v<low||v>high))throw new Error(fields[f]+'需要 '+low+'～'+high+' 的整数。');}
  const stage=row[kind==='members'?'training_count':'limit_break_count'];if(Number.isInteger(stage)&&Number.isInteger(row.level)&&row.level>c.caps[stage])throw new Error('等级超过实际阶段上限 '+c.caps[stage]+'，修改未应用。');
}
function saveCard(){
  try{const {kind,id}=editor,row=clone(state.profile.inventory[kind].find(r=>r.id===id));$('editorFields').querySelectorAll('[data-editor-field]').forEach(el=>row[el.dataset.editorField]=number(el));validateRow(kind,row,card(kind,id));Object.assign(state.profile.inventory[kind].find(r=>r.id===id),row);changed();renderCards();$('cardEditor').close();}
  catch(e){$('editorError').textContent=e.message;}
}
function checkRequired(kind,id,index=null){
  const ids=[...state.team_settings[requiredKey(kind)]];if(index!==null)ids.splice(index,1);if(!ids.includes(id))ids.push(id);
  const bound=state.team_settings.required_bindings.map(p=>kind==='members'?p.member_id:p.snap_id);
  if(new Set([...ids,...bound]).size>5)throw new Error('必带卡和固定绑定合计每种卡最多 5 张。');
  if(kind==='members'&&new Set(ids.map(i=>card(kind,i).character_id)).size!==ids.length)throw new Error('必带成员不能属于同一角色。');
}
function openPicker(target,kind,index){
  if(jobId||evaluating||stale)return;picker={target,kind,index};
  $('pickerTitle').textContent=(target==='required'?'选择必带':'指定队伍 · 选择')+(kind==='members'?'成员':'留影');
  $('pickerHint').textContent=target==='required'?'从符合本次筛选的已拥有卡中选择，选中后加入候选。':'从已拥有卡中选择，保留此位置的成员与留影绑定。';
  $('pickerSearch').value='';$('pickerType').value='0';$('pickerError').textContent='';$('pickerClear').textContent=target==='required'?'恢复自动选择':'移除此卡';
  renderPicker();$('cardPicker').showModal();
}
function renderPicker(){
  const {target,kind,index}=picker,query=$('pickerSearch').value.trim().toLowerCase().replace(/^#/,''),s=state.team_settings;
  const current=target==='required'?s[requiredKey(kind)][index]:state.fixed_team[kind==='members'?'member_ids':'snap_ids'][index];
  const picked=target==='required'?s[requiredKey(kind)].filter((_,i)=>i!==index):state.fixed_team[kind==='members'?'member_ids':'snap_ids'].filter((id,i)=>id&&i!==index);
  const rows=state.profile.inventory[kind].map(r=>card(kind,r.id)).filter(c=>{
    if(number($('pickerType'))&&c.type!==number($('pickerType')))return false;
    if(query&&!`${c.id} ${c.title} ${c.name}`.toLowerCase().includes(query))return false;
    if(picked.includes(c.id)||(kind==='members'&&picked.some(id=>card(kind,id).character_id===c.character_id)))return false;
    if(target==='required'){const type=s[kind==='members'?'member_type':'snap_type'];if(type&&c.type!==type)return false;if(c.rarity<s[kind==='members'?'member_min_rarity':'snap_min_rarity'])return false;if(kind==='members'&&((s.member_band&&c.band_id!==s.member_band)||(s.allowed_band_ids.length&&!s.allowed_band_ids.includes(c.band_id))||(s.allowed_character_ids.length&&!s.allowed_character_ids.includes(c.character_id))))return false;}
    return true;
  }).sort((a,b)=>b.id-a.id);
  $('pickerList').innerHTML=rows.map(c=>`<button class="picker-card ${kind} ${c.id===current?'active':''}" data-pick="${c.id}"><img src="${esc(c.thumbnail)}" loading="lazy" alt=""><span><strong>${esc(c.title)}</strong><small>${esc(c.name)}</small><small>#${c.id} · ${esc(typeShort[c.type])}</small></span></button>`).join('')||'<div class="catalog-empty">没有可选卡。请先录入卡库，或调整筛选。</div>';
}
function pickCard(id){
  try{const {target,kind,index}=picker;
    if(target==='required'){const ids=state.team_settings[requiredKey(kind)],old=ids[index];if(id){checkRequired(kind,id,index);if(index<ids.length)ids[index]=id;else ids.push(id);toggle(state[selectedKey(kind)],id,true);}else ids.splice(index,1);if(kind==='members'&&old!==id){state.team_settings.required_bindings=state.team_settings.required_bindings.filter(p=>p.member_id!==old);if(state.team_settings.required_leader_id===old)state.team_settings.required_leader_id=id||0;}}
    else{const key=kind==='members'?'member_ids':'snap_ids';state.fixed_team[key][index]=id||null;if(kind==='members'&&!state.fixed_team.member_ids.includes(state.fixed_team.leader_id))state.fixed_team.leader_id=0;}
    changed();renderCards();$('cardPicker').close();
  }catch(e){$('pickerError').textContent=e.message;}
}
function updateIndexed(array,key,id,field,value){let row=array.find(r=>r[key]===id);if(!row){row={[key]:id};array.push(row);}row[field]=value;}
function renderGrowth(){
  $('name').value=state.name;$('totalRank').value=state.profile.character_total_rank??'';$('tgwRank').value=state.profile.tgw_card_rank??'';
  $('growthBandTabs').innerHTML=`<button data-growth-band="0" class="${!growthBand?'active':''}">全部乐队</button>`+catalog.bands.map(b=>`<button data-growth-band="${b.id}" class="${growthBand===b.id?'active':''}">${esc(b.name)}</button>`).join('');
  $('growthFields').innerHTML=catalog.bands.filter(b=>!growthBand||b.id===growthBand).map(b=>`<section class="panel growth-band"><h3>${esc(b.name)}</h3><div class="growth-caption">角色等级</div><div class="growth-row">${catalog.characters.filter(c=>c.band_id===b.id).map(c=>`<label>${esc(c.name)}<input type="number" min="1" max="1000" data-rank="${c.id}" value="${state.profile.character_ranks.find(r=>r.character_id===c.id)?.rank??''}" placeholder="实际等级"></label>`).join('')}</div><div class="growth-caption">乐队道具</div><div class="growth-row">${catalog.facilities.filter(f=>f.band_id===b.id).map(f=>`<label>${esc(f.name)}<input type="number" min="1" max="${f.max_level}" data-facility="${f.id}" value="${state.profile.facilities.find(r=>r.id===f.id)?.level??''}" placeholder="实际等级"></label>`).join('')}</div></section>`).join('');
}
function renderArchive(){
  $('archiveCount').textContent=archives.profiles.length+' 份';
  $('archiveList').innerHTML=archives.profiles.map(p=>`<button class="archive-entry ${p.id===archives.active_id?'active':''}" data-profile="${esc(p.id)}"><span class="badge ${p.input.profile_kind==='plan'?'plan':''}">${p.input.is_demo?'合成示例':p.input.profile_kind==='plan'?'升级规划':'实际养成'}</span><strong>${esc(p.input.name)}</strong><p>${p.input.profile.inventory.members.length} 成员 · ${p.input.profile.inventory.snaps.length} 留影${p.id===archives.active_id?' · 使用中':''}</p></button>`).join('');
  $('archiveName').value=state.name;$('profileKind').value=state.profile_kind;$('profileKind').disabled=state.is_demo||!!jobId||evaluating||stale;
  $('deleteProfile').disabled=archives.profiles.length<2||!!jobId||evaluating||stale;
  $('profileStats').innerHTML=`<div><strong>${state.profile.inventory.members.length}</strong><span>成员卡</span></div><div><strong>${state.profile.inventory.snaps.length}</strong><span>留影</span></div><div><strong>${state.profile.facilities.filter(r=>r.level!=null).length} / ${catalog.facilities.length}</strong><span>已填写道具</span></div>`;
}
function renderAll(){renderControls();renderCards();renderGrowth();renderArchive();}
function download(value,name){downloadBlob(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),name);}
function downloadBlob(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function post(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Planner-Token':token},body:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw new Error(value.error||'操作失败');return value;}
function busy(on){
  $('inputArea').disabled=on||stale;for(const id of ['sample','new','import','profileSelect','calculate','calculateInline','profileManage'])$(id).disabled=on||stale;
  $('progressBox').hidden=!on;$('runStatus').innerHTML=on?'<span class="status-dot working"></span>正在计算':'<span class="status-dot"></span>'+(stale?'请刷新档案':'就绪');
}
function growthInput(){
  const x=clone(state);if(x.operation==='evaluate'){x.candidate_member_ids=x.fixed_team.member_ids.filter(Boolean);x.candidate_snap_ids=x.fixed_team.snap_ids.filter(Boolean);Object.assign(x.team_settings,{member_type:0,snap_type:0,member_band:0,member_min_rarity:0,snap_min_rarity:0,allowed_band_ids:[],allowed_character_ids:[]});}return x;
}
async function checkGrowth(){
  try{const value=await post('/api/check-growth',growthInput());issues=value.issues;
    $('growthIssues').innerHTML=issues.length?issues.map((i,index)=>`<button class="issue-button" data-issue="${index}">${esc(i.label)}：${esc(i.reason)} <span>→ 补填</span></button>`).join(''):'<p class="tiny muted">所需实际养成检查通过。</p>';
    return value;
  }catch(e){notice(e.message,true);return null;}
}
function gotoIssue(index){const issue=issues[index];if(issue.target==='card'){openEditor(issue.kind,issue.id);return;}growthBand=0;tab('growth');renderGrowth();const id=issue.target==='global'?(issue.field==='character_total_rank'?'totalRank':'tgwRank'):null;const el=id?$(id):$('growthFields').querySelector(`[data-${issue.target==='rank'?'rank':'facility'}="${issue.id}"]`);el?.scrollIntoView({behavior:'smooth',block:'center'});el?.focus();}
function skillText(slot){return slot.live_effects.map(e=>`${e.effect_value/100}% × ${e.effective_activation_ms/1000} 秒${e.skill_effect_type===2004?'（PERFECT 目标 '+e.judgement_targets.filter(t=>t===5).length+' 项）':''}`).join('；');}
function teamText(team,index){
  const settings=result.settings,lines=[`Our Notes 配队 ${index+1} · ${team.reason}`,`档案：${resultInput.name}${resultInput.profile_kind==='plan'?'（升级规划）':resultInput.is_demo?'（合成示例）':''}`,`模式：${settings.mode==='normal'?'普通单人':'活动 1 挑战'}`,`综合力场景：${settings.music_attribute?types[settings.music_attribute]+'属性匹配，不含标签':'基础综合力，不含乐曲属性/标签'}`,`综合力：${team.power}`,`活动加成：${team.bonuses_10000.event_pt/100}%；商店加成：${team.bonuses_10000.shop_pt/100}%`, ''];
  team.slots.forEach((slot,i)=>{const m=card('members',slot.member_id),s=card('snaps',slot.snap_id),mr=resultInput.profile.inventory.members.find(r=>r.id===m.id),sr=resultInput.profile.inventory.snaps.find(r=>r.id===s.id);lines.push(`${i+1}. 成员 #${m.id} ${m.title} / ${m.name}${m.id===team.leader_id?' [队长]':''}`,`   Lv.${mr.level}；特训 ${mr.training_count+1} 阶；觉醒 ${mr.awakening_count}；Live Lv.${slot.live_skill_level}：${skillText(slot)}`,`   Snap #${s.id} ${s.title} / ${s.name}；Lv.${sr.level}；突破 ${sr.limit_break_count}；延长 ${slot.extension_ms/1000} 秒`);});
  lines.push('','显示位置用于成员与留影绑定，不代表技能触发顺序。', '在 bdon 按成员、留影和实际综合力设置重放。潜力指数不作为谱面分数。','数据快照 2026-10-01 · AP/PERFECT · 撃奏关闭',result.bdon_url);return lines.join('\n');
}
function teamMetrics(team,detail=false){return `<div class="team-metrics ${detail?'detail-metrics':''}"><div><strong>${fmt(team.power)}</strong><span>综合力</span></div><div><strong>${fmt(team.potential['120'])}</strong><span>综合潜力 · 120 秒参考</span></div><div><strong>${fmt(team.skill_percent_seconds)}</strong><span>技能积分 · %·秒</span></div><div class="bonus-metric"><strong>+${fmt(team.bonuses_10000.event_pt/100)}% / +${fmt(team.bonuses_10000.shop_pt/100)}%</strong><span>活动 / 商店加成</span></div></div>`;}
function resultCategory(team){return ['short','skill'].includes(team.selected_for)?'skill':['balanced','long','power'].includes(team.selected_for)?'balanced':team.selected_for;}
function renderResult(go=true){
  $('emptyResult').hidden=true;$('results').hidden=false;$('resultCount').textContent=result.teams.length+' 套';
  $('resultTitle').textContent=result.status==='complete_fixed_team'?'指定队伍验算完成':`找到 ${result.teams.length} 套不同配队`;
  $('resultHint').textContent=`${resultInput.name} · ${result.settings.mode==='normal'?'普通单人':'活动 1 挑战'} · ${result.settings.music_attribute?types[result.settings.music_attribute]+'匹配场景':'基础综合力场景'} · ${result.search.elapsed_seconds} 秒${result.search.cached_teams?' · 复用 '+result.search.cached_teams+' 套':''}${result.search.exhausted?' · 当前条件下没有更多卡组':''}`;
  resultFilter='all';$('resultSort').value='recommended';renderTeamList();renderComparison();if(go){tab('output');window.scrollTo({top:0,behavior:'smooth'});}
}
function renderTeamList(){
  const categories={all:'全部',balanced:'综合 / 基础力',skill:'技能覆盖',event:'活动加成',shop:'商店加成',fixed:'指定队伍'};
  const visible=new Set(result.teams.map(resultCategory));$('resultFilters').innerHTML=Object.entries(categories).filter(([key])=>key==='all'||visible.has(key)).map(([key,label])=>`<button data-result-filter="${key}" class="${key===resultFilter?'active':''}">${label} ${key==='all'?result.teams.length:result.teams.filter(t=>resultCategory(t)===key).length}</button>`).join('');
  const sort=$('resultSort').value,key=t=>sort==='potential'?t.potential['120']:sort==='power'?t.power:sort==='skill'?t.skill_percent_seconds:sort==='event'?t.bonuses_10000.event_pt:t.bonuses_10000.shop_pt;
  let rows=result.teams.map((t,i)=>({t,i})).filter(({t})=>resultFilter==='all'||resultCategory(t)===resultFilter);if(sort!=='recommended')rows.sort((a,b)=>key(b.t)-key(a.t)||a.i-b.i);
  $('teamList').innerHTML=rows.map(({t,i})=>`<article class="team-card ${comparison.includes(i)?'comparing':''}"><div class="team-card-head"><div><span class="team-number">${String(i+1).padStart(2,'0')}</span><span class="team-direction">${esc(t.reason)}</span></div><label class="check-label"><input type="checkbox" data-compare="${i}" ${comparison.includes(i)?'checked':''}>对比</label></div><div class="team-strip">${t.slots.map(slot=>{const m=card('members',slot.member_id),s=card('snaps',slot.snap_id);return `<div class="team-mini-slot">${m.id===t.leader_id?'<span class="mini-leader">队长</span>':''}<img src="${esc(m.thumbnail)}" loading="lazy" alt="${esc(m.title)}"><img class="mini-snap" src="${esc(s.thumbnail)}" loading="lazy" alt="${esc(s.title)}"><small>${esc(m.name)}</small></div>`;}).join('')}</div>${teamMetrics(t)}<div class="team-card-footer"><button class="secondary" data-detail="${i}">队伍详情</button><div class="actions"><button class="text-button" data-evaluate="${i}">指定验算</button><button class="secondary" data-copy="${i}">复制参数</button></div></div></article>`).join('')||'<div class="panel">当前条件下没有完整队伍，请调整必带卡或同属性条件。</div>';
}
function renderComparison(){
  $('comparison').hidden=comparison.length<2;if(comparison.length<2)return;
  const rows=[['综合力',t=>t.power],['综合潜力（120 秒）',t=>t.potential['120']],['技能积分（%·秒）',t=>t.skill_percent_seconds],['活动加成（%）',t=>t.bonuses_10000.event_pt/100],['商店加成（%）',t=>t.bonuses_10000.shop_pt/100]];
  $('comparison').innerHTML=`<div class="panel-head"><h2>同条件队伍对比</h2><button id="clearComparison" class="text-button">清空对比</button></div><table class="compare-table"><thead><tr><th>指标</th>${comparison.map(i=>`<th>候选 ${i+1}</th>`).join('')}</tr></thead><tbody>${rows.map(([label,get])=>{const max=Math.max(...comparison.map(i=>get(result.teams[i])));return `<tr><td>${label}</td>${comparison.map(i=>`<td class="${get(result.teams[i])===max?'best':''}">${fmt(get(result.teams[i]))}</td>`).join('')}</tr>`;}).join('')}</tbody></table>`;
  $('clearComparison').onclick=()=>{comparison=[];renderComparison();renderTeamList();};
}
function openDetails(index){
  detailIndex=index;const t=result.teams[index];$('teamDetailTitle').textContent=`候选 ${index+1} · ${t.reason}`;
  $('teamDetailBody').innerHTML=teamMetrics(t,true)+`<div class="detail-slots">${t.slots.map(slot=>{
    const m=card('members',slot.member_id),s=card('snaps',slot.snap_id),mr=resultInput.profile.inventory.members.find(r=>r.id===m.id),sr=resultInput.profile.inventory.snaps.find(r=>r.id===s.id);
    return `<section class="detail-slot"><img src="${esc(m.thumbnail)}" alt="${esc(m.title)}"><h4>${m.id===t.leader_id?'<span class="leader-chip">队长 · </span>':''}${esc(m.title)}<br>${esc(m.name)}</h4><p>#${m.id} · Lv.${mr.level}<br>特训 ${mr.training_count+1} 阶 · 觉醒 ${mr.awakening_count}<br>Live Lv.${slot.live_skill_level}<br>${esc(skillText(slot))}</p><img class="detail-snap" src="${esc(s.thumbnail)}" alt="${esc(s.title)}"><h4>${esc(s.title)}</h4><p>Snap #${s.id} · Lv.${sr.level} · 突破 ${sr.limit_break_count}<br>生效延长 ${slot.extension_ms/1000} 秒</p></section>`;
  }).join('')}</div><p class="tiny muted">位置用于绑定留影，不代表技能触发顺序。综合力采用本次场景；潜力是参考指标，实际歌曲得分请在 bdon 验证。</p>`;
  $('imageHint').textContent='';$('teamDetails').showModal();
}
function openCopy(index){$('copyText').value=teamText(result.teams[index],index);$('copyHint').textContent='';$('copyDialog').showModal();copy();}
function loadFixed(index){
  const t=clone(result.teams[index]);state.fixed_team={member_ids:t.member_ids,snap_ids:t.snap_ids,leader_id:t.leader_id};state.operation='evaluate';
  $('teamDetails').close();changed();renderControls();tab('plan');notice('已载入指定队伍。可以替换成员、留影或队长，再直接验算。');
}
async function saveTeamImage(){
  try{
    const t=result.teams[detailIndex],canvas=document.createElement('canvas');canvas.width=1500;canvas.height=1010;const ctx=canvas.getContext('2d');
    ctx.fillStyle='#faf7fd';ctx.fillRect(0,0,1500,1010);ctx.fillStyle='#302b3c';ctx.font='bold 36px Microsoft YaHei';ctx.fillText('Our Notes · 队伍 '+(detailIndex+1)+' / '+t.reason,55,73);
    ctx.font='21px Microsoft YaHei';ctx.fillStyle='#83728f';ctx.fillText(resultInput.name+' · '+(result.settings.mode==='normal'?'普通单人':'活动 1 挑战')+' · '+(result.settings.music_attribute?typeShort[result.settings.music_attribute]+'属性匹配':'基础综合力'),55,116);
    ctx.fillStyle='#83539b';ctx.font='bold 30px Microsoft YaHei';ctx.fillText('综合力 '+fmt(t.power)+'     活动 +'+fmt(t.bonuses_10000.event_pt/100)+'%     商店 +'+fmt(t.bonuses_10000.shop_pt/100)+'%',55,170);
    const load=src=>new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('卡图加载失败，请重试'));img.src=src;});
    const images=await Promise.all(t.slots.flatMap(slot=>[load(card('members',slot.member_id).thumbnail),load(card('snaps',slot.snap_id).thumbnail)]));
    function art(img,x,y,w,h){const ratio=Math.min(w/img.width,h/img.height);ctx.drawImage(img,x+(w-img.width*ratio)/2,y+(h-img.height*ratio)/2,img.width*ratio,img.height*ratio);}
    function wrap(text,x,y,width,maxLines=3){let line='',lines=0;for(const ch of text){if(ctx.measureText(line+ch).width>width){if(lines+1===maxLines){while(ctx.measureText(line+'…').width>width)line=line.slice(0,-1);ctx.fillText(line+'…',x,y);return y+26;}ctx.fillText(line,x,y);y+=26;line=ch;lines++;}else line+=ch;}if(line){ctx.fillText(line,x,y);y+=26;}return y;}
    t.slots.forEach((slot,i)=>{const x=55+i*280,m=card('members',slot.member_id),s=card('snaps',slot.snap_id);ctx.fillStyle='white';ctx.fillRect(x,210,266,677);art(images[i*2],x+12,229,242,286);ctx.fillStyle='#342e3f';ctx.font='bold 19px Microsoft YaHei';let y=wrap((m.id===t.leader_id?'队长 · ':'')+m.title,x+12,541,242,2);ctx.font='17px Microsoft YaHei';ctx.fillStyle='#81708e';wrap('#'+m.id+' · '+m.name,x+12,y+3,242,1);art(images[i*2+1],x+12,618,242,126);ctx.fillStyle='#342e3f';ctx.font='17px Microsoft YaHei';wrap('Snap #'+s.id+' · '+s.title,x+12,765,242,2);ctx.fillStyle='#81708e';ctx.font='16px Microsoft YaHei';wrap('Live Lv.'+slot.live_skill_level+' · '+skillText(slot),x+12,829,242,2);});
    ctx.font='19px Microsoft YaHei';ctx.fillStyle='#82728f';ctx.fillText('数据快照 2026-10-01 · AP/PERFECT · 撃奏关闭 · 成员/留影绑定位置不代表技能触发顺序',55,931);ctx.fillText('配队参考，实际歌曲出分请核对：https://bdon.moe/tools/chart-data',55,969);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('图片生成失败');downloadBlob(blob,resultInput.name+'-队伍'+(detailIndex+1)+'.png');$('imageHint').textContent='队伍图片已保存。';
  }catch(e){$('imageHint').textContent=e.message;}
}
function rememberResult(){activeProfile().last_result={input:clone(resultInput),result:clone(result),signature};save();}
async function follow(id){
  jobId=id;sessionStorage.setItem(JOB,id);busy(true);
  try{while(true){const response=await fetch('/api/jobs/'+id),job=await response.json();if(!response.ok)throw new Error(job.error||'任务已失效，请重新计算。');
    $('progressTitle').textContent=job.stage;$('progressCount').textContent=`${job.done} / ${job.total}`;$('progressBar').max=job.total;$('progressBar').value=job.done;
    if(job.status==='complete'){result=job.result;rememberResult();renderResult();break;}
    if(job.status==='cancelled'){notice(job.stage);break;}if(job.status==='error')throw new Error(job.error);
    await new Promise(resolve=>setTimeout(resolve,250));
  }}catch(e){notice(e.message,true);}finally{jobId=null;sessionStorage.removeItem(JOB);busy(false);}
}
async function calculate(){
  if(jobId||evaluating||starting||stale)return;notice('');
  const f=filteredCandidates();
  if(state.operation!=='evaluate'&&(new Set(f.members.map(id=>card('members',id).character_id)).size<5||f.snaps.length<5)){tab('plan');notice('筛选后至少需要 5 位不同角色的成员卡和 5 张留影，请先调整卡库或筛选。',true);return;}
  if(state.operation==='evaluate'&&(state.fixed_team.member_ids.some(id=>!id)||state.fixed_team.snap_ids.some(id=>!id))){tab('plan');notice('请为指定队伍选择 5 名成员并分别绑定 5 张留影。',true);return;}
  starting=true;busy(true);
  const growth=await checkGrowth();if(!growth||!growth.complete||stale){starting=false;busy(false);tab('plan');notice(stale?'另一标签页已修改档案，请刷新。':'请先补齐所需养成。点击下方项目可直接填写。',true);return;}
  try{resultInput=clone(state);result=null;comparison=[];activeProfile().last_result=null;save();$('results').hidden=true;$('emptyResult').hidden=false;
    if(state.operation==='evaluate'){evaluating=true;$('progressTitle').textContent='正在验算指定队伍';$('progressCount').textContent='';$('progressBar').value=0;result=await post('/api/evaluate',resultInput);rememberResult();renderResult();evaluating=false;busy(false);}
    else{const value=await post('/api/optimize',resultInput);await follow(value.job_id);}
  }catch(e){evaluating=false;notice(e.message,true);busy(false);}finally{starting=false;}
}
async function importFile(file){
  if(!file)return;if(file.size>5*1024*1024)throw new Error('档案文件超过 5 MiB。');const raw=JSON.parse(await file.text());
  if(raw.schema_version===2&&Array.isArray(raw.profiles)){const entries=raw.profiles.map(p=>normalize(p.input));if(!entries.length)throw new Error('备份中没有档案。');for(const input of entries)archives.profiles.push({id:crypto.randomUUID(),input,last_result:null});archives.active_id=archives.profiles.at(-1).id;state=activeProfile().input;changed();renderAll();notice('已追加导入 '+entries.length+' 份档案。原有档案保留。');}
  else{addProfile(raw);notice('卡库已导入为独立档案，原有档案保留。歌曲设置不参与计算。');}
}
async function init(){
  try{
    const response=await fetch('/api/bootstrap'),value=await response.json();if(!response.ok)throw new Error(value.error||'启动失败');
    ({catalog,token,sample,defaults,version}=value);signature=value.engine_signature;cardIndex={members:new Map(catalog.members.map(c=>[c.id,c])),snaps:new Map(catalog.snaps.map(c=>[c.id,c]))};$('version').textContent='v'+version.replace('-local','');
    for(const id of ['memberType','snapType','cardType','pickerType'])$(id).innerHTML=options(types);$('powerAttribute').innerHTML=options(types,'基础值（无乐曲加成）');
    const bands=Object.fromEntries(catalog.bands.map(b=>[b.id,b.name]));for(const id of ['memberBand','cardBand'])$(id).innerHTML=options(bands);$('cardCharacter').innerHTML=options(Object.fromEntries(catalog.characters.map(c=>[c.id,c.name])));
    for(const[id,kind]of [['memberRarity','members'],['snapRarity','snaps']])$(id).innerHTML='<option value="0">不限稀有度</option>'+[...new Set(catalog[kind].map(c=>c.rarity))].sort((a,b)=>a-b).map(r=>`<option value="${r}">${esc(rarity[r]||r)} 及以上</option>`).join('');
    $('cardRarity').innerHTML=options(Object.fromEntries([...new Set([...catalog.members,...catalog.snaps].map(c=>c.rarity))].sort((a,b)=>a-b).map(r=>[r,rarity[r]||r])));
    const stored=localStorage.getItem(ARCHIVES);savedArchives=stored;savedProfile=localStorage.getItem(STORE);
    try{if(stored){archives=JSON.parse(stored);if(archives.schema_version!==2||!Array.isArray(archives.profiles)||!archives.profiles.length)throw new Error('档案格式不正确');const ids=new Set();for(const p of archives.profiles){if(typeof p.id!=='string'||ids.has(p.id))throw new Error('档案编号重复');ids.add(p.id);p.input=normalize(p.input);}if(!ids.has(archives.active_id))throw new Error('当前档案不存在');}
      else{const legacy=localStorage.getItem(STORE),id=crypto.randomUUID();archives={schema_version:2,active_id:id,profiles:[{id,input:legacy?normalize(JSON.parse(legacy)):fresh(),last_result:null}]};}
    }catch(e){const id=crypto.randomUUID();archives={schema_version:2,active_id:id,profiles:[{id,input:fresh(),last_result:null}]};notice('已保存档案无法读取：'+e.message+'。请导入备份；本次不会覆盖旧记录。',true);stale=true;}
    state=activeProfile().input;$('cardStatus').value=state.profile.inventory.members.length?'owned':'all';renderAll();renderEvent();restoreResult();tab(location.hash.slice(1)||'plan',false);if(stale)busy(false);
    document.querySelectorAll('[data-tab]').forEach(el=>el.addEventListener('click',()=>tab(el.dataset.tab)));window.addEventListener('hashchange',()=>tab(location.hash.slice(1),false));
    for(const id of ['editInventory'])on(id,'click',()=>tab('inventory'));for(const id of ['backPlan','emptyPlan'])on(id,'click',()=>tab('plan'));on('profileManage','click',()=>tab('archive'));
    on('profileSelect','change',()=>switchProfile($('profileSelect').value));on('archiveList','click',e=>{const b=e.target.closest('[data-profile]');if(b)switchProfile(b.dataset.profile);});
    for(const[id,key]of Object.entries({memberType:'member_type',snapType:'snap_type',memberBand:'member_band',mode:'mode',strategy:'strategy',count:'count',powerAttribute:'music_attribute',memberRarity:'member_min_rarity',snapRarity:'snap_min_rarity'}))on(id,'change',()=>{state.team_settings[key]=['mode','strategy'].includes(key)?$(id).value:Number($(id).value);if(id==='strategy')state.operation='recommend';if(id==='memberBand')state.team_settings.allowed_band_ids=[];changed();renderGoal();});
    on('allowedBands','click',e=>{const b=e.target.closest('[data-allowed-band]');if(!b)return;const id=Number(b.dataset.allowedBand);state.team_settings.member_band=0;$('memberBand').value=0;if(!id)state.team_settings.allowed_band_ids=[];else toggle(state.team_settings.allowed_band_ids,id,!state.team_settings.allowed_band_ids.includes(id));changed();});
    on('allowedCharacters','click',e=>{const b=e.target.closest('[data-allowed-character]');if(!b)return;const id=Number(b.dataset.allowedCharacter);if(!id)state.team_settings.allowed_character_ids=[];else toggle(state.team_settings.allowed_character_ids,id,!state.team_settings.allowed_character_ids.includes(id));changed();});
    on('requiredLeader','change',()=>{const id=number($('requiredLeader'));try{if(id){checkRequired('members',id);toggle(state.team_settings.required_member_ids,id,true);}state.team_settings.required_leader_id=id;changed();renderCards();}catch(e){renderRequiredLeader();throw e;}});
    for(const[id,key]of [['minEventBonus','min_event_bonus_10000'],['minShopBonus','min_shop_bonus_10000']])on(id,'change',()=>{const v=number($(id))??0,raw=Math.round(v*100);if(!Number.isFinite(v)||v<0||v>10000||Math.abs(v*100-raw)>1e-7){$(id).value=state.team_settings[key]?state.team_settings[key]/100:'';throw new Error('加成底线需要非负百分比，最多两位小数。');}state.team_settings[key]=raw;changed();});
    on('bindingRows','change',e=>{const el=e.target;if(!el.dataset.bindMember)return;const mid=Number(el.dataset.bindMember),sid=number(el),bindings=state.team_settings.required_bindings.filter(p=>p.member_id!==mid);if(sid&&bindings.some(p=>p.snap_id===sid)){renderBindings();throw new Error('同一张留影不能固定绑定给两名成员。');}if(sid&&new Set([...state.team_settings.required_snap_ids,...bindings.map(p=>p.snap_id),sid]).size>5){renderBindings();throw new Error('必带与固定绑定合计不能超过 5 张留影。');}if(sid){bindings.push({member_id:mid,snap_id:sid});toggle(state.candidate_snap_ids,sid,true);}state.team_settings.required_bindings=bindings;changed();renderCards();});
    on('pureType','change',()=>{state.team_settings.pure_type=$('pureType').checked;changed();});on('goalButtons','click',e=>{const b=e.target.closest('[data-goal]');if(!b)return;state.operation=b.dataset.goal==='fixed'?'evaluate':'recommend';if(state.operation==='recommend')state.team_settings.strategy=b.dataset.goal;changed();renderControls();});
    on('resetConstraints','click',()=>{Object.assign(state.team_settings,{member_type:0,snap_type:0,member_band:0,pure_type:false,required_member_ids:[],required_snap_ids:[],member_min_rarity:0,snap_min_rarity:0,allowed_band_ids:[],allowed_character_ids:[],required_leader_id:0,required_bindings:[],min_event_bonus_10000:0,min_shop_bonus_10000:0});changed();renderControls();renderCards();});
    for(const id of ['memberLocks','snapLocks'])on(id,'click',e=>{const b=e.target.closest('[data-lock-kind]');if(b)openPicker('required',b.dataset.lockKind,Number(b.dataset.lockIndex));const bound=e.target.closest('[data-bound-snap]');if(bound){$('bindingDetails').open=true;const mid=state.team_settings.required_bindings.find(p=>p.snap_id===Number(bound.dataset.boundSnap))?.member_id;$('bindingRows').querySelector(`[data-bind-member="${mid}"]`)?.focus();}});
    on('fixedSlots','click',e=>{const b=e.target.closest('[data-fixed-kind]');if(b)openPicker('fixed',b.dataset.fixedKind,Number(b.dataset.fixedIndex));});
    on('fixedSlots','change',e=>{if(e.target.dataset.leader!==undefined){state.fixed_team.leader_id=state.fixed_team.member_ids[Number(e.target.dataset.leader)];changed();}});
    on('autoLeader','change',()=>{state.fixed_team.leader_id=$('autoLeader').checked?0:state.fixed_team.member_ids.find(Boolean)||0;changed();});on('clearFixed','click',()=>{state.fixed_team={member_ids:Array(5).fill(null),snap_ids:Array(5).fill(null),leader_id:0};changed();});
    for(const id of ['kind','cardType','cardRarity','cardBand','cardCharacter','cardStatus'])on(id,'change',renderCards);on('cardSearch','input',renderCards);
    on('kindButtons','click',e=>{const b=e.target.closest('[data-kind-tab]');if(b){$('kind').value=b.dataset.kindTab;renderCards();}});on('cardTypeChips','click',e=>{const b=e.target.closest('[data-card-type]');if(b){$('cardType').value=b.dataset.cardType;renderCards();}});
    on('clearFilters','click',()=>{for(const id of ['cardType','cardRarity','cardBand','cardCharacter'])$(id).value=0;$('cardSearch').value='';$('cardStatus').value=state.profile.inventory[$('kind').value].length?'owned':'all';renderCards();});
    on('selectVisible','click',()=>selectVisible(true));on('unselectVisible','click',()=>selectVisible(false));on('selectAllOwned','click',()=>selectVisible(true,true));
    on('sample','click',()=>{addProfile(sample);tab('plan');notice('已新建合成示例档案，可直接计算。原档案已保留。');});on('new','click',()=>{addProfile(fresh());tab('archive');});
    on('import','change',async()=>{try{await importFile($('import').files[0]);}finally{$('import').value='';}});on('export','click',()=>download(state,state.name+'-卡库.json'));on('exportAll','click',()=>download({schema_version:2,profiles:archives.profiles.map(p=>({input:p.input}))},'OurNotes-全部档案.json'));
    on('copyProfile','click',()=>{const input=clone(state);input.name+=' · 升级规划';input.profile_kind='plan';addProfile(input);});on('deleteProfile','click',()=>{if(archives.profiles.length<2)return;if(!confirm('删除档案「'+state.name+'」？如需保留，请先导出。'))return;archives.profiles=archives.profiles.filter(p=>p.id!==archives.active_id);archives.active_id=archives.profiles[0].id;state=activeProfile().input;result=null;comparison=[];save();renderAll();restoreResult();});
    for(const id of ['name','archiveName'])on(id,'change',()=>{state.name=$(id).value.trim()||'我的卡库';changed();$('name').value=state.name;$('archiveName').value=state.name;});on('profileKind','change',()=>{state.profile_kind=$('profileKind').value;changed();});
    for(const[id,key]of [['totalRank','character_total_rank'],['tgwRank','tgw_card_rank']])on(id,'change',()=>{state.profile[key]=number($(id));changed();});
    on('growthFields','change',e=>{const el=e.target;if(el.dataset.rank)updateIndexed(state.profile.character_ranks,'character_id',Number(el.dataset.rank),'rank',number(el));if(el.dataset.facility)updateIndexed(state.profile.facilities,'id',Number(el.dataset.facility),'level',number(el));changed();});
    on('growthBandTabs','click',e=>{const b=e.target.closest('[data-growth-band]');if(b){growthBand=Number(b.dataset.growthBand);renderGrowth();}});
    on('cardCatalog','change',e=>{const el=e.target,id=Number(el.dataset.id),kind=el.dataset.candidate||el.dataset.required;if(!kind)return;
      if(el.dataset.candidate){toggle(state[selectedKey(kind)],id,el.checked);if(!el.checked)toggle(state.team_settings[requiredKey(kind)],id,false);}
      if(el.dataset.required){try{if(el.checked)checkRequired(kind,id);toggle(state.team_settings[requiredKey(kind)],id,el.checked);if(el.checked)toggle(state[selectedKey(kind)],id,true);}catch(error){el.checked=false;throw error;}}
      if(kind==='members'&&((el.dataset.candidate||el.dataset.required)&&!el.checked)){state.team_settings.required_bindings=state.team_settings.required_bindings.filter(p=>p.member_id!==id);if(state.team_settings.required_leader_id===id)state.team_settings.required_leader_id=0;}if(kind==='snaps'&&el.dataset.candidate&&!el.checked)state.team_settings.required_bindings=state.team_settings.required_bindings.filter(p=>p.snap_id!==id);
      changed();renderCards();});
    on('cardCatalog','click',e=>{const b=e.target.closest('[data-add],[data-remove],[data-edit]');if(!b)return;const kind=b.dataset.add||b.dataset.remove||b.dataset.edit,id=Number(b.dataset.id);
      if(b.dataset.remove){state.profile.inventory[kind]=state.profile.inventory[kind].filter(r=>r.id!==id);toggle(state[selectedKey(kind)],id,false);toggle(state.team_settings[requiredKey(kind)],id,false);state.team_settings.required_bindings=state.team_settings.required_bindings.filter(p=>p[kind==='members'?'member_id':'snap_id']!==id);if(kind==='members'&&state.team_settings.required_leader_id===id)state.team_settings.required_leader_id=0;const key=kind==='members'?'member_ids':'snap_ids';state.fixed_team[key]=state.fixed_team[key].map(v=>v===id?null:v);if(state.fixed_team.leader_id===id&&kind==='members')state.fixed_team.leader_id=0;changed();renderCards();}else openEditor(kind,id);});
    on('editorFields','change',editorHint);on('saveCard','click',saveCard);on('pickerSearch','input',renderPicker);on('pickerType','change',renderPicker);on('pickerClear','click',()=>pickCard(null));on('pickerList','click',e=>{const b=e.target.closest('[data-pick]');if(b)pickCard(Number(b.dataset.pick));});
    on('applyBatch','click',()=>{const kind=$('kind').value,ids=new Set(shownCards().map(c=>c.id)),changes={};document.querySelectorAll('[data-batch]').forEach(el=>{if(el.value==='')return;const v=number(el);if(!Number.isInteger(v)||v<Number(el.min)||v>Number(el.max))throw new Error(fields[el.dataset.batch]+'数值不正确');changes[el.dataset.batch]=v;});if(!Object.keys(changes).length)throw new Error('请填写至少一项实际养成。');
      const inventory=clone(state.profile.inventory[kind]);let count=0;for(const row of inventory)if(ids.has(row.id)){for(const[k,v]of Object.entries(changes))if(!$('batchMissing').checked||row[k]==null)row[k]=v;validateRow(kind,row,card(kind,row.id));count++;}if(!count)throw new Error('当前筛选没有已拥有卡');state.profile.inventory[kind]=inventory;changed();renderCards();notice('已应用到 '+count+' 张卡牌。');});
    for(const id of ['calculate','calculateInline'])on(id,'click',calculate);on('checkGrowth','click',checkGrowth);on('growthIssues','click',e=>{const b=e.target.closest('[data-issue]');if(b)gotoIssue(Number(b.dataset.issue));});
    on('cancel','click',async()=>{if(jobId)await post('/api/jobs/'+jobId+'/cancel',{});});on('exit','click',async()=>{await post('/api/exit',{});notice('本地程序已退出，档案保存在浏览器。');});
    on('exportResult','click',()=>download({schema_version:1,version:result.version,input:resultInput,result},resultInput.name+'-配队结果.json'));
    on('teamList','click',e=>{const b=e.target.closest('[data-detail],[data-copy],[data-evaluate]');if(!b)return;if(b.dataset.detail!==undefined)openDetails(Number(b.dataset.detail));if(b.dataset.copy!==undefined)openCopy(Number(b.dataset.copy));if(b.dataset.evaluate!==undefined)loadFixed(Number(b.dataset.evaluate));});
    on('teamList','change',e=>{if(e.target.dataset.compare===undefined)return;const i=Number(e.target.dataset.compare);if(e.target.checked&&comparison.length>=3){e.target.checked=false;notice('最多对比 3 套队伍，请先移除一套。');return;}toggle(comparison,i,e.target.checked);renderComparison();renderTeamList();});
    on('resultFilters','click',e=>{const b=e.target.closest('[data-result-filter]');if(b){resultFilter=b.dataset.resultFilter;renderTeamList();}});on('resultSort','change',renderTeamList);
    on('detailCopy','click',()=>openCopy(detailIndex));on('detailEvaluate','click',()=>loadFixed(detailIndex));on('detailImage','click',saveTeamImage);on('copyAgain','click',copy);on('closeCopy','click',()=>$('copyDialog').close());document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));
    window.addEventListener('storage',e=>{if(e.key!==null&&e.key!==ARCHIVES&&e.key!==STORE)return;const expected=e.key===ARCHIVES?savedArchives:savedProfile;if(e.key!==null&&e.newValue===expected)return;stale=true;busy(!!jobId||starting||evaluating);notice('另一标签页已修改档案。请导出需要保留的输入，然后刷新读取最新档案。',true);});
    const saved=sessionStorage.getItem(JOB);if(saved){resultInput=clone(state);await follow(saved);}
  }catch(e){$('loadError').hidden=false;$('loadError').textContent='加载失败：'+e.message;}
}
async function copy(){try{await navigator.clipboard.writeText($('copyText').value);$('copyHint').textContent='已复制，可到 bdon 对照填写。';}catch{$('copyText').select();$('copyHint').textContent='请按 Ctrl+C 复制已选中的文本。';}}
init();
