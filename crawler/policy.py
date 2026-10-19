"""采集与展示共用的发布时间范围，历史数据库无需破坏性清理。"""
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

POLICY_PATH = Path(__file__).with_name('policy.json')


def publication_since():
    value = json.loads(POLICY_PATH.read_text(encoding='utf-8'))['published_since']
    return date.fromisoformat(value).isoformat()


def within_publication_window(article, since=None):
    value = article.get('published_at')
    if not isinstance(value, str) or not value:
        return False
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
        else:
            moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
            day = moment.astimezone(ZoneInfo('Asia/Shanghai')).date()
        return day >= date.fromisoformat(since or publication_since())
    except (ValueError, TypeError):
        return False
