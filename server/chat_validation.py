"""校验模型回答中的来源与数字；只兼容可还原的排版差异。"""
from __future__ import annotations

import re
from decimal import Decimal
from datetime import date, datetime, timedelta

from .ai import AIError
from .domain import DATE_RE, TZ, explicit_dates


_MARKER = re.compile(r'\[(\d+)\]')
_LIST_PREFIX = re.compile(
    r'^[ \t]*(?:>[ \t]*)?(?:#{1,6}[ \t]+)?(?:[-+][ \t]+)?'
    r'(?:\d{1,3}[.)][ \t]+|\d{1,3}、[ \t]*|[（(]\d{1,3}[）)][ \t]+)'
)
_EMPHASIS = re.compile(
    r'(?<!\w)(?:\*{1,3}|_{1,3}|`{1,3})|(?:\*{1,3}|_{1,3}|`{1,3})(?!\w)'
)
_NUMBER = re.compile(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?')
_MONTH_DAY = re.compile(r'(?<!\d)(\d{1,2})月(\d{1,2})日')
_CLOCK = re.compile(r'(?<!\d)(\d{1,2})[:：](\d{2})(?::(\d{2}))?(?!\d)')
_PARTIAL_DATETIME = re.compile(
    r'(?<!\d)(\d{1,2})月(\d{1,2})日(?:[（(][^）)\n]{1,10}[）)])?'
    r'\s*(上午|下午|晚上|晚)?\s*(\d{1,2})[:：](\d{2})(?::(\d{2}))?'
)
_SPLIT_DATE = re.compile(
    r'(?<!\d)(?:\d(?:\s*\d){3}\s*年\s*)?'
    r'\d(?:\s*\d)?\s*月\s*\d(?:\s*\d)?\s*日'
)
_SPLIT_AMOUNT = re.compile(r'(?<!\d)\d{1,3}(?:[ \t]*\r?\n[ \t]*\d{3})+\s*(?=元)')
_AMOUNT_CONTEXT = re.compile(r'(?:奖励标准|金额|每人每年|每人|每年|资助标准|补助标准|人民币|奖金|学费|费用|共计|合计)[^\d。；！？]{0,30}$')
_SAME_MONTH_RANGE = re.compile(
    r'(?<!\d)(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日'
    r'(?P<connector>至|到|[—–－~～-]{1,2})(\d{1,2})日'
)
_WEEKDAY_AFTER_DATE = re.compile(
    r'((?:20\d{2}[年/.-])?\d{1,2}[月/.-]\d{1,2}日?)'
    r'[（(](?:周|星期|礼拜)[一二三四五六日天1-7][）)]'
)


def _numeric_layout_spans(text: str):
    """只合并日期单位或明确金额语境下的拆行，普通编号不合并。"""
    spans = [(match.start(), match.end()) for match in _SPLIT_DATE.finditer(text)]
    for match in _SPLIT_AMOUNT.finditer(text):
        if _AMOUNT_CONTEXT.search(text[max(0, match.start() - 100):match.start()]):
            spans.append((match.start(), match.end()))
    return spans


def _layout_text(text: str):
    """中文空白可省略，英文单词和相邻数字之间仍保留分隔。"""
    chars, positions = [], []
    numeric_spans = _numeric_layout_spans(text)
    offset = 0
    while offset < len(text):
        if not text[offset].isspace():
            chars.append(text[offset])
            positions.append((offset, offset + 1))
            offset += 1
            continue
        end = offset + 1
        while end < len(text) and text[end].isspace():
            end += 1
        before = text[offset - 1] if offset else ''
        after = text[end] if end < len(text) else ''
        numeric_layout = any(start <= offset < span_end for start, span_end in numeric_spans)
        if not numeric_layout and before and after and before.isascii() and after.isascii() and before.isalnum() and after.isalnum():
            chars.append(' ')
            positions.append((offset, end))
        offset = end
    return ''.join(chars), positions


def _original_excerpt(quote: str, source: str) -> str:
    quote = quote.strip()
    if not quote:
        raise AIError('模型引用未通过原文核对，已退回原文检索结果。')
    if quote in source:
        return quote
    normalized_quote, _ = _layout_text(quote)
    normalized_source, positions = _layout_text(source)
    start = normalized_source.find(normalized_quote) if normalized_quote else -1
    if start < 0:
        raise AIError('模型引用未通过原文核对，已退回原文检索结果。')
    # 返回原文自身的连续切片，前端展示的证据不会变成模型改写文字。
    return source[positions[start][0]:positions[start + len(normalized_quote) - 1][1]]


def _checked_statements(answer: str):
    checked = _EMPHASIS.sub('', answer)
    lines = []
    for line in checked.splitlines():
        line = _LIST_PREFIX.sub('', line)
        # 仅将独立引用行接回相邻上一行，不跨空段落借用引用。
        if re.fullmatch(r'[ \t]*(?:\[\d+\][ \t]*)+', line) and lines and lines[-1].strip():
            lines[-1] += line.strip()
        else:
            lines.append(line)
    checked = '\n'.join(lines)
    checked = re.sub(r'([。！？!?])([ \t]*(?:\[\d+\][ \t]*)+)', r'\2\1', checked)
    return re.split(r'[。！？!?\n]', checked)


def _numbers(text: str):
    # 日期和时刻由独立规则校验，避免日期里的数字被借作名额等依据。
    text = DATE_RE.sub(' ', text)
    text = _MONTH_DAY.sub(' ', text)
    text = _CLOCK.sub(' ', text)
    return {Decimal(value.replace(',', '')) for value in _NUMBER.findall(text)}


def _chat_dates(text: str):
    """只为问答核验接纳明确日期的 24:00，不改变全局抽取。"""
    dates = []
    for match in DATE_RE.finditer(text):
        year, month, day, period, hour, minute, second = match.groups()
        if hour is not None and int(hour) == 24:
            if int(minute or 0) or int(second or 0) or period == '上午':
                continue
            try:
                next_day = date(int(year), int(month), int(day)) + timedelta(days=1)
                value = datetime(next_day.year, next_day.month, next_day.day, tzinfo=TZ).isoformat(timespec='seconds')
                dates.append((value, 'datetime'))
            except ValueError:
                continue
        else:
            dates.extend(explicit_dates(match.group()))
    return dates


def _unsupported_error(message: str, kind: str, values):
    """只反馈少量数字/时间标记，不回传整句原文或任意模型文字。"""
    details = []
    for value in values:
        value = re.sub(r'[^0-9.月日年:：/ T+－—–\-]', '', str(value))
        if not value or value in details:
            continue
        if len(value) > 40:
            value = '数字过长（未回显）'
        details.append(value)
        if len(details) == 6:
            break
    suffix = f' 未支持的{kind}：' + '、'.join(details) + '。' if details else ''
    error = AIError(message + suffix)
    error.unsupported = {'kind': kind, 'values': details}
    return error


def _calendar_layout(text: str, strict=False):
    """仅供数值核验；范围补全须明确同月，星期括号不产生新日期。"""
    text = _WEEKDAY_AFTER_DATE.sub(r'\1 ', text)

    def expand_range(match):
        year, month, first_day, connector, last_day = match.groups()
        try:
            start = date(int(year or 2000), int(month), int(first_day))
            end = date(int(year or 2000), int(month), int(last_day))
            if end < start:
                raise ValueError()
        except ValueError:
            if strict:
                raise _unsupported_error('AI 回答日期与原文依据不一致，已保留原文检索结果。', '日期范围', [match.group()]) from None
            return match.group()
        prefix = (year + '年') if year else ''
        return f'{prefix}{month}月{first_day}日{connector}{prefix}{month}月{last_day}日'

    return _SAME_MONTH_RANGE.sub(expand_range, text)


def _validate_numbers(statement: str, evidence: str):
    statement, _ = _layout_text(_MARKER.sub('', statement))
    evidence, _ = _layout_text(evidence)
    statement = _calendar_layout(statement, strict=True)
    evidence = _calendar_layout(evidence)
    unsupported_numbers = sorted(_numbers(statement) - _numbers(evidence))
    if unsupported_numbers:
        raise _unsupported_error('AI 回答引入了证据中不存在的数字，已保留原文检索结果。', '数字', unsupported_numbers)
    evidence_dates = set(_chat_dates(evidence))
    # 24:00 在日历上对应次日零点，也允许引用原文所写的当天日期。
    for match in DATE_RE.finditer(evidence):
        year, month, day, _, hour, _, _ = match.groups()
        if hour is not None and int(hour) == 24 and _chat_dates(match.group()):
            evidence_dates.add((date(int(year), int(month), int(day)).isoformat(), 'date'))
    # 正文给出完整时刻时，仅回答同一天也有依据；不能反向补出时刻。
    evidence_dates.update((value[:10], 'date') for value, precision in list(evidence_dates) if precision == 'datetime')
    for match in DATE_RE.finditer(statement):
        dates = _chat_dates(match.group())
        if not dates or any(value not in evidence_dates for value in dates):
            raise _unsupported_error('AI 回答日期与原文依据不一致，已保留原文检索结果。', '日期', [match.group()])
    # 月日和时分必须成对出现，不能拼接证据中互不相关的数字。
    def month_days(text, original_full_dates=True):
        # 完整时间已按同一时刻核对，不能再把 24:00 的书写日独立判错。
        partial_text = text if original_full_dates else DATE_RE.sub(' ', text)
        pairs = {(int(month), int(day)) for month, day in _MONTH_DAY.findall(partial_text)}
        pairs.update((int(value[5:7]), int(value[8:10])) for value, _ in _chat_dates(text))
        return pairs

    def clocks(text):
        times = set()
        previous_end, previous_period = None, None
        for match in _CLOCK.finditer(text):
            hour, minute, second = (int(value or 0) for value in match.groups())
            period_match = re.search(r'(上午|下午|晚上)\s*$', text[:match.start()])
            period = period_match.group(1) if period_match else None
            if period is None and previous_end is not None and re.fullmatch(r'\s*(?:[—–－~-]+|～|至|到)\s*', text[previous_end:match.start()]):
                period = previous_period
            if hour < 12 and period in ('下午', '晚上'):
                hour += 12
            if hour == 24 and minute == second == 0 and period != '上午':
                hour = 0
            times.add((hour, minute, second))
            previous_end, previous_period = match.end(), period
        return times

    def partial_times(text):
        values = set()
        for value, precision in _chat_dates(text):
            if precision == 'datetime':
                moment = datetime.fromisoformat(value)
                values.add((moment.month, moment.day, moment.hour, moment.minute, moment.second))
        for match in _PARTIAL_DATETIME.finditer(text):
            month, day, period, hour, minute, second = match.groups()
            hour, minute, second = int(hour), int(minute), int(second or 0)
            if hour < 12 and period in ('下午', '晚上', '晚'):
                hour += 12
            try:
                # 用闰年保留二月二十九日的可能性；明确年份由完整日期校验。
                base = date(2000, int(month), int(day))
                if hour == 24 and minute == second == 0 and period != '上午':
                    base += timedelta(days=1)
                    hour = 0
                datetime(base.year, base.month, base.day, hour, minute, second)
            except ValueError:
                continue
            values.add((base.month, base.day, hour, minute, second))
        return values

    # 月日省略年份时同样核对日期和时刻绑定，不能把当日零点当当日24点。
    for match in _PARTIAL_DATETIME.finditer(DATE_RE.sub(' ', statement)):
        values = partial_times(match.group())
        if not values or not values.issubset(partial_times(evidence)):
            raise _unsupported_error('AI 回答日期或时间与原文依据不一致，已保留原文检索结果。', '日期时间', [match.group()])

    unsupported_days = sorted(month_days(statement, False) - month_days(evidence))
    unsupported_clocks = sorted(clocks(statement) - clocks(evidence))
    if unsupported_days or unsupported_clocks:
        values = [f'{month}月{day}日' for month, day in unsupported_days]
        values += [f'{hour:02d}:{minute:02d}' + (f':{second:02d}' if second else '') for hour, minute, second in unsupported_clocks]
        raise _unsupported_error('AI 回答日期或时间与原文依据不一致，已保留原文检索结果。', '日期时间', values)


def validate_chat_answer(payload: dict, references: list[dict]) -> tuple[str, list[dict]]:
    """返回原回答和已核对的真实原文片段，失败时抛出 AIError。"""
    answer = payload.get('answer') if isinstance(payload, dict) else None
    citations = payload.get('citations') if isinstance(payload, dict) else None
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 6000 or not isinstance(citations, list) or not citations:
        raise AIError('模型回答缺少有效引用，已退回原文检索结果。')
    by_id = {reference['id']: reference for reference in references}
    by_number = {reference['number']: reference for reference in references}
    checked_citations = []
    for citation in citations:
        if not isinstance(citation, dict) or not isinstance(citation.get('id'), str) or citation['id'] not in by_id or not isinstance(citation.get('source_excerpt'), str):
            raise AIError('模型引用未通过原文核对，已退回原文检索结果。')
        quote = _original_excerpt(citation['source_excerpt'], by_id[citation['id']]['excerpt'])
        checked_citations.append({'id': citation['id'], 'source_excerpt': quote})
    cited_ids = {citation['id'] for citation in checked_citations}
    markers = [int(number) for number in _MARKER.findall(answer)]
    if not markers or any(number not in by_number or by_number[number]['id'] not in cited_ids for number in markers):
        raise AIError('模型引用序号未对应来源，已退回原文检索结果。')
    for statement in _checked_statements(answer):
        if not re.search(r'\d', _MARKER.sub('', statement)):
            continue
        statement_numbers = [int(number) for number in _MARKER.findall(statement)]
        if not statement_numbers:
            raise AIError('模型数字事实没有逐句来源，已退回原文检索结果。')
        source_ids = {by_number[number]['id'] for number in statement_numbers}
        evidence = '\n'.join(citation['source_excerpt'] for citation in checked_citations if citation['id'] in source_ids)
        _validate_numbers(statement, evidence)
    return answer.strip(), checked_citations
