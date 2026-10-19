import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { ArrowRight, Bookmark, CalendarDays, ChevronRight, Clock3, FileText, MapPin, Search, X } from 'lucide-react';
import type { Article, CalendarEntry } from '../types';
import { SOURCES } from '../data/demo';
import { categoryLabel, today, dayOf, deadlinePassed, shortDate, sourceOf, timeOf } from '../lib/domain';
export const categoryStyles: Record<string, string> = { academics: 'blue', research: 'purple', awards: 'orange', international: 'blue', practice: 'green', career: 'teal', campus: 'green', other: 'gray' };
export const articlePath = (a: Article, focus?: string) => '/item/' + a.id + (focus ? '?focus=' + encodeURIComponent(focus) : '');
export function Badge({ category }: { category: string }) { return <span className={'badge ' + (categoryStyles[category] || 'gray')}>{categoryLabel(category)}</span>; }
export function SourceIcon({ id }: { id: string }) { const s = SOURCES.find(x => x.id === id); return <span className="source-icon" style={{ color: s?.color, backgroundColor: (s?.color || '#7852A5') + '12' }}>{s?.short || <FileText size={13} />}</span>; }
export function Empty({ title = '没有找到符合条件的资讯', text = '试试调整关键词，或清除部分筛选条件。', action }: { title?: string; text?: string; action?: () => void }) { return <div className="empty-state"><Search size={30} strokeWidth={1.3} /><h3>{title}</h3><p>{text}</p>{action && <button className="button secondary small" onClick={action}>清除筛选</button>}</div>; }
export function ArticleRow({ article: a, saved, onSave }: { article: Article; saved: boolean; onSave: (id: string) => void }) {
  const event = a.events[0], deadline = a.deadlines.find(d => !deadlinePassed(d));
  return <article className="article-row"><div className="article-content">
    <div className="article-meta"><Badge category={a.category} /><span>{sourceOf(a)}</span><span className="meta-dot" /><time dateTime={a.publishedAt}>{!a.publishedAt ? '发布时间未确认' : dayOf(a.publishedAt) === today() ? '今天 ' + timeOf(a.publishedAt) : shortDate(a.publishedAt)}</time><span className="sample-tag">{a.demo ? '示例' : a.ingestion === 'crawler' ? '真实采集' : '手动录入'}</span></div>
    <h3><a href={'#' + articlePath(a)}>{a.title}</a></h3><p className="article-summary">{a.summary}</p>
    <div className="article-foot">{event && <span><CalendarDays size={13} />{shortDate(event.start)} {event.precision === 'day' ? '具体时间未注明' : timeOf(event.start)}</span>}{deadline && <span className="deadline-meta"><Clock3 size={13} />{shortDate(deadline.at)}截止</span>}{!event && !deadline && <span>{a.audience}</span>}<span className="row-action">{a.actions[0]}</span></div>
  </div><button className={'icon-button save-button ' + (saved ? 'is-saved' : '')} onClick={() => onSave(a.id)} aria-label={(saved ? '取消收藏：' : '收藏：') + a.title} aria-pressed={saved}><Bookmark size={18} fill={saved ? 'currentColor' : 'none'} /></button></article>;
}
export function CalendarRow({ entry: e }: { entry: CalendarEntry }) { return <a className={'calendar-row ' + e.kind} href={'#' + articlePath(e.article, e.id)}><div className="calendar-time">{e.precision === 'day' ? '时刻未明' : timeOf(e.at)}<span>{e.kind === 'deadline' ? '截止' : '活动'}</span></div><div className="calendar-mark" /><div className="calendar-row-body"><h3>{e.kind === 'deadline' || ['活动时间', '系列讲座'].includes(e.title) ? e.article.title : e.title}</h3><p>{e.kind === 'event' ? <><MapPin size={12} />{e.location}</> : <><Clock3 size={12} />{e.title}</>}</p><span className="calendar-meta">{sourceOf(e.article)}{e.article.demo && ' · 示例'}</span></div><ChevronRight size={16} /></a>; }
export function MoreLink({ href, children }: { href: string; children: ReactNode }) { return <a href={href} className="text-link">{children}<ArrowRight size={14} /></a>; }
export function Modal({ title, onClose, children, className = '' }: { title: string; onClose: () => void; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null), callback = useRef(onClose); callback.current = onClose;
  useEffect(() => {
    const dialog = ref.current, previous = document.activeElement as HTMLElement | null;
    if (!dialog) return;
    dialog.showModal();
    const close = (event: Event) => { event.preventDefault(); callback.current(); };
    // 原生弹窗之外再处理首尾循环，避免 Tab 将焦点移到浏览器工具栏。
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const controls = Array.from(dialog.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]'))
        .filter(el => el.tabIndex >= 0 && !el.matches(':disabled') && el.getClientRects().length > 0);
      const first = controls[0], last = controls[controls.length - 1];
      if (!first) { event.preventDefault(); dialog.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    dialog.addEventListener('cancel', close);
    dialog.addEventListener('keydown', trapFocus);
    const old = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      dialog.removeEventListener('cancel', close);
      dialog.removeEventListener('keydown', trapFocus);
      dialog.close();
      document.body.style.overflow = old;
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return <dialog ref={ref} className={'modal ' + className} aria-label={title} onClick={e => { if (e.target === e.currentTarget) { const r = e.currentTarget.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) onClose(); } }}><div className="modal-head"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭窗口"><X size={20} /></button></div>{children}</dialog>;
}
