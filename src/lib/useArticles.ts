import { useEffect, useRef, useState } from 'react';
import type { Article } from '../types';
import { getPage } from './api';

export function useArticles(path: string, query: URLSearchParams, enabled: boolean, revision: number) {
  const key = path + '?' + query.toString();
  const [articles, setArticles] = useState<Article[]>([]), [total, setTotal] = useState(0), [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false), [error, setError] = useState('');
  const active = useRef(key), request = useRef<AbortController | null>(null);
  const cache = useRef(new Map<string, { articles: Article[]; total: number; page: number }>());
  useEffect(() => {
    request.current?.abort(); active.current = key;
    if (!enabled) { setLoading(false); return; }
    const saved = cache.current.get(key);
    setArticles(saved?.articles || []); setTotal(saved?.total || 0); setPage(saved?.page || 1); setError(''); setLoading(true);
    const controller = new AbortController(); request.current = controller;
    const params = new URLSearchParams(query.toString()); params.set('page', '1');
    const timer = window.setTimeout(() => { getPage(path, params, controller.signal).then(async first => {
      if (controller.signal.aborted) return;
      const pageCount = Math.min(saved?.page || 1, Math.max(1, Math.ceil(first.total / first.pageSize)));
      const rest = await Promise.all(Array.from({ length: pageCount - 1 }, (_, index) => {
        const next = new URLSearchParams(query.toString()); next.set('page', String(index + 2));
        return getPage(path, next, controller.signal);
      }));
      if (controller.signal.aborted) return;
      const next = [...new Map([first, ...rest].flatMap(result => result.articles).map(article => [article.id, article])).values()];
      setArticles(next); setTotal(first.total); setPage(pageCount);
      cache.current.set(key, { articles: next, total: first.total, page: pageCount });
    }).catch((e: unknown) => { if (!controller.signal.aborted) setError((e instanceof Error ? e.message : '资讯读取失败。') + (saved ? ' 当前显示上次加载的缓存。' : ' 可刷新重试。')); }).finally(() => { if (!controller.signal.aborted) setLoading(false); if (request.current === controller) request.current = null; });
    }, 180); return () => { clearTimeout(timer); controller.abort(); };
  }, [key, enabled, revision]);
  const loadMore = () => {
    if (!enabled || loading || request.current || articles.length >= total) return;
    const controller = new AbortController(); request.current = controller;
    const params = new URLSearchParams(query.toString()); params.set('page', String(page + 1));
    setLoading(true); setError('');
    getPage(path, params, controller.signal).then(result => {
      if (controller.signal.aborted || active.current !== key) return;
      const next = [...new Map([...articles, ...result.articles].map(a => [a.id, a])).values()];
      setArticles(next); setTotal(result.total); setPage(result.page);
      cache.current.set(key, { articles: next, total: result.total, page: result.page });
    }).catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '加载下一页失败，请重试。'); }).finally(() => { if (!controller.signal.aborted) setLoading(false); if (request.current === controller) request.current = null; });
  };
  return { articles, total, loading, error, hasMore: articles.length < total, loadMore };
}
