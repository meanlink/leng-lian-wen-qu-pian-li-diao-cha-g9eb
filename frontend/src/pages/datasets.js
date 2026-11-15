import {datasets,intakes} from '../api/entities.js';
import {putSegment} from '../api/client.js';
import {fileSHA256} from '../utils/sha256.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';
import {
  CHUNK_SIZE,formatBytes,segmentCount,describeFailure,
  rememberReceipt,forgetReceipt,rememberedReceipt,rememberLast,
} from '../utils/intake.js';

const statusLabel={receiving:'接收中',failed:'校验未通过 · 现场保留',published:'已发布批次'};
const PARALLEL=4;
let activeFile=null;
let activeState=null;
let busy=false;
let pendingResume=false;
let refreshPage=null;

export async function dataPage(){
 workspace.datasets=await datasets.list();
 const selected=workspace.datasets[0]?await datasets.get(workspace.datasets[0].id,0,30):null;
 const m=workspace.meta,canWrite=session.user.role!=='viewer';
 const receipt=rememberedReceipt();
 const last=lastResult();
 return heading('02 / DATA INTAKE','数据接入与批次追溯','大文件分段接入、凭接收单续传；只有全部分段齐全且整份校验通过才生成可分析批次。原有 5–5,000 条小批次仍可直接接入。')
 +'<div class="dataset-grid"><section class="panel"><div class="section-title"><h2>业务批次</h2><span>'+workspace.datasets.length+' 批</span></div><div class="batch-list">'
 +workspace.datasets.map(d=>'<button class="batch-item" data-dataset="'+d.id+'"><strong>'+esc(d.name)+'</strong><span>'+d.rows+' 条 · '+esc(d.source)+'</span><small>SHA256 '+esc(d.checksum.slice(0,18))+'…</small></button>').join('')
 +(!workspace.datasets.length?'<p class="empty">尚无已发布批次。</p>':'')+'</div>'
 +'<div class="intake-block"><div class="section-title"><h2>分段接收大文件</h2><span>整份校验后发布</span></div>'
 +'<div class="intake-resume"><label>接收单号码<input id="intake-receipt" placeholder="R-…" value="'+esc(receipt)+'" autocomplete="off"></label><button type="button" class="button small" id="intake-lookup">凭接收单续传 ↻</button></div>'
 +'<div id="intake-state" class="intake-state" aria-live="polite"></div>'
 +(last?'<p class="hint">上次发布：'+esc(last.name)+(last.record_count?' · '+last.record_count+' 条':'')+' · 接收单 '+esc(last.receipt)+'</p>':'')
 +'<form id="intake-form"><label>批次名称<input name="name" required minlength="2" placeholder="例如：十一月全量测温"></label><label>温度记录 CSV<input type="file" id="intake-file" accept=".csv,text/csv"></label><label>分段大小<select name="chunk_size"><option value="'+CHUNK_SIZE+'">512 KB（推荐）</option><option value="262144">256 KB</option><option value="1048576">1 MB</option></select></label><button type="submit" class="primary" id="intake-start" '+(canWrite?'':'disabled')+'>分段校验并发布批次</button><p class="hint">支持十万条以上记录；中文、带引号的逗号和跨行字段即使被分段切开也能还原。失败时保留接收现场，可凭接收单补传。分析员与负责人可操作。</p></form></div>'
 +'<div class="section-title"><h2>小批次直接接入</h2><span>5–5,000 条</span></div>'
 +'<form id="dataset-form"><label>批次名称<input name="name" required minlength="2" placeholder="例如：11 月复测批次"></label><label>上传 CSV 或 JSON<input type="file" id="file-input" accept=".json,.csv"></label><label>记录 JSON<textarea name="records" rows="6" placeholder="粘贴 JSON 数组，或先选择上方文件" required></textarea></label><p class="hint">字段：'+m.columns.map(c=>esc(c.key)+' / '+esc(c.label)).join('，')+'。5–5,000 条记录。</p><button type="submit" class="primary" '+(canWrite?'':'disabled')+'>校验并接入批次</button></form></section>'
 +'<section class="panel"><div class="section-title"><h2 id="preview-name">'+esc(selected?.name||'尚无批次')+'</h2><span id="preview-range">前 30 条</span></div><div id="data-preview">'+table(m.columns,selected?.records||[])+'</div><div id="preview-pager" class="pager"></div></section></div>';
}

