"""按公开来源补采历史，复用已核验正文，每个来源完成后更新网页快照。"""
import argparse
import json
from pathlib import Path

from crawler.collect import Client, Collector, Store as RawStore, canonical_url, now
from crawler.policy import publication_since, within_publication_window
from server.store import Store

ROOT = Path(__file__).resolve().parent.parent


def snapshot(store):
    articles = store.all()
    articles.sort(key=lambda item: (item.get('published_at') or '', item['id']), reverse=True)
    states = store.source_status()['sources']
    document = {'generated_at': now(), 'count': len(articles), 'published_since': publication_since(),
                'timezone': 'Asia/Shanghai', 'sources': {state['id']: state for state in states}, 'articles': articles}
    path = ROOT / 'public/data/articles.json'
    temporary = path.with_suffix('.backfill.tmp')
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return len(articles)


class CombinedStore:
    def __init__(self, raw, web):
        self.raw, self.web = raw, web

    def upsert_result(self, article):
        self.raw.upsert(article)
        return self.web.upsert_result(article)

    def set_status(self, source_id, result):
        self.raw.set_status(source_id, result)
        self.web.set_status(source_id, result)


class HistoryCollector(Collector):
    def __init__(self, client, store, raw, since):
        super().__init__(client, store, since)
        self.cache = {}
        self.cached, self.downloaded = 0, 0
        for row in raw.db.execute('SELECT payload FROM articles'):
            article = json.loads(row[0])
            if article.get('content') and article.get('account_name') and within_publication_window(article, since):
                for url in (article.get('source_urls') or []) + [article['source_url'], article['canonical_url']]:
                    self.cache[canonical_url(url)] = article

    def article(self, url, source_id, source, title='', published=None):
        cached = self.cache.get(canonical_url(url))
        if cached:
            self.cached += 1
            return json.loads(json.dumps(cached))
        article = super().article(url, source_id, source, title, published)
        self.downloaded += 1
        if self.downloaded % 10 == 0:
            print(json.dumps({'progress': source_id, 'newly_downloaded': self.downloaded, 'cached': self.cached}, ensure_ascii=False), flush=True)
        return article


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', default='ee,sports_site,voice,grad,ee_wechat,tsinghua_wechat,aishengquan,thu_su,sports,wechat')
    parser.add_argument('--limit', type=int, default=200)
    parser.add_argument('--info-start-page', type=int, default=1)
    parser.add_argument('--info-pages', type=int, default=20)
    parser.add_argument('--info-detail-limit', type=int, default=80)
    parser.add_argument('--info-batches', type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error('--limit 必须在 1–500 之间')
    if not 1 <= args.info_batches <= 10 or not 1 <= args.info_pages <= 20:
        parser.error('info 批数须为 1–10、每批页数须为 1–20')
    configs = json.loads((ROOT / 'crawler/sources.json').read_text())
    selected = list(dict.fromkeys(args.sources.split(',')))
    if any(key not in configs for key in selected):
        parser.error('存在未配置的信源')
    raw = RawStore(ROOT / 'crawler/data/articles.sqlite3')
    web = Store(ROOT / 'server/data/radar.sqlite3')
    job, reused = web.create_job('historical_backfill', selected, args.limit)
    if reused:
        print(json.dumps({'status': 'busy', 'message': '已有采集运行，请在完成后再补采。'}, ensure_ascii=False), flush=True)
        return
    states = []
    totals = dict(inserted=0, updated=0, unchanged=0, stale=0, fetched=0)
    try:
        collector = HistoryCollector(Client(interval=1.5), CombinedStore(raw, web), raw, publication_since())
        tasks = [(source, batch) for source in selected for batch in range(args.info_batches if source == 'info' else 1)]
        for source_id, batch in tasks:
            config = dict(configs[source_id])
            config.update(listing_pages=6, index_pages=12)
            if source_id == 'info':
                config.update(start_page=args.info_start_page + batch * args.info_pages, pages=args.info_pages, detail_limit=args.info_detail_limit, prioritize_action_details=True)
            print(json.dumps({'started': source_id, 'since': publication_since(), 'start_page': config.get('start_page')}, ensure_ascii=False), flush=True)
            result = collector.run(source_id, config, args.limit)
            states.append({'source_id': source_id, **result})
            for key in totals:
                totals[key] += result.get(key, 0)
            web.update_job(job['job_id'], sources=states, **totals)
            count = snapshot(web)
            raw.export(ROOT / 'crawler/data/articles.json')
            print(json.dumps({'source_id': source_id, **result, 'web_count': count}, ensure_ascii=False), flush=True)
            access_stopped = any(error.get('status') in ('auth_required', 'blocked', 'rate_limited') for error in result.get('errors', []))
            if source_id == 'info' and (access_stopped or not result.get('fetched')):
                break
        status = 'success' if all(state['status'] == 'success' for state in states) else 'partial'
        web.update_job(job['job_id'], status=status, sources=states, finished_at=now(), **totals)
        report = {'finished_at': now(), 'published_since': publication_since(), 'sources': states, 'totals': totals, 'web_count': snapshot(web)}
        report_path = ROOT / 'crawler/data/backfill-latest.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        history = ROOT / 'crawler/data/backfill-history.jsonl'
        with history.open('a') as handle:
            handle.write(json.dumps(report, ensure_ascii=False) + '\n')
        print(json.dumps({'finished_at': report['finished_at'], 'totals': totals, 'web_count': report['web_count']}, ensure_ascii=False), flush=True)
    except BaseException:
        web.update_job(job['job_id'], status='failed', finished_at=now(), error_message='历史补采中断，已保存数据保留。', **totals)
        snapshot(web)
        raise
    finally:
        raw.db.close()


if __name__ == '__main__':
    main()
