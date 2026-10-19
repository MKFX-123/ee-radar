import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { CalendarDays, Check, ChevronRight, Compass, FileText, Info, LayoutGrid, LoaderCircle, Plus, Radar, Search, Settings2, Sparkles } from 'lucide-react';
import type { Article, Filters } from './types';
import type { ChatMessage } from './lib/assistant';
import { isRow, loadCrawled, toArticle } from './data/crawled';
import type { CrawlSnapshot } from './data/crawled';
import { addDays, calendarEntries, defaultFilters, filterArticles, opportunityStatus, readLocalArticles, safeRead, safeWrite, today, weekStart } from './lib/domain';
import { api, getEventArticles, getItem, getSources, listParams, rawId, startSync } from './lib/api';
import { useArticles } from './lib/useArticles';
import { useSyncStatus } from './lib/useSyncStatus';
import Home from './pages/Home';
import Feed from './pages/Feed';
import Opportunities from './pages/Opportunities';
import Schedule from './pages/Schedule';
import Detail from './pages/Detail';
import { articlePath, Empty } from './components/ui';
import ImportModal from './components/ImportModal';
import SourceModal from './components/SourceModal';
import Assistant from './components/Assistant';

type Route = { page: 'home' | 'feed' | 'opportunities' | 'schedule' | 'item' | 'missing'; id?: string; focus?: string };
function readRoute(): Route {
  const [path, query = ''] = (window.location.hash.slice(1) || '/').split('?');
  if (path === '/') return { page: 'home' };
  if (path === '/feed') return { page: 'feed' };
  if (path === '/opportunities') return { page: 'opportunities' };
  if (path === '/schedule') return { page: 'schedule' };
  if (path.startsWith('/item/')) return { page: 'item', id: path.slice(6), focus: new URLSearchParams(query).get('focus') ?? undefined };
  return { page: 'missing' };
}
function parseFilters(): Filters { const p = new URLSearchParams(window.location.hash.split('?')[1] || ''); const f = { ...defaultFilters }; for (const key of ['q', 'category', 'source', 'audience', 'status', 'period'] as const) f[key] = p.get(key) || ''; f.saved = p.get('saved') === '1'; return f; }
function filterHash(f: Filters) { const p = new URLSearchParams(); for (const [k, v] of Object.entries(f)) if (v) p.set(k, v === true ? '1' : String(v)); return '#/feed' + (p.size ? '?' + p.toString() : ''); }
function localStrings(key: string): string[] { const v = safeRead<unknown>(key, []); return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []; }
const goto = (path: string) => { window.location.hash = path; };
const unique = (articles: Article[]) => [...new Map(articles.map(a => [a.id, a])).values()];
const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const [mode, setMode] = useState<'checking' | 'api' | 'snapshot' | 'empty'>('checking');
  const [snapshot, setSnapshot] = useState<CrawlSnapshot>({ articles: [], generatedAt: '', sources: {} });
  const [sourceStates, setSourceStates] = useState<CrawlSnapshot['sources']>({});
  const [lastSync, setLastSync] = useState(''), [partialFailure, setPartialFailure] = useState(false), [modelConfigured, setModelConfigured] = useState(false);
  const [dataError, setDataError] = useState(''), [reload, setReload] = useState(0);
  const [imported, setImported] = useState<Article[]>(readLocalArticles);
  const [bookmarks, setBookmarks] = useState<string[]>(() => localStrings('ee-radar-saved')), [disabled, setDisabled] = useState<string[]>(() => localStrings('ee-radar-disabled'));
  const [filters, storeFilters] = useState<Filters>(() => readRoute().page === 'feed' ? parseFilters() : { ...defaultFilters });
  const [homeCategory, setHomeCategory] = useState(''), [search, setSearch] = useState(() => parseFilters().q);
  const [opportunityQuery, setOpportunityQuery] = useState({ category: '', status: 'open' });
  const [scheduleRange, setScheduleRange] = useState(() => ({ start: weekStart(today()), end: addDays(weekStart(today()), 6) }));
  const [eventArticles, setEventArticles] = useState<Article[]>([]), [eventLoading, setEventLoading] = useState(false), [eventError, setEventError] = useState('');
  const [detailArticle, setDetailArticle] = useState<Article>(), [detailLoading, setDetailLoading] = useState(false), [detailError, setDetailError] = useState('');
  const [analyzing, setAnalyzing] = useState(false), [analysisError, setAnalysisError] = useState('');
  const [submittingSync, setSubmittingSync] = useState(false), [syncNote, setSyncNote] = useState('');
  const [modal, setModal] = useState<'import' | 'sources' | null>(null), [assistant, setAssistant] = useState(false), [initialQuestion, setInitialQuestion] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]), [toast, setToast] = useState('');
  const searchRef = useRef<HTMLInputElement>(null), mainRef = useRef<HTMLElement>(null);
  const connectedBefore = useRef(false), syncRequestPending = useRef(false);
  const sync = useSyncStatus(() => setReload(value => value + 1), () => { connectedBefore.current = true; setMode('api'); setDataError(''); });
  const syncing = submittingSync || sync.status?.running === true;
  const scrollPositions = useRef<Record<string, number>>({}), currentPath = useRef(window.location.hash || '#/');
  const feedReturn = useRef(readRoute().page === 'feed' ? window.location.hash : '#/feed'); const priorList = useRef(window.location.hash || '#/');
  const apiReady = mode === 'api', disabledKey = disabled.join(',');
  const homeParams = listParams(defaultFilters, disabled, []);
  const feedParams = listParams(filters, disabled, bookmarks);
  const opportunityParams = listParams({ ...defaultFilters, ...opportunityQuery }, disabled, []);
  const homeList = useArticles('/items', homeParams, apiReady && route.page === 'home', reload);
  const feedList = useArticles('/items', feedParams, apiReady && route.page === 'feed', reload);
  const opportunityList = useArticles('/opportunities', opportunityParams, apiReady && route.page === 'opportunities', reload);
  const localVisible = useMemo(() => imported.filter(a => !disabled.includes(a.sourceId)), [imported, disabled]);
  const offlineArticles = useMemo(() => unique([...localVisible, ...snapshot.articles.filter(a => !disabled.includes(a.sourceId))]), [localVisible, snapshot, disabled]);
  const allArticles = useMemo(() => unique([...imported, ...snapshot.articles, ...homeList.articles, ...feedList.articles, ...opportunityList.articles, ...eventArticles, ...(detailArticle ? [detailArticle] : [])]), [imported, snapshot, homeList.articles, feedList.articles, opportunityList.articles, eventArticles, detailArticle]);
  const visibleArticles = allArticles.filter(a => !disabled.includes(a.sourceId));
  const activeArticle = route.page === 'item' ? detailArticle?.id === route.id ? detailArticle : allArticles.find(a => a.id === route.id) : undefined;
  const pageArticles = apiReady ? unique([...filterArticles(localVisible, filters, bookmarks), ...feedList.articles]) : offlineArticles;
  const homeArticles = apiReady ? unique([...localVisible, ...homeList.articles, ...eventArticles]) : offlineArticles;
  const opportunityArticles = apiReady ? unique([...localVisible.filter(a => a.isOpportunity && (!opportunityQuery.category || a.category === opportunityQuery.category) && (!opportunityQuery.status || opportunityStatus(a) === opportunityQuery.status)), ...opportunityList.articles]) : offlineArticles;
  const entries = calendarEntries(apiReady ? unique([...localVisible, ...eventArticles]) : offlineArticles);
  const onRangeChange = useCallback((start: string, end: string) => setScheduleRange(current => current.start === start && current.end === end ? current : { start, end }), []);
  const onOpportunityQueryChange = useCallback((category: string, status: string) => setOpportunityQuery(current => current.category === category && current.status === status ? current : { category, status }), []);

  useEffect(() => {
    let cancelled = false; const controller = new AbortController(); const timeout = window.setTimeout(() => controller.abort(), 8000);
    setDataError('');
    api('/health', { signal: controller.signal }).then(health => {
      if (controller.signal.aborted) return;
      connectedBefore.current = true; setMode('api'); setModelConfigured(health.model_configured === true || health.ai_configured === true);
    }).catch(async () => {
      if (cancelled) return;
      if (connectedBefore.current) { setDataError('资讯服务连接暂时中断，当前已加载内容会保留；连接恢复后自动更新。'); return; }
      setModelConfigured(false);
      try { const saved = await loadCrawled(); if (cancelled) return; setSnapshot(saved); setSourceStates(saved.sources); setMode('snapshot'); setDataError('API 暂不可用，当前展示离线采集快照。'); }
      catch (error: unknown) { if (cancelled) return; setMode('empty'); setDataError(errorText(error, 'API 和离线快照均暂不可用。')); }
    }).finally(() => clearTimeout(timeout));
    return () => { cancelled = true; clearTimeout(timeout); controller.abort(); };
  }, [reload]);
  useEffect(() => {
    if (!apiReady) return;
    const controller = new AbortController();
    getSources(controller.signal).then(result => { setSourceStates(result.sources); setLastSync(result.lastSuccess); setPartialFailure(result.partialFailure); }).catch((error: unknown) => { if (!controller.signal.aborted) setDataError(errorText(error, '信源状态暂不可用。')); });
    return () => controller.abort();
  }, [apiReady, reload, sync.status?.latest_job?.job_id, sync.status?.latest_job?.status, sync.status?.latest_job?.finished_at]);
  useEffect(() => {
    if (!apiReady || !['home', 'schedule'].includes(route.page)) return;
    const controller = new AbortController();
    const start = route.page === 'home' ? today() : scheduleRange.start, end = route.page === 'home' ? addDays(today(), 6) : scheduleRange.end;
    setEventLoading(true); setEventError('');
    getEventArticles(start, end, disabled, controller.signal).then(setEventArticles).catch((error: unknown) => { if (!controller.signal.aborted) setEventError(errorText(error, '日程暂时无法读取。')); }).finally(() => { if (!controller.signal.aborted) setEventLoading(false); });
    return () => controller.abort();
  }, [apiReady, route.page, scheduleRange.start, scheduleRange.end, disabledKey, reload]);
  useEffect(() => {
    if (!apiReady || route.page !== 'item' || !route.id?.startsWith('crawl-')) { setDetailLoading(false); return; }
    const controller = new AbortController(); setDetailLoading(true); setDetailError(''); setAnalysisError('');
    getItem(route.id, controller.signal).then(setDetailArticle).catch((error: unknown) => { if (!controller.signal.aborted) setDetailError(errorText(error, '资讯详情读取失败。')); }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [apiReady, route.page, route.id, reload]);
  useEffect(() => {
    const onHash = () => { const next = readRoute(); if (next.page === 'item' && !currentPath.current.startsWith('#/item/')) feedReturn.current = priorList.current; scrollPositions.current[currentPath.current] = window.scrollY; currentPath.current = window.location.hash || '#/'; setRoute(next); if (next.page !== 'item') priorList.current = window.location.hash || '#/'; if (next.page === 'feed') { const f = parseFilters(); storeFilters(f); setSearch(f.q); feedReturn.current = window.location.hash; } requestAnimationFrame(() => { mainRef.current?.focus({ preventScroll: true }); window.scrollTo({ top: scrollPositions.current[currentPath.current] || 0 }); }); };
    const keyboard = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) { e.preventDefault(); if (searchRef.current?.parentElement && window.getComputedStyle(searchRef.current.parentElement).display === 'none') goto('/feed'); else searchRef.current?.focus(); } };
    window.addEventListener('hashchange', onHash); window.addEventListener('keydown', keyboard); return () => { window.removeEventListener('hashchange', onHash); window.removeEventListener('keydown', keyboard); };
  }, []);
  useEffect(() => { if (route.page === 'opportunities') { const query = new URLSearchParams(); if (opportunityQuery.category) query.set('category', opportunityQuery.category); if (opportunityQuery.status !== 'open') query.set('status', opportunityQuery.status); priorList.current = '#/opportunities' + (query.size ? '?' + query : ''); } }, [opportunityQuery, route.page]); useEffect(() => { const id = window.setTimeout(() => setToast(''), 4300); return () => window.clearTimeout(id); }, [toast]);
  useEffect(() => { const labels = { home: '今日概览', feed: '全部资讯', opportunities: '机会中心', schedule: '校园日程', item: '资讯详情', missing: '页面未找到' }; document.title = labels[route.page] + ' · EE Radar'; }, [route.page]);
  function setFilters(v: Filters | ((f: Filters) => Filters)) { const next = typeof v === 'function' ? v(filters) : v; storeFilters(next); const hash = filterHash(next); history.replaceState(null, '', hash); currentPath.current = hash; feedReturn.current = hash; priorList.current = hash; }
  function save(id: string) { const next = bookmarks.includes(id) ? bookmarks.filter(x => x !== id) : [...bookmarks, id]; setBookmarks(next); if (!safeWrite('ee-radar-saved', next)) setToast('收藏已暂存，本浏览器无法持久保存。'); else setToast(next.includes(id) ? '已收藏，可在“我的收藏”中查看' : '已取消收藏'); }
  function toggleSource(id: string) { const next = disabled.includes(id) ? disabled.filter(x => x !== id) : [...disabled, id]; setDisabled(next); if (!safeWrite('ee-radar-disabled', next)) setToast('来源显示设置仅在当前页面生效'); }
  function ask(q = '') { setInitialQuestion(q); setAssistant(true); }
  function submitSearch(e: FormEvent) { e.preventDefault(); const next = { ...defaultFilters, q: search }; storeFilters(next); goto(filterHash(next).slice(1)); }
  function deleteLocal(id: string) { const next = imported.filter(a => a.id !== id); setImported(next); const stored = safeWrite('ee-radar-imports-v1', next); const nextBookmarks = bookmarks.filter(b => b !== id); setBookmarks(nextBookmarks); safeWrite('ee-radar-saved', nextBookmarks); goto('/feed'); setToast(stored ? '已删除本地录入的资讯' : '仅从当前页面删除，刷新后可能恢复'); }
  async function analyzeArticle() {
    if (!activeArticle || analyzing) return; setAnalyzing(true); setAnalysisError('');
    try { const result = await api('/items/' + encodeURIComponent(rawId(activeArticle.id)) + '/analyze', { method: 'POST' }); const item = toArticle(isRow(result.item) ? result.item : result); if (item) setDetailArticle(item); setToast('已完成结构化分析，可核对原文依据'); setReload(v => v + 1); }
    catch (error: unknown) { setAnalysisError(errorText(error, 'AI 分析暂未完成，已收录正文仍可查看。')); }
    finally { setAnalyzing(false); }
  }
  async function syncSources() {
    if (!apiReady || syncing || syncRequestPending.current) return;
    syncRequestPending.current = true; setSubmittingSync(true); setSyncNote('正在提交同步请求，已有资讯仍可浏览。');
    try {
      const task = await startSync(); sync.acceptJob(task.job);
      const note = task.reused ? '已加入正在运行的同步，本轮完成后自动更新页面。' : '同步已在后台开始，本轮完成后自动更新页面。';
      setSyncNote(''); setToast(note);
    } catch (error: unknown) { setSyncNote(errorText(error, '同步暂时失败，已有资讯仍可浏览。')); }
    finally { syncRequestPending.current = false; setSubmittingSync(false); }
  }
  const statusText = mode === 'checking' ? '正在检查资讯服务…' : dataError || (apiReady ? sync.error || (syncing ? '正在后台更新资讯，已加载内容仍可浏览。' : partialFailure ? '部分来源暂未更新，已收录资料仍可浏览。' : '已连接本地资讯服务 · 发布时间、采集时间与活动时间分别展示') : '当前仅显示本浏览器手动录入的资料');
  return <><a className="skip-link" href="#main-content" onClick={e => { e.preventDefault(); mainRef.current?.focus(); }}>跳至主要内容</a><header className="topbar"><div className="topbar-inner"><a className="brand" href="#/" aria-label="EE Radar 首页"><img src="/radar.svg" width="38" height="38" alt="" /><div><strong>EE Radar<span>信息雷达</span></strong><small>电子系同学的校园信息站</small></div></a><nav className="main-nav" aria-label="主导航">{[{ p: 'home', href: '/', text: '首页', icon: LayoutGrid }, { p: 'feed', href: '/feed', text: '资讯', icon: FileText }, { p: 'opportunities', href: '/opportunities', text: '机会', icon: Compass }, { p: 'schedule', href: '/schedule', text: '日程', icon: CalendarDays }].map(({ p, href, text, icon: Icon }) => <a key={p} href={'#' + href} className={route.page === p || (p === 'feed' && route.page === 'item') ? 'active' : ''} aria-current={route.page === p ? 'page' : undefined}><Icon size={17} />{text}</a>)}</nav><div className="top-actions"><form className="global-search" onSubmit={submitSearch} role="search"><Search size={16} /><input ref={searchRef} value={search} onChange={e => setSearch(e.target.value)} aria-label="全站搜索" placeholder="搜索校园资讯" /><kbd>⌘ K</kbd></form><button className="button primary small add-button" onClick={() => setModal('import')}><Plus size={16} /><span>添加资讯</span></button><button className="icon-button source-button" onClick={() => setModal('sources')} aria-label="信源管理"><Settings2 size={19} /></button></div></div></header>
    <div className="preview-strip" role="status"><span className="preview-dot" />{apiReady ? '本地资料库' : mode === 'snapshot' ? '离线快照' : '服务状态'}<span className="preview-detail">{statusText}</span><button disabled={mode === 'checking' || syncing} onClick={() => { if (apiReady) void syncSources(); else { setReload(n => n + 1); sync.refresh(); } }}>{syncing ? '正在同步…' : apiReady ? '刷新数据' : '重试连接'}<ChevronRight size={13} /></button><button onClick={() => { setModal('sources'); sync.refresh(); }}>查看信源<ChevronRight size={13} /></button></div>
    <main id="main-content" tabIndex={-1} ref={mainRef} className={'main-shell page-' + route.page}>
      {route.page === 'home' && <>{(homeList.error || eventError) && <div className="inline-feedback warning" role="alert"><Info size={16} />{homeList.error || eventError}</div>}{homeList.loading && <div className="inline-feedback" role="status"><LoaderCircle size={16} className="is-spinning" />正在加载最新资讯…</div>}<Home articles={homeArticles} entries={entries} bookmarks={bookmarks} save={save} category={homeCategory} setCategory={setHomeCategory} ask={ask} openSources={() => setModal('sources')} disabled={disabled} lastSync={lastSync} sourceStates={sourceStates} /></>}
      {route.page === 'feed' && <Feed articles={pageArticles} filters={filters} setFilters={setFilters} bookmarks={bookmarks} save={save} onImport={() => setModal('import')} remote={apiReady ? { ...feedList, total: feedList.total + filterArticles(localVisible, filters, bookmarks).length } : undefined} />}
      {route.page === 'opportunities' && <Opportunities articles={opportunityArticles} bookmarks={bookmarks} save={save} onQueryChange={onOpportunityQueryChange} remote={apiReady ? { ...opportunityList, total: opportunityList.total + opportunityArticles.filter(a => a.sourceId === 'manual').length } : undefined} />}
      {route.page === 'schedule' && <Schedule entries={entries} onRangeChange={onRangeChange} loading={apiReady && eventLoading} error={apiReady ? eventError : undefined} />}
      {route.page === 'item' && <>{detailError && <div className="inline-feedback warning" role="alert"><Info size={16} />{detailError}{activeArticle && ' 当前显示已加载的版本。'}</div>}{detailLoading && <div className="inline-feedback" role="status"><LoaderCircle size={16} className="is-spinning" />正在读取完整资讯…</div>}{activeArticle ? <>{apiReady && activeArticle.ingestion === 'crawler' && <div className="analysis-toolbar"><span><Sparkles size={16} />{activeArticle.summaryKind === 'ai' ? '已缓存 AI 摘要与提取结果' : modelConfigured ? '可根据已收录正文生成结构化摘要' : '模型未配置，当前展示原文摘录与规则分类'}</span>{modelConfigured && activeArticle.summaryKind !== 'ai' && <button className="button secondary small" onClick={analyzeArticle} disabled={analyzing}>{analyzing ? '正在分析…' : '分析这条资讯'}</button>}</div>}{analysisError && <div className="inline-feedback warning" role="alert"><Info size={16} />{analysisError}</div>}<Detail key={activeArticle.id} article={activeArticle} focus={route.focus} saved={bookmarks.includes(activeArticle.id)} save={save} ask={ask} backHref={feedReturn.current} onDelete={deleteLocal} /></> : !detailLoading && <Empty title="这条资讯暂时不存在" text="它可能已被删除、当前服务不可用，或链接地址不完整。请返回资讯页查找。" />}</>}
      {route.page === 'missing' && <Empty title="这个页面还不存在" text="通过顶部导航返回首页、资讯、机会或日程。" />}
    </main>
    <footer className="site-footer"><span><Radar size={14} />EE Radar</span><span>从信息碎片到行动日程。</span><span>{apiReady ? '使用本地 SQLite 资料库 · 北京时间' : snapshot.generatedAt ? '快照导出：' + new Date(snapshot.generatedAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) + ' 北京时间' : '尚未连接资讯服务'}</span></footer><button className="assistant-launcher" onClick={() => ask()} aria-label="打开 Radar 助手"><Sparkles size={19} /><span>问问 Radar</span><span className="launcher-key">{modelConfigured ? 'AI' : '检索'}</span></button>{toast && <div className="toast" role="status"><Check size={16} />{toast}</div>}
    {modal === 'import' && <ImportModal onClose={() => setModal(null)} onImport={a => { const next = [a, ...imported]; setImported(next); const stored = safeWrite('ee-radar-imports-v1', next); setModal(null); goto(articlePath(a)); setToast(stored ? '已保存到本浏览器，可在资讯中查看' : '已加入当前页面，但浏览器未允许持久保存'); }} articles={allArticles} />}
    {modal === 'sources' && <SourceModal disabled={disabled} toggle={toggleSource} articles={allArticles} states={sourceStates} apiReady={apiReady} syncing={syncing} syncNote={syncNote} syncStatus={sync.status} syncError={sync.error} onSync={syncSources} onClose={() => setModal(null)} onImport={() => setModal('import')} />}
    {assistant && <Assistant articles={visibleArticles} initialQuestion={initialQuestion} messages={messages} setMessages={setMessages} apiReady={apiReady} disabledSources={disabled} modelConfigured={modelConfigured} onModelConfigured={setModelConfigured} onClose={() => { setAssistant(false); setInitialQuestion(''); }} />}
  </>;
}
