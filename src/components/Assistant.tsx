import { useEffect, useRef, useState } from 'react';
import { ArrowRight, ChevronRight, ExternalLink, Settings2, Send, Sparkles } from 'lucide-react';
import type { Article } from '../types';
import type { ChatMessage } from '../lib/assistant';
import { localAnswer } from '../lib/assistant';
import { api } from '../lib/api';
import { isRow, string } from '../data/crawled';
import { safeUrl } from '../lib/domain';
import { articlePath, Modal } from './ui';
import ModelSettings from './ModelSettings';
export default function Assistant({ onClose, articles, initialQuestion, messages, setMessages, apiReady=false, disabledSources=[], modelConfigured=false, onModelConfigured }: {
  onClose:()=>void; articles:Article[]; initialQuestion:string; messages:ChatMessage[];setMessages:(v:ChatMessage[]|((m:ChatMessage[])=>ChatMessage[]))=>void;
  apiReady?:boolean;disabledSources?:string[];modelConfigured?:boolean;onModelConfigured?:(configured:boolean)=>void;
}) {
  const [settings,setSettings]=useState(false),[configured,setConfigured]=useState(modelConfigured);
  useEffect(()=>setConfigured(modelConfigured),[modelConfigured]);
  const settingsButton=useRef<HTMLButtonElement>(null);
  const closeSettings=()=>{setSettings(false);requestAnimationFrame(()=>settingsButton.current?.focus());};
  const [input,setInput]=useState(''),[busy,setBusy]=useState(false),[waitStage,setWaitStage]=useState(0);
  const initialized=useRef(false),bottom=useRef<HTMLDivElement>(null),activeRequest=useRef<AbortController|null>(null),pending=useRef(false),mounted=useRef(false);
  const scope=disabledSources.join(',');
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;activeRequest.current?.abort();activeRequest.current=null;pending.current=false;};},[]);
  useEffect(()=>{
    if(!busy)return;
    const timers=[12000,35000,70000].map((delay,index)=>window.setTimeout(()=>setWaitStage(index+1),delay));
    return()=>timers.forEach(timer=>window.clearTimeout(timer));
  },[busy]);
  const busyMessage=waitStage>=3?'模型响应较慢，正在等待结果或原文兜底…':waitStage===2?'资料分析仍在进行，完成后会展示来源；也可关闭窗口取消。':waitStage===1?'正在对照多条资料与原文依据…':'正在检索资料'+(configured?'并生成回答':'')+'…';
  async function send(question:string){
    const q=question.trim();if(!q||pending.current)return;
    pending.current=true;setWaitStage(0);setBusy(true);setInput('');
    const user:ChatMessage={id:crypto.randomUUID(),role:'user',text:q};
    setMessages(m=>[...m,user].slice(-20));
    const controller=new AbortController();activeRequest.current=controller;
    try{
      if(!apiReady){const answer=localAnswer(q,articles);setMessages(m=>[...m,{id:crypto.randomUUID(),role:'assistant' as const,...answer,mode:'offline'}].slice(-20));return;}
      const data=await api('/chat',{method:'POST',body:JSON.stringify({message:q,history:messages.filter(m=>m.mode!=='error').slice(-4).map(m=>({role:m.role,content:m.text})),exclude_sources:disabledSources}),signal:controller.signal});
      if(controller.signal.aborted)return;
      if(typeof data.answer!=='string'||!Array.isArray(data.references))throw new Error('回答格式不正确，请重试。');
      if(!data.answer.trim())throw new Error('服务未返回可显示的回答。问题已保留，请重试。');
      const answer=data.answer.trim();
      const references=data.references.filter(isRow).map(r=>({id:string(r.id).startsWith('crawl-')?string(r.id):'crawl-'+string(r.id),title:string(r.title),sourceUrl:safeUrl(string(r.source_url))})).filter(r=>r.id!=='crawl-'&&r.title);
      setMessages(m=>[...m,{id:crypto.randomUUID(),role:'assistant' as const,text:answer,references,mode:string(data.mode),modelError:string(data.model_error)||undefined,fallbackReason:string(data.fallback_reason)||undefined,modelErrorCode:string(data.model_error_code)||undefined}].slice(-20));
    }catch(e){
      const text=controller.signal.aborted?'助手已关闭，本次请求已取消。重新打开后可再次提问。':e instanceof Error?e.message:'助手暂不可用，请稍后重试。';
      setMessages(m=>[...m,{id:crypto.randomUUID(),role:'assistant' as const,text,mode:'error'}].slice(-20));
      if(mounted.current)setInput(value=>value||q);
    }finally{
      if(activeRequest.current===controller){activeRequest.current=null;pending.current=false;if(mounted.current)setBusy(false);}
    }
  }
  useEffect(()=>{
    const timer=window.setTimeout(()=>{if(!initialized.current){initialized.current=true;if(initialQuestion)void send(initialQuestion);}},0);
    return()=>window.clearTimeout(timer);
  },[]);
  useEffect(()=>{bottom.current?.scrollIntoView({block:'nearest'});},[messages,busy]);
  return <Modal title="Radar 助手" onClose={onClose} className="assistant-modal">{settings?<ModelSettings onBack={closeSettings} onConfiguredChange={value=>{setConfigured(value);onModelConfigured?.(value);}}/>:<><div className="assistant-scope"><span className="preview-dot"/>{apiReady?configured?'数据库检索与 AI 问答':'数据库原文检索':'离线快照检索'}<span>{scope?'按已启用来源检索':'检索已收录资料'}</span></div><div className="assistant-settings-bar"><span>{configured?'模型已配置':'添加 API Key，启用模型回答'}</span><button ref={settingsButton} className="text-link" onClick={()=>setSettings(true)} disabled={busy}><Settings2 size={14}/>模型配置</button></div><div className="chat-messages" role="log" aria-live="polite">
    {!messages.length&&<div className="chat-welcome"><span className="ai-symbol"><Sparkles size={25}/></span><h3>你关心什么，问问 Radar</h3><p>查找校园通知，<br/>核对申请机会与原文依据。</p><div className="chat-suggestions">{['最近有哪些交换项目？','有哪些奖学金快截止？','本周有什么学术讲座？'].map(q=><button key={q} disabled={busy} onClick={()=>void send(q)}>{q}<ArrowRight size={14}/></button>)}</div></div>}
    {messages.map(m=><div key={m.id} className={'chat-message '+m.role}>{m.role==='assistant'&&<span className="message-avatar"><Sparkles size={15}/></span>}<div>{m.role==='assistant'&&<small className="muted">{m.mode==='ai'?'AI 回答，请核对原文':m.mode==='offline'?'离线资料规则检索':m.mode==='error'?'本次请求未完成':m.fallbackReason==='no_results'?'未找到匹配资料':m.fallbackReason==='unconfigured'?'模型未配置，返回原文检索':'数据库原文摘录'}</small>}{m.role==='assistant'&&(m.modelError||m.fallbackReason==='model_error')&&<p className="form-error" role="status">本次模型回答未完成，已回退原文检索。{m.modelError||'请稍后重试，或在模型配置中检查连接。'}</p>}<div className={'message-text'+(m.mode==='error'?' form-error':'')} role={m.mode==='error'?'status':undefined}>{m.text}</div>
      {m.references&&m.references.length>0&&<div className="chat-citations">{m.references.map((r,i)=><div key={r.id}><a href={'#/item/'+encodeURIComponent(r.id)} onClick={onClose}><span>{i+1}</span>{r.title}<ChevronRight size={12}/></a>{r.sourceUrl&&<a href={r.sourceUrl} target="_blank" rel="noreferrer">阅读原文<ExternalLink size={12}/></a>}</div>)}</div>}
      {m.citations&&m.citations.length>0&&<div className="chat-citations">{m.citations.map((id,i)=>{const a=articles.find(x=>x.id===id);return a?<a key={id} href={'#'+articlePath(a)} onClick={onClose}><span>{i+1}</span>{a.title}<ChevronRight size={12}/></a>:<span key={id} className="muted">引用 {i+1} 的来源已隐藏</span>;})}</div>}
    </div></div>)}
    {busy&&<p role="status">{busyMessage}</p>}<div ref={bottom}/></div>
    <div className="chat-bottom">{messages.length>0&&<button className="text-link clear-chat" disabled={busy} onClick={()=>setMessages([])}>清空对话</button>}<form onSubmit={e=>{e.preventDefault();void send(input);}} className="chat-input"><input autoFocus aria-label="向 Radar 提问" value={input} maxLength={500} onChange={e=>setInput(e.target.value)} placeholder="例如：近一个月的国际交流项目"/><button className="send-button" aria-label="发送问题" disabled={busy||!input.trim()}><Send size={17}/></button></form><p>{apiReady?configured?'先检索资料，再依据原文回答；重要条件请核对来源。':'模型尚未配置；当前返回数据库中的原文检索结果。':'服务未连接，仅检索已加载的离线资料和本地录入。'}</p></div>
  </>}</Modal>;
}
