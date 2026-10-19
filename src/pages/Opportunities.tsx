import { useEffect, useState } from 'react';
import { ArrowRight, Bookmark, CheckCheck, Clock3, ExternalLink, FileCheck2, Info, LoaderCircle, Users } from 'lucide-react';
import type { Article } from '../types';
import { categoryLabel, deadlinePassed, opportunityStatus, safeUrl, shortDate, sourceOf, timeOf } from '../lib/domain';
import { articlePath, Badge, Empty } from '../components/ui';

type RemoteOpportunities = { total: number; loading: boolean; error?: string; hasMore: boolean; loadMore: () => void };
const opportunityCategories = ['', 'awards', 'international', 'practice', 'research', 'career', 'campus', 'academics', 'other'];
const states = [{ value: '', label: '全部机会' }, { value: 'open', label: '可报名' }, { value: 'upcoming', label: '尚未开放' }, { value: 'closed', label: '已截止' }, { value: 'unknown', label: '待确认' }];
const readQuery = () => { const query = new URLSearchParams(window.location.hash.split('?')[1] || ''); return { category: query.get('category') || '', status: query.get('status') ?? 'open' }; }; const stateLabels = { open: '可报名', upcoming: '尚未开放', closed: '已截止', unknown: '待确认' };
export default function Opportunities({ articles, bookmarks, save, remote, onQueryChange }: { articles: Article[]; bookmarks: string[]; save: (id: string) => void; remote?: RemoteOpportunities; onQueryChange?: (category: string, status: string) => void }) {
  const [category, setCategory] = useState(() => readQuery().category), [status, setStatus] = useState(() => readQuery().status), [limit, setLimit] = useState(8);
  useEffect(() => { const query = new URLSearchParams(); if (category) query.set('category', category); if (status !== 'open') query.set('status', status); history.replaceState(null, '', '#/opportunities' + (query.size ? '?' + query : '')); onQueryChange?.(category, status); setLimit(8); }, [category, status, onQueryChange]);
  const opportunities = articles.filter(a => a.isOpportunity);
  const filtered = (remote ? opportunities : opportunities.filter(a => (!category || a.category === category) && (!status || opportunityStatus(a) === status))).slice().sort((a, b) => {
    const nextDeadline = (article: Article) => article.deadlines.filter(d => d.at && !deadlinePassed(d)).map(d => d.at).sort()[0] || '9999';
    return nextDeadline(a).localeCompare(nextDeadline(b)) || (Date.parse(b.publishedAt) || 0) - (Date.parse(a.publishedAt) || 0);
  });
  const visible = remote ? filtered : filtered.slice(0, limit);
  const clear = () => { setCategory(''); setStatus(''); };
  return <>
    <div className="page-heading"><div><div className="eyebrow">机会中心</div><h1>把值得争取的，放在眼前</h1><p>从同一份资讯里，找到可申请、可报名的机会。</p></div><span className="soft-badge"><Clock3 size={14} />按近期截止优先</span></div>
    <div className="opportunity-notice"><Info size={17} /><p>「可报名」表示原通知的报名时间尚未截止；名额、资格及入口能否提交，请以原页面为准。限额或时间不明的机会列在「待确认」。</p></div>
    <section className="opportunities-workspace">
      <div className="category-tabs large-tabs opportunity-categories" aria-label="机会主题">{opportunityCategories.map(c => <button key={c} className={category === c ? 'active' : ''} aria-pressed={category === c} onClick={() => setCategory(c)}>{c ? categoryLabel(c) : '全部主题'}</button>)}</div>
      <div className="opportunity-toolbar"><div className="segmented opportunity-states" aria-label="机会状态">{states.map(s => <button key={s.value} className={status === s.value ? 'active' : ''} aria-pressed={status === s.value} onClick={() => setStatus(s.value)}>{s.label}</button>)}</div><span className="muted" aria-live="polite">{remote ? remote.total : filtered.length} 条机会</span></div>
      {remote?.error && <div className="inline-feedback warning" role="alert"><Info size={16} /><span>{remote.error}</span></div>}
      <div className="opportunity-list" aria-busy={remote?.loading}>{visible.map(a => {
        const state = opportunityStatus(a), url = safeUrl(a.sourceUrl);
        const deadlines = a.deadlines.filter(d => d.at).slice().sort((x, y) => x.at.localeCompare(y.at));
        const deadline = deadlines.find(d => !deadlinePassed(d)) || deadlines.at(-1);
        return <article className="opportunity-card" key={a.id}>
          <div className="opportunity-card-top"><div className="article-meta"><Badge category={a.category} /><span className={'opportunity-status ' + state}>{stateLabels[state]}</span>{a.demo && <span className="sample-tag">虚构示例</span>}</div><button className={'icon-button ' + (bookmarks.includes(a.id) ? 'is-saved' : '')} aria-label={(bookmarks.includes(a.id) ? '取消收藏：' : '收藏：') + a.title} aria-pressed={bookmarks.includes(a.id)} onClick={() => save(a.id)}><Bookmark size={18} fill={bookmarks.includes(a.id) ? 'currentColor' : 'none'} /></button></div>
          <h2><a href={'#' + articlePath(a)}>{a.title}</a></h2><p className="article-summary">{a.summary || '暂未生成摘要，可查看详情和原文。'}</p>
          <div className="opportunity-facts"><div><span><Users size={15} />适用人群</span><strong>{a.audience === '未说明' ? '原文未确认，请核对资格' : a.audience}</strong></div><div><span><Clock3 size={15} />{deadline?.label || '截止时间'}</span><strong className={state === 'closed' ? 'expired' : ''}>{deadline ? shortDate(deadline.at) + (deadline.precision === 'minute' ? ' ' + timeOf(deadline.at) : ' · 具体时刻未注明') : '截止时间待确认'}</strong></div></div>
          <div className="opportunity-requirements"><section><h3><FileCheck2 size={15} />所需材料</h3>{a.materials?.length ? <ul>{a.materials.slice(0, 3).map((material, i) => <li key={i}>{material}</li>)}</ul> : <p>未在当前收录内容中确认。</p>}</section><section><h3><CheckCheck size={15} />下一步</h3>{a.actions.length ? <ul>{a.actions.slice(0, 3).map((action, i) => <li key={i}>{action}</li>)}</ul> : <p>先阅读原文，确认资格与申请方式。</p>}</section></div>
          <div className="opportunity-card-bottom"><span>{sourceOf(a)}</span><div>{url && <a className="text-link" href={url} target="_blank" rel="noopener noreferrer">原文<ExternalLink size={13} /></a>}<a className="text-link" href={'#' + articlePath(a)}>查看要求<ArrowRight size={14} /></a></div></div>
        </article>;
      })}</div>
      {!visible.length && !remote?.loading && <Empty title="当前条件下没有收录的机会" text="可以切换其他主题，或查看待确认、已截止的资讯。没有结果不代表没有相关项目。" action={category || status ? clear : undefined} />}
      {remote?.loading && <div className="inline-feedback" role="status"><LoaderCircle size={16} className="is-spinning" />正在加载机会…</div>}
      {(remote ? remote.hasMore : limit < filtered.length) && <button className="panel-more" disabled={remote?.loading} onClick={() => remote ? remote.loadMore() : setLimit(v => v + 8)}>加载更多机会<ArrowRight size={14} /></button>}
    </section>
  </>;
}
