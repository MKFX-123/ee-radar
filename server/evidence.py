"""从通知正文提取报名与日期依据；省略年份时明确记录采用的发布日期。"""
import hashlib
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

EXTRACTION_VERSION = 6
TZ = ZoneInfo('Asia/Shanghai')
DATE = re.compile(r'(?<!\d)(?:(20\d{2})[年/.-])?(\d{1,2})[月/.-](\d{1,2})(?:日|号)?(?:[（(][^）)]{0,20}(?:周|星期)[^）)]{0,12}[）)])?(?:[，,]?(?:第[一二三四五六]大节)?(?:时间[：:])?(上午|下午|晚上|晚|中午)?(\d{1,2})(?:[:：](\d{2})|点(?:整)?))?')
ACTION = r'报名|申请|申报|招募|领票|征稿|征文|投稿|提交|报送|登记|投递'


def main_text(text):
    return re.split(r'\n\s*(?:往期推荐|往期回顾|推荐阅读|相关阅读|延伸阅读|历史推送)\s*[:：]?\s*\n', text, maxsplit=1)[0]


def opportunity_candidate(title, text):
    body = re.sub(r'\s+', '', main_text(text))
    if re.search(r'采购|招标|中标|成交公告|征求意见|名单公示|拟录取|获奖名单|立项公示|讣告|活动回顾|圆满(?:结束|举行|收官)|顺利(?:举行|举办|召开)|蝉联|夺冠|为祖国庆生', title):
        return False
    if re.search(r'无需(?:提前)?报名|无需预约', body) and not re.search(r'报名截止|申请截止|招募志愿者', body):
        return False
    if re.search(r'报名|招募|征稿|征文|领票|招生简章|招聘|项目申报|申报.*通知', title):
        return True
    if re.search(r'报名(?:方式|方法|通道|问卷|截止|流程|页面)|扫描.{0,20}二维码.{0,15}报名|填写.{0,16}报名|开放申请|申请(?:条件|流程)|个人申请|欢迎.{0,12}报名', body) and not re.search(r'体质.*测试|体测', title):
        return True
    return False


