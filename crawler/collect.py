"""EE Radar 独立公开信息采集器，不依赖 WeRSS 服务或管理页面。"""
import argparse
import hashlib
import html
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
if __package__:
    from .classification import assign_category
    from .policy import publication_since, within_publication_window
    from .wechat_index import discover as discover_wechat
else:
    from classification import assign_category
    from policy import publication_since, within_publication_window
    from wechat_index import discover as discover_wechat

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Shanghai')
ALLOWED_HOSTS = {'info.tsinghua.edu.cn', 'www.ee.tsinghua.edu.cn', 'www.thsports.tsinghua.edu.cn', 'www.tsinghua.edu.cn', 'mp.weixin.qq.com', 'pkuknow.cn'}
WECHAT_ACCOUNTS = {'清华体育': 'sports', '无限之声': 'voice', 'E研E行': 'grad', '清华大学电子工程系': 'ee_wechat', '清华大学': 'tsinghua_wechat', '艾生权': 'aishengquan', '清华大学学生会': 'thu_su'}
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'
INFO_BASE = 'https://info.tsinghua.edu.cn'
INFO_ENDPOINTS = {
    'combined': INFO_BASE + '/b/all/search/info/all/common/getXnzxAndHdrcPageList',
    'notices': INFO_BASE + '/b/xnzx/search/info/xxfb_fg/teacher/getMobilePageList',
}
INFO_DETAIL = INFO_BASE + '/b/info/xxfb_fg/xnzx/template/detail'


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


class FetchError(Exception):
    def __init__(self, message, status='failed'):
        super().__init__(message)
        self.status = status


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in ALLOWED_HOSTS or parts.username or parts.password or parts.port not in (None, 443):
        raise FetchError('链接不属于已配置的 HTTPS 公开来源')
    return url


def canonical_url(url):
    parts = urlsplit(html.unescape(url))
    query = parse_qs(parts.query)
    if parts.hostname == 'mp.weixin.qq.com' and all(query.get(k) for k in ('__biz', 'mid', 'idx')):
        return 'https://mp.weixin.qq.com/s?' + urlencode({k: query[k][0] for k in ('__biz', 'mid', 'idx')})
    ignored = {'from', 'scene', 'subscene', 'clicktime', 'enterid', 'sessionid', 'isappinstalled', 'chksm'}
    clean = [(k, v) for k in sorted(query) if not k.startswith('utm_') and k not in ignored for v in query[k]]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(clean), ''))


class Client:
    def __init__(self, interval=1.5):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT, 'Accept-Language': 'zh-CN,zh;q=0.9'})
        self.interval = max(1.0, interval)
        self.last_request = 0.0

    def request(self, method, url, **kwargs):
        for _ in range(5):
            validate_url(url)
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.request(method, url, timeout=(8, 25), allow_redirects=False, **kwargs)
            except requests.RequestException as exc:
                raise FetchError('网络请求失败：' + type(exc).__name__) from exc
            if response.is_redirect:
                url = urljoin(response.url, response.headers['Location'])
                if urlsplit(url).hostname not in ALLOWED_HOSTS:
                    raise FetchError('跳转至登录页或未配置来源，未继续请求', 'auth_required')
                method, kwargs = 'GET', {}
                continue
            if response.status_code in (401, 403):
                raise FetchError('来源拒绝访问或需要登录', 'auth_required')
            if response.status_code == 429:
                raise FetchError('来源限制请求频率，本轮停止该请求', 'rate_limited')
            if response.status_code != 200:
                raise FetchError('HTTP ' + str(response.status_code))
            response.encoding = 'utf-8'
            return response
        raise FetchError('页面跳转次数过多')


def text_of(element):
    if element is None:
        return ''
    for tag in element.select('script, style, noscript'):
        tag.decompose()
    return '\n'.join(line.strip() for line in element.get_text('\n', strip=True).splitlines() if line.strip())


