import { useEffect, useState } from 'react';
import { ArrowDownUp, Bookmark, ChevronRight, Info, LoaderCircle, Plus, Search, SlidersHorizontal, X } from 'lucide-react';
import type { Article, Filters } from '../types';
import { CATEGORIES, SOURCES } from '../data/demo';
import { categoryLabel, defaultFilters, filterArticles } from '../lib/domain';
import { ArticleRow, Empty } from '../components/ui';

type RemoteFeed = { total: number; loading: boolean; error?: string; hasMore: boolean; loadMore: () => void };
export default function Feed({ articles, filters, setFilters, bookmarks, save, onImport, remote }: { articles: Article[]; filters: Filters; setFilters: (v: Filters | ((f: Filters) => Filters)) => void; bookmarks: string[]; save: (id: string) => void; onImport: () => void; remote?: RemoteFeed }) {
  const [limit, setLimit] = useState(8), [sort, setSort] = useState('new'), [search, setSearch] = useState(filters.q);
  useEffect(() => { const timer = window.setTimeout(() => setSearch(filters.q), 180); return () => window.clearTimeout(timer); }, [filters.q]);
  useEffect(() => setLimit(8), [filters]);
  const filtered = remote ? articles.filter(a => !filters.saved || bookmarks.includes(a.id)).slice().sort((a, b) => (Date.parse(b.publishedAt) || 0) - (Date.parse(a.publishedAt) || 0)) : filterArticles(articles, { ...filters, q: search }, bookmarks);
  if (sort === 'old') filtered.reverse();
  const update = (key: keyof Filters, value: string | boolean) => setFilters(f => ({ ...f, [key]: value }));
  const active = Object.entries(filters).some(([k, v]) => k !== 'saved' && Boolean(v));
  const categories = ['', ...CATEGORIES];
  const clear = () => { setSearch(''); setFilters({ ...defaultFilters, saved: filters.saved }); };
  const visible = remote ? filtered : filtered.slice(0, limit);
  const total = remote ? remote.total : filtered.length;
  return <><div className="page-heading"><div><div className="eyebrow">资讯中心</div><h1>{filters.saved ? '我的收藏' : '所有消息，有条有理'}</h1><p>{filters.saved ? '保存值得回看的资讯，仅存储在当前浏览器。' : '按主题、来源和时间，找到真正需要的信息。'}</p></div><button className="button secondary" onClick={onImport}><Plus size={16} />添加资讯</button></div>
    <div className="feed-workspace"><div className="feed-toolbar"><div className="feed-search"><Search size={18} /><input aria-label="搜索资讯内容" placeholder="搜索标题、摘要、正文或标签…" value={filters.q} onChange={e => update('q', e.target.value)} />{filters.q && <button aria-label="清除搜索" className="icon-button" onClick={() => { setSearch(''); update('q', ''); }}><X size={16} /></button>}</div><button className={'button ' + (filters.saved ? 'selected' : 'secondary')} onClick={() => update('saved', !filters.saved)}><Bookmark size={16} />我的收藏<span>{bookmarks.length}</span></button></div>
    <div className="category-tabs large-tabs" aria-label="资讯主分类">{categories.map(c => <button key={c} className={filters.category === c ? 'active' : ''} onClick={() => update('category', c)} aria-pressed={filters.category === c}>{c ? categoryLabel(c) : '全部资讯'}</button>)}</div>
    <div className="filters-row"><span className="filter-caption"><SlidersHorizontal size={15} />筛选</span><label><span className="sr-only">信息来源</span><select value={filters.source} onChange={e => update('source', e.target.value)}><option value="">全部来源</option>{SOURCES.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}<option value="manual">手动录入</option></select></label><label><span className="sr-only">适用人群</span><select value={filters.audience} onChange={e => update('audience', e.target.value)}><option value="">全部人群</option><option>本科生</option><option>研究生</option><option>未说明</option></select></label><label><span className="sr-only">发布时间</span><select value={filters.period} onChange={e => update('period', e.target.value)}><option value="">全部发布时间</option><option value="today">今天发布</option><option value="7">近七天发布</option><option value="30">近三十天发布</option></select></label><label><span className="sr-only">活动状态</span><select value={filters.status} onChange={e => update('status', e.target.value)}><option value="">全部状态</option><option value="open">仍可报名</option><option value="upcoming">即将开始</option><option value="ongoing">进行中</option><option value="closed">已结束或截止</option><option value="unknown">待确认</option></select></label>{active && <button className="text-link clear-filter" onClick={clear}>清除筛选<X size={13} /></button>}</div>
    <div className="result-bar" aria-live="polite"><span>找到 <strong>{total}</strong> 条资讯<span className="results-scope">{remote ? filters.saved ? ' · 收藏筛选结果' : ' · 分页加载' : ' · 当前收录资料'}</span></span><label className="sort-control"><ArrowDownUp size={13} /><select aria-label="资讯排序" value={sort} onChange={e => setSort(e.target.value)}><option value="new">最新发布</option><option value="old">{remote ? '已加载中最早发布' : '最早发布'}</option></select></label></div>
    {remote?.error && <div className="inline-feedback warning" role="alert"><Info size={16} /><span>{remote.error}</span></div>}
    <div aria-busy={remote?.loading || (!remote && search !== filters.q)}>{visible.map(a => <ArticleRow key={a.id} article={a} saved={bookmarks.includes(a.id)} onSave={save} />)}{!visible.length && !remote?.loading && <Empty title={filters.saved && !active ? '还没有匹配的收藏资讯' : undefined} text={filters.saved && !active ? remote ? '收藏仅在已加载的结果中筛选，可继续加载或调整条件。' : '点击资讯右侧的书签，将值得关注的消息留在这里。' : undefined} action={active ? clear : undefined} />}</div>
    {remote?.loading && <div className="inline-feedback" role="status"><LoaderCircle size={16} className="is-spinning" />正在加载资讯…</div>}
    {(remote ? remote.hasMore : limit < filtered.length) ? <button className="panel-more" disabled={remote?.loading} onClick={() => remote ? remote.loadMore() : setLimit(n => n + 8)}>加载更多资讯<ChevronRight size={14} /></button> : filtered.length > 0 && !remote?.loading && <div className="list-end">{remote ? '当前结果已加载完毕' : '已经看到全部 ' + filtered.length + ' 条资讯'}</div>}</div></>;
}
