import type { Article } from '../types';
import { SOURCES } from '../data/demo';
import { today as currentDay, addDays, dayOf, deadlinePassed, isOpen, shortDate, timeOf, weekStart } from './domain';
export interface ChatMessage { id: string; role: 'user' | 'assistant'; text: string; citations?: string[]; references?: {id:string;title:string;sourceUrl?:string}[]; mode?: string; modelError?: string; fallbackReason?: string; modelErrorCode?: string; }
export function localAnswer(question: string, articles: Article[]): { text: string; citations: string[] } {
  const q = question.trim(), TODAY = currentDay();
  let pool = [...articles];
  const nextWeek = /下周/.test(q), thisWeek = /本周|这周/.test(q), today = /今天|今日/.test(q), tomorrow = /明天|明日/.test(q);
  const contentOf = (a: Article) => [a.title, a.summary, ...a.tags, ...a.events.map(e => e.title)].join(' ');
  if (/竞赛|比赛/.test(q)) pool = pool.filter(a => /竞赛|比赛|挑战赛/.test(contentOf(a)));
  else if (/讲座/.test(q)) pool = pool.filter(a => /讲座/.test(contentOf(a)));
  else if (/奖学金|助学金|评优/.test(q)) pool = pool.filter(a => a.category === 'awards');
  else if (/交换|海外|出国|暑校|国际/.test(q)) pool = pool.filter(a => a.category === 'international');
  else if (/志愿|实践|公益/.test(q)) pool = pool.filter(a => a.category === 'practice');
  else if (/科研|学术/.test(q)) pool = pool.filter(a => a.category === 'research');
  else if (/体育|运动|跑步|羽毛球/.test(q)) pool = pool.filter(a => a.tags.includes('体育运动'));
  if (/跑步/.test(q)) pool = pool.filter(a => /跑步|轻松跑/.test(contentOf(a)));
  if (/羽毛球/.test(q)) pool = pool.filter(a => /羽毛球/.test(contentOf(a)));
  if (/本科/.test(q)) pool = pool.filter(a => a.audience === '本科生' || a.audience === '全体同学');
  if (/研究生/.test(q)) pool = pool.filter(a => a.audience === '研究生' || a.audience === '全体同学');
  const requestedSources = SOURCES.filter(s => q.includes(s.name));
  if (requestedSources.length) pool = pool.filter(a => requestedSources.some(s => s.id === a.sourceId));
  if (/已截止|已结束|不能报名/.test(q)) pool = pool.filter(a => a.registration === 'closed' || a.deadlines.some(deadlinePassed));
  else if (/报名|还能|可以报/.test(q)) pool = pool.filter(isOpen);
  let dateNote = '';
  let inRange = (_value: string) => true;
  const byPublication = /发布|通知|资讯/.test(q) && !/活动|讲座|截止|报名/.test(q);
  if (nextWeek || thisWeek || today || tomorrow) {
    const start = today ? TODAY : tomorrow ? addDays(TODAY, 1) : addDays(weekStart(TODAY), nextWeek ? 7 : 0), end = today || tomorrow ? start : addDays(start, 6);
    inRange = value => dayOf(value) >= start && dayOf(value) <= end;
    pool = pool.filter(a => byPublication ? inRange(a.publishedAt) : [...a.events.map(e => e.start), ...a.deadlines.map(d => d.at)].some(inRange));
    dateNote = '时间范围：' + shortDate(start) + (start !== end ? '至' + shortDate(end) : '') + '，按' + (byPublication ? '发布时间' : '活动或截止日期') + '筛选。';
  }
  const structured = /奖学金|助学金|评优|交换|海外|出国|暑校|国际|志愿|实践|公益|竞赛|比赛|讲座|科研|学术|体育|运动|跑步|羽毛球|报名|还能|可以报|本周|这周|下周|今天|今日|明天|明日|本科|研究生|已截止|已结束/.test(q) || requestedSources.length > 0;
  const exact = articles.filter(a => q === a.title);
  if (exact.length) { pool = exact; inRange = () => true; dateNote = ''; }
  else if (!structured) { const terms = q.replace(/[？?。，,！!]/g, ' ').split(/\s+/).filter(Boolean); pool = pool.filter(a => terms.some(t => [a.title,a.summary,a.content].join(' ').includes(t))); }
  const total = pool.length; pool = pool.slice(0,5);
  if (!pool.length) return { text: '当前已收录的信息中未找到匹配结果。可以试试“讲座”“竞赛”“体育”等关键词，或在资讯页调整筛选。当前只检索已保存的采集结果与手动录入资料，未收录的信息不会出现在结果中。', citations: [] };
  const scope = '在当前启用来源的 ' + articles.length + ' 条本地信息中，找到 ' + total + ' 条相关记录' + (total > 5 ? '，展示前 5 条' : '') + '：\n' + dateNote + '\n';
  const text = scope + pool.map((a,i) => (i+1) + '. ' + a.title + '\n' + a.events.filter(e => inRange(e.start)).map(e => '活动：' + shortDate(e.start) + ' ' + timeOf(e.start) + '，' + e.title).concat(a.deadlines.filter(d => inRange(d.at)).map(d => d.label + '：' + shortDate(d.at) + (d.precision === 'minute' ? ' ' + timeOf(d.at) : '（时刻未说明）'))).join('；') + '\n' + (a.actions[0] || '查看正文与原文')).join('\n\n') + '\n\n请点击引用核对详情。以上为本地规则检索，未调用 AI 模型。具体安排与参与要求请以来源原文为准。';
  return { text, citations: pool.map(a => a.id) };
}
