import {DiscoveryDraft} from './DiscoveryDraft';
import {useState,useEffect} from 'react';
import {api,ApiError} from './api';
export function GoalCreator({classes,onCreated}:{classes:any[];onCreated:(goalId:string)=>void}){
 const [catalog,setCatalog]=useState<{id:string;name:string;diagnostic_available?:boolean}[]>([]);
 const [catalogError,setCatalogError]=useState(''),[loadingCatalog,setLoadingCatalog]=useState(false);
 async function loadCatalog(){setLoadingCatalog(true);setCatalogError('');try{const result=await api<any>('/curriculum/skills');if(!Array.isArray(result.skills)||!result.skills.length)throw new Error('当前没有可用的知识范围，请联系管理员维护知识图谱和题库');setCatalog(result.skills);}catch(e){setCatalogError(e instanceof ApiError&&e.status===404?'当前后端缺少题库目录接口，请更新并重启服务':e instanceof TypeError?'无法连接后端，请确认8000服务正在运行':(e as Error).message);}finally{setLoadingCatalog(false)}}
 useEffect(()=>{void loadCatalog()},[]);
 const [title,setTitle]=useState(''),[skills,setSkills]=useState<string[]>([]),[date,setDate]=useState('');
 const [minutes,setMinutes]=useState(300),[preference,setPreference]=useState('unspecified');
 const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const [route,setRoute]=useState<any>(null);
 const [confirmed,setConfirmed]=useState(false);
 const issues=[...(!title.trim()?['填写学习目标']:[]),...(loadingCatalog?['等待知识点读取完成']:[]),...(catalogError?['重新获取知识点题库']:[]),...(!skills.length?['在“确认目标知识范围”中至少勾选一个知识点']:[]),...(!date?['填写截止日期']:[]),...(!Number.isInteger(minutes)||minutes<1||minutes>10080?['每周分钟须为1至10080的整数']:[]),...(!confirmed?['勾选“已核对目标范围、时间与偏好”']:[])];
 async function interpret(){
  setBusy(true);setMessage('');setConfirmed(false);
  try{
   const value=await api<any>('/goals/interpret','POST',{text:title});
   setSkills(value.target_skill_ids);if(value.due_date)setDate(value.due_date);
   if(value.weekly_minutes)setMinutes(value.weekly_minutes);setPreference(value.preference);
   setMessage(value.needs_clarification.length?value.needs_clarification.join('；'):'初步范围：'+(value.scope||[]).map((n:any)=>n.name+(n.role==='prerequisite'?'（必要先修）':'（目标）')).join('、')+'。请核对后保存；诊断按最终选择及必要先修出题。');
  }catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
 }
 return <section className="card" aria-label="已有主题目标创建"><h2>按已有主题创建目标</h2><p>输入目标文字和勾选确认框还不够，还需选择知识点并填写截止日期。关联班级可以保持“私有目标”。</p>{message&&<p role="status">{message}</p>}
  <form noValidate onSubmit={async e=>{
   e.preventDefault();if(busy)return;if(issues.length){setMessage('目标尚未保存，请先完成：'+issues.join('；'));return;}const values=new FormData(e.currentTarget);setBusy(true);
   try{
    const saved=await api('/goals','POST',{title,target_skill_ids:skills,due_date:date,weekly_minutes:minutes,class_id:values.get('class_id')||null,preference});
    setMessage('目标已保存');onCreated(saved.id);
   }catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
  }}>
   <label className="field">学习目标<textarea aria-label="学习目标" disabled={busy} value={title} maxLength={200} required onChange={e=>{setRoute(null);setTitle(e.target.value);setSkills([]);setMessage('');setConfirmed(false)}} placeholder="例如：学习函数基础，每周3小时，偏好阅读文本"/></label>
   <DiscoveryDraft text={title} due={date} weekly={minutes}/>
   <button type="button" disabled={busy||!title.trim()} onClick={interpret}>解析目标描述</button>
   <button type="button" disabled={busy||!title.trim()} onClick={async()=>{if(!confirm('允许将目标文字发送给已配置的云端模型以推荐课程吗？'))return;setBusy(true);setRoute(null);try{setRoute(await api('/goals/course-route-preview','POST',{text:title,consent:true}))}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}}>查看跨课程路线草案</button>
   {route&&<section><h3>课程路线参考</h3><p>{route.cloud_called?'已调用云端分析':'未调用云端模型'}。确认前不会创建学习目标。</p><ol>{route.route.map((r:any)=><li key={r.id}>{r.course_name} · {r.name}：{r.reason}</li>)}</ol>{route.gaps.map((g:string,i:number)=><p key={i}>范围提示：{g}</p>)}{route.proposal_id&&<><h4>将用于诊断的范围</h4><ol>{route.scope.map((n:any)=><li key={n.id}>{n.course_name} · {n.name}（{n.required_prerequisite?'必要先修':'目标知识点'}）</li>)}</ol><button type="button" disabled={busy||!date||minutes<1||minutes>10080} onClick={async()=>{if(!confirm('确认按上述课程范围创建目标？'))return;setBusy(true);try{const g=await api('/engineering-goals/proposals/'+route.proposal_id+'/confirm','POST',{confirmed:true,due_date:date,weekly_minutes:minutes,preference});onCreated(g.id)}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}}>确认跨课范围并创建目标</button><p>请先填写下方的截止日期和每周可用分钟；创建后在目标详情开始诊断。</p></>}</section>}
   <fieldset><legend>确认目标知识范围</legend>{loadingCatalog&&<p role="status">正在读取知识范围与题库覆盖…</p>}{catalogError&&<p role="alert" className="error">{catalogError}</p>}<button type="button" disabled={loadingCatalog||busy} onClick={()=>void loadCatalog()}>重新获取题库</button>{catalog.map(({id,name,diagnostic_available})=><label key={id}><input type="checkbox" disabled={diagnostic_available===false} checked={skills.includes(id)} onChange={e=>{setSkills(e.target.checked?[...skills,id]:skills.filter(s=>s!==id));setConfirmed(false)}}/>{name}{diagnostic_available===false?'（诊断题库待管理员补充）':''}</label>)}</fieldset>
   <label className="field">每周目标分钟<input aria-label="每周目标分钟" type="number" min={1} max={10080} required value={minutes} onChange={e=>{setMinutes(Number(e.target.value));setConfirmed(false)}}/></label>
   <label className="field">截止日期<input aria-label="主题目标截止日期" type="date" value={date} required onChange={e=>{setDate(e.target.value);setConfirmed(false)}}/></label>
   <label className="field">学习偏好<select aria-label="学习偏好" value={preference} onChange={e=>{setPreference(e.target.value);setConfirmed(false)}}><option value="unspecified">未指定</option><option value="text">文本阅读</option><option value="video">视频</option></select></label>
   <label className="field">关联班级（默认私有）<select name="class_id" onChange={()=>setConfirmed(false)}><option value="">私有目标</option>{classes.filter(c=>c.status==='active').map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
   <label className="choice"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)} required/>已核对目标范围、时间与偏好</label>
   <p className={issues.length?'notice':'result'} role="status">{issues.length?'保存前请补齐：'+issues.join('；'):'填写完整，可以保存目标。'}</p>
   <button className="primary goal-save-button" disabled={busy}>{busy?'正在保存…':'保存目标'}</button>
  </form>
 </section>
}
