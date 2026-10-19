"""报名与日程语义回归，使用固定时间及模拟通知。"""
from datetime import datetime
from server.domain import normalize, decorate, TZ
from server.store import Store

AT = datetime(2026, 10, 18, 17, tzinfo=TZ)


def notice(text, title='活动报名通知', **extra):
    return normalize({'id': 'evidence', 'source_id': 'voice', 'source_url': 'https://mp.weixin.qq.com/s/evidence',
                      'title': title, 'content': text, 'published_at': '2026-10-17', **extra})


def test_separate_registration_open_and_deadline():
    item = notice('报名方式\n开始时间：10.17 8:00\n截止时间：10.19 12:00\n平台：1911星球')
    assert item['registration_start_at'] == '2026-10-17T08:00:00+08:00'
    assert [e['start_at'] for e in item['events']] == ['2026-10-19T12:00:00+08:00']
    assert decorate(item, AT)['registration'] == 'open'
    assert all(e['source_excerpt'] in item['content'] for e in item['events'])


def test_registration_future_and_midnight_boundary():
    item = notice('报名时间：2026年10月19日（周一）00:00开始，至2026年10月25日（周日）24:00截止。')
    assert decorate(item, AT)['registration'] == 'upcoming'
    assert item['events'][0]['start_at'] == '2026-10-26T00:00:00+08:00'
    assert decorate(item, datetime(2026, 10, 19, 1, tzinfo=TZ))['registration'] == 'open'


def test_student_deadline_not_supervisor_review_period():
    item = notice('学生报名时间为10月14日至10月25日，立项人可接收学生报名时间为10月15日至10月29日。10月26日至10月29日学生不再报名。')
    assert [(e['event_type'], e['start_at']) for e in item['events']] == [('deadline', '2026-10-25')]


def test_submission_deadline_and_consultation_are_distinct():
    item = notice('时间安排：线上初赛：10月31日截稿。报名方式：扫描二维码提交作品。', '书法大赛开启报名')
    assert decorate(item, AT)['registration'] == 'open'
    assert item['events'][0]['start_at'] == '2026-10-31'
    assert not notice('请提交意见。', '项目申报指南征求意见通知')['is_opportunity']


def test_no_registration_for_walkins_news_or_procurement():
    for title, text in [('游园会', '活动时间：10月20日，无需提前报名，自由参与。'),
                        ('研究成果新闻', '实验室申报成功。\n往期推荐\n报名截止：2026年10月20日'),
                        ('采购招标公告', '提交申请材料截止2026年10月20日。')]:
        assert not notice(text, title)['is_opportunity']


def test_explicit_signup_channels_not_missed():
    for text in ['报名流程：扫描二维码进入活动报名页面。', '扫描上方二维码，报名参加现场活动。']:
        assert notice(text, '学术报告')['is_opportunity']


def test_limited_seats_and_unknown_year_stay_unknown():
    assert decorate(notice('报名截止：2026年10月20日，名额有限，报满为止。'), AT)['registration'] == 'unknown'
    assert notice('活动时间10月20日14:00，报名截止10月19日。', published_at=None)['events'] == []
    assert notice('报名截止1月2日。', published_at='2026-12-28')['events'] == []


def test_cancelled_time_and_recurring_lecture():
    assert notice('取消原定于10月17日举行的活动。')['events'] == []
    item = notice('讲座时间：2026年10月24日至12月12日，每周六14:00–17:00，共八期。', '具身智能系列讲座')
    assert len(item['events']) == 8
    assert item['events'][0]['start_at'] == '2026-10-24T14:00:00+08:00'
    assert item['events'][-1]['end_at'] == '2026-12-12T17:00:00+08:00'


def test_source_counts_and_provenance_do_not_depend_on_pagination(tmp_path):
    store = Store(tmp_path / 'test.sqlite3')
    for i in range(23):
        store.upsert({'id': str(i), 'source_id': 'sports', 'title': '讲座' + str(i), 'source_url': f'https://mp.weixin.qq.com/s/test{i}', 'published_at': '2026-10-17',
                      'discovered_by': [{'source_id': 'sports_site', 'url': 'https://www.thsports.tsinghua.edu.cn/zhxw.htm'}]})
    store.ensure_sources({'sports_site': {'name': '体育部官网', 'kind': 'website', 'enabled': True}})
    counts = {s['id']: s for s in store.source_status()['sources']}
    assert counts['sports']['article_count'] == 23
    assert counts['sports_site']['linked_count'] == 23
    assert store.query(source='sports_site', page_size=5)['total'] == 23
