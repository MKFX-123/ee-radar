import { useState } from 'react';
import type { FormEvent } from 'react';
import { Info, Plus } from 'lucide-react';
import type { Article, Category } from '../types';
import { CATEGORIES } from '../data/demo';
import { categoryLabel, safeUrl } from '../lib/domain';
import { Modal } from './ui';
export default function ImportModal({ onClose, onImport, articles }: { onClose: () => void; onImport: (a: Article) => void; articles: Article[] }) {
  const [title, setTitle] = useState(''), [url, setUrl] = useState(''), [content, setContent] = useState(''), [category, setCategory] = useState<Category>('other'), [error, setError] = useState('');
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!title.trim() || !content.trim()) { setError('请填写标题和正文。当前版本不会自动抓取链接。'); return; }
    if (url && !safeUrl(url)) { setError('原文链接需要以 http:// 或 https:// 开头。'); return; }
    if (url && articles.some(a => safeUrl(a.sourceUrl) === safeUrl(url))) { setError('这个链接已存在，请在资讯列表中查找。'); return; }
    onImport({ id: 'local-' + crypto.randomUUID(), title: title.trim(), content: content.trim(), summary: content.trim().slice(0,150) + (content.trim().length > 150 ? '…' : ''), category, tags: [], sourceId: 'manual', sourceName: '手动录入', sourceUrl: safeUrl(url), publishedAt: '', fetchedAt: new Date().toISOString(), ingestion: 'manual', isOpportunity: false, audience: '未说明', events: [], deadlines: [], actions: [], registration: 'unknown', demo: false, summaryKind: 'excerpt' });
  }
  return <Modal title="添加资讯" onClose={onClose}><form className="import-form" onSubmit={submit}><p className="form-intro">粘贴一条值得关注的信息，先保存在当前浏览器。</p><div className="notice compact"><Info size={16} /><p>此表单用于手动录入。录入内容仅保存在此浏览器；不会推测发布时间、截止日期或报名状态。</p></div><label>标题<span className="required">*</span><input autoFocus required maxLength={180} placeholder="这条通知讲了什么？" value={title} onChange={e => setTitle(e.target.value)} /></label><label>原文链接<span className="optional">选填</span><input type="url" placeholder="https://…" value={url} onChange={e => setUrl(e.target.value)} /></label><label>信息类别<select value={category} onChange={e => setCategory(e.target.value as Category)}>{CATEGORIES.map(c => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label><label>正文<span className="required">*</span><textarea required rows={5} maxLength={20000} placeholder="粘贴通知正文，便于搜索与阅读…" value={content} onChange={e => setContent(e.target.value)} /></label>{error && <p className="form-error" role="alert">{error}</p>}<div className="modal-footer"><span>仅保存在此浏览器</span><button type="button" className="button secondary" onClick={onClose}>取消</button><button className="button primary" type="submit"><Plus size={16} />保存资讯</button></div></form></Modal>;
}
