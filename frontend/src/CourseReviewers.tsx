import {useEffect,useState} from 'react';
import {api} from './api';
export function CourseReviewers({courseId}:{courseId:string}) {
 const [teachers,setTeachers]=useState<any[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function load(){setTeachers(await api('/learning/courses/'+courseId+'/reviewers'))}
 useEffect(()=>{let live=true;setTeachers([]);setError('');api('/learning/courses/'+courseId+'/reviewers').then(x=>{if(live)setTeachers(x)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[courseId]);
 async function toggle(id:string,assigned:boolean){setBusy(true);setError('');try{await api('/learning/courses/'+courseId+'/reviewers/'+id,'PUT',{assigned});await load()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <section aria-label="课程审核教师"><h4>指定课程审核教师</h4><p>指定教师可查看本课程完整题目及答案，审核发布题目；项目课程中还可查看学生作品并按量规评分。撤销后失去审核权限，已有审核记录保留。</p>{error&&<p role="alert">{error}</p>}{teachers.map(t=><label key={t.id} className="choice"><input type="checkbox" disabled={busy} checked={!!t.assigned} onChange={e=>void toggle(t.id,e.target.checked)}/>{t.name}（{t.username}）</label>)}{!teachers.length&&!error&&<p>暂无可分配教师。</p>}</section>
}