function renderIntakeState(s,detail=''){
 const host=document.querySelector('#intake-state');if(!host)return;
 if(!s){
  host.innerHTML=detail?'<p class="warning">'+esc(detail)+'</p>':'<p class="hint">选择文件后创建接收单；中断后在此输入接收单号码继续。同一位置重传相同内容不会重复计入，异值会被拒收。</p>';
  return;
 }
 const percent=s.total_bytes?Math.round(s.confirmed_bytes/s.total_bytes*100):0;
 const failure=describeFailure(s);
 host.innerHTML='<div class="intake-card '+esc(s.status)+'"><div class="intake-head"><div><strong>'+esc(s.name)+'</strong><p>'+esc(s.source)+' · '+formatBytes(s.total_bytes)+' · '+s.total_chunks+' 段 · 每段 '+formatBytes(s.chunk_size)+'</p></div><span class="intake-badge">'+esc(statusLabel[s.status]||s.status)+'</span></div>'
  +'<p class="receipt-line">接收单 <code>'+esc(s.receipt)+'</code><button type="button" class="text-button" data-copy="'+esc(s.receipt)+'">复制</button></p>'
  +'<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+percent+'"><span style="width:'+percent+'%"></span></div>'
  +'<p class="intake-facts">已确认 <strong>'+s.confirmed_count+' / '+s.total_chunks+'</strong> 段 · '+formatBytes(s.confirmed_bytes)+' / '+formatBytes(s.total_bytes)+'（'+percent+'%）<br>已确认连续位置：第 1–'+s.confirmed_prefix+' 段'+(s.missing_count?'；尚缺 '+s.missing_count+' 段':'；全部分段齐全')+(s.dataset_id?' · 关联批次 #'+s.dataset_id+(s.record_count?'（'+s.record_count+' 条）':''):'')+'</p>'
  +(s.missing_count?'<details class="intake-missing"><summary>查看缺段位置</summary><p>'+esc(s.missing_segments.join('、'))+'</p></details>':'')
  +(failure?'<p class="warning">失败原因：'+esc(failure)+'</p>':'')
  +(s.status==='published'?'<p class="hint">批次已发布，可在上方批次列表打开预览，或到工作台执行分析。重试完成不会新增批次。</p>':'')
  +'<div class="intake-actions">'+(s.status!=='published'?'<button type="button" class="button" id="intake-continue">'+(s.missing_count?'补传缺失分段':'校验并发布')+' ↗</button>':'')+'<button type="button" class="text-button" id="intake-forget">清除本机接收单</button></div></div>'
  +(detail?'<p class="hint">'+esc(detail)+'</p>':'');
 bindIntakeActions();
}

async function lookup(receipt){
 if(!receipt){notify('请输入接收单号码',true);return;}
 try{
  const s=await intakes.get(receipt);
  activeState=s;rememberReceipt(s.receipt);
  const nameInput=document.querySelector('#intake-form [name=name]');
  if(nameInput&&!nameInput.value)nameInput.value=s.name;
  renderIntakeState(s);
 }catch(e){
  activeState=null;activeFile=null;
  renderIntakeState(null,'无法读取接收单：'+e.message);
 }
}