def meta(soup, key):
    tag = soup.find('meta', attrs={'property': key}) or soup.find('meta', attrs={'name': key})
    return html.unescape(tag.get('content', '')).strip() if tag else ''


def script_value(raw, key):
    pattern = r'\b(?:var\s+)?' + re.escape(key) + r'\s*=\s*([\x22\x27])(.*?)\1'
    match = re.search(pattern, raw, re.S)
    return html.unescape(match.group(2)).replace('\\x26', '&').replace('\\/', '/') if match else ''


def date_value(value, strict=False):
    if not value:
        return None
    value = str(value).strip()
    if re.search(r'\b(?:null|undefined)\b', value, re.I):
        return None
    if re.fullmatch(r'\d{10}|\d{13}', value):
        try:
            return datetime.fromtimestamp(int(value) / (1000 if len(value) == 13 else 1), TZ).isoformat(timespec='seconds')
        except (ValueError, OverflowError, OSError):
            return None
    # ISO 输入保留时分与时区，不把带 T 的时间截成日期。
    iso_pattern = r'\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})?'
    iso_match = re.fullmatch(iso_pattern, value) if strict else re.search(iso_pattern, value)
    if iso_match:
        try:
            parsed = datetime.fromisoformat(iso_match.group().replace('Z', '+00:00').replace('z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=TZ)
            return parsed.astimezone(TZ).isoformat(timespec='seconds')
        except ValueError:
            return None
    pattern = r'(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})(?:日)?(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?'
    match = re.fullmatch(pattern, value) if strict else re.search(pattern, value)
    if not match:
        return None
    parts = [int(x) if x else 0 for x in match.groups()]
    try:
        parsed = datetime(*parts, tzinfo=TZ)
        return parsed.isoformat(timespec='seconds') if match.group(4) else parsed.date().isoformat()
    except ValueError:
        return None


def record(url, source_id, source, title, content='', published_at=None, **extra):
    canonical = canonical_url(url)
    return assign_category({
        'id': hashlib.sha256(canonical.encode()).hexdigest()[:24],
        'source_id': source_id, 'source': source, 'source_url': url,
        'canonical_url': canonical, 'title': html.unescape(title).strip(),
        'content': content, 'summary': None, 'category': None,
        'published_at': published_at, 'fetched_at': now(),
        'content_status': 'web_text' if content else 'metadata_only',
        'content_fetched_at': now() if content else None,
        'attachments': [], 'attachments_status': 'not_checked',
        'event_start_at': None, 'event_end_at': None, 'deadline_at': None, 'location': None,
        'target_audience': [], 'action_items': [], 'ai_status': 'pending',
        'discovered_by': [{'source_id': source_id, 'source': source, 'url': url}],
        **extra,
    })


def attachment_links(soup, base_url):
    attachments, seen = [], set()
    for anchor in soup.select('a[href]'):
        href = urljoin(base_url, html.unescape(anchor['href']))
        name = anchor.get_text(' ', strip=True)
        if not re.search(r'\.(?:pdf|docx?|xlsx?|pptx?|zip|rar)(?:$|[?#])', href, re.I) and not re.search(r'\.(?:pdf|docx?|xlsx?|pptx?|zip|rar)$', name, re.I):
            continue
        if urlsplit(href).scheme not in ('https', 'http') or href in seen:
            continue
        seen.add(href)
        attachments.append({'name': name or Path(urlsplit(href).path).name, 'url': href, 'content_status': 'not_fetched'})
    return attachments


def parse_article(raw, url, source_id, source, fallback_title='', fallback_date=None):
    soup = BeautifulSoup(raw, 'html.parser')
    wechat = urlsplit(url).hostname == 'mp.weixin.qq.com'
    body = soup.select_one('#js_content' if wechat else '.v_news_content')
    if not body:
        page_text = soup.get_text(' ', strip=True)
        if any(x in page_text for x in ('环境异常', '完成验证', '访问过于频繁')):
            raise FetchError('原文返回验证页，未采集正文', 'blocked')
        if any(x in page_text for x in ('内容已被发布者删除', '该内容暂时无法查看')):
            raise FetchError('原文已删除或暂时不可见', 'unavailable')
        raise FetchError('未找到正文容器', 'content_missing')
    title_el = soup.select_one('#activity-name' if wechat else 'h1, .news-title, .article-title')
    title = meta(soup, 'og:title') or text_of(title_el) or fallback_title
    if not title and soup.title:
        title = soup.title.get_text(strip=True).removesuffix('-清华大学电子工程系').removesuffix('-清华大学体育部')
    content = text_of(body)
    if not title or not content:
        raise FetchError('标题或正文为空', 'content_missing')
    attachments = attachment_links(soup, url)
    extras = {
        'attachments': attachments,
        'attachments_status': 'not_fetched' if attachments else 'none_detected',
        'content_status': 'web_text_with_attachments' if attachments else 'web_text',
        'coverage': '已提取网页文字；附件、图片文字及完整历史未采集',
    }
    published = fallback_date
    if wechat:
        source = text_of(soup.select_one('#js_name')) or meta(soup, 'og:article:author')
        if not source:
            raise FetchError('原文未提供可核验的公众号名称，未写入该来源', 'source_unknown')
        published = date_value(script_value(raw, 'ct')) or date_value(text_of(soup.select_one('#publish_time')))
        extras['account_name'] = source
        # 官网转载公众号链接时，来源归属仍以实际公众号为准。
        if source in WECHAT_ACCOUNTS:
            source_id = WECHAT_ACCOUNTS[source]
        elif source_id in ('info', 'ee', 'sports_site'):
            source_id = 'wechat'
        extras['account_biz'] = script_value(raw, 'biz') or parse_qs(urlsplit(url).query).get('__biz', [''])[0] or None
        query = parse_qs(urlsplit(url).query)
        mid = script_value(raw, 'mid') or query.get('mid', [''])[0]
        idx = script_value(raw, 'idx') or query.get('idx', [''])[0]
        if extras['account_biz'] and mid.isdigit() and idx.isdigit():
            extras['external_id'] = ':'.join((extras['account_biz'], mid, idx))
        permanent = script_value(raw, 'msg_link')
        if permanent.startswith('https://mp.weixin.qq.com/s'):
            url = permanent
        extras['discovered_url'] = url
    else:
        published = published or date_value(meta(soup, 'PubDate')) or date_value(text_of(soup.select_one('.news-info, .article-info, .info, .date')))
        website_id = re.search(r'/info/\d+/(\d+)\.htm$', urlsplit(url).path)
        if website_id:
            extras['external_id'] = website_id.group(1)
    return record(url, source_id, source, title, content, published, **extras)


def unique_values(values):
    result, seen = [], set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def merge_articles(old, new):
    if (new.get('fetched_at') or '') < (old.get('fetched_at') or ''):
        return dict(old)
    merged = {**old, **new}
    merged['id'] = old['id']
    merged['first_seen_at'] = old.get('first_seen_at', old['fetched_at'])
    for key in ('discovered_by', 'source_endpoints', 'source_record_ids', 'source_categories', 'source_urls'):
        merged[key] = unique_values((old.get(key) or []) + (new.get(key) or []))
    ranks = {'metadata_only': 0, 'list_excerpt': 1, 'event_description': 1, 'full_text': 2, 'web_text': 2, 'web_text_with_attachments': 2}
    if not new.get('content') or ranks.get(old.get('content_status'), 0) > ranks.get(new.get('content_status'), 0):
        for key in ('content', 'content_status', 'content_fetched_at', 'attachments', 'attachments_status', 'coverage'):
            if key in old:
                merged[key] = old[key]
    for key in ('summary', 'category', 'event_start_at', 'event_end_at', 'deadline_at', 'location', 'published_at', 'external_id', 'content_preview', 'source_excerpt'):
        merged[key] = new.get(key) or old.get(key)
    for key in ('target_audience', 'action_items'):
        merged[key] = new.get(key) or old.get(key) or []
    if new.get('ai_status') == 'pending' and old.get('ai_status'):
        merged['ai_status'] = old['ai_status']
    return merged


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, canonical_url TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS article_keys (source_id TEXT NOT NULL, external_id TEXT NOT NULL, article_id TEXT NOT NULL, PRIMARY KEY(source_id, external_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS article_urls (canonical_url TEXT PRIMARY KEY, article_id TEXT NOT NULL)')
        # 索引与原表并存，兼容已有数据库和 JSON 快照。
        for article_id, payload in self.db.execute('SELECT id,payload FROM articles'):
            article = json.loads(payload)
            external_id = article.get('external_id') or article.get('source_record_id')
            if external_id and article.get('source_id'):
                self.db.execute('INSERT OR IGNORE INTO article_keys VALUES (?,?,?)', (article['source_id'], str(external_id), article_id))
            for url in unique_values((article.get('source_urls') or []) + [article['canonical_url']]):
                self.db.execute('INSERT OR IGNORE INTO article_urls VALUES (?,?)', (canonical_url(url), article_id))
        self.db.commit()

    def upsert(self, article):
        article = dict(article)
        external_id = article.get('external_id') or article.get('source_record_id')
        if external_id:
            article['external_id'] = str(external_id)
        keyed = self.db.execute('SELECT article_id FROM article_keys WHERE source_id=? AND external_id=?', (article['source_id'], str(external_id))).fetchone() if external_id else None
        linked = self.db.execute('SELECT article_id FROM article_urls WHERE canonical_url=?', (article['canonical_url'],)).fetchone()
        existing = self.db.execute('SELECT payload FROM articles WHERE canonical_url=? OR id=? OR id=?', (article['canonical_url'], keyed[0] if keyed else '', linked[0] if linked else '')).fetchall()
        if existing:
            # 旧快照既不能覆盖新正文，也不能作为合并依据删除较新的记录。
            if any((article.get('fetched_at') or '') < (json.loads(row[0]).get('fetched_at') or '') for row in existing):
                return False
            old = json.loads(existing[0][0])
            for duplicate in existing[1:]:
                duplicate = json.loads(duplicate[0])
                old = merge_articles(old, duplicate)
                self.db.execute('UPDATE article_keys SET article_id=? WHERE article_id=?', (old['id'], duplicate['id']))
                self.db.execute('UPDATE article_urls SET article_id=? WHERE article_id=?', (old['id'], duplicate['id']))
                self.db.execute('DELETE FROM articles WHERE id=?', (duplicate['id'],))
            article = merge_articles(old, article)
        else:
            article['first_seen_at'] = article['fetched_at']
        article['source_urls'] = unique_values((article.get('source_urls') or []) + [article['source_url']])
        article = assign_category(article)
        self.db.execute('INSERT INTO articles (id,canonical_url,payload) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET canonical_url=excluded.canonical_url,payload=excluded.payload', (article['id'], article['canonical_url'], json.dumps(article, ensure_ascii=False)))
        if external_id:
            self.db.execute('INSERT INTO article_keys VALUES (?,?,?) ON CONFLICT(source_id,external_id) DO UPDATE SET article_id=excluded.article_id', (article['source_id'], str(external_id), article['id']))
        for url in article['source_urls']:
            self.db.execute('INSERT INTO article_urls VALUES (?,?) ON CONFLICT(canonical_url) DO UPDATE SET article_id=excluded.article_id', (canonical_url(url), article['id']))
        self.db.commit()
        return not bool(existing)

    def set_status(self, source_id, result):
        existing = self.db.execute('SELECT payload FROM sources WHERE id=?', (source_id,)).fetchone()
        old = json.loads(existing[0]) if existing else {}
        result['last_attempt_at'] = result.get('last_attempt_at') or now()
        result['last_success_at'] = result['last_attempt_at'] if result['status'] == 'success' else old.get('last_success_at')
        result['error_message'] = '；'.join(error.get('message', '') for error in result.get('errors', [])) or None
        result['last_finished_at'] = now()
        self.db.execute('INSERT INTO sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (source_id, json.dumps(result, ensure_ascii=False)))
        self.db.commit()

    def export(self, path, since=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        articles = [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM articles ORDER BY id')]
        since = since or publication_since()
        articles = [assign_category(article) for article in articles if within_publication_window(article, since)]
        articles.sort(key=lambda x: (x.get('published_at') or '', x['id']), reverse=True)
        states = {key: json.loads(payload) for key, payload in self.db.execute('SELECT id,payload FROM sources')}
        document = {'generated_at': now(), 'count': len(articles), 'published_since': since, 'timezone': 'Asia/Shanghai', 'sources': states, 'articles': articles}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
        return document


class Collector:
    def __init__(self, client, store, since=None):
        self.client, self.store = client, store
        self.since = since or publication_since()
        self.info_errors = []
        self.info_pages = 0

    def article(self, url, source_id, source, title='', published=None):
        response = self.client.request('GET', url)
        return parse_article(response.text, response.url, source_id, source, title, published)

    def website(self, config, limit):
        links, seen = [], set()
        queue = [(url, 1) for url in (config.get('listing_urls') or [config['url']])]
        visited = set()
        page_limit = max(1, min(20, int(config.get('listing_pages', 1))))
        while queue and len(links) < limit:
            listing, page_number = queue.pop(0)
            if listing in visited:
                continue
            visited.add(listing)
            response = self.client.request('GET', listing)
            soup = BeautifulSoup(response.text, 'html.parser')
            page_dates = []
            for anchor in soup.select(config['selector']):
                url = urljoin(response.url, anchor.get('href', ''))
                host = urlsplit(url).hostname
                if config.get('kind') == 'wechat' and host != 'mp.weixin.qq.com':
                    continue
                if not (re.search(r'/info/\d+/\d+\.htm$', urlsplit(url).path) or host == 'mp.weixin.qq.com'):
                    continue
                title = anchor.get_text(' ', strip=True)
                if not title or title in ('联系我们', '系主任信箱') or url in seen:
                    continue
                seen.add(url)
                parent = anchor.find_parent('li')
                date_text = text_of(parent.select_one('.date, .tz-list-date')) if parent else ''
                date_text = re.sub(r'(20\d{2}-\d{1,2})\s+(\d{1,2})(?!\d)', r'\1-\2', date_text)
                published = date_value(date_text)
                page_dates.append(published)
                if published and not within_publication_window({'published_at': published}, self.since):
                    continue
                links.append((url, title, published))
            too_old = page_dates and all(day and day[:10] < self.since for day in page_dates)
            if page_number < page_limit and not too_old:
                next_link = next((a for a in soup.select('a[href]') if re.sub(r'\s+', '', a.get_text()) in ('下页', '下一页')), None)
                if next_link:
                    next_url = urljoin(response.url, next_link['href'])
                    if urlsplit(next_url).hostname == urlsplit(listing).hostname and next_url not in visited:
                        queue.append((next_url, page_number + 1))
        if not links:
            raise FetchError('列表未找到文章链接', 'empty_list')
        return links[:limit]

    def info(self, config, limit):
        self.info_errors, self.info_pages = [], 0
        page = self.client.request('GET', config['url'])
        token = next((c.value for c in self.client.session.cookies if c.name == 'XSRF-TOKEN' and c.domain.endswith('tsinghua.edu.cn')), None)
        if not token:
            raise FetchError('公开检索未返回 CSRF 令牌', 'auth_required')
        headers = {'Referer': page.url, 'X-Requested-With': 'XMLHttpRequest'}
        try:
            pages = max(1, min(20, int(config.get('pages', 3))))
            start_page = max(1, min(200, int(config.get('start_page', 1))))
        except (ValueError, TypeError) as exc:
            raise FetchError('info pages 必须为页数整数', 'invalid_config') from exc
        rows, seen_ids, seen_urls = [], set(), set()
        for number in range(start_page, start_page + pages):
            params = {'params': {}, 'filterParams': {}, 'orderMap': {'sort': 'time'}, 'departmentSearchFlag': False, 'dwnm': '', 'currentPage': number}
            try:
                response = self.client.request('POST', INFO_ENDPOINTS['notices'], params={'_csrf': unquote(token)}, data={'esParamClass': json.dumps(params)}, headers=headers)
                payload = response.json()
                if not isinstance(payload, dict) or payload.get('result') != 'success':
                    raise FetchError('公开检索接口未返回成功状态', 'auth_required')
                obj = payload.get('object') or {}
                if not isinstance(obj, dict):
                    raise FetchError('公开检索结果结构变化', 'invalid_response')
                current = obj.get('resultsList') or []
                if not isinstance(current, list):
                    raise FetchError('公开检索结果结构变化', 'invalid_response')
                self.info_pages += 1
                for row in current:
                    if not isinstance(row, dict) or not row.get('bt') or not (row.get('url') or row.get('zxurl')):
                        continue
                    published = date_value(row.get('fbsj') or row.get('time'))
                    if not within_publication_window({'published_at': published}, self.since):
                        continue
                    url = urljoin(INFO_BASE, row.get('zxurl') or row.get('url'))
                    canonical = canonical_url(url)
                    external_id = parse_qs(urlsplit(url).query).get('xxid', [str(row.get('xxid') or '')])[0]
                    if canonical in seen_urls or (external_id and external_id in seen_ids):
                        continue
                    seen_urls.add(canonical)
                    if external_id:
                        seen_ids.add(external_id)
                    rows.append((row, url))
                dated = [date_value(row.get('fbsj') or row.get('time')) for row in current if isinstance(row, dict)]
                if not current or (dated and all(value and value[:10] < self.since for value in dated)) or len(rows) >= limit:
                    break
            except (ValueError, TypeError):
                self.info_errors.append({'status': 'invalid_response', 'message': '公开检索接口未返回有效 JSON 结构', 'page': number})
                break
            except FetchError as exc:
                self.info_errors.append({'status': exc.status, 'message': str(exc), 'page': number})
                break
        # 按发布时间排序与原文链接去重；限量采集不声称覆盖完整历史。
        rows.sort(key=lambda item: date_value(item[0].get('fbsj') or item[0].get('time')) or '', reverse=True)
        result, stop_details = [], bool(self.info_errors)
        detail_count = 0
        detail_limit = max(0, int(config.get('detail_limit', limit)))
        detail_urls = None
        if config.get('prioritize_action_details'):
            priority = sorted(rows[:limit], key=lambda entry: not bool(re.search(r'报名|申请|招募|征稿|招聘|招生|讲座|竞赛|奖学金|交流项目|申报', entry[0].get('bt') or '')))
            detail_urls = {url for _, url in priority[:detail_limit]}
        for row, url in rows[:limit]:
            preview = BeautifulSoup(html.unescape(row.get('nr') or ''), 'html.parser').get_text(' ', strip=True)
            external_id = parse_qs(urlsplit(url).query).get('xxid', [str(row.get('xxid') or '')])[0] or None
            article = record(url, 'info', config['name'], row['bt'], published_at=date_value(row.get('fbsj') or row.get('time')), external_id=external_id, source_record_id=external_id, publisher=row.get('dwmc'), source_category=row.get('lmmc'), source_excerpt=preview or None, content_preview=preview or None, coverage='公开列表摘录；正文未取得，未覆盖完整历史')
            if config.get('fetch_details', True) and not stop_details and detail_count < detail_limit and (detail_urls is None or url in detail_urls) and external_id and urlsplit(url).hostname == 'info.tsinghua.edu.cn':
                detail_count += 1
                try:
                    response = self.client.request('POST', INFO_DETAIL, params={'xxid': external_id, '_csrf': unquote(token)}, headers={**headers, 'Referer': url})
                    detail = response.json()
                    if not isinstance(detail, dict) or detail.get('result') != 'success':
                        raise FetchError('公开详情接口未返回成功状态', 'auth_required')
                    obj = detail.get('object') or {}
                    if not isinstance(obj, dict):
                        raise FetchError('公开详情结果结构变化', 'invalid_response')
                    dto = obj.get('xxDto') or {}
                    if not isinstance(dto, dict):
                        raise FetchError('公开详情结果结构变化', 'invalid_response')
                    body = BeautifulSoup(html.unescape(dto.get('nr_show') or dto.get('nr') or ''), 'html.parser')
                    content = text_of(body)
                    if not content:
                        raise FetchError('公开详情没有可用正文', 'content_missing')
                    attachments = attachment_links(body, url)
                    for attachment in dto.get('fjs_template') or []:
                        if isinstance(attachment, dict):
                            attachments.append({'name': attachment.get('wjmc') or '原文附件', 'id': attachment.get('fjid'), 'file_id': attachment.get('wjid'), 'content_status': 'not_fetched'})
                    article.update(content=content, content_status='web_text_with_attachments' if attachments else 'web_text', content_fetched_at=now(), attachments=attachments, attachments_status='not_fetched' if attachments else 'none_detected', coverage='已取得公开详情网页文字；附件、图片文字及完整历史未采集')
                    article['published_at'] = date_value(dto.get('fbsj')) or article['published_at']
                except (ValueError, TypeError):
                    self.info_errors.append({'url': url, 'status': 'invalid_response', 'message': '公开详情未返回有效 JSON 结构'})
                except FetchError as exc:
                    self.info_errors.append({'url': url, 'status': exc.status, 'message': str(exc)})
                    if exc.status in ('auth_required', 'blocked', 'rate_limited'):
                        stop_details = True
            result.append(article)
        return result

    def run(self, source_id, config, limit):
        result = {'name': config['name'], 'status': 'success', 'fetched': 0, 'inserted': 0, 'updated': 0, 'unchanged': 0, 'stale': 0, 'skipped_before_since': 0, 'skipped_unknown_date': 0, 'published_since': self.since, 'errors': [], 'last_attempt_at': now()}
        def save(article):
            if not within_publication_window(article, self.since):
                result['skipped_unknown_date' if not article.get('published_at') else 'skipped_before_since'] += 1
                return
            # 后端数据库返回真实变更；独立导出工具仍兼容原来的布尔接口。
            if hasattr(self.store, 'upsert_result'):
                change = self.store.upsert_result(article)['change']
                result[change] += 1
            else:
                result['inserted' if self.store.upsert(article) else 'updated'] += 1
            result['fetched'] += 1
        try:
            if config['kind'] == 'info':
                articles = self.info(config, limit)
                result['errors'].extend(self.info_errors)
                result['pages_fetched'] = self.info_pages
                result['coverage'] = '公开检索从第 ' + str(config.get('start_page', 1)) + ' 页起读取 ' + str(self.info_pages) + ' 页；不覆盖完整历史'
                for article in articles:
                    save(article)
            else:
                if config['kind'] == 'wechat':
                    links = [(u, '', None) for u in config.get('urls', [])]
                    if config.get('public_index_source'):
                        try:
                            indexed = discover_wechat(self.client, config, self.since, limit)
                            known = {url for url, _, _ in indexed}
                            links = indexed + [item for item in links if item[0] not in known]
                            result['index_candidates'] = len(indexed)
                            result['coverage'] = '从清华知道公开索引发现链接，逐篇核验微信原文账号与发布日期；不保证完整历史'
                        except FetchError as exc:
                            result['errors'].append({'status': exc.status, 'message': '公开索引：' + str(exc)})
                        except (ValueError, TypeError, KeyError):
                            result['errors'].append({'status': 'invalid_response', 'message': '公开索引格式异常，继续读取已有文章链接'})
                    if config.get('discovery_url'):
                        try:
                            discovered = self.website({**config, 'url': config['discovery_url'], 'selector': 'a[href]'}, limit)
                            known = {url for url, _, _ in links}
                            links.extend(item for item in discovered if item[0] not in known)
                        except FetchError as exc:
                            result['errors'].append({'status': exc.status, 'message': str(exc)})
                    links = links[:limit]
                else:
                    links = self.website(config, limit)
                if not links:
                    raise FetchError('尚未配置经核实的公开文章链接', 'not_configured')
                for url, title, published in links:
                    try:
                        article = self.article(url, source_id, config['name'], title, published)
                        if config['kind'] == 'website':
                            article['discovered_by'].append({'source_id': source_id, 'source': config['name'], 'url': config['url']})
                        if config.get('expected_account') and article.get('account_name') != config['expected_account']:
                            raise FetchError('原文公众号名称与配置不一致，未写入该来源', 'source_mismatch')
                        if config.get('public_index_source'):
                            article['discovered_by'].append({'source_id': 'thu_know', 'source': '清华知道公开索引（链接发现）', 'url': 'https://pkuknow.cn/thu?source=' + config['public_index_source'] + '&group=wechat'})
                        save(article)
                    except FetchError as exc:
                        result['errors'].append({'url': url, 'status': exc.status, 'message': str(exc)})
                        if title and config['kind'] != 'wechat':
                            article = record(url, source_id, config['name'], title, published_at=published, content_error=str(exc), coverage='列表已发现文章，但正文尚未取得')
                            save(article)
                        if exc.status in ('auth_required', 'blocked', 'rate_limited'):
                            break
        except FetchError as exc:
            result['errors'].append({'status': exc.status, 'message': str(exc)})
        if result['errors']:
            result['status'] = 'partial' if result['fetched'] else result['errors'][0]['status']
        self.store.set_status(source_id, result)
        return result


def main():
    parser = argparse.ArgumentParser(description='直接采集清华公开信息，无需 WeRSS 管理页面')
    parser.add_argument('--source', default='all', help='all 或 sources.json 中的来源标识')
    parser.add_argument('--since', default=publication_since(), help='只采集此日期起发布的内容，YYYY-MM-DD')
    parser.add_argument('--url', help='导入单篇已知公开文章链接')
    parser.add_argument('--limit', type=int, default=20, help='每个来源最多处理的条数，范围 1–100')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--web-output', type=Path, default=ROOT.parent / 'public' / 'data' / 'articles.json', help='同步到网页读取的本地 JSON 文件')
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('--limit 必须在 1–100 之间')
    if date_value(args.since, strict=True) != args.since or len(args.since) != 10:
        parser.error('--since 必须是有效的 YYYY-MM-DD 日期')
    store = Store(args.data_dir / 'articles.sqlite3')
    collector = Collector(Client(), store, since=args.since)
    sources = json.loads((ROOT / 'sources.json').read_text())
    if not args.url and args.source != 'all' and args.source not in sources:
        parser.error('--source 不在 sources.json 中')
    if args.url:
        validate_url(args.url)
        sources = {'manual': {'name': '单篇链接导入', 'kind': 'wechat', 'urls': [args.url]}}
    states = []
    for source_id, config in sources.items():
        if not args.url and (not config.get('enabled') or args.source not in ('all', source_id)):
            continue
        result = collector.run(source_id, config, args.limit)
        states.append(result)
        print(json.dumps({'source_id': source_id, **result}, ensure_ascii=False), flush=True)
    document = store.export(args.data_dir / 'articles.json', since=args.since)
    args.web_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.web_output.with_suffix('.tmp')
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(args.web_output)
    print(json.dumps({'total_stored': document['count'], 'output': str(args.data_dir / 'articles.json')}, ensure_ascii=False))
    print(json.dumps({'web_output': str(args.web_output)}, ensure_ascii=False))
    store.db.close()
    if any(x['status'] != 'success' for x in states):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
