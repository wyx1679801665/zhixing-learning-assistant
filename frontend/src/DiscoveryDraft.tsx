import {useEffect,useState} from 'react';
import {api} from './api';

interface DraftNode {ref:string;name:string;catalog_id:string|null;objective:string;activity:string;
  minutes:number;prerequisites:string[];issue?:string;sources:{url:string;title:string;quote:string}[]}
interface Summary {id:string;description:string;status:string}
interface Draft extends Summary {revision:number;nodes:DraftNode[];selected:string[];error?:string;
  goal_gaps?:string[];goal_checks?:unknown[];blocked?:string[];shortfall_minutes?:number;schedule:{ref:string;date:string;minutes:number}[]}

export function DiscoveryDraft({text,due,weekly}:{text:string;due:string;weekly:number}){
  const [draft,setDraft]=useState<Draft|null>(null),[history,setHistory]=useState<Summary[]>([]);
  const [selected,setSelected]=useState<string[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  function show(value:Draft){setDraft(value);setSelected(value.selected)}
  useEffect(()=>{let live=true;api<Summary[]>('/discovery-drafts').then(list=>{if(live)setHistory(list)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[]);
  useEffect(()=>{
    if(draft?.status!=='running')return;
    const id=draft.id;let live=true;let timer:ReturnType<typeof setTimeout>;
    async function poll(){try{const value=await api<Draft>('/discovery-drafts/'+id);if(!live)return;
      if(value.status!=='running'){show(value);return}
    }catch(e){if(live)setError((e as Error).message)}
    if(live)timer=setTimeout(poll,1500)}
    timer=setTimeout(poll,1500);return()=>{live=false;clearTimeout(timer)};
  },[draft?.id,draft?.status]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  return <section aria-label="联网个人路线"><h3>联网个人学习草案</h3>
    <button type="button" disabled={busy||draft?.status==='running'||!text.trim()||!due||weekly<1} onClick={()=>void act(async()=>{
      if(!confirm('允许将目标文字发送给云端模型，并按知识点联网检索公开网页？可能产生模型与检索费用。'))return;
      const value=await api<Draft>('/discovery-drafts','POST',{text,weekly_minutes:weekly,due_date:due,web_consent:true});
      show(value);setHistory(await api('/discovery-drafts'))
    })}>联网补充并生成个人草案</button>
    {history.length>0&&<label>打开已保存草案<select disabled={busy} value={draft?.id||''} onChange={e=>{const id=e.target.value;if(id)void act(async()=>show(await api('/discovery-drafts/'+id)))}}>
      <option value="">请选择</option>{history.map(h=><option key={h.id} value={h.id}>{h.description} · {h.id.slice(0,6)}</option>)}</select></label>}
    {error&&<p role="alert">{error}</p>}
    {draft&&<><h4>{draft.description}</h4>{draft.status==='running'?<p role="status">正在匹配目录并检索，可稍后重新打开。</p>:<>
      <p role="status">{draft.status==='ready'&&draft.goal_checks?.length?'已核验目标覆盖与排期':'完整目标尚未通过核验'}</p>
      {draft.goal_gaps?.map((gap,i)=><p key={i}>待补：{gap}</p>)}
      {draft.error&&<p>{draft.error}</p>}{draft.status==='interrupted'&&<p>服务重启中断了检索，可重新生成。</p>}
      {draft.nodes.map(n=><div key={n.ref} className="listrow"><label><input type="checkbox" checked={selected.includes(n.ref)} onChange={e=>setSelected(e.target.checked?[...selected,n.ref]:selected.filter(k=>k!==n.ref))}/>{n.name} · {n.catalog_id?'目录知识点':'库外候选'} · {n.minutes}分钟</label>
        <p>{n.objective}</p><p>{n.activity||n.issue}</p>
        {n.prerequisites.length>0&&<p>先修：{n.prerequisites.map(k=>draft.nodes.find(s=>s.ref===k)?.name).join('、')}</p>}
        {n.sources.map(s=><details key={s.url}><summary>查看来源依据</summary><blockquote>{s.quote}</blockquote><a href={s.url} target="_blank" rel="noreferrer">{s.title}</a></details>)}</div>)}
      {draft.nodes.length>0&&<button type="button" disabled={busy} onClick={()=>void act(async()=>show(await api('/discovery-drafts/'+draft.id,'PATCH',{revision:draft.revision,selected})))}>保存选择并更新预计排期</button>}
      {draft.schedule.map(s=><p key={s.date+s.ref}>{s.date} · {draft.nodes.find(n=>n.ref===s.ref)?.name} · {s.minutes}分钟</p>)}
      {!!draft.shortfall_minutes&&<p>截止前尚缺 {draft.shortfall_minutes} 分钟</p>}
      {!!draft.blocked?.length&&<p>来源或先修待补：{draft.blocked.map(k=>draft.nodes.find(n=>n.ref===k)?.name).join('、')}</p>}
      <p>个人草案不占用正式预算；诊断、打卡和掌握度使用已审核的正式目标。</p>
    </>}</>}
  </section>
}