async function runIntake(file,chunkSize,name){
 if(busy)return;
 busy=true;
 const start=document.querySelector('#intake-start');
 if(start)start.disabled=true;
 try{
  let s=activeState;
  const reuse=!!(s&&s.status!=='published'&&s.total_bytes===file.size&&s.source===file.name&&s.chunk_size===chunkSize&&s.name===name.trim());
  if(!reuse){
   const progress=v=>{if(start)start.textContent='计算整份指纹 '+Math.round(v*100)+'%';};
   const checksum=await fileSHA256(file,progress);
   if(s&&s.status!=='published'&&s.checksum&&s.checksum!==checksum)notify('本机接收单对应另一份文件，已新建接收单',true);
   s=await intakes.create({name,source:file.name,total_bytes:file.size,chunk_size:chunkSize,checksum});
  }
  activeState=s;activeFile=file;rememberReceipt(s.receipt);renderIntakeState(s);
  if(start)start.textContent='分段接收中…';

  s=await intakes.get(s.receipt);
  const prefix=s.confirmed_prefix;
  const missing=[];
  for(let i=0;i<s.total_chunks;i++)if(!s.confirmed_segments.includes(i))missing.push(i);
  // 已确认位置之后优先，使连续位置尽快推进；已确认之前的空洞随后补齐。
  missing.sort((a,b)=>(a<prefix)-(b<prefix)||a-b);
  const queue=missing.slice();
  const baseConfirmed=s.confirmed_count,baseBytes=s.confirmed_bytes;
  let doneCount=0,doneBytes=0;
  const worker=async()=>{
   while(queue.length){
    const index=queue.shift();
    const from=index*s.chunk_size,to=Math.min(s.total_bytes,from+segmentCount(s,index));
    const chunk=file.slice(from,to,file.type||'text/csv');
    const sha=await fileSHA256(chunk);
    await putSegment(s.receipt,index,chunk,sha);
    doneCount++;doneBytes+=to-from;
    renderIntakeState({...s,confirmed_count:baseConfirmed+doneCount,confirmed_bytes:baseBytes+doneBytes},
      '正在接收第 '+(index+1)+' / '+s.total_chunks+' 段…');
   }
  };
  await Promise.all(Array.from({length:Math.min(PARALLEL,Math.max(1,queue.length))},worker));

  if(start)start.textContent='整份校验中…';
  const finalState=await intakes.finalize(s.receipt);
  activeState=finalState;activeFile=null;
  renderIntakeState(finalState);
  if(finalState.status==='published'){
   rememberLast({receipt:finalState.receipt,name:finalState.name,dataset_id:finalState.dataset_id,record_count:finalState.record_count});
   notify('全部核对通过，批次已发布：'+finalState.name+'（'+(finalState.record_count||0)+' 条）');
   await refreshPage?.();
   await lookup(finalState.receipt);
  }else notify('校验未通过，接收现场已保留：'+(finalState.failure_reason||'请检查接收单'),true);
 }catch(e){
  notify(e.message,true);
  if(activeState)await lookup(activeState.receipt).catch(()=>{});
 }finally{
  busy=false;
  const button=document.querySelector('#intake-start');
  if(button){button.disabled=false;button.textContent='分段校验并发布批次';}
 }
}

function bindIntakeActions(){
 const continueButton=document.querySelector('#intake-continue');
 if(continueButton)continueButton.onclick=async()=>{
  if(!activeState||busy)return;
  if(!activeFile&&activeState.missing_count){
   pendingResume=true;
   notify('请选择同一份源文件，将只补传缺失分段');
   document.querySelector('#intake-file')?.click();
   return;
  }
  if(activeFile){await runIntake(activeFile,activeState.chunk_size,activeState.name);return;}
  try{
   const s=await intakes.finalize(activeState.receipt);
   activeState=s;renderIntakeState(s);
   if(s.status==='published'){notify('批次已发布');await refreshPage?.();await lookup(s.receipt);}
   else notify(s.failure_reason||'请选择源文件补传缺失分段',true);
  }catch(e){notify(e.message,true);await lookup(activeState.receipt);}
 };
 document.querySelector('#intake-forget')?.addEventListener('click',()=>{
  forgetReceipt();activeState=null;activeFile=null;pendingResume=false;
  renderIntakeState(null);
  const input=document.querySelector('#intake-receipt');if(input)input.value='';
 });
 document.querySelector('[data-copy]')?.addEventListener('click',async e=>{
  try{await navigator.clipboard.writeText(e.target.dataset.copy);notify('接收单已复制');}
  catch(_){notify('请手动选择接收单号码复制');}
 });
}

