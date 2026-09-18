import React,{useState,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type Row={requirement?:string;tool?:string;jd_requirement?:string;resume_evidence?:string;status:string;required?:string;found_in_resume?:boolean;experience?:string;evidence?:string;disqualifier?:boolean};
const Tag=({v}:{v:string})=><span className={'tag '+v}>{v.replaceAll('_',' ')}</span>;

function Table({rows,mandatory}:{rows:Row[],mandatory?:boolean}){
  return <table><thead><tr>
    {mandatory?<><th>Requirement</th><th>JD requirement</th><th>Resume evidence</th></>:<><th>Tool / technology</th><th>Required</th><th>Found</th><th>Experience / evidence</th></>}
    <th>Status</th></tr></thead>
  <tbody>{rows.map((x,i)=><tr key={i}>
    {mandatory?<><td>{x.requirement}</td><td>{x.jd_requirement}</td><td>{x.resume_evidence}</td></>:<><td>{x.tool}</td><td>{x.required}</td><td>{x.found_in_resume?'Yes':'No'}</td><td>{x.experience}<br/><small>{x.evidence}</small></td></>}
    <td><Tag v={x.status}/></td></tr>)}</tbody></table>;
}

function AnalysisReport({r}:{r:any}){
  const c=r.candidate, s=r.scores;
  return <div className="report">
    <div className="alert">{s.potential_disqualification?'Potential disqualification: '+s.failed_or_unproven_mandatory.join(', '):'No failed or unproven mandatory requirement identified.'}</div>
    <h3>Candidate information</h3>
    <div className="cards">
      <div>Name<br/><b>{c.name||'Not clearly mentioned'}</b></div>
      <div>Total experience<br/><b>{c.total_years_experience??'Not clearly mentioned'}</b></div>
      <div>Relevant experience<br/><b>{c.relevant_years_experience??'Not clearly mentioned'}</b></div>
      <div>Roles<br/><b>{c.roles?.join(', ')||'Not clearly mentioned'}</b></div>
    </div>
    <h3>Matching scores</h3>
    <div className="cards">
      <div>Mandatory criteria<br/><b>{s.mandatory_criteria_match??'N/A'}%</b></div>
      <div>Technical skills<br/><b>{s.technical_skills_match??'N/A'}%</b></div>
      <div>Overall score<br/><b>{s.overall_match}%</b></div>
    </div>
    <h3>Mandatory criteria</h3>
    <Table rows={r.mandatory_criteria} mandatory/>
    <h3>Technical skills</h3>
    <Table rows={r.technical_skills}/>
    <h3>Summary</h3>
    <p>{r.summary.narrative}</p>
    <div className="summary">
      <p><b>Satisfied:</b> {r.summary.satisfied.join(', ')||'None identified'}</p>
      <p><b>Not satisfied:</b> {r.summary.not_satisfied.join(', ')||'None identified'}</p>
      <p><b>Missing tools:</b> {r.summary.missing_tools.join(', ')||'None identified'}</p>
      <p><b>Needs verification:</b> {r.summary.verification_needed.join(', ')||'None identified'}</p>
    </div>
  </div>;
}

type QA = {role:'user'|'assistant', content:string};

function FollowUpChat({analysisId}:{analysisId:string}){
  const [qa,setQa]=useState<QA[]>([]);
  const [question,setQuestion]=useState('');
  const [asking,setAsking]=useState(false);

  async function ask(){
    const q=question.trim();
    if(!q||asking) return;
    const nextHistory=[...qa,{role:'user',content:q} as QA];
    setQa(nextHistory);
    setQuestion('');
    setAsking(true);
    try{
      const r=await fetch(`/api/analyses/${analysisId}/ask`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({question:q, history:qa})
      });
      const data=await r.json();
      setQa(h=>[...h,{role:'assistant',content:data.answer||'No answer returned.'}]);
    }catch(e:any){
      setQa(h=>[...h,{role:'assistant',content:'Something went wrong asking that question.'}]);
    }finally{
      setAsking(false);
    }
  }

  return <div className="followup">
    <div className="followup-title">Ask about this candidate</div>
    {qa.length>0 && <div className="followup-thread">
      {qa.map((m,i)=><div key={i} className={'qa-line qa-'+m.role}>{m.content}</div>)}
      {asking && <div className="qa-line qa-assistant"><span className="dot"/><span className="dot"/><span className="dot"/></div>}
    </div>}
    <div className="followup-row">
      <input value={question} onChange={e=>setQuestion(e.target.value)}
        onKeyDown={e=>{if(e.key==='Enter') ask();}}
        placeholder="e.g. Does this candidate meet the AWS requirement?"/>
      <button onClick={ask} disabled={asking||!question.trim()}>Ask</button>
    </div>
  </div>;
}