def extract(item_id, title, text, published_at=None):
    original = main_text(text)
    # 保存字符映射，让提取依据始终是原文的逐字片段。
    positions = [i for i, c in enumerate(original) if not c.isspace()]
    compact = ''.join(original[i] for i in positions)
    opportunity = opportunity_candidate(title, original)
    events, starts = [], []
    pub = None
    try:
        pub = date.fromisoformat((published_at or '')[:10])
    except ValueError:
        pass
    matches = list(DATE.finditer(compact))
    for index, match in enumerate(matches):
        y, mo, da, period, hr, mi = match.groups()
        if not y and not pub:
            continue
        year = int(y) if y else pub.year
        try:
            day = date(year, int(mo), int(da))
            # 不凭月日猜测跨年或远离发布日期的年份。
            if not y and abs((day - pub).days) > 120:
                continue
            hour = int(hr) if hr else None
            minute = int(mi or 0)
            if hour is not None and period in ('下午', '晚上', '晚', '中午') and hour < 12:
                hour += 12
            if hour == 24 and minute == 0:
                value = datetime.combine(day + timedelta(days=1), datetime.min.time(), TZ).isoformat(timespec='seconds')
            elif hour is not None:
                value = datetime(year, day.month, day.day, hour, minute, tzinfo=TZ).isoformat(timespec='seconds')
            else:
                value = day.isoformat()
        except ValueError:
            continue
        left = max(0, match.start() - 65)
        for punctuation in ('。', '；', '！', '？'):
            left = max(left, compact.rfind(punctuation, left, match.start()) + 1)
        right = min(len(compact), match.end() + 45)
        for punctuation in ('。', '；', '！', '？'):
            pos = compact.find(punctuation, match.end(), right)
            if pos >= 0:
                right = min(right, pos + 1)
        next_match = matches[index + 1] if index + 1 < len(matches) else None
        previous = matches[index - 1] if index else None
        before, after = compact[left:match.start()], compact[match.end():right]
        # 只看当前日期前后的语义，不能把下一个日期的“截止”套到报名开始日。
        after_clause = compact[match.end():min(right, next_match.start() if next_match else right)]
        before_clause = compact[max(left, previous.end() if previous else left):match.start()]
        context = before + match.group() + after_clause
        between_next = compact[match.end():next_match.start()] if next_match else ''
        between_prev = compact[previous.end():match.start()] if previous else ''
        range_start = next_match is not None and bool(re.fullmatch(r'(?:开始)?[，,]?(?:至|到|—|–|-)', between_next))
        range_end = previous is not None and bool(re.fullmatch(r'(?:开始)?[，,]?(?:至|到|—|–|-)', between_prev))
        action_context = bool(re.search(ACTION, context))
        deadline = action_context and (bool(re.search(r'截止|截至|最迟', before_clause[-30:])) or bool(re.match(r'(?:前)?截止', after_clause)) or (range_end and bool(re.search(r'(?:报名|申请|申报|招募|征稿|征文|投稿系统开放)(?:时间|日期|期限)(?:为)?[：:]?$', compact[max(0, previous.start()-25):previous.start()]))))
        if opportunity and re.match(r'截稿', after_clause):
            deadline = True
        if opportunity and re.search(r'(?:报名|申请).{0,20}开始时间[：:]?$', before):
            starts.append({'at': value, 'precision': 'datetime' if hr else 'date'})
            deadline = False
        if range_start:
            deadline = False
            if opportunity and re.search(r'报名时间|申请时间|报名开始|开始报名|投稿系统开放时间', before):
                starts.append({'at': value, 'precision': 'datetime' if hr else 'date'})
        if opportunity and re.search(r'(?:报名|申请)(?:开始)?时间', before[-25:]) and re.match(r'(?:开始|起)', after):
            starts.append({'at': value, 'precision': 'datetime' if hr else 'date'})
        if not deadline and opportunity and re.search(r'报名|申请|申报|提交|报送', before) and re.match(r'前', after):
            deadline = True
        # 单独的“截止时间”必须处于已核实有报名动作的通知中。
        if opportunity and re.search(r'截止(?:时间|日期)?[：:为]?$', before) and not range_start:
            deadline = True
        activity = bool(re.search(r'(?:活动|比赛|讲座|会议|报告|演出|项目|展映|培训|开幕式)(?:举办)?(?:时间|日期)(?:Time)?[：:]?$', before_clause))
        activity |= bool(re.search(r'活动安排日期[：:]?$|活动信息日期[：:]?$|第[一二三四五六七八九十]+场(?:展映影片详细介绍|\+闭幕式)?$', before_clause))
        activity |= bool(re.search(r'(?:将于|定于)$', before) and re.search(r'举行|举办|开幕|开展', after))
        activity |= bool(re.search(r'(?:^|[^截止票])时间[：:]?$', before_clause) and re.search(r'讲座|讲坛|学长话前程|招聘会|培训|论坛|舞会|工作坊|比赛|竞赛|展演|宣讲|音乐会', title))
        if re.search(r'取消|原定|开票时间|报名时间|截止时间|开始时间', before_clause):
            activity = False
        if re.search(r'讣告|逝世', title):
            activity = False
        if not deadline and not activity:
            continue
        if deadline and (not opportunity or re.search(r'接收学生报名|立项人|资格审查|成立于', before)):
            continue
        kind = 'deadline' if deadline else 'event'
        source_excerpt = original[positions[left]:positions[right - 1] + 1]
        end_at = None
        end_match = re.match(r'[—–~至-](\d{1,2})[:：](\d{2})', after_clause)
        if activity and hr and end_match:
            try:
                finish = datetime(year, day.month, day.day, int(end_match[1]), int(end_match[2]), tzinfo=TZ)
                if finish.isoformat(timespec='seconds') > value:
                    end_at = finish.isoformat(timespec='seconds')
            except ValueError:
                pass
        event = {'id': hashlib.sha256(f'{item_id}:{kind}:{value}'.encode()).hexdigest()[:20], 'item_id': item_id,
                 'event_type': kind, 'title': '报名 / 申请截止' if deadline else '活动时间', 'start_at': value,
                 'end_at': end_at, 'precision': 'datetime' if hr else 'date', 'location': None,
                 'source_excerpt': source_excerpt, 'year_basis': None if y else f'采用发布时间 {published_at} 的年份'}
        if not any(e['event_type'] == kind and e['start_at'] == value for e in events):
            events.append(event)
    # 原文明示的每周重复讲座；起止日期、星期与时段必须同时存在。
    repeat = re.search(r'讲座时间[：:](20\d{2})年(\d{1,2})月(\d{1,2})日至(\d{1,2})月(\d{1,2})日，每周([一二三四五六日天])(\d{1,2}):(\d{2})[–—-](\d{1,2}):(\d{2})', compact)
    if repeat:
        try:
            y, m1, d1, m2, d2, weekday, h1, n1, h2, n2 = repeat.groups()
            first, last = date(int(y), int(m1), int(d1)), date(int(y), int(m2), int(d2))
            target = '一二三四五六日'.index('日' if weekday == '天' else weekday)
            if 0 <= (last-first).days <= 120:
                quote = original[positions[repeat.start()]:positions[repeat.end()-1]+1]
                day = first
                while day <= last:
                    if day.weekday() == target:
                        start = datetime.combine(day, datetime.min.time(), TZ).replace(hour=int(h1), minute=int(n1)).isoformat(timespec='seconds')
                        end = datetime.combine(day, datetime.min.time(), TZ).replace(hour=int(h2), minute=int(n2)).isoformat(timespec='seconds')
                        events.append({'id': hashlib.sha256(f'{item_id}:event:{start}'.encode()).hexdigest()[:20], 'item_id': item_id, 'event_type': 'event', 'title': '系列讲座', 'start_at': start, 'end_at': end if end > start else None, 'precision': 'datetime', 'location': None, 'source_excerpt': quote})
                    day += timedelta(days=1)
        except ValueError:
            pass
    # 同一天有更精确的时刻时，不重复产生日期型事项。
    precise = {(e['event_type'], e['start_at'][:10]) for e in events if e['precision'] == 'datetime'}
    events = [e for e in events if e['precision'] != 'date' or (e['event_type'], e['start_at']) not in precise]
    return {'is_opportunity': opportunity, 'events': events, 'registration_start_at': min((s['at'] for s in starts), default=None),
            'registration_limited': bool(re.search(r'报满为止|先到先得|名额有限', compact)), 'extraction_version': EXTRACTION_VERSION}
