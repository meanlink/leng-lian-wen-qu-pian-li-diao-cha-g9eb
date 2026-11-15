import {session} from '../stores/session.js';
export async function api(path,options={}){
 const r=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(session.token?{Authorization:'Bearer '+session.token}:{}),...options.headers}});
 if(!r.ok){let d=await r.json().catch(()=>({detail:'连接失败，请稍后重试'}));if(r.status===401&&path!='/auth/login'){session.clear();location.hash='#/login';}throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));}
 return r.json();
}
// 分段原文以二进制请求体上传：不经过 multipart，也不会把整份文件读入内存。
export async function putSegment(receipt,index,chunk,sha256){
 const r=await fetch('/api/intakes/'+encodeURIComponent(receipt)+'/segments/'+index,{method:'PUT',body:chunk,headers:{'Content-Type':'application/octet-stream','X-Segment-SHA256':sha256,Authorization:'Bearer '+session.token}});
 if(!r.ok){let d=await r.json().catch(()=>({detail:'分段上传失败，请重试'}));throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));}
 return r.json();
}
export async function download(id,format){
 const r=await fetch('/api/deliveries/'+id+'/download?format='+format,{headers:{Authorization:'Bearer '+session.token}});
 if(!r.ok)throw new Error('下载失败，请重试');
 const u=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=u;a.download='分析归档-'+id+'.'+format;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
}
