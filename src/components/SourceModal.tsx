import { Info, Plus, RefreshCw } from 'lucide-react';
import type { Article } from '../types';
import type { CrawlSource } from '../data/crawled';
import type { SyncStatus } from '../lib/api';
import { SOURCES } from '../data/demo';
import { Modal, SourceIcon } from './ui';
export default function SourceModal({ onClose, disabled, toggle, articles, states, onImport, apiReady=false, syncing=false, syncNote='', syncStatus=null, syncError='', onSync }: {
  onClose:()=>void; disabled:string[]; toggle:(id:string)=>void; articles:Article[]; states:Record<string,CrawlSource>; onImport:()=>void;
  apiReady?:boolean; syncing?:boolean; syncNote?:string; syncStatus?:SyncStatus|null; syncError?:string; onSync?:()=>void;
}) {
  const date=(value?:string)=>value&&Number.isFinite(Date.parse(value))?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'暂无记录';
  const since=Object.values(states).find(s=>s.published_since)?.published_since;
  const labels:Record<string,string>={success:'同步成功',partial:'部分成功',failed:'同步失败',error:'同步失败',auth_required:'需要授权',blocked:'来源暂不可用',rate_limited:'来源限流',unavailable:'来源暂不可用',disabled:'未启用',not_configured:'尚未接入'};
  const job=syncStatus?.running?syncStatus.active_job:syncStatus?.latest_job;
  const jobLabels:Record<string,string>={running:'正在后台同步',success:'本轮同步完成',partial:'本轮部分成功',failed:'本轮同步失败',interrupted:'上轮同步已中断'};
  const frequency=syncStatus?.interval_seconds?syncStatus.interval_seconds%3600===0?`每 ${syncStatus.interval_seconds/3600} 小时`:syncStatus.interval_seconds%60===0?`每 ${syncStatus.interval_seconds/60} 分钟`:`每 ${syncStatus.interval_seconds} 秒`:'按服务设置';
  return <Modal title="信息来源" onClose={onClose}><div className="source-modal-content">
    <p className="form-intro">显示开关控制资讯、日程和助手的检索范围。最近成功同步与最近尝试分别记录。</p>
    {since&&<p className="form-intro">采集范围：北京时间 {since} 起发布的资讯；未确认发布日期的内容不收录。</p>}
    <div className="notice compact"><Info size={16}/><p>{apiReady?'已接入的公开来源自动增量更新，也可手动同步。已有资讯在同步期间仍可浏览。':'当前展示离线快照。启动资讯服务后会自动恢复连接，再更新公开来源。'}</p></div>
    {syncStatus&&<p className="form-intro">{syncStatus.enabled?`自动更新：${frequency}`:'自动更新未开启，可手动同步'}{syncStatus.enabled&&syncStatus.next_run_at?` · 下次 ${date(syncStatus.next_run_at)}`:''}</p>}
    {job&&<p className="form-intro" role="status">{jobLabels[job.status]||'等待同步结果'} · 新增 {job.inserted}，更新 {job.updated}，未变化 {job.unchanged}，AI 已处理 {job.ai_processed}</p>}
    {(syncError||job?.error_message||!!job?.ai_failed)&&<p className="form-error" role="status">{syncError||job?.error_message||`有 ${job?.ai_failed} 条 AI 处理未完成，已收录原文仍可查看。`}</p>}
    {SOURCES.map(s=>{
      const state=states[s.id];
      const rows=articles.filter(a=>a.sourceId===s.id&&a.ingestion==='crawler');
      const hasSharedWechat=['voice','grad','sports','wechat'].includes(s.id)&&!!states.wechat;
      const label=state?labels[state.status]||'等待核对':hasSharedWechat?'共用公众号采集记录':rows.length?'已有历史资料':'尚未接入';
      const errors=[...new Set(state?.errors?.map(e=>e.message).filter((x):x is string=>!!x)||[])]; const error=errors.length?errors.slice(0,3).join('；')+(errors.length>3?'（其余错误请查看采集记录）':''):(state?.error_message||'').slice(0,360);
      return <div className="source-manage-row" key={s.id}><SourceIcon id={s.id}/><div>
        <h3>{s.name}<span>{label}</span></h3><p>{s.description}</p>
        <small>最近成功：{date(state?.last_success_at)}<br/>最近尝试：{date(state?.last_attempt_at)}</small>
        {error&&<p className="form-error">{error}</p>}
        {hasSharedWechat&&!state&&<small>公众号总采集最近成功：{date(states.wechat?.last_success_at)}；不代表本账号已更新。</small>}
      </div><button className={'switch '+(!disabled.includes(s.id)?'on':'')} role="switch" aria-checked={!disabled.includes(s.id)} aria-label={'显示'+s.name+'的资讯'} onClick={()=>toggle(s.id)}><span/></button></div>;
    })}
    {syncNote&&<p role="status">{syncNote}</p>}
    <div className="source-modal-bottom"><span><Info size={14}/>公众号只保留已收录文章，不承诺完整历史。</span><button className="button secondary small" onClick={onImport}><Plus size={15}/>手动录入</button></div>
    <button className="button primary" disabled={!apiReady||syncing} onClick={onSync}><RefreshCw size={15} className={syncing?'is-spinning':undefined}/>{syncing?'正在同步…':'同步公开资讯'}</button>
  </div></Modal>;
}
