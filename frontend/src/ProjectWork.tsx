import {useEffect,useState} from 'react';
import {api} from './api';

type RecordData=Record<string,any>;
const labels:Record<string,string>={submitted:'待教师评分',graded:'已评分',revision_requested:'教师要求修订'};

export function ProjectWork({courseId,role,rubric,canReview,courseStatus}:{courseId:string;role:string;rubric:RecordData;canReview:boolean;courseStatus:string}){
 const [items,setItems]=useState<RecordData[]>([]),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const [scores,setScores]=useState<Record<string,Record<string,number>>>({});
 const base='/learning/project-courses/'+courseId;
 async function load(){
  if(role==='student')setItems(await api(base+'/my-submissions'));
  else if(role==='teacher'&&canReview)setItems(await api(base+'/review-queue'));
 }
 useEffect(()=>{setItems([]);setError('');void load().catch(e=>setError((e as Error).message))},[courseId,role,canReview]);
 async function act(fn:()=>Promise<void>){setBusy(true);setError('');setMessage('');try{await fn();await load()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 const latest=items.find(item=>item.rubric_id===rubric.id);
 const canSubmit=role==='student'&&(!latest||latest.status==='revision_requested');
 return <section className="card" aria-label="项目作品提交与教师评分">
  <h3>项目作品提交与教师评分</h3>
  <p>提交代码、可运行测试及实际输出；系统不执行上传代码，教师按下列量规逐项核对。选择题分数不会自动成为项目分数。</p>
  {courseStatus!=='published'&&<p className="notice">该项目课程仍是草稿试行，页面评分仅用于课程方案验证，不代表正式课程成绩。</p>}
  {error&&<p role="alert" className="error">{error}</p>}{message&&<p role="status" className="notice">{message}</p>}
  {canSubmit&&<form onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);void act(async()=>{await api(base+'/submissions','POST',{
   code_text:f.get('code_text'),test_code:f.get('test_code'),
   observed:{nested:Number(f.get('nested')),empty:Number(f.get('empty')),single:Number(f.get('single'))},
   input_contract:f.get('input_contract'),explanation:f.get('explanation')});setMessage('项目作品已提交，等待指定课程教师评分')})}}>
   <label>函数代码<textarea name="code_text" required minLength={20} maxLength={20000} rows={10}/></label>
   <label>输入约定<textarea name="input_contract" required minLength={10} maxLength={2000}/></label>
   <label>三个样例的可运行测试代码<textarea name="test_code" required minLength={20} maxLength={12000} rows={7}/></label>
   <div className="formgrid"><label>嵌套目录实测值<input name="nested" type="number" required/></label><label>空目录实测值<input name="empty" type="number" required/></label><label>单文件实测值<input name="single" type="number" required/></label></div>
   <label>基本情况、递归汇总与调用返回顺序解释<textarea name="explanation" required minLength={20} maxLength={6000} rows={5}/></label>
   <button className="primary" disabled={busy}>提交项目作品供教师审核</button>
  </form>}
  {role==='student'&&latest?.status==='submitted'&&<p role="status">当前版本正等待教师评分。修改请等教师提出修订意见，历史提交不会被覆盖。</p>}
  {role==='student'&&latest?.status==='graded'&&<p role="status">当前版本已评分。请查看下方逐项反馈；再次提交需教师明确要求修订。</p>}
  {role==='teacher'&&!canReview&&<p>未获本课程的项目审核权限；请管理员先指定课程审核教师。</p>}
  {items.map(item=><article className="card" key={item.id}>
   <h4>版本 {item.version} · {labels[item.status]||item.status} · {new Date(item.created_at).toLocaleString()}</h4>
   {role==='teacher'&&<p>提交学生：{item.student_id}</p>}
   <details open={item.status==='submitted'}><summary>查看作品代码、测试与解释</summary>
    <p>输入约定：{item.input_contract}</p><pre style={{whiteSpace:'pre-wrap'}}>{item.code_text}</pre>
    <h5>测试代码</h5><pre style={{whiteSpace:'pre-wrap'}}>{item.test_code}</pre>
    <p>学生报告实测：嵌套目录 {item.observed.nested}；空目录 {item.observed.empty}；单文件 {item.observed.single}。这些值由学生填写，教师需结合代码核对。</p>
    <p>{item.explanation}</p>
   </details>
   {item.review&&<section><p>教师评分：{item.review.total_score}/{item.rubric.max_score} · {item.review.requires_revision?'需修订':'已评分'}</p>
    {item.rubric.criteria.map((criterion:RecordData)=><p key={criterion.name}>{criterion.name}：{item.review.criterion_scores[criterion.name]}/{criterion.max_score}</p>)}
    <p>教师意见：{item.review.comment}</p></section>}
   {role==='teacher'&&canReview&&item.status==='submitted'&&<form onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);void act(async()=>{const values=Object.fromEntries(item.rubric.criteria.map((c:RecordData)=>[c.name,Number(scores[item.id]?.[c.name]??0)]));await api(base+'/submissions/'+item.id+'/review','POST',{criterion_scores:values,requires_revision:f.get('requires_revision')==='on',comment:f.get('comment')});setMessage('逐项评分已保存，学生可查看结果')})}}>
    <h5>按提交时量规逐项评分</h5>{item.rubric.criteria.map((criterion:RecordData)=><label key={criterion.name}>{criterion.name}（0—{criterion.max_score} 分）<input type="number" min={0} max={criterion.max_score} required value={scores[item.id]?.[criterion.name]??0} onChange={e=>setScores(old=>({...old,[item.id]:{...old[item.id],[criterion.name]:Number(e.target.value)}}))}/><small>{criterion.evidence}</small></label>)}
    <p>当前合计 {Object.values(scores[item.id]||{}).reduce((total,value)=>total+value,0)} / {item.rubric.max_score}</p>
    <label><input name="requires_revision" type="checkbox"/>要求学生修订后重交</label>
    <label>逐项评分依据与改进意见<textarea name="comment" required minLength={10} maxLength={4000}/></label>
    <button className="primary" disabled={busy}>保存教师评分</button>
   </form>}
  </article>)}
 </section>
}
