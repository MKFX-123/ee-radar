import type { Article, CalendarEntry, Category, Deadline, Filters } from '../types';
import { CATEGORY_LABELS, SOURCES } from '../data/demo';
export const defaultFilters: Filters = { q: '', category: '', source: '', audience: '', status: '', period: '', saved: false };
export const now = () => new Date();
export const today = () => dayOf(now());
export function categoryLabel(category: string) { return CATEGORY_LABELS[category as Category] || '其他'; }
export function normalizeCategory(value: unknown): Category {
  if (typeof value !== 'string') return 'other';
  const legacy: Record<string, Category> = { 教务通知: 'academics', 学业教务: 'academics', 学术科研: 'research', 竞赛科创: 'research', 科研科创: 'research', 奖助评优: 'awards', 国际交流: 'international', 实践公益: 'practice', 实习就业: 'career', 校园生活: 'campus' };
  return Object.hasOwn(CATEGORY_LABELS, value) ? value as Category : legacy[value] || 'other';
}
export function dayOf(value: string | Date) {
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date) : '';
}
export function addDays(day: string, amount: number) { const d = new Date(day + 'T12:00:00+08:00'); d.setUTCDate(d.getUTCDate() + amount); return dayOf(d); }
export function shortDate(value: string) { const d = dayOf(value); return d ? `${Number(d.slice(5, 7))}月${Number(d.slice(8))}日` : '时间未确认'; }
export function timeOf(value: string) { return !value || /^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value)) ? '具体时间未注明' : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)); }
export function weekDay(day: string) { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', weekday: 'short' }).format(new Date(day + 'T12:00:00+08:00')); }
export function weekStart(day: string) { const n = new Date(day + 'T12:00:00+08:00').getUTCDay(); return addDays(day, -((n + 6) % 7)); }
export function sourceOf(article: Article) { return article.sourceName || SOURCES.find(s => s.id === article.sourceId)?.name || '手动录入'; }
export function deadlinePassed(deadline: Deadline) { return deadline.precision === 'day' ? deadline.at < today() : new Date(deadline.at) <= now(); }
export function isOpen(article: Article) { return article.registration === 'open' && article.deadlines.length > 0 && article.deadlines.every(d => !deadlinePassed(d) && (d.precision === 'minute' || d.at > today())); }
export function opportunityStatus(article: Article): 'open' | 'upcoming' | 'closed' | 'unknown' {
  if (article.registration === 'closed' || article.deadlines.some(deadlinePassed)) return 'closed';
  if (article.registration === 'upcoming') return 'upcoming';
  if (isOpen(article)) return 'open';
  // 活动在未来不能证明报名尚未截止；缺乏明确依据统一待确认。
  return 'unknown';
}
export function articleStatus(article: Article): 'open' | 'upcoming' | 'ongoing' | 'closed' | 'unknown' {
  if (isOpen(article)) return 'open';
  if (article.isOpportunity && opportunityStatus(article) === 'closed') return 'closed';
  if (article.events.some(e => e.precision !== 'day' && !!e.end && new Date(e.start) <= now() && new Date(e.end) > now())) return 'ongoing';
  if (article.events.some(e => e.precision === 'day' ? dayOf(e.start) > today() : new Date(e.start) > now())) return 'upcoming';
  if (article.events.length && article.events.every(e => e.precision === 'day' ? dayOf(e.start) < today() : !!e.end && new Date(e.end) <= now())) return 'closed';
  return 'unknown';
}
export function deadlineLabel(deadline: Deadline) {
  if (deadlinePassed(deadline)) return '已截止';
  if (deadline.precision === 'day') return `${shortDate(deadline.at)}截止（时刻待确认）`;
  const diff = new Date(deadline.at).getTime() - now().getTime();
  return diff < 86400000 ? `剩余 ${Math.max(1, Math.ceil(diff / 3600000))} 小时` : `剩余 ${Math.ceil(diff / 86400000)} 天`;
}
export function filterArticles(articles: Article[], f: Filters, bookmarks: string[]) {
  return articles.filter(a => {
    const text = [a.title, a.summary, a.content, a.tags.join(' '), sourceOf(a)].join(' ').toLowerCase();
    if (f.q && !text.includes(f.q.trim().toLowerCase())) return false;
    if (f.category && a.category !== f.category) return false;
    if (f.source && a.sourceId !== f.source) return false;
    if (f.audience && a.audience !== f.audience && !(f.audience !== '未说明' && a.audience === '全体同学')) return false;
    if (f.saved && !bookmarks.includes(a.id)) return false;
    if (f.status && ((!a.isOpportunity && !a.events.length && !a.deadlines.length) || articleStatus(a) !== f.status)) return false;
    if (f.period) {
      if (!a.publishedAt || a.publishedAtInferred || a.ingestion === 'manual') return false;
      const start = f.period === 'today' ? today() : addDays(today(), -(Number(f.period) || 1) + 1);
      if (dayOf(a.publishedAt) < start || dayOf(a.publishedAt) > today()) return false;
    }
    return true;
  }).sort((a, b) => (Date.parse(b.publishedAt) || 0) - (Date.parse(a.publishedAt) || 0));
}
export function calendarEntries(articles: Article[]): CalendarEntry[] {
  return articles.flatMap(a => [
    ...a.events.filter(e => Number.isFinite(Date.parse(e.start))).map(e => ({ id: e.id, article: a, title: e.title, at: e.start, location: e.location, kind: 'event' as const, precision: e.precision || (/^\d{4}-\d{2}-\d{2}$/.test(e.start) ? 'day' as const : 'minute' as const) })),
    ...a.deadlines.filter(d => Number.isFinite(Date.parse(d.at))).map(d => ({ id: d.id, article: a, title: d.label, at: d.at, kind: 'deadline' as const, precision: d.precision })),
  ]).filter((entry, index, all) => all.findIndex(other => other.kind === entry.kind && other.at === entry.at && other.article.title === entry.article.title) === index).sort((a,b) => a.at.localeCompare(b.at));
}
export function safeRead<T>(key: string, fallback: T): T { try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) as T : fallback; } catch { return fallback; } }
export function safeWrite(key: string, value: unknown) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
export function validArticle(v: unknown): v is Article {
  if (!v || typeof v !== 'object') return false;
  const a = v as Article;
  return typeof a.id === 'string' && a.id.startsWith('local-') && typeof a.title === 'string' && a.title.trim().length > 0
    && typeof a.content === 'string' && typeof a.summary === 'string' && Object.hasOwn(CATEGORY_LABELS, a.category)
    && a.sourceId === 'manual' && (a.sourceName === undefined || typeof a.sourceName === 'string')
    && (a.sourceUrl === undefined || (typeof a.sourceUrl === 'string' && Boolean(safeUrl(a.sourceUrl))))
    && ['本科生', '研究生', '全体同学', '未说明'].includes(a.audience)
    && a.registration === 'unknown' && a.summaryKind === 'excerpt'
    && typeof a.publishedAt === 'string' && (!a.publishedAt || !Number.isNaN(Date.parse(a.publishedAt)))
    && Array.isArray(a.events) && Array.isArray(a.deadlines) && Array.isArray(a.actions) && Array.isArray(a.tags)
    && a.events.length === 0 && a.deadlines.length === 0 && a.actions.every(x => typeof x === 'string')
    && a.tags.every(x => typeof x === 'string') && a.demo === false && typeof a.isOpportunity === 'boolean';
}
export function readLocalArticles(): Article[] {
  const stored = safeRead<unknown>('ee-radar-imports-v1', []);
  if (!Array.isArray(stored)) return [];
  return stored.map(v => {
    if (!v || typeof v !== 'object') return v;
    return { ...v, category: normalizeCategory(v.category), isOpportunity: v.isOpportunity === true, ingestion: 'manual', fetchedAt: v.fetchedAt || v.publishedAt, publishedAt: '' };
  }).filter(validArticle);
}
export function safeUrl(value: string | undefined) { if (!value) return undefined; try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : undefined; } catch { return undefined; } }
