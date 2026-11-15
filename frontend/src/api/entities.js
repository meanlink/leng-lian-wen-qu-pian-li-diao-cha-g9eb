import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:(id,offset=0,limit=30)=>api('/datasets/'+id+'?offset='+offset+'&limit='+limit),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const intakes={list:()=>api('/intakes'),get:receipt=>api('/intakes/'+encodeURIComponent(receipt)),create:data=>api('/intakes',{method:'POST',body:JSON.stringify(data)}),finalize:receipt=>api('/intakes/'+encodeURIComponent(receipt)+'/finalize',{method:'POST'})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
