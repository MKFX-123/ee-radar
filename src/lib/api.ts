import type { Article, Filters } from '../types';
import { isRow, sourceStates, string, toArticle } from '../data/crawled';
import type { Row } from '../data/crawled';
export async function api(path: string, options: RequestInit = {}): Promise<Row> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const isChat=path.split('?')[0]==='/chat';
  let timedOut=false;
  if(options.signal?.aborted)controller.abort();
  options.signal?.addEventListener('abort',abort,{once:true});
  const timeout=window.setTimeout(()=>{timedOut=true;abort();},isChat?100000:35000);
  try {
    const response=await fetch('/api'+path,{...options,signal:controller.signal,headers:{'Content-Type':'application/json',...options.headers}});
    const value:unknown=await response.json().catch(()=>null);
    if(!response.ok) throw new Error(isRow(value)&&typeof value.detail==='string'?value.detail:'资讯服务暂时不可用，请重试。');
    if(timedOut)throw new Error('请求超时。');
    if(!isRow(value))throw new Error('服务响应格式不正确。');
    return value;
  } catch(error) {
    if(timedOut)throw new Error(isChat?'等待回答超过 100 秒。问题已保留，请稍后重试。':'资讯服务响应超时，请稍后重试。');
    if(!controller.signal.aborted&&error instanceof TypeError)throw new Error('无法连接本地服务，请确认后端正在运行后重试。');
    throw error;
  } finally { clearTimeout(timeout); options.signal?.removeEventListener('abort',abort); }
}
export const rawId=(id:string)=>id.replace(/^crawl-/,'');
export interface ArticlePage { articles:Article[];total:number;page:number;pageSize:number; }
export async function getPage(path:string,params:URLSearchParams,signal?:AbortSignal):Promise<ArticlePage> {
  const data=await api(path+'?'+params,{signal});
  if(!Array.isArray(data.items)||typeof data.total!=='number')throw new Error('资讯列表格式不正确。');
  return {articles:data.items.filter(isRow).map(toArticle).filter((a):a is Article=>!!a),total:data.total,page:Number(data.page)||1,pageSize:Number(data.page_size)||20};
}
export function listParams(filters:Filters,disabled:string[],bookmarks:string[],page=1){
  const params=new URLSearchParams({page:String(page),page_size:'20'});
  for(const [key,value]of Object.entries({keyword:filters.q,category:filters.category,source:filters.source,audience:filters.audience,status:filters.status,days:filters.period==='today'?'1':filters.period}))if(value)params.set(key,value);
  if(disabled.length)params.set('exclude_sources',disabled.join(','));
  if(filters.saved)params.set('ids',bookmarks.filter(b=>b.startsWith('crawl-')).map(rawId).join(',')||'__none__');
  return params;
}
export async function getSources(signal?:AbortSignal){const data=await api('/sources/status',{signal});return {sources:sourceStates(data.sources),lastSuccess:string(data.last_success_at),partialFailure:data.partial_failure===true};}
export async function getItem(id:string,signal?:AbortSignal){const data=await api('/items/'+encodeURIComponent(rawId(id)),{signal});const article=toArticle(isRow(data.item)?data.item:data);if(!article)throw new Error('这条资讯暂时无法读取。');return article;}
export async function getEventArticles(start:string,end:string,disabled:string[],signal?:AbortSignal){
  const params=new URLSearchParams({start,end,exclude_sources:disabled.join(',')});
  const data=await api('/events?'+params,{signal});
  if(!Array.isArray(data.items))throw new Error('日程数据格式不正确。');
  const entries=data.items.filter(isRow), items=entries.map(e=>isRow(e.item)?toArticle(e.item):undefined).filter((a):a is Article=>!!a);
  return [...new Map(items.map(a=>[a.id,a])).values()];
}

export interface SyncSourceResult {
  source_id: string; status: string; fetched: number; inserted: number; updated: number; unchanged: number; error_message: string;
}
export interface SyncJob {
  job_id: string; status: string; trigger: string; started_at: string; finished_at: string;
  sources: SyncSourceResult[]; inserted: number; updated: number; unchanged: number; ai_processed: number; ai_failed: number; error_message: string;
}
export interface SyncStatus {
  enabled: boolean; interval_seconds: number; next_run_at: string; running: boolean;
  active_job: SyncJob | null; latest_job: SyncJob | null; data_revision: string | null;
}
const syncCount = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0;
function syncJob(value: unknown): SyncJob | null {
  if (!isRow(value) || !string(value.job_id)) return null;
  return {
    job_id: string(value.job_id), status: string(value.status), trigger: string(value.trigger), started_at: string(value.started_at), finished_at: string(value.finished_at),
    sources: Array.isArray(value.sources) ? value.sources.filter(isRow).map(source => ({ source_id: string(source.source_id), status: string(source.status), fetched: syncCount(source.fetched), inserted: syncCount(source.inserted), updated: syncCount(source.updated), unchanged: syncCount(source.unchanged), error_message: string(source.error_message) })) : [],
    inserted: syncCount(value.inserted), updated: syncCount(value.updated), unchanged: syncCount(value.unchanged), ai_processed: syncCount(value.ai_processed), ai_failed: syncCount(value.ai_failed), error_message: string(value.error_message),
  };
}
export async function getSyncStatus(signal?: AbortSignal): Promise<SyncStatus> {
  const value = await api('/sync/status', { signal });
  const active = syncJob(value.active_job);
  return {
    enabled: value.enabled === true, interval_seconds: syncCount(value.interval_seconds), next_run_at: string(value.next_run_at),
    running: value.running === true || active?.status === 'running', active_job: active, latest_job: syncJob(value.latest_job),
    data_revision: typeof value.data_revision === 'string' || typeof value.data_revision === 'number' ? String(value.data_revision) : null,
  };
}
export async function startSync(): Promise<{ job: SyncJob; reused: boolean }> {
  const value = await api('/sync/refresh', { method: 'POST', body: JSON.stringify({}) });
  const job = syncJob(isRow(value.job) ? value.job : value);
  if (!job) throw new Error('同步任务未返回有效编号，请稍后查看同步状态。');
  return { job, reused: value.reused === true };
}
