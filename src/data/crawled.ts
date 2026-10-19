import type { Article, Audience } from '../types';
import { normalizeCategory, safeUrl } from '../lib/domain';
export interface CrawlSource {
  article_count?: number; linked_count?: number;
  name: string; status: string; fetched?: number; enabled?: boolean; published_since?: string; last_attempt_at?: string; last_success_at?: string; error_message?: string; errors?: {message?:string}[];
}
export interface CrawlSnapshot { articles: Article[]; generatedAt: string; sources: Record<string, CrawlSource>; }
export type Row = Record<string, unknown>;
export const string = (value: unknown) => typeof value === 'string' ? value : '';
const validDate = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : '';
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : [];
export const isRow = (value: unknown): value is Row => !!value && typeof value === 'object' && !Array.isArray(value);
export function toArticle(row: Row): Article | undefined {
  const title = string(row.title), rawId = string(row.id), sourceUrl = safeUrl(string(row.source_url));
  if (!rawId || !title) return undefined;
  const id = rawId.startsWith('crawl-') ? rawId : 'crawl-' + rawId;
  const content = string(row.content), excerpt = content || string(row.content_preview) || string(row.source_excerpt);
  const sourceName = string(row.account_name) || string(row.source_name) || string(row.source);
  let sourceId = string(row.source_id) || string(row.source);
  if (sourceUrl?.includes('thsports.tsinghua.edu.cn')) sourceId = 'sports_site';
  else if (sourceName === '清华体育') sourceId = 'sports';
  else if (sourceName === '无限之声') sourceId = 'voice';
  else if (sourceName === 'E研E行') sourceId = 'grad';
  if (sourceId === 'ee_website') sourceId = 'ee';
  const audience = strings(row.audience).concat(strings(row.target_audience));
  const rawAudience = typeof row.audience === 'string' ? row.audience : '';
  const audienceText: Audience = audience.includes('全体同学') || (audience.includes('本科生') && audience.includes('研究生')) ? '全体同学' : (['本科生', '研究生', '全体同学'].includes(rawAudience) ? rawAudience as Audience : audience.includes('本科生') ? '本科生' : audience.includes('研究生') ? '研究生' : '未说明');
  const rows = Array.isArray(row.events) ? row.events.filter(isRow) : [];
  const events: Article['events'] = rows.filter(e => e.event_type === 'event' && validDate(e.start_at)).map((e,i) => ({ id: string(e.id) || id+'-event-'+i, title: string(e.title) || title, start: validDate(e.start_at), end: validDate(e.end_at) || undefined, precision: (e.precision === 'date' || /^\d{4}-\d{2}-\d{2}$/.test(string(e.start_at)) ? 'day' : 'minute') as 'day'|'minute', location: string(e.location) || '原文未说明', sourceExcerpt: string(e.source_excerpt), yearBasis: string(e.year_basis) }));
  const deadlines = rows.filter(e => e.event_type === 'deadline' && validDate(e.start_at)).map((e,i) => ({ id: string(e.id) || id+'-deadline-'+i, label: string(e.title) || '申请截止', at: validDate(e.start_at), precision: (e.precision === 'date' || /^\d{4}-\d{2}-\d{2}$/.test(string(e.start_at)) ? 'day' : 'minute') as 'day'|'minute', sourceExcerpt: string(e.source_excerpt), yearBasis: string(e.year_basis) }));
  const start = validDate(row.event_start_at);
  if (!rows.length && start) events.push({ id:id+'-event',title,start,end:validDate(row.event_end_at)||undefined,precision:/^\d{4}-\d{2}-\d{2}$/.test(start)?'day':'minute', location:string(row.location)||'原文未说明',sourceExcerpt:'',yearBasis:'' });
  const summaryKind = row.summary_kind === 'ai' ? 'ai' : 'excerpt';
  const registration = row.registration === 'open' || row.registration === 'closed' || row.registration === 'upcoming' ? row.registration : 'unknown';
  return {
    id, title, content, summary: string(row.summary) || (excerpt ? excerpt.replace(/\s+/g,' ').slice(0,160) + (excerpt.length >160?'…':'') : '仅收录标题与来源信息，请查看原文。'),
    category: normalizeCategory(row.category), tags: strings(row.tags), sourceId, sourceName, sourceUrl,
    publishedAt: validDate(row.published_at), fetchedAt: validDate(row.fetched_at), audience: audienceText,
    events, deadlines, actions: strings(row.action_items), registration, isOpportunity: row.is_opportunity === true, materials: strings(row.materials),
    publisher:string(row.publisher), sourceCategory:string(row.source_category), processingStatus:string(row.processing_status)||string(row.ai_status)||'pending',
    processingError:string(row.processing_error), processedAt: validDate(row.processed_at), classificationMethod:string(row.classification_method)||string(row.category_method),
    demo:false, summaryKind, ingestion:'crawler',contentStatus:string(row.content_status),
    attachments: Array.isArray(row.attachments) ? row.attachments.filter(isRow).map(x => ({name:string(x.name),url:safeUrl(string(x.url))})) : [],
  };
}
export function sourceStates(value: unknown): Record<string,CrawlSource> {
  const pairs: [string,unknown][] = Array.isArray(value) ? value.filter(isRow).map(s => [string(s.id)||string(s.source_id)||string(s.key),s]) : isRow(value) ? Object.entries(value) : [];
  return Object.fromEntries(pairs.filter(([key,v]) => key && isRow(v)).map(([key,v]) => { const s=v as Row; return [key,{ article_count:typeof s.article_count==='number'?s.article_count:undefined,linked_count:typeof s.linked_count==='number'?s.linked_count:undefined,name:string(s.name)||key,status:string(s.status)||'unknown',published_since:validDate(s.published_since),fetched:typeof s.fetched==='number'?s.fetched:undefined,enabled:s.enabled!==false,last_attempt_at:validDate(s.last_attempt_at),last_success_at:validDate(s.last_success_at),error_message:string(s.error_message),errors:Array.isArray(s.errors)?s.errors.filter(isRow).map(e=>({message:string(e.message)})):[] }]; }));
}
export async function loadCrawled(signal?: AbortSignal): Promise<CrawlSnapshot> {
  const response=await fetch('/data/articles.json',{cache:'no-store',signal});
  if(!response.ok)throw new Error('离线快照暂时无法读取，请启动服务或重试。');
  const data:unknown=await response.json();
  if(!isRow(data)||!Array.isArray(data.articles))throw new Error('离线快照格式不正确。');
  return {articles:data.articles.filter(isRow).map(toArticle).filter((a):a is Article=>!!a),generatedAt:validDate(data.generated_at),sources:sourceStates(data.sources)};
}
