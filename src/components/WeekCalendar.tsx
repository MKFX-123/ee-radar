import type { CalendarEntry } from '../types';
import { dayOf, shortDate, timeOf, today, weekDay } from '../lib/domain';
import { articlePath } from './ui';

export default function WeekCalendar({ days, entries }: { days: string[]; entries: CalendarEntry[] }) {
  const timed = entries.filter(e => e.precision === 'minute');
  const hours = timed.map(e => Number(timeOf(e.at).slice(0, 2)));
  const first = Math.min(8, ...hours), last = Math.max(21, ...hours);
  const slots = Array.from({ length: last - first + 1 }, (_, i) => first + i);
  const card = (e: CalendarEntry) => <a key={e.article.id + e.id} className={'week-event ' + e.kind} href={'#' + articlePath(e.article, e.id)} title={e.article.title}>
    <span>{e.kind === 'deadline' ? '截止' : '活动'}{e.precision === 'minute' ? ' · ' + timeOf(e.at) : ' · 时刻未注明'}</span>
    <strong>{e.article.title}</strong>
    {e.location && e.location !== '原文未说明' && <small>{e.location}</small>}
  </a>;
  return <div className="week-calendar-scroll" tabIndex={0} aria-label="整周时间表，可横向滚动查看七天">
    <div className="week-calendar" role="table" aria-label="周一至周日活动与截止时间表">
      <div className="week-calendar-row week-calendar-head" role="row"><div className="week-axis" role="columnheader">北京时间</div>{days.map(day => <div key={day} className={day === today() ? 'is-today' : ''} role="columnheader"><span>{weekDay(day)}</span><strong>{shortDate(day)}</strong>{day === today() && <small>今天</small>}</div>)}</div>
      <div className="week-calendar-row week-undated" role="row"><div className="week-axis" role="rowheader">时间未注明</div>{days.map(day => <div role="cell" key={day}>{entries.filter(e => e.precision === 'day' && dayOf(e.at) === day).map(card)}</div>)}</div>
      {slots.map(hour => <div className="week-calendar-row week-hour" role="row" key={hour}><div className="week-axis" role="rowheader">{String(hour).padStart(2, '0')}:00</div>{days.map(day => <div role="cell" key={day} className={day === today() ? 'is-today' : ''}>{timed.filter(e => dayOf(e.at) === day && Number(timeOf(e.at).slice(0, 2)) === hour).map(card)}</div>)}</div>)}
    </div>
  </div>;
}