export function bindData(refresh){
 refreshPage=refresh;
 let source='manual.json';
 const intakeInput=document.querySelector('#intake-file');
 intakeInput.addEventListener('change',async()=>{
  if(!pendingResume)return;
  pendingResume=false;
  const file=intakeInput.files[0];
  if(!file||!activeState)return;
  if(file.size!==activeState.total_bytes||file.name!==activeState.source){
   notify('所选文件与接收单大小或文件名不一致，请选择原文件',true);return;
  }
  await runIntake(file,activeState.chunk_size,activeState.name);
 });

 document.querySelector('#file-input').addEventListener('change',async e=>{
  try{
   const file=e.target.files[0];if(!file)return;
   source=file.name;
   const text=await file.text();
   const records=file.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);
   document.querySelector('#dataset-form [name=records]').value=JSON.stringify(records,null,2);
  }catch(error){notify(error.message,true);}
 });
 document.querySelectorAll('[data-dataset]').forEach(button=>button.onclick=async()=>{
  try{
   const d=await datasets.get(button.dataset.dataset,0,30);
   document.querySelector('#preview-name').textContent=d.name;
   document.querySelector('#data-preview').innerHTML=table(workspace.meta.columns,d.records);
   bindPreviewPager(button.dataset.dataset,d);
  }catch(error){notify(error.message,true);}
 });

 const lookupButton=document.querySelector('#intake-lookup');
 lookupButton.onclick=()=>lookup(document.querySelector('#intake-receipt').value.trim());
 document.querySelector('#intake-receipt').addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();lookupButton.click();}
 });
 submit(document.querySelector('#intake-form'),async f=>{
  const file=intakeInput.files[0];
  if(!file)throw new Error('请选择温度记录 CSV 文件');
  if(!file.name.toLowerCase().endsWith('.csv'))throw new Error('分段接收只支持 CSV；JSON 请使用下方小批次接入');
  if(file.size===0)throw new Error('文件为空');
  await runIntake(file,Number(f.get('chunk_size')),f.get('name'));
 });
 submit(document.querySelector('#dataset-form'),async f=>{
  await datasets.create({name:f.get('name'),source,records:JSON.parse(f.get('records'))});
  notify('数据已接入，可前往工作台执行分析');await refresh();
 });

 bindIntakeActions();
 const existing=rememberedReceipt();
 if(existing)lookup(existing);else renderIntakeState(activeState);
 const selected=workspace.datasets[0];
 if(selected)bindPreviewPager(selected.id,selected);
}

function bindPreviewPager(id,detail){
 const pager=document.querySelector('#preview-pager');if(!pager)return;
 const total=detail.total||detail.rows||0;
 if(total<=30){pager.innerHTML='';document.querySelector('#preview-range').textContent='前 30 条';return;}
 const offset=detail.offset||0;
 document.querySelector('#preview-range').textContent='第 '+(offset+1)+'–'+Math.min(offset+30,total)+' 条 / 共 '+total+' 条';
 pager.innerHTML='<button type="button" class="button small" data-page="-30" '+(offset===0?'disabled':'')+'>上一页</button><button type="button" class="button small" data-page="30" '+(offset+30>=total?'disabled':'')+'>下一页</button>';
 pager.querySelectorAll('[data-page]').forEach(b=>b.onclick=async()=>{
  try{
   const d=await datasets.get(id,Math.max(0,offset+Number(b.dataset.page)),30);
   document.querySelector('#data-preview').innerHTML=table(workspace.meta.columns,d.records);
   bindPreviewPager(id,d);
  }catch(error){notify(error.message,true);}
 });
}
