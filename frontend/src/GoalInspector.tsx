import {useState} from 'react';
import {api} from './api';

function budgetResult(status:string){
 switch(status){
  case 'feasible': return '参考方案可在截止日前排完';
  case 'budget_conflict': return '参考方案在截止日前排不完';
  case 'missing_structure': return '请先确认目标知识点，再试算时间';
  case 'evidence_gap': return '参考方案时间充足，仍需补齐学习资料';
  default: return '暂时无法判断排期';
 }
}
export function GoalInspector({goal}:{goal:any}){
 const [check,setCheck]=useState<any>(null);
 const [trial,setTrial]=useState<any>(null);
 const [trialQuery,setTrialQuery]=useState('');
 const [history,setHistory]=useState<any[]>([]);
 const [message,setMessage]=useState('');
 const [busy,setBusy]=useState(false);
 async function inspect(kind:'feasibility'|'history'){
  setBusy(true);setMessage('');
  try{
   const result=await api<any>('/goals/'+goal.id+'/'+kind);
   if(kind==='history')setHistory(result);else setCheck(result);
  }catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
 }
 async function trySuggestedDate(date:string,previousQuery:string){
  const query=new URLSearchParams(previousQuery);
  query.set('due_date',date);
  setBusy(true);setMessage('');
  try{
   const result=await api('/goals/'+goal.id+'/feasibility?'+query);
   setTrial(result);setTrialQuery(query.toString());
  }catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
 }
 return <section><p>目标版本 {goal.version} · {goal.class_id?'已显式关联班级':'本人私有目标'}</p>
  {goal.requires_replan&&<p role="status">{goal.replan_reasons?.length?goal.replan_reasons.join('、'):'目标或预算已变化'}，请重新评估活动路径</p>}
  {goal.requires_diagnosis&&<p role="status">当前目标版本尚无已提交诊断，请先点击“开始/查看诊断”完成检测，再生成或重规划。</p>}
  <button disabled={busy} onClick={()=>inspect('feasibility')}>检查时间可行性</button>
  <button disabled={busy} onClick={()=>inspect('history')}>查看目标版本历史</button>
  <details open={trial?true:undefined}><summary>试算调整后的学习预算</summary>
   <p>按目标及先修的完整学习分钟估算，保留当前学习日、单目标周上限和其他目标占用。</p>
   <form onSubmit={async event=>{
    event.preventDefault();
    const data=new FormData(event.currentTarget);
    const query=new URLSearchParams();
    for(const field of ['daily_minutes','weekly_minutes']){
     const value=String(data.get(field)||'');
     if(value)query.set(field,value);
    }
    setBusy(true);setMessage('');setTrial(null);
    try{setTrial(await api('/goals/'+goal.id+'/feasibility?'+query));setTrialQuery(query.toString());}
    catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
   }}>
    <label className="field">每日学习分钟<input name="daily_minutes" type="number" min={1} max={1440} placeholder="留空使用当前预算"/></label>
    <label className="field">每周学习分钟<input name="weekly_minutes" type="number" min={1} max={10080} placeholder="留空使用当前预算"/></label>
    <button disabled={busy}>试算</button>
   </form>
   {trial&&<div role="status">
    <p>{budgetResult(trial.status)} · 未保存设置</p>
    {new URLSearchParams(trialQuery).get('due_date')&&<p>试算截止日 {new URLSearchParams(trialQuery).get('due_date')}</p>}
    {trial.budget_conflict?.earliest_finish_date&&<button disabled={busy} onClick={()=>trySuggestedDate(trial.budget_conflict.earliest_finish_date,trialQuery)}>用最早完成日再试算</button>}
    {trial.reasons.map((reason:string)=><p key={reason}>{reason}</p>)}
    {trial.schedule?.map((day:any)=><p key={day.date}>{day.date} · {day.minutes}分钟</p>)}
    <a href="#password">前往个人设置保存预算</a>
   </div>}
  </details>
  {message&&<p role="alert">{message}</p>}
  {check&&<div role="status"><p>{budgetResult(check.status)}</p>
   {check.budget_conflict?.earliest_finish_date&&<button disabled={busy} onClick={()=>trySuggestedDate(check.budget_conflict.earliest_finish_date,'')}>用最早完成日试算</button>}
   <p>参考方案所需分钟 {check.required_minutes??'待确定'}</p>{check.reasons.map((r:string)=><p key={r}>{r}</p>)}
   {check.schedule?.map((s:any)=><p key={s.date}>{s.date} · {s.minutes}分钟</p>)}
  </div>}
  {!!history.length&&<details open><summary>目标版本历史（{history.length}版）</summary>
   {history.map(h=>{const p=JSON.parse(h.payload);return <p key={h.version}>版本{h.version} · {p.title} · {p.weekly_minutes}分钟/周 · 截止{p.due_date} · {p.status}</p>})}
  </details>}
 </section>
}
