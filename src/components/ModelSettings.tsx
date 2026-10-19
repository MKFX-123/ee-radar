import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowLeft, CheckCircle2, KeyRound, LoaderCircle, PlugZap, Save, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { string } from '../data/crawled';
interface ModelConfig {configured:boolean;baseUrl:string;model:string;hasKey:boolean;source:string;}
export default function ModelSettings({onBack,onConfiguredChange}:{onBack:()=>void;onConfiguredChange:(configured:boolean)=>void}) {
  const [config,setConfig]=useState<ModelConfig>(),[baseUrl,setBaseUrl]=useState(''),[model,setModel]=useState(''),[key,setKey]=useState('');
  const [operation,setOperation]=useState<'load'|'save'|'test'|'reset'|''>('load'),[error,setError]=useState(''),[notice,setNotice]=useState(''),[version,setVersion]=useState(0);
  const active=useRef<AbortController|null>(null),firstInput=useRef<HTMLInputElement>(null);
  const dirty=!!config&&(baseUrl.trim()!==config.baseUrl||model.trim()!==config.model||key.trim().length>0);
  function accept(data:Record<string,unknown>){
    const next={configured:data.configured===true,baseUrl:string(data.base_url),model:string(data.model),hasKey:data.has_api_key===true,source:string(data.source)};
    setConfig(next);setBaseUrl(next.baseUrl);setModel(next.model);setKey('');onConfiguredChange(next.configured);
  }
  useEffect(()=>{
    const controller=new AbortController();active.current=controller;setOperation('load');setError('');
    api('/model/config',{signal:controller.signal}).then(data=>{if(!controller.signal.aborted)accept(data);}).catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:'无法读取配置，请确认本地服务已启动。');}).finally(()=>{if(!controller.signal.aborted){setOperation('');requestAnimationFrame(()=>firstInput.current?.focus());}});
    return()=>controller.abort();
  },[version]);
  useEffect(()=>()=>{active.current?.abort();},[]);
  async function perform(action:'save'|'test'|'reset'){
    if(operation)return;
    const controller=new AbortController();active.current=controller;setOperation(action);setError('');setNotice('');
    const submittedKey=key.trim();
    if(action==='save')setKey('');
    try{
      const result=action==='save'?await api('/model/config',{method:'POST',signal:controller.signal,body:JSON.stringify({base_url:baseUrl.trim(),model:model.trim(),...(submittedKey?{api_key:submittedKey}:{})})}):action==='test'?await api('/model/test',{method:'POST',signal:controller.signal,body:'{}'}):await api('/model/config',{method:'DELETE',signal:controller.signal});
      if(controller.signal.aborted)return;
      if(action!=='test')accept(result);
      setNotice(action==='save'?'配置已保存；保存不验证连接。请点击测试连接确认服务可用。':action==='test'?'基础连接测试成功。正式问答还会检查 JSON 格式及原文引用。':'已清除本地配置；如有环境变量配置，将继续使用。');
    }catch(e){if(!controller.signal.aborted)setError((e instanceof Error?e.message:'操作未完成，请重试。')+(action==='save'&&submittedKey?' 为保护密钥，请重新填写后再保存。':''));}
    finally{if(!controller.signal.aborted)setOperation('');}
  }
  function save(e:FormEvent){e.preventDefault();void perform('save');}
  return <section className="model-settings" aria-label="模型配置"><button className="text-link model-back" onClick={onBack} disabled={!!operation&&operation!=='load'}><ArrowLeft size={15}/>返回对话</button>
    <div className="model-settings-title"><KeyRound size={22}/><div><h3>连接你的模型</h3><p>配置兼容接口，让 Radar 基于资讯回答问题。</p></div></div>
    {operation==='load'&&<p className="inline-feedback" role="status"><LoaderCircle size={15} className="is-spinning"/>正在读取配置状态…</p>}
    {error&&<div className="form-error" role="alert">{error}{!config&&<button className="text-link" onClick={()=>setVersion(n=>n+1)} disabled={!!operation}>重新读取</button>}</div>}
    {config&&<><div className={'model-config-status '+(config.configured?'ready':'')}><span className="preview-dot"/>{config.configured?'配置已填写（不代表连接成功）':'尚未配置'}<span>{config.source==='environment'?'来自环境变量':config.source==='local'?'保存在本机':'等待填写'}</span></div>
      <form className="model-settings-form" onSubmit={save}>
        <fieldset disabled={!!operation}>
          <label htmlFor="model-base-url">接口地址（Base URL）</label><input ref={firstInput} id="model-base-url" type="url" required maxLength={500} autoComplete="off" placeholder="https://你的服务地址/v1" value={baseUrl} onChange={e=>{setBaseUrl(e.target.value);setNotice('');}} aria-describedby="model-base-hint"/>
          <small id="model-base-hint">填写服务商提供的基础地址，保留其要求的 /v1 等前缀；程序会追加 /chat/completions。</small>
          <label htmlFor="model-name">模型名称</label><input id="model-name" required maxLength={200} autoComplete="off" placeholder="填写服务商提供的模型 ID" value={model} onChange={e=>{setModel(e.target.value);setNotice('');}}/>
          <label htmlFor="model-api-key">API Key <span>{config.hasKey?'已设置，留空保留原密钥':'必填'}</span></label><input id="model-api-key" type="password" required={!config.hasKey} maxLength={4096} autoComplete="new-password" autoCapitalize="none" spellCheck={false} placeholder={config.hasKey?'输入新密钥可替换':'粘贴你的 API Key'} value={key} onChange={e=>{setKey(e.target.value);setNotice('');}} aria-describedby="model-key-hint"/>
          <small id="model-key-hint">密钥只保存在本机后端文件，不进入浏览器存储，也不会回显。更换服务地址的主机时需重新填写。</small>
          <button className="button primary" type="submit" disabled={!baseUrl.trim()||!model.trim()||(!config.hasKey&&!key.trim())}><Save size={15}/>{operation==='save'?'正在保存…':'保存配置'}</button>
        </fieldset>
      </form>
      {notice&&<p className="model-success" role="status"><CheckCircle2 size={16}/>{notice}</p>}
      <div className="model-test-area"><button className="button secondary" disabled={!!operation||!config.configured||dirty} onClick={()=>void perform('test')}><PlugZap size={15}/>{operation==='test'?'正在测试…':'测试连接'}</button><p>{dirty?'请先保存修改，再测试连接。':'点击后会向已保存的服务地址发送一次最小测试请求，可能产生少量 API 费用；不发送校园资料。'}</p></div>
      {config.source==='local'&&<button className="text-link model-reset" disabled={!!operation} onClick={()=>void perform('reset')}><Trash2 size={14}/>{operation==='reset'?'正在清除…':'清除本地配置'}</button>}
    </>}
  </section>;
}
