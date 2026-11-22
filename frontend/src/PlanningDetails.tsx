import {useState} from 'react';
import {api} from './api';
// Shift the decimal point without rounding a saved fractional check-in to a whole percent.
function shiftDecimal(value:number, places:number){
 const [digits, exponent='0']=String(value).split('e');
 return Number(digits+'e'+(Number(exponent)+places));
}
function completedTimeComparison(planned:number, actual:number){
 const difference=actual-planned;
 if(difference===0)return '与计划一致';
 return '比计划'+(difference>0?'多':'少')+Math.abs(difference)+'分钟';
}
export function PlanningDetails({path,onChanged,onPractice}:{path:any;onChanged:()=>void;onPractice:(value:any)=>void}){
 const [message,setMessage]=useState('');
 const [busy,setBusy]=useState(false);
 const validation=path.validation;
 return <section><h3>规划依据与规则</h3><p>{validation.cloud_called?'云端四智能体生成':'本地规则规划'}</p>{validation.cloud_outputs?.diagnosis?.summary&&<p>{validation.cloud_outputs.diagnosis.summary}</p>}{validation.cloud_outputs?.replan?.reason&&<p>{validation.cloud_outputs.replan.reason}</p>}
  {Boolean(path.remediations?.length)&&<p className="notice">除主任务外，本版另有{path.remediations.length}项独立补强，共{path.remediations.reduce((n:number,t:any)=>n+t.minutes,0)}分钟。<a href="#!" onClick={e=>{e.preventDefault();document.getElementById('practice-followup-'+path.id)?.scrollIntoView()}}>查看补强练习与测验</a></p>}
  {Boolean(validation.cloud_outputs?.replan?.recommendations?.length)&&<section><h4>云端建议的下一步</h4><ul>{validation.cloud_outputs.replan.recommendations.map((tip:string,i:number)=><li key={i}>{tip}</li>)}</ul></section>}
  <p>图谱版本 {validation.graph_version||'历史版本'} · 范围 {({initial:'首次',local:'局部',global:'全局'} as any)[validation.mode]||'历史规划'}</p>
  {validation.learner_evidence?.skills&&<details><summary>查看本次规划使用的诊断与掌握度</summary><p>题库版本：{validation.learner_evidence.bank_version}。掌握度由有效测验计算，学习时长不直接提高掌握度。</p><table><thead><tr><th>知识点</th><th>本次诊断答对 / 题数</th><th>累计证据数</th><th>当前掌握概率</th><th>状态</th></tr></thead><tbody>{validation.learner_evidence.skills.map((s:any)=><tr key={s.skill_id}><td>{s.name}</td><td>{s.diagnostic_correct} / {s.diagnostic_total}</td><td>{s.evidence_count}</td><td>{Math.round(s.probability*100)}%</td><td>{({unknown:'尚未测评',weak:'需要补强',developing:'学习中',mastered:'已达标'} as any)[s.status]}</td></tr>)}</tbody></table><p>这是生成时保存的依据；后续学习后请生成重规划预览。</p></details>}
  {validation.reference_budget&&<section aria-label="预算与最终排期">
   <h4>预算与最终排期</h4>
   <p>参考任务 {validation.reference_budget.reference_minutes} 分钟，截止日前可安排其中 {validation.reference_budget.schedulable_reference_minutes} 分钟。{validation.reference_budget.shortfall_minutes>0&&<>参考方案超出预算 {validation.reference_budget.shortfall_minutes} 分钟。</>}</p>
   {Number.isFinite(validation.reference_budget.fixed_remaining_minutes)&&Number.isFinite(validation.reference_budget.available_for_adjustable_tasks)&&
    <p>需保留的剩余任务 {validation.reference_budget.fixed_remaining_minutes} 分钟，可调整任务可用 {validation.reference_budget.available_for_adjustable_tasks} 分钟。</p>}
   <p>最终排期 {(validation.schedule||[]).reduce((sum:number,day:any)=>sum+day.minutes,0)} 分钟。</p>
  </section>}
  {validation.triggers?.map((s:string)=><p key={s}>{s}</p>)}
  {path.status==='pending_acceptance'&&path.requires_refresh&&<p role="alert">这份预览生成后，目标、学习证据、资料或预算已经变化。请重新生成预览；旧预览无法接受。</p>}
  {path.status==='pending_acceptance'&&validation.execution?.length===0&&<p role="alert">这份历史预览没有待学任务，不能接受。当前活动路径保持不变；如有新的学习需求，请重新生成预览。</p>}
  {path.status==='active'&&path.source_refresh_required&&<p role="alert">当前路径引用的资料已更新、下架或不再可用。请先核对资料并生成重规划预览，再按新版本继续学习；已完成记录仍会保留。</p>}
  {path.replan_warnings?.map((s:string)=><p className="notice" key={s}>保留原路径的风险提醒 {s}</p>)}
  {path.status==='active'&&validation.execution?.length===0&&<p role="status">当前路径没有待学任务；已完成的学习记录仍会保留。目标、证据或资料变化后，可以生成重规划预览。</p>}
  {validation.rules?.map((rule:any)=><p key={rule.rule_id}>{rule.rule_id} · {rule.passed?'通过':'未通过'} · {rule.reason}</p>)}
  {validation.diff&&(()=>{
   const diff=validation.diff;
   const groups=[['kept','保留'],['removed','移除'],['added','新增'],['reinforced','补强']];
   const ids=(key:string):string[]=>diff[key]||[];
   const skills=[...new Set<string>(groups.flatMap(([key])=>ids(key)))];
   const formatSchedule=(rows:any[])=>rows.map(s=>`${s.date} ${s.minutes}分钟`).join('；');
   const dates=(rows:any[])=>[...new Set(rows.map(s=>String(s.date)))].sort().join('、');
   const before=diff.before_schedule||[], after=diff.after_schedule||[];
   return <div><h3>重规划差异</h3>
   {skills.length>0?<table><thead><tr><th>知识点</th><th>任务变化</th><th>安排依据</th></tr></thead><tbody>{skills.map(skill=>{
    const evidence=validation.learner_evidence?.skills?.find((s:any)=>s.skill_id===skill);
    const item=validation.execution?.find((s:any)=>s.skill_id===skill);
    const name=evidence?.name||item?.name||validation.graph?.find((s:any)=>s.id===skill)?.name||skill;
    const change=ids('reinforced').includes(skill)?'补强':groups.filter(([key])=>ids(key).includes(skill)).map(([,label])=>label).join('、');
    const reason=ids('reinforced').includes(skill)?'保留原完成记录，并安排独立补强。':
     ids('removed').includes(skill)?(evidence?.status==='mastered'?'本次测验证据已达到掌握标准，无需继续安排该任务。':'新方案未安排该任务；请结合上方规划依据核对。'):
     ids('added').includes(skill)?'原路径没有此待学任务，新方案已纳入。':'新方案继续安排该知识点的学习。';
    return <tr key={skill}><td>{name}</td><td>{change}</td><td>{reason}{Number.isFinite(evidence?.probability)&&` 规划时掌握概率 ${Math.round(evidence.probability*100)}%。`}</td></tr>;
   })}</tbody></table>:<p>无任务变化。</p>}
   <h4>日期与排期</h4><p>学习日期：{dates(before)===dates(after)?'未变化':`${dates(before)||'无'} → ${dates(after)||'无'}`}</p>
   <p>原排期 {formatSchedule(before)||'无'}</p>
   <p>新排期 {formatSchedule(after)||'无需新增时间'}</p>
   <h4>剩余学习时间</h4>
   {Number.isFinite(diff.before_remaining_minutes)&&Number.isFinite(diff.after_remaining_minutes)&&<p>原路径剩余 {diff.before_remaining_minutes} 分钟 → 新路径安排 {diff.after_remaining_minutes} 分钟</p>}
   {diff.remaining_by_skill?.map((item:any)=><p key={item.skill_id}>
    {validation.learner_evidence?.skills?.find((s:any)=>s.skill_id===item.skill_id)?.name||validation.graph?.find((skill:any)=>skill.id===item.skill_id)?.name||item.skill_id}：
    {item.original_minutes!=null&&<>首次计划 {item.original_minutes} 分钟 · {item.previous_plan_minutes!=null&&<>上一版安排 {item.previous_plan_minutes} 分钟 · </>}本版已完成 {shiftDecimal(item.completion_proportion??0,2)}% · </>}
    规划前剩余 {item.before} 分钟 · 本次安排 {item.after} 分钟
   </p>)}
   <p>保留完成历史 {validation.preserved?.length||0}项</p>
   </div>;
  })()}
  {['active','superseded'].includes(path.status)&&[...(path.tasks||[]),...(path.remediations||[])].some(item=>item.checkin||item.latest_assessment_score!=null)&&
   <section><h3>本版学习记录</h3><table><thead><tr><th>任务</th><th>计划时长</th><th>实际打卡</th><th>完成进度</th><th>最近测验得分</th></tr></thead>
    <tbody>{[...(path.tasks||[]),...(path.remediations||[])].map(item=><tr key={item.id}><td>{item.name}</td><td>{item.minutes} 分钟</td>
     <td>{item.checkin?item.checkin.minutes+' 分钟':'未打卡'}{item.checkin?.proportion===1&&<small style={{display:'block'}}>{completedTimeComparison(item.minutes,item.checkin.minutes)}</small>}</td><td>{item.checkin?shiftDecimal(item.checkin.proportion,2)+'%':'—'}</td>
     <td>{item.latest_assessment_score!=null?Math.round(item.latest_assessment_score*100)+'%':'暂无得分'}{item.assessment_count>1&&item.first_assessment_score!=null&&<small style={{display:'block'}}>首次 {Math.round(item.first_assessment_score*100)}% · 共 {item.assessment_count} 次已评分测验</small>}{item.latest_assessment_at&&<><br/><time dateTime={item.latest_assessment_at}>{new Date(item.latest_assessment_at).toLocaleString('zh-CN',{hour12:false})}</time></>}</td></tr>)}</tbody></table></section>}
  <h3>执行顺序与材料</h3>
  {validation.execution?.map((item:any,index:number)=><div key={item.id} className="listrow">
   <p>{index+1}. {item.name} · {item.minutes}分钟 · {item.dates?.map((d:any)=>d.date+' '+d.minutes+'分钟').join('；')} · {item.assessment} · {item.instructions||''}</p>
   <p>安排依据：{item.prerequisites?.length?`先修 ${item.prerequisites.join('、')}；`:''}{item.reinforcement?'需要补强；':''}按当前诊断与剩余预算安排。</p>
   {item.citations?.map((citation:any,i:number)=>{const source=path.sources.find((s:any)=>s.version_id===citation.version_id);return <details key={citation.version_id+'-'+i}><summary>阅读来源：{source?.title||citation.resource_id} · 版本 {citation.version_id.slice(0,8)} · {Number.isInteger(citation.start)&&Number.isInteger(citation.end)?`正文字符 ${citation.start}—${citation.end}`:source?.location||'查看下方来源正文'}</summary><p style={{whiteSpace:'pre-wrap'}}>{citation.excerpt||source?.excerpt||'旧路径未保存对应片段，请到资源版本查看正文。'}</p></details>})}
  </div>)}
  {message&&<p role="status">{message}</p>}
  <div id={'practice-followup-'+path.id} />
  {path.remediations?.map((item:any)=><section className="card" key={item.id}><h3>独立补强 · {item.name}</h3>
   {item.instructions&&<p style={{whiteSpace:'pre-line'}}>{item.instructions}</p>}
   {item.citations?.map((c:any)=><details key={c.version_id}><summary>查看本项引用的教材片段</summary><p style={{whiteSpace:'pre-line'}}>{c.excerpt||'请在路径来源中查看该版本。'}</p></details>)}
   {path.status==='pending_acceptance'&&!path.requires_refresh&&validation.previous&&item.source_item_id&&!item.locked&&<><p>可以先在原活动路径复测，系统按题库现状领取一道未见题或继续未完成的测验。若缺题，可先做上面的教材练习。提交后请重新生成预览。</p><button disabled={busy} onClick={async()=>{setBusy(true);setMessage('');try{await api('/paths/'+validation.previous+'/items/'+item.source_item_id+'/retests','POST');onPractice({path_id:validation.previous,item_id:item.source_item_id})}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}}>领取原任务复测题</button></>}
   <p>来源任务 {item.source_item_id} · {item.minutes}分钟 · {item.locked?'先修未达标':'可学习'}</p>
   {item.checkin&&<p>已记录 {item.checkin.minutes} 分钟 · 本版已完成 {shiftDecimal(item.checkin.proportion,2)}%</p>}
   {path.status==='active'&&!item.locked&&<>
    {(!item.checkin||item.checkin.proportion<1)&&<form className="actions" key={item.id+'-'+(item.checkin?.proportion??0)} onSubmit={async e=>{
     e.preventDefault();
     const form=new FormData(e.currentTarget);
     setBusy(true);
     try{
      await api('/check-ins','POST',{path_id:path.id,item_id:item.id,minutes:Number(form.get('minutes')),proportion:shiftDecimal(Number(form.get('percent')),-2),difficulty:'补强学习',note:''});
      setMessage('补强进度已保存；掌握度需经测验验证。');
      onChanged();
     }catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
    }}>
     <label>补强累计实际分钟<input name="minutes" type="number" min={Math.max(1,item.checkin?.minutes??0)} max={1440} placeholder="实际学习分钟" defaultValue={item.checkin?.minutes??''} required/></label>
     <label>本版补强完成百分比<input name="percent" type="number" step="any" min={shiftDecimal(item.checkin?.proportion??0,2)} max={100} placeholder="例如 50" defaultValue={item.checkin?shiftDecimal(item.checkin.proportion,2):''} required/></label>
     <button disabled={busy}>保存补强进度</button>
    </form>}
    <button disabled={busy} onClick={async()=>{setBusy(true);try{onPractice({path_id:path.id,item_id:item.id})}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}}>补强知识测验</button></>}
  </section>)}
 </section>
}

