"""标准化已采集文字；规则结果不冒充 AI，不补造缺失事实。"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from crawler.collect import canonical_url
from crawler.classification import CATEGORIES, CATEGORY_TERMS, RULE_VERSION, classify
from .evidence import extract, EXTRACTION_VERSION

TZ = ZoneInfo('Asia/Shanghai')
DATE_RE = re.compile(r'(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})(?:日)?(?:[ T]*(?:(上午|下午|晚上)\s*)?(\d{1,2})[:：](\d{2})(?::(\d{2}))?)?')


def now() -> str:
    return datetime.now(TZ).isoformat(timespec='seconds')


def clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    if re.search(r'<[a-zA-Z/][^>]*>', value):
        soup = BeautifulSoup(value, 'html.parser')
        for node in soup.select('script,style,noscript,iframe'):
            node.decompose()
        value = soup.get_text('\n', strip=True)
    return value.strip()


def iso_value(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value).isoformat()
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            result = result.replace(tzinfo=TZ)
        return result.astimezone(TZ).isoformat(timespec='seconds')
    except ValueError:
        return None


def moment(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return result.replace(tzinfo=TZ) if result.tzinfo is None else result.astimezone(TZ)


def explicit_dates(text: str) -> list[tuple[str, str]]:
    result = []
    for match in DATE_RE.finditer(text):
        year, month, day, period, hour, minute, second = match.groups()
        try:
            if hour is None:
                value = date(int(year), int(month), int(day)).isoformat()
                result.append((value, 'date'))
            else:
                h = int(hour)
                if period in ('下午', '晚上') and h < 12:
                    h += 12
                value = datetime(int(year), int(month), int(day), h, int(minute), int(second or 0), tzinfo=TZ).isoformat(timespec='seconds')
                result.append((value, 'datetime'))
        except ValueError:
            continue
    return result


def strings(value: Any) -> list[str]:
    return [clean_text(x) for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


def explicit_audience(title: str, text: str) -> list[str]:
    lines = re.split(r'[\n。；]', text)
    result = []
    for audience in ('本科生', '研究生', '教职工'):
        title_support = audience in title and bool(re.search(r'奖学金|招生|报名|招募|申请|资助', title))
        explicit = any(re.search(r'(?:面向|对象[：:为]?|申请人|资格|招募|在校).{0,20}' + audience + r'|' + audience + r'.{0,8}(?:可申请|可报名|均可|参加)', line) for line in lines)
        if title_support or explicit:
            result.append(audience)
    return result


def rule_events(item_id: str, text: str) -> list[dict]:
    events = []
    for line in re.split(r'[\n。；]', text):
        line = line.strip()
        dates = explicit_dates(line)
        if len(dates) != 1 or len(line) > 220:
            continue
        deadline = bool(re.search(r'截止|截至|最迟', line) and re.search(r'报名|申请|申报|提交|材料|报送|登记|缴费|上传|投递|招募', line))
        activity = bool(re.search(r'(?:活动|会议|讲座|报告|开幕|举办|召开|开展)(?:时间)?|举行', line)) and not bool(re.search(r'截至|截止', line))
        if not deadline and not activity:
            continue
        start, precision = dates[0]
        kind = 'deadline' if deadline else 'event'
        events.append({'id': hashlib.sha256(f'{item_id}:{kind}:{start}:{line}'.encode()).hexdigest()[:20], 'item_id': item_id, 'event_type': kind, 'title': '截止事项' if deadline else '活动时间', 'start_at': start, 'end_at': None, 'precision': precision, 'location': None, 'source_excerpt': line})
    return events


def normalize(raw: dict) -> dict:
    url = clean_text(raw.get('source_url'))
    parts = urlsplit(url)
    if parts.scheme not in ('https', 'http') or not parts.hostname:
        raise ValueError('资讯缺少有效原文 URL')
    canonical = canonical_url(url)
    source_id = str(raw.get('source_id') or raw.get('source') or 'unknown')
    if source_id == 'sports' and parts.hostname != 'mp.weixin.qq.com':
        source_id = 'sports_site'
    source = 'info' if source_id == 'info' else 'wechat' if parts.hostname == 'mp.weixin.qq.com' or source_id == 'wechat' else 'ee_website'
    account_name = clean_text(raw.get('account_name') or raw.get('source_name') or raw.get('source'))
    if source == 'wechat':
        source_id = {'清华体育': 'sports', '无限之声': 'voice', 'E研E行': 'grad'}.get(account_name, source_id)
    item_id = str(raw.get('id') or hashlib.sha256(canonical.encode()).hexdigest()[:24])
    title = clean_text(raw.get('title'))
    if not title:
        raise ValueError('资讯标题为空')
    content = clean_text(raw.get('content'))
    excerpt = clean_text(raw.get('source_excerpt') or raw.get('content_preview'))
    source_name = clean_text(raw.get('account_name') or raw.get('source_name') or raw.get('source') or source_id)
    external_id = raw.get('external_id') or raw.get('source_record_id')
    if source == 'info' and not external_id:
        external_id = parse_qs(parts.query).get('xxid', [None])[0]
    category = raw.get('category') if raw.get('category') in CATEGORIES else classify(title, clean_text(raw.get('source_category')), content or excerpt)
    audience = explicit_audience(title, content or excerpt)
    actions = []
    for line in (content or excerpt).splitlines():
        line = line.strip()
        if 3 <= len(line) <= 180 and re.search(r'报名|申请|提交|填写|扫码|招募', line):
            actions.append(line)
    actions = list(dict.fromkeys(actions))[:6]
    extracted = extract(item_id, title, content or excerpt, iso_value(raw.get('published_at')))
    events = extracted['events']
    return {
        'id': item_id, 'source': source, 'source_id': source_id, 'source_name': source_name,
        'external_id': str(external_id) if external_id is not None else None,
        'source_url': url, 'canonical_url': canonical, 'publisher': clean_text(raw.get('publisher')) or None,
        'source_category': clean_text(raw.get('source_category')) or None, 'title': title,
        'content': content, 'content_preview': excerpt or None,
        'summary': (content or excerpt)[:240] or None, 'summary_kind': 'excerpt',
        'category': category, 'category_method': 'rule', 'rule_version': RULE_VERSION, 'subcategory': None, 'tags': [],
        'audience': audience, 'target_audience': audience,
        'action_items': actions, 'action_evidence': [{'text': x, 'source_excerpt': x} for x in actions],
        'materials': [], 'material_evidence': [], 'events': events,
        'published_at': iso_value(raw.get('published_at')), 'fetched_at': iso_value(raw.get('fetched_at')) or now(),
        'processed_at': None, 'processing_status': 'pending', 'processing_error': None,
        'content_status': raw.get('content_status') or ('web_text' if content else 'metadata_only'),
        'coverage': clean_text(raw.get('coverage')) or None,
        'attachments': raw.get('attachments') if isinstance(raw.get('attachments'), list) else [],
        'attachments_status': raw.get('attachments_status') or 'not_checked',
        'data_kind': 'real',
        'discovered_by': raw.get('discovered_by') if isinstance(raw.get('discovered_by'), list) else [],
        **extracted,
    }


def registration_status(item: dict, at: datetime | None = None) -> str:
    at = at or datetime.now(TZ)
    if not item.get('is_opportunity'):
        return 'unknown'
    start = item.get('registration_start_at')
    if start and moment(start) > at:
        return 'upcoming'
    deadlines = [e for e in item.get('events', []) if e['event_type'] == 'deadline']
    if not deadlines:
        activities = [e for e in item.get('events', []) if e['event_type'] == 'event']
        if activities and all((moment(e.get('end_at') or e['start_at']) <= at if e.get('end_at') or e['precision'] == 'datetime' else e['start_at'] < at.date().isoformat()) for e in activities):
            return 'closed'
        return 'unknown'
    future, ambiguous, past = False, False, False
    for event in deadlines:
        value = event['start_at']
        if event['precision'] == 'date':
            day = date.fromisoformat(value)
            future |= day > at.date()
            ambiguous |= day == at.date()
            past |= day < at.date()
        else:
            future |= moment(value) > at
            past |= moment(value) <= at
    if past:
        return 'closed'
    if ambiguous:
        return 'unknown'
    if future and item.get('is_opportunity'):
        if item.get('registration_limited'):
            return 'unknown'
        return 'active'
    return 'unknown'


def item_status(item: dict, at: datetime | None = None) -> str:
    at = at or datetime.now(TZ)
    for event in item.get('events', []):
        if event['event_type'] == 'event' and event['precision'] == 'datetime' and event.get('end_at') and moment(event['start_at']) <= at < moment(event['end_at']):
            return 'ongoing'
    registration = registration_status(item, at)
    if registration != 'unknown':
        return registration
    future_events = [e for e in item.get('events', []) if e['event_type'] == 'event' and (moment(e['start_at']).date() > at.date() if e['precision'] == 'date' else moment(e['start_at']) > at)]
    if future_events and not item.get('is_opportunity'):
        return 'upcoming'
    activities = [e for e in item.get('events', []) if e['event_type'] == 'event']
    if activities and not item.get('is_opportunity') and all(e.get('end_at') and moment(e['end_at']) <= at for e in activities):
        return 'closed'
    return 'unknown'


def decorate(item: dict, at: datetime | None = None) -> dict:
    result = dict(item)
    result['status'] = item_status(item, at)
    result['registration'] = {'active': 'open', 'closed': 'closed', 'upcoming': 'upcoming'}.get(registration_status(item, at), 'unknown')
    deadlines = sorted(e['start_at'] for e in item.get('events', []) if e['event_type'] == 'deadline')
    activities = sorted(e['start_at'] for e in item.get('events', []) if e['event_type'] == 'event')
    result['deadline_at'] = deadlines[0] if deadlines else None
    result['event_start_at'] = activities[0] if activities else None
    return result