type Message =
  | {role:'user', file:string, jdPreview:string}
  | {role:'assistant', status:'loading'}
  | {role:'assistant', status:'error', text:string}
  | {role:'assistant', status:'done', id:string, result:any};

function App(){
  const [messages,setMessages]=useState<Message[]>([]);
  const [file,setFile]=useState<File>();
  const [jd,setJd]=useState('');
  const [weight,setWeight]=useState(.7);
  const [sending,setSending]=useState(false);
  const bottomRef=useRef<HTMLDivElement>(null);

  function scrollDown(){ setTimeout(()=>bottomRef.current?.scrollIntoView({behavior:'smooth'}),50); }

  async function send(){
    if(!file||!jd.trim()||sending) return;
    const thisFile=file, thisJd=jd, thisWeight=weight;
    setMessages(m=>[...m,{role:'user',file:thisFile.name,jdPreview:thisJd.slice(0,160)+(thisJd.length>160?'…':'')}]);
    setMessages(m=>[...m,{role:'assistant',status:'loading'}]);
    setFile(undefined); setJd(''); setSending(true);
    scrollDown();
    const f=new FormData();
    f.append('resume',thisFile); f.append('jd_text',thisJd); f.append('mandatory_weight',String(thisWeight));
    try{
      const r=await fetch('/api/analyses',{method:'POST',body:f});
      if(!r.ok) throw new Error((await r.json()).detail||'Analysis failed');
      const data=await r.json();
      setMessages(m=>{ const c=[...m]; c[c.length-1]={role:'assistant',status:'done',id:data.id,result:data.result}; return c; });
    }catch(e:any){
      setMessages(m=>{ const c=[...m]; c[c.length-1]={role:'assistant',status:'error',text:e.message}; return c; });
    }finally{
      setSending(false); scrollDown();
    }
  }

  return <div className="chat-app">
    <header className="chat-header">
      <h1>Resume ↔ JD Matcher</h1>
      <p>Upload a resume and paste a job description — then keep asking follow-up questions about that candidate.</p>
    </header>

    <div className="chat-thread">
      {messages.length===0 && <div className="empty-state">Attach a resume and paste a job description below to start.</div>}
      {messages.map((m,i)=>{
        if(m.role==='user') return <div key={i} className="bubble bubble-user">
          <div className="bubble-meta">You attached <b>{m.file}</b></div>
          <div className="bubble-jd">{m.jdPreview}</div>
        </div>;
        if(m.status==='loading') return <div key={i} className="bubble bubble-assistant"><div className="typing"><span/><span/><span/></div></div>;
        if(m.status==='error') return <div key={i} className="bubble bubble-assistant bubble-error">{m.text}</div>;
        return <div key={i} className="bubble bubble-assistant">
          <AnalysisReport r={m.result}/>
          <FollowUpChat analysisId={m.id}/>
        </div>;
      })}
      <div ref={bottomRef}/>
    </div>

    <div className="composer">
      <div className="composer-row">
        <label className="file-btn">
          {file? file.name : 'Attach resume (PDF/DOCX)'}
          <input type="file" accept=".pdf,.docx" onChange={e=>setFile(e.target.files?.[0])}/>
        </label>
        <label className="weight-inline">Mandatory weight {Math.round(weight*100)}%
          <input type="range" min="0" max="1" step=".05" value={weight} onChange={e=>setWeight(+e.target.value)}/>
        </label>
      </div>
      <div className="composer-row">
        <textarea placeholder="Paste the job description…" value={jd} onChange={e=>setJd(e.target.value)}/>
        <button onClick={send} disabled={sending||!file||!jd.trim()}>{sending?'Analyzing…':'Send'}</button>
      </div>
    </div>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
