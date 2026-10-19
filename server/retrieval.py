"""本地资讯检索：自然问句去除问法，主题与过滤条件仍需真实命中。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from .domain import CATEGORY_TERMS, TZ, moment
from .store import Store


TOPICS = (
    ('讲座', ('讲座', '学术报告', '报告会')),
    ('说明会', ('说明会',)),
    ('竞赛', ('竞赛', '比赛', '挑战赛', '大赛')),
    ('奖学金', ('奖学金',)),
    ('助学金', ('助学金',)),
    ('交换', ('交换',)),
    ('暑校', ('暑校', '暑期学校')),
    ('志愿', ('志愿',)),
    ('实习', ('实习',)),
)

# 这些词表达浏览范围；马杯、体测等具体主题不能作为类别词直接丢弃。
CATEGORY_LABELS = {
    'academics': ('学业教务', '教务'),
    'research': ('科研科创', '科研', '科创', '学术'),
    'awards': ('奖助评优',),
    'international': ('国际交流',),
    'practice': ('实践公益',),
    'career': ('实习就业', '就业'),
    'campus': ('校园生活',),
    'other': ('其他类别',),
}

QUESTION_PHRASES = (
    '我想了解一下', '我想了解', '我想知道', '我想看看', '我想看', '能不能帮我',
    '可以帮我', '麻烦帮我', '请帮我', '请问一下', '介绍一下', '介绍下', '详细介绍',
    '帮我看看', '帮我看', '帮我', '告诉我', '请问', '查找', '查询', '查一下',
    '搜索一下', '搜索', '看一下', '看看', '了解一下', '了解', '介绍',
    '有哪些', '有什么', '是什么', '哪一些', '哪些', '有没有', '是否有',
    '帮我总结一下', '总结一下', '总结下', '概括一下', '梳理一下', '整理一下',
    '值得关注', '需要注意', '需要关注', '值得注意', '重点关注', '重点介绍',
    '有什么新进展', '有哪些新进展', '最新进展', '新进展', '新动态',
    '总结', '概括', '梳理', '汇总', '整理', '重要', '重点', '要紧', '注意', '关注', '动态',
    '主要观点', '核心观点', '主要内容', '核心内容', '关键内容', '关键结论', '主要结论',
    '需要怎么操作', '应该怎么操作', '怎么操作', '如何操作', '需要怎么做', '怎么做',
    '有什么区别', '有哪些区别', '哪个更紧急', '哪个更合适', '有什么不同', '有何区别',
    '比较一下', '对比一下', '对比', '比较', '区别', '差异', '金额', '额度', '奖励标准',
    '怎么报名', '如何报名', '怎样报名', '在哪里报名', '在哪报名', '报名方式',
    '怎么申请', '如何申请', '怎样申请', '在哪里申请', '申请方式', '申请流程',
    '怎么参加', '如何参加', '怎样参加', '参与方式', '报名流程',
    '需要什么材料', '需要哪些材料', '有什么要求', '有哪些要求', '申请要求',
    '报名要求', '参赛要求', '申请条件', '报名条件', '报名材料', '申请材料',
    '什么时候截止', '何时截止', '几点截止', '截止时间', '什么时间', '什么时候',
    '什么地点', '在哪里举行', '在哪里举办', '在哪举行', '在哪举办', '何时',
    '在哪儿', '在哪里', '在哪', '时间', '地点', '内容', '要求', '材料',
    '近一个月', '一个月', '近月', '近七天', '近7天', '近 7 天', '30天', '30 天',
    '下星期', '这星期', '下周', '本周', '这周', '今天', '明天', '今日', '明日',
    '最近', '近期', '当前', '现在', '最新', '本科学生', '本科同学', '本科生', '本科', '研究生',
    '还可以报名', '还能报名', '还可以申请', '还能申请', '可以申请', '可以报名',
    '可申请', '快要截止', '即将截止', '快截止', '未截止', '可报名', '已截止',
    '截止', '报名', '申请', '通知', '资讯', '消息', '信息', '活动', '项目', '机会',
    '推荐', '适合', '关于', '有关', '相关', '一下', '详细', '具体',
)
FOLLOWUP_PREFIX = re.compile(r'^(?:那(?:么)?|这个|这项|这场|这些|它们?|上述|上面|刚才|前面)')
ACTION_QUESTION = re.compile(r'怎么|如何|怎样|报名|申请|参加|参与|截止|时间|地点|材料|要求|条件|什么时候|何时|在哪')
TIME_QUESTION = re.compile(r'下星期|这星期|下周|本周|这周|今天|明天|今日|明日|最近|近期|一个月|近月|30\s*天|近\s*[7七]\s*天')
EXCHANGE_TOPIC = re.compile(r'(?:校际|校级|学生|本科生|研究生|境外|海外|国际).{0,4}交换|交换(?:生|项目|学习|留学|申请|名额)|交换.{0,10}(?:项目|留学|申请|名额)')
RELATED_CONTENT = re.compile(r'往期精选|往期回顾|往期推荐|推荐阅读|相关阅读')
OVERVIEW_QUESTION = re.compile(r'总结|概括|梳理|汇总|整理|值得关注|需要注意|需要关注|值得注意|重点关注|动态')
NEAR_DEADLINE = re.compile(r'快要截止|即将截止|快截止')
OPPORTUNITY_QUESTION = re.compile(r'机会|可(?:以)?申请|还能申请|还可以申请|可(?:以)?报名|还能报名|还可以报名|快要截止|即将截止|快截止')
DATE_IN_QUESTION = re.compile(r'(?<!\d)(?:20\d{2}\s*[年/.-]\s*)?\d{1,2}\s*[月/.-]\s*\d{1,2}\s*(?:日|号)?(?!\d)(?:\s*\d{1,2}[:：]\d{2})?')
SEASON_WORDS = ('冬季', '寒假', '暑期', '暑假', '夏季', '秋季', '春季')
EE_SOURCES = {'ee', 'ee_wechat', 'voice', 'grad', 'aishengquan'}


def _parse(question: str, today) -> dict:
    lowered = question.casefold()
    topics = [(name, aliases) for name, aliases in TOPICS if any(alias in lowered for alias in aliases)]
    category = next((key for key, terms in CATEGORY_TERMS.items() if any(term.casefold() in lowered for term in terms)), None)
    for key, labels in CATEGORY_LABELS.items():
        if any(label in question for label in labels):
            category = key
            break

    activity_query = bool(topics and any(name in ('讲座', '说明会', '竞赛') for name, _ in topics)) or any(word in question for word in ('活动', '会议'))
    deadline_query = '截止' in question or 'ddl' in lowered
    has_relative_time = bool(re.search(r'下星期|这星期|下周|本周|这周|今天|明天|今日|明日', question))
    by_publication = any(word in question for word in ('发布', '公布', '更新')) and not deadline_query
    time_kind = 'deadline' if deadline_query else 'event' if activity_query and has_relative_time and not by_publication else 'published'
    days = 30 if re.search(r'一个月|近月|30\s*天', question) else 7 if re.search(r'近\s*[7七]\s*天', question) else None
    recent_default = bool(days is None and not has_relative_time and re.search(r'最近|近期', question))
    if recent_default:
        days = 30
    audience = '本科生' if '本科' in question else '研究生' if '研究生' in question else None
    near_deadline = bool(NEAR_DEADLINE.search(question))
    status = 'closed' if '已截止' in question else 'active' if near_deadline or any(word in question for word in ('还能报名', '还可以报名', '还能申请', '还可以申请', '可申请', '可以申请', '未截止', '可报名', '可以报名')) else None

    start = end = None
    if '下周' in question or '下星期' in question:
        start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        end = start + timedelta(days=6)
    elif any(word in question for word in ('本周', '这周', '这星期')):
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif '明天' in question or '明日' in question:
        start = end = today + timedelta(days=1)
    elif '今天' in question or '今日' in question:
        start = end = today
    if near_deadline and start is None:
        start, end = today, today + timedelta(days=6)

    organization = '电子系' if any(word in question for word in ('电子系', '电子工程系')) else None
    cleaned = DATE_IN_QUESTION.sub(' ', question)
    remove = list(QUESTION_PHRASES) + ['发布', '公布', '更新', 'DDL', 'ddl']
    if organization:
        remove += ['清华大学电子工程系', '清华电子工程系', '清华大学电子系', '清华电子系', '电子工程系', '电子系']
    remove += [alias for _, aliases in topics for alias in aliases]
    remove += [label for labels in CATEGORY_LABELS.values() for label in labels]
    for phrase in sorted(remove, key=len, reverse=True):
        cleaned = cleaned.replace(phrase, ' ')
    for season in SEASON_WORDS:
        cleaned = cleaned.replace(season, ' ' + season + ' ')
    # 只在片段边缘移除语气词，避免破坏标题内部的汉字。
    tokens = []
    for token in re.findall(r'[A-Za-z0-9]{2,}|[一-鿿]+', cleaned.casefold()):
        token = FOLLOWUP_PREFIX.sub('', token).strip('的了吗呢呀啊吧')
        if len(token) >= 2 and token not in ('什么', '怎么', '怎样', '如何', '是否', '还有', '可以', '一下', '给我'):
            tokens.append(token)
    return {
        'question': question, 'category': category, 'topics': topics, 'tokens': list(dict.fromkeys(tokens)),
        'days': days, 'audience': audience, 'status': status, 'time_kind': time_kind,
        'start': start, 'end': end, 'has_time': bool(TIME_QUESTION.search(question)) or near_deadline,
        'recent_window_default': recent_default, 'near_deadline': near_deadline,
        'opportunity': bool(OPPORTUNITY_QUESTION.search(question)),
        'organization': organization,
        'intent': 'overview' if OVERVIEW_QUESTION.search(question) or not tokens and re.search(r'有哪些|有什么|有没有|哪些|机会', question) else 'lookup',
    }


def _with_history(query: dict, history: Optional[list[dict]], today) -> tuple[dict, bool]:
    # 只有指代式/操作式追问才继承主题；独立新问题绝不拿上一主题凑答案。
    if not history or query['tokens']:
        return query, False
    question = query['question'].strip()
    followup = bool(FOLLOWUP_PREFIX.match(question))
    action_only = not query['topics'] and not query['category'] and bool(ACTION_QUESTION.search(question))
    if not followup and not action_only:
        return query, False
    current_topics = {name for name, _ in query['topics']}
    recent = history[-6:]
    for index in range(len(recent) - 1, -1, -1):
        entry = recent[index]
        if not isinstance(entry, dict) or entry.get('role') != 'user':
            continue
        text = entry.get('content')
        if not isinstance(text, str) or not text.strip():
            continue
        previous, _ = _with_history(_parse(text[:2000], today), recent[:index], today)
        if not previous['tokens'] and not previous['topics'] and not previous['category']:
            continue
        previous_topics = {name for name, _ in previous['topics']}
        if current_topics and not current_topics.issubset(previous_topics):
            return query, False
        merged = {**previous, 'question': question}
        for key in ('audience', 'status', 'organization'):
            if query[key]:
                merged[key] = query[key]
        if query['opportunity']:
            merged['opportunity'] = True
        if query['has_time']:
            for key in ('days', 'start', 'end', 'has_time', 'near_deadline', 'recent_window_default'):
                merged[key] = query[key]
        # 日期维度由主题与当前追问共同判断，如“本周讲座”→“那下周呢”。
        topic_words = ' '.join(name for name, _ in previous['topics'])
        merged['time_kind'] = _parse(topic_words + ' ' + previous['question'] + ' ' + question, today)['time_kind']
        return merged, True
    return query, False


def _date_in_range(value, start, end) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return start <= moment(value).date() <= end
    except (TypeError, ValueError):
        return False


def _topic_matches(text: str, topic) -> bool:
    name, aliases = topic
    if name == '交换':
        # “交换意见/协议/状态”不等于面向学生的交换项目。
        return bool(EXCHANGE_TOPIC.search(text))
    return any(alias in text for alias in aliases)


def _matches(text: str, topics, tokens) -> bool:
    def contains(token):
        if re.fullmatch(r'[a-z0-9]+', token):
            return bool(re.search(r'(?<![a-z0-9])' + re.escape(token) + r'(?![a-z0-9])', text))
        return token in text
    return all(_topic_matches(text, topic) for topic in topics) and all(contains(token) for token in tokens)


def _comparison_groups(question: str, today) -> list[dict]:
    match = re.search(r'(.+?)(?:和|与|跟|以及)(.+?)(?:有什么区别|有哪些区别|有何区别|有什么不同|哪个更紧急|哪个更合适|对比|比较|区别|差异)', question)
    if not match:
        return []
    left, right = [part.strip(' ，,。？?') for part in match.groups()]
    left = re.sub(r'^(?:请|帮我)?(?:对比|比较)(?:一下)?', '', left)
    # 只继承可确认的修饰词：同一学校的季节项目，或同一赛事的不同运动。
    shared = re.match(r'(.+?)(?:冬季|寒假|暑期|暑假|夏季|秋季|春季)', left)
    if shared and any(season in right for season in SEASON_WORDS):
        prefix = shared.group(1)
        if len(prefix) >= 2 and prefix not in right:
            right = prefix + right
    sports = re.match(r'(.+?(?:杯|赛))(?:篮球|足球|排球|乒乓球|羽毛球)', left)
    if sports and re.match(r'篮球|足球|排球|乒乓球|羽毛球', right):
        right = sports.group(1) + right
    result = []
    for label in (left, right):
        parsed = _parse(label, today)
        if not parsed['topics'] and not parsed['tokens']:
            return []
        result.append({'label': label, 'topics': parsed['topics'], 'tokens': parsed['tokens']})
    return result


def _audience_matches(item: dict, audience: Optional[str], content: str) -> bool:
    if not audience or audience in item.get('audience', []):
        return True
    target = r'本科(?:生|学生|同学)' if audience == '本科生' else '研究生'
    # 只认适用对象或资格证据；嘉宾履历中的学位不代表活动面向该人群。
    compact = re.sub(r'\s+', '', content)
    patterns = (
        r'(?:面向|对象[：:为]?|适用|限|招募).{0,25}' + target,
        target + r'.{0,12}(?:可申请|可报名|均可|均能|均可参加)',
        r'(?:全日制|正式注册|在校).{0,25}' + target,
        r'(?:申请条件|报名条件|申请资格|报名资格).{0,100}' + target,
    )
    if any(re.search(pattern, compact) for pattern in patterns):
        return True
    title = item.get('title') or ''
    return bool(re.search(target, title) and re.search(r'奖学金|招生|报名|招募|申请|资助', title))


def _has_opportunity_evidence(item: dict, title: str, content: str, topics) -> bool:
    action = re.compile(r'报名|申请|招募|申报|提交|填[写报]|选拔|截止|资格|条件|名额|资助对象')
    paragraphs = [part.strip() for part in re.split(r'[。；\n]', content) if part.strip()]
    award_topics = [topic for topic in topics if topic[0] in ('奖学金', '助学金')]
    if award_topics and not all(_topic_matches(title, topic) for topic in award_topics):
        # 讲者曾获奖学金等履历，不应混成可申请的奖学金信息。
        return any(all(_topic_matches(part, topic) for topic in award_topics) and action.search(part) for part in paragraphs)
    if re.search(r'报名|申请|招募|招收|申报|征集|招聘|选拔', title):
        return True
    if item.get('is_opportunity'):
        return True
    return bool(action.search(content) and (not topics or any(all(_topic_matches(part, topic) for topic in topics) for part in paragraphs) or _matches(title, topics, [])))


def _constraint_failures(item: dict, query: dict) -> list[str]:
    failures = []
    if query['status'] == 'active' and item.get('registration') != 'open':
        failures.append('registration_open')
    elif query['status'] == 'closed' and item.get('status') != 'closed':
        failures.append('registration_closed')
    if query['time_kind'] == 'deadline' and query['start']:
        if not any(event.get('event_type') == 'deadline' and _date_in_range(event.get('start_at'), query['start'], query['end']) for event in item.get('events', [])):
            failures.append('deadline_window')
    return failures


def _deadline_key(item: dict, query: dict) -> str:
    deadlines = [event.get('start_at') for event in item.get('events', []) if event.get('event_type') == 'deadline' and isinstance(event.get('start_at'), str)]
    if query['start'] and query['end']:
        inside = [value for value in deadlines if _date_in_range(value, query['start'], query['end'])]
        if inside:
            return min(inside)
    return min(deadlines) if deadlines else '9999'


def _select(candidates: list[dict], query: dict, limit: int) -> list[dict]:
    if query.get('comparison_groups'):
        ordered = sorted(candidates, key=lambda value: value['score'], reverse=True)
        selected = []
        for group_id in range(len(query['comparison_groups'])):
            hit = next((candidate for candidate in ordered if group_id in candidate.get('groups', []) and candidate not in selected), None)
            if hit:
                selected.append(hit)
        return (selected + [candidate for candidate in ordered if candidate not in selected])[:limit]
    if query['near_deadline'] or query['time_kind'] == 'deadline':
        return sorted(candidates, key=lambda value: (_deadline_key(value['item'], query), -value['score']))[:limit]
    if query['intent'] != 'overview':
        return sorted(candidates, key=lambda value: value['score'], reverse=True)[:limit]
    selected, remaining = [], list(candidates)
    categories, sources = set(), set()
    while remaining and len(selected) < limit:
        # 保持相关性，在同等相关资料之间覆盖更多类别/来源，正文充足者优先。
        best = max(remaining, key=lambda value: (
            value['score']
            + (3 if value['item'].get('category') not in categories else 0)
            + (2 if value['item'].get('source_id') not in sources else 0)
            + (2 if len(value['item'].get('content') or '') >= 80 else 0),
        ))
        selected.append(best)
        remaining.remove(best)
        categories.add(best['item'].get('category'))
        sources.add(best['item'].get('source_id'))
    return selected


def retrieve(store: Store, question: str, exclude_sources: list[str], history: Optional[list[dict]] = None):
    """具体查询最多六条，概括查询最多八条；未证实条件必须在范围中标明。"""
    today = datetime.now(TZ).date()
    query, contextual = _with_history(_parse(question, today), history, today)
    groups = _comparison_groups(question, today)
    query['comparison_groups'] = groups
    if groups:
        query['intent'] = 'comparison'
    category, topics, tokens = query['category'], query['topics'], query['tokens']
    # 具体主题优先匹配正文；分类结果可能有误，不应先把命中标题过滤掉。
    category_filter = category if not groups and not topics and not tokens else None
    scope = {
        'category': category_filter, 'category_hint': category, 'days': query['days'],
        'audience': query['audience'], 'status': query['status'], 'exclude_sources': exclude_sources,
        'time_kind': query['time_kind'], 'keywords': tokens + [name for name, _ in topics],
        'contextual': contextual, 'intent': query['intent'],
        'organization': query['organization'],
        'recent_window_default': query['recent_window_default'], 'opportunity': query['opportunity'],
        'constraint_status': 'no_results', 'strict_match_count': 0, 'unmet_constraints': [],
        'constraint_evidence': [], 'total_candidates': 0, 'returned_count': 0, 'truncated': False,
    }
    if groups:
        scope['keywords'] = list(dict.fromkeys(word for group in groups for word in group['tokens'] + [name for name, _ in group['topics']]))
        scope['comparison_groups'] = [{'label': group['label'], 'matched_count': 0, 'returned_count': 0} for group in groups]
    if query['near_deadline']:
        scope['deadline_window_days'] = (query['end'] - query['start']).days + 1
    if query['start']:
        scope.update(start=query['start'].isoformat(), end=query['end'].isoformat())
    has_scope = bool(category_filter or topics or tokens or query['start'] or query['days'] or query['audience'] or query['status'] or query['opportunity'] or query['organization'])
    broad_browse = bool(re.search(r'(?:有哪些|有什么|查看|看看|查找|查询|推荐).*(?:通知|资讯|消息|信息|活动)|(?:通知|资讯|消息|信息|活动).*(?:有哪些|有什么)', question))
    if not has_scope and not broad_browse and query['intent'] != 'overview':
        return [], scope

    # 结构化期限未提取出来时，仍保留原文供核对；不把相关资料当作满足状态条件。
    items = store.query(category=category_filter, days=query['days'], exclude_sources=exclude_sources, page_size=10000)['items']
    candidates = []
    for item in items:
        if query['start'] and query['time_kind'] != 'deadline':
            if query['time_kind'] == 'event':
                matches_date = any(event.get('event_type') == query['time_kind'] and _date_in_range(event.get('start_at'), query['start'], query['end']) for event in item.get('events', []))
            else:
                matches_date = _date_in_range(item.get('published_at'), query['start'], query['end'])
            if not matches_date:
                continue
        title = (item.get('title') or '').casefold()
        content = RELATED_CONTENT.split(item.get('content') or item.get('content_preview') or '', maxsplit=1)[0].casefold()
        if query['organization'] and item.get('source_id') not in EE_SOURCES and not any(term in title + ' ' + content for term in ('电子系', '电子工程系')):
            continue
        if not _audience_matches(item, query['audience'], content):
            continue
        # 正文中的无关段落不能各贡献一个普通词，拼出一条虚假的主题命中。
        passages = [title] + [part.strip() for part in re.split(r'[。；\n]', content) if part.strip()]
        matched_groups = [index for index, group in enumerate(groups) if any(_matches(passage, group['topics'], group['tokens']) for passage in passages)]
        if groups:
            if not matched_groups:
                continue
        elif (topics or tokens) and not any(_matches(passage, topics, tokens) for passage in passages):
            continue
        if query['opportunity'] and not _has_opportunity_evidence(item, title, content, topics):
            continue
        score = sum(10 for token in tokens if token in title)
        score += sum(6 for topic in topics if _topic_matches(title, topic))
        if groups:
            score = max(sum(10 for token in groups[index]['tokens'] if token in title) + sum(6 for topic in groups[index]['topics'] if _topic_matches(title, topic)) for index in matched_groups)
        score += int(bool(category and item.get('category') == category))
        candidates.append({'score': score, 'item': item, 'unmet': _constraint_failures(item, query), 'groups': matched_groups})
    strict = [candidate for candidate in candidates if not candidate['unmet']]
    scope.update(total_candidates=len(candidates), strict_match_count=len(strict))
    limit = 8 if query['intent'] == 'overview' else 6
    selected = _select(strict, query, limit)
    if selected:
        scope['constraint_status'] = 'matched'
        scope['truncated'] = len(strict) > limit
    elif candidates and (query['status'] == 'active' or query['time_kind'] == 'deadline'):
        selected = _select(candidates, query, limit)
        scope.update(
            constraint_status='evidence_only', truncated=len(candidates) > limit,
            unmet_constraints=sorted({failure for candidate in selected for failure in candidate['unmet']}),
            evidence_notice='未找到已核实符合全部条件的资讯。以下仅为主题相关的原文核查依据，不能据此确认仍可报名、可申请或即将截止；请核对原文时限与资格。',
        )
    scope['constraint_evidence'] = [
        {'id': candidate['item']['id'], 'unmet_constraints': candidate['unmet'],
         'registration': candidate['item'].get('registration'), 'deadline_at': candidate['item'].get('deadline_at')}
        for candidate in selected
    ]
    scope['returned_count'] = len(selected)
    for index, group_scope in enumerate(scope.get('comparison_groups', [])):
        group_scope['matched_count'] = sum(index in candidate['groups'] for candidate in candidates)
        group_scope['returned_count'] = sum(index in candidate['groups'] for candidate in selected)
    return [candidate['item'] for candidate in selected], scope
