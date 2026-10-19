"""仅使用临时资料检验检索，不请求模型或访问真实缓存。"""
from datetime import datetime, timedelta

import pytest

from server.domain import TZ
from server.retrieval import retrieve
from server.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'retrieval.sqlite3')

def add(store, item_id, title, content='', **changes):
    current = datetime.now(TZ)
    store.upsert({
        'id': item_id, 'source_id': 'info', 'source': '清华 info',
        'source_record_id': item_id, 'source_url': 'https://info.tsinghua.edu.cn/' + item_id,
        'title': title, 'content': content, 'published_at': current.date().isoformat(),
        'fetched_at': current.isoformat(), **changes,
    })

def ids(result):
    return [item['id'] for item in result[0]]


@pytest.mark.parametrize('question', [
    '新生理论知识竞赛怎么报名？',
    '请问新生理论知识竞赛的报名方式是什么？',
    '帮我查找新生理论知识竞赛',
    '我想了解一下新生理论知识竞赛的报名要求',
])
def test_natural_question_keeps_specific_topic_without_requiring_question_words(store, question):
    add(store, 'newstudent', '忆往無前 | 新生理论知识竞赛来啦！', '填写报名表参加活动。', category='campus')
    add(store, 'robot', '机器人竞赛报名通知', '填写报名表参加活动。')
    assert ids(retrieve(store, question, [])) == ['newstudent']


def test_generic_project_word_does_not_hide_exchange_information(store):
    add(store, 'exchange', '本科交换生招募通知', '面向本科生开放申请。')
    add(store, 'research', '科研项目申请', '科研项目报名。')
    assert ids(retrieve(store, '最近有哪些交换项目？', [])) == ['exchange']


def test_exchange_topic_excludes_literal_swaps_and_cross_paragraph_keywords(store):
    add(store, 'exchange', '校级交换项目常见问题', '介绍申请流程。')
    add(store, 'sound', '让声音像光束一样聚焦', '下一半周期，两者交换状态。此研究项目获得支持。')
    add(store, 'strategy', '清华全球战略十周年', '双方交换合作协议。介绍学校科研项目。')
    add(store, 'visit', '代表团访问', '双方就科研项目交换意见。')
    assert ids(retrieve(store, '最近有哪些交换项目？', [])) == ['exchange']


def test_body_topic_and_specific_keywords_need_same_passage(store):
    add(store, 'exact', '理论学习活动', '新生理论知识竞赛即将举行。')
    add(store, 'unrelated', '学生工作回顾', '学习新生理论知识。稍后介绍机器人大赛。')
    assert ids(retrieve(store, '新生理论知识竞赛怎么报名？', [])) == ['exact']


def test_related_article_footer_does_not_become_current_article_topic(store):
    add(store, 'exact', '新生理论知识竞赛通知', '提交报名表。')
    add(store, 'footer', '食堂营业时间调整', '本周营业时间调整。\n往期推荐\n新生理论知识竞赛通知')
    assert ids(retrieve(store, '新生理论知识竞赛怎么报名？', [])) == ['exact']


def test_topic_synonyms_remain_required(store):
    add(store, 'lecture', '人工智能学术报告', '欢迎参加。')
    add(store, 'signup', '科研项目报名通知', '提交材料。')
    assert ids(retrieve(store, '有哪些讲座？', [])) == ['lecture']


def test_specific_unknown_topic_never_returns_generic_competitions(store):
    add(store, 'robot', '机器人竞赛报名通知', '报名时间另行通知。')
    assert ids(retrieve(store, '量子橡皮鸭竞赛怎么报名？', [])) == []
    assert ids(retrieve(store, '不存在的量子橡皮鸭', [])) == []


def test_pure_category_browse_keeps_category_constraint(store):
    add(store, 'campus', '校园活动', '欢迎参加。', category='campus')
    add(store, 'research', '科研项目申请', '提交材料。', category='research')
    result = retrieve(store, '最近有哪些科研资讯？', [])
    assert ids(result) == ['research']
    assert result[1]['category'] == 'research'


def test_specific_campus_topic_is_not_lost_as_category_word(store):
    add(store, 'mayuehan', '马杯比赛通知', '报名信息。', category='campus')
    add(store, 'other', '校园知识竞赛通知', '报名信息。', category='campus')
    assert ids(retrieve(store, '马杯比赛怎么报名？', [])) == ['mayuehan']


@pytest.mark.parametrize('question', ['怎么报名？', '这个需要什么材料？', '那竞赛什么时候截止？'])
def test_action_followup_inherits_previous_user_topic(store, question):
    add(store, 'newstudent', '新生理论知识竞赛通知', '提交报名表。', category='campus')
    add(store, 'robot', '机器人竞赛通知', '提交报名表。')
    history = [
        {'role': 'user', 'content': '帮我查找新生理论知识竞赛'},
        {'role': 'assistant', 'content': '已找到这条通知[1]。'},
    ]
    result = retrieve(store, question, [], history)
    assert ids(result) == ['newstudent']
    assert result[1]['contextual'] is True


def test_explicit_new_topic_does_not_reuse_previous_subject(store):
    add(store, 'newstudent', '新生理论知识竞赛通知', '提交报名表。')
    add(store, 'scholarship', '奖学金申请通知', '提交申请表。')
    history = [{'role': 'user', 'content': '新生理论知识竞赛怎么报名'}]
    result = retrieve(store, '那奖学金怎么申请？', [], history)
    assert ids(result) == ['scholarship']
    assert result[1]['contextual'] is False
    assert ids(retrieve(store, '量子橡皮鸭项目怎么申请？', [], history)) == []


def test_assistant_output_is_not_treated_as_search_instruction(store):
    add(store, 'robot', '机器人竞赛通知', '提交报名表。')
    assert ids(retrieve(store, '怎么报名？', [], [{'role': 'assistant', 'content': '机器人竞赛'}])) == []
    assert ids(retrieve(store, '怎么报名？', [])) == []


def test_followup_preserves_excluded_sources(store):
    add(store, 'newstudent', '新生理论知识竞赛通知', '提交报名表。')
    history = [{'role': 'user', 'content': '新生理论知识竞赛怎么报名'}]
    assert ids(retrieve(store, '那需要什么材料？', ['info'], history)) == []


def test_relative_dates_still_use_events_and_followup_overrides_week(store):
    today = datetime.now(TZ).date()
    next_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    add(store, 'today', '科研学术讲座', '活动时间：' + today.isoformat() + '。')
    add(store, 'next', '科研学术讲座', '活动时间：' + next_monday.isoformat() + '。')
    add(store, 'old', '科研学术讲座', '活动时间：' + (today - timedelta(days=20)).isoformat() + '。')
    add(store, 'signup', '科研项目报名通知', '活动时间：' + today.isoformat() + '。')
    assert ids(retrieve(store, '今天有哪些讲座', [])) == ['today']
    result = retrieve(store, '那下周呢？', [], [{'role': 'user', 'content': '本周有哪些讲座？'}])
    assert ids(result) == ['next']
    assert result[1]['time_kind'] == 'event'
    assert result[1]['start'] == next_monday.isoformat()
    assert set(ids(retrieve(store, '科研讲座有哪些', []))) == {'today', 'next', 'old'}
    history = [{'role': 'user', 'content': '本周有哪些讲座？'}, {'role': 'user', 'content': '那下周呢？'}]
    assert ids(retrieve(store, '怎么参加？', [], history)) == ['next']


def test_registration_filter_is_not_relaxed_by_better_keyword_matching(store):
    today = datetime.now(TZ).date()
    add(store, 'open', '新生理论知识竞赛', '报名截止：' + (today + timedelta(days=2)).isoformat() + '。')
    add(store, 'closed', '新生理论知识竞赛', '报名截止：' + (today - timedelta(days=2)).isoformat() + '。')
    assert ids(retrieve(store, '新生理论知识竞赛还有哪些可报名的？', [])) == ['open']
    history = [{'role': 'user', 'content': '新生理论知识竞赛有哪些可报名的？'}]
    assert ids(retrieve(store, '那需要什么材料？', [], history)) == ['open']


def test_followup_keeps_previous_audience_and_can_change_status(store):
    today = datetime.now(TZ).date()
    add(store, 'undergraduate', '交换生招募', '面向本科生开放申请。报名截止：' + (today + timedelta(days=2)).isoformat() + '。')
    add(store, 'graduate', '交换生招募', '面向研究生开放申请。报名截止：' + (today + timedelta(days=2)).isoformat() + '。')
    history = [{'role': 'user', 'content': '本科生有哪些交换项目？'}]
    result = retrieve(store, '那哪些还可以报名？', [], history)
    assert ids(result) == ['undergraduate']
    assert result[1]['audience'] == '本科生'
    assert result[1]['status'] == 'active'


def test_title_match_ranks_above_incidental_body_mention(store):
    add(store, 'exact', '新生理论知识竞赛通知', '提交报名表。')
    add(store, 'z-incidental', '学生会活动回顾', '往期曾举办新生理论知识竞赛。')
    assert ids(retrieve(store, '新生理论知识竞赛怎么报名？', []))[0] == 'exact'


def test_greetings_do_not_fill_with_unrelated_records(store):
    add(store, 'robot', '机器人竞赛通知', '提交报名表。')
    assert ids(retrieve(store, '你好', [])) == []
    assert ids(retrieve(store, '最近有哪些资讯？', [])) == ['robot']


@pytest.mark.parametrize('question', ['本周有什么值得关注？', '近期有哪些通知需要注意？', '帮我总结一下最近的资讯'])
def test_overview_words_are_not_required_literal_keywords(store, question):
    add(store, 'research', '人工智能科研成果发布', '团队介绍最新研究成果。', category='research')
    add(store, 'campus', '食堂营业时间调整', '请同学们关注营业时间变化。', category='campus')
    result = retrieve(store, question, [])
    assert set(ids(result)) == {'research', 'campus'}
    assert result[1]['intent'] == 'overview'
    assert result[1]['constraint_status'] == 'matched'
    assert result[1]['returned_count'] == 2


def test_overview_keeps_explicit_category_and_recent_publication_window(store):
    today = datetime.now(TZ).date()
    add(store, 'research', '人工智能科研成果发布', '团队介绍研究成果。', category='research')
    add(store, 'old', '科研项目进展', '研究内容。', category='research', published_at=(today - timedelta(days=31)).isoformat())
    add(store, 'campus', '校园食堂通知', '内容。', category='campus')
    result = retrieve(store, '总结最近科研动态', [])
    assert ids(result) == ['research']
    assert result[1]['category'] == 'research'
    assert result[1]['days'] == 30
    assert result[1]['recent_window_default'] is True


def test_overview_near_seven_days_does_not_become_current_week(store):
    today = datetime.now(TZ).date()
    add(store, 'recent', '科研成果发布', '内容。', category='research', published_at=(today - timedelta(days=6)).isoformat())
    add(store, 'older', '科研成果发布', '内容。', category='research', published_at=(today - timedelta(days=7)).isoformat())
    result = retrieve(store, '总结近七天科研动态', [])
    assert ids(result) == ['recent']
    assert result[1]['days'] == 7
    assert 'start' not in result[1]


def test_quick_deadline_window_is_seven_calendar_days_and_orders_deadlines(store):
    today = datetime.now(TZ).date()
    for item_id, offset in [('near', 1), ('later', 6), ('outside', 7), ('past', -1)]:
        add(store, item_id, '奖学金申请通知', '报名截止：' + (today + timedelta(days=offset)).isoformat() + '。')
    result = retrieve(store, '有哪些奖学金快截止？', [])
    assert ids(result) == ['near', 'later']
    assert result[1]['start'] == today.isoformat()
    assert result[1]['end'] == (today + timedelta(days=6)).isoformat()
    assert result[1]['deadline_window_days'] == 7
    assert result[1]['strict_match_count'] == 2
    assert result[1]['constraint_status'] == 'matched'


def test_unstructured_scholarship_kept_only_as_evidence_not_open_result(store):
    add(store, 'scholarship', '国家励志奖学金评选通知', '我校正式注册的二年级及以上全日制本科生。向所在院系提出书面申请。各项工作时间根据院系安排。')
    add(store, 'speaker', '音乐工作坊招募', '嘉宾曾获得音乐学院全额奖学金。欢迎报名工作坊。')
    result = retrieve(store, '有哪些奖学金快截止？', [])
    assert ids(result) == ['scholarship']
    assert result[1]['constraint_status'] == 'evidence_only'
    assert result[1]['strict_match_count'] == 0
    assert result[1]['unmet_constraints'] == ['deadline_window', 'registration_open']
    assert '不能据此确认' in result[1]['evidence_notice']
    assert result[1]['constraint_evidence'][0]['registration'] == 'unknown'


def test_application_overview_recognizes_explicit_audience_in_body(store):
    add(store, 'scholarship', '励志奖学金申请通知', '申请条件：我校正式注册的二年级及以上的全日制本科生。向所在院系提出书面申请。')
    add(store, 'graduate', '奖学金申请通知', '面向研究生开放申请。')
    add(store, 'biography', '嘉宾分享会', '嘉宾本科毕业于清华大学。欢迎申请旁听。')
    result = retrieve(store, '本科生可申请机会', [])
    assert ids(result) == ['scholarship']
    assert result[1]['constraint_status'] == 'evidence_only'
    assert result[1]['audience'] == '本科生'


def test_evidence_fallback_preserves_explicit_publication_window_and_excluded_source(store):
    today = datetime.now(TZ).date()
    add(store, 'scholarship', '奖学金申请通知', '申请时间由院系另行公布。', published_at=(today - timedelta(days=8)).isoformat())
    assert ids(retrieve(store, '近七天有哪些奖学金快截止？', [])) == []
    add(store, 'recent', '奖学金申请通知', '申请时间由院系另行公布。')
    assert ids(retrieve(store, '有哪些奖学金快截止？', ['info'])) == []


def test_evidence_fallback_does_not_broaden_unknown_specific_topic(store):
    add(store, 'scholarship', '国家奖学金申请通知', '申请截止时间另行通知。')
    result = retrieve(store, '量子橡皮鸭奖学金快截止了吗？', [])
    assert ids(result) == []
    assert result[1]['constraint_status'] == 'no_results'
    assert result[1]['total_candidates'] == 0


def test_closed_and_far_deadline_evidence_are_explicitly_marked(store):
    today = datetime.now(TZ).date()
    add(store, 'closed', '奖学金申请通知', '报名截止：' + (today - timedelta(days=1)).isoformat() + '。')
    add(store, 'far', '奖学金申请通知', '报名截止：' + (today + timedelta(days=20)).isoformat() + '。')
    result = retrieve(store, '有哪些奖学金快截止？', [])
    assert set(ids(result)) == {'closed', 'far'}
    assert result[1]['constraint_status'] == 'evidence_only'
    evidence = {entry['id']: entry for entry in result[1]['constraint_evidence']}
    assert evidence['closed']['registration'] == 'closed'
    assert evidence['closed']['unmet_constraints'] == ['registration_open', 'deadline_window']
    assert evidence['far']['unmet_constraints'] == ['deadline_window']


def test_overview_returns_at_most_eight_and_covers_available_categories_and_sources(store):
    for index in range(9):
        add(store, 'research-' + str(index), '科研动态发布', '这条资讯完整介绍了研究进展及其背景。' * 10, category='research')
    add(store, 'campus', '校园生活通知', '这条资讯完整介绍了校园服务变化。' * 10, category='campus', source_id='ee')
    result = retrieve(store, '总结最近资讯', [])
    assert len(ids(result)) == 8
    assert 'campus' in ids(result)
    assert result[1]['total_candidates'] == 10
    assert result[1]['strict_match_count'] == 10
    assert result[1]['truncated'] is True


def test_specific_query_still_returns_at_most_six(store):
    for index in range(9):
        add(store, 'robot-' + str(index), '机器人竞赛通知', '提交报名表。')
    result = retrieve(store, '机器人竞赛怎么报名？', [])
    assert len(ids(result)) == 6
    assert result[1]['intent'] == 'lookup'
    assert result[1]['truncated'] is True


def test_deadline_today_without_time_remains_unconfirmed_evidence(store):
    today = datetime.now(TZ).date()
    add(store, 'today', '奖学金申请通知', '报名截止：' + today.isoformat() + '。')
    result = retrieve(store, '有哪些奖学金快截止？', [])
    assert ids(result) == ['today']
    assert result[1]['constraint_status'] == 'evidence_only'
    assert result[1]['unmet_constraints'] == ['registration_open']
    assert result[1]['constraint_evidence'][0]['registration'] == 'unknown'


def test_explicit_next_week_deadline_window_overrides_default_seven_days(store):
    today = datetime.now(TZ).date()
    next_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    add(store, 'nextweek', '奖学金申请通知', '报名截止：' + (next_monday + timedelta(days=6)).isoformat() + '。')
    add(store, 'later', '奖学金申请通知', '报名截止：' + (next_monday + timedelta(days=7)).isoformat() + '。')
    result = retrieve(store, '下周有哪些奖学金快截止？', [])
    assert ids(result) == ['nextweek']
    assert result[1]['start'] == next_monday.isoformat()
    assert result[1]['end'] == (next_monday + timedelta(days=6)).isoformat()


def test_overview_does_not_override_hidden_sources_or_audience(store):
    add(store, 'info', '科研机会申请', '面向本科生开放申请。', source_id='info', category='research')
    add(store, 'ee', '科研机会申请', '面向本科生开放申请。', source_id='ee', category='research')
    add(store, 'graduate', '科研机会申请', '面向研究生开放申请。', source_id='ee', category='research')
    result = retrieve(store, '帮我总结最近适合本科生的科研机会', ['info'])
    assert ids(result) == ['ee']
    assert result[1]['intent'] == 'overview'
    assert result[1]['audience'] == '本科生'


@pytest.mark.parametrize('question', ['国家励志奖学金申请条件和金额？', '国家励志奖学金10月21日截止，我还能申请吗？'])
def test_requested_fields_and_user_claimed_dates_are_not_topic_requirements(store, question):
    add(store, 'scholarship', '国家励志奖学金评选通知', '正式注册的本科生可申请。各院系安排个人申请时间。')
    result = retrieve(store, question, [])
    assert ids(result) == ['scholarship']
    assert result[1]['keywords'] == ['国家励志', '奖学金']
    if '还能申请' in question:
        assert result[1]['constraint_status'] == 'evidence_only'


def test_srt_action_question_and_specific_report_summary(store):
    add(store, 'srt', '秋季学期SRT报名通知', '提交报名表。')
    add(store, 'report', '具身智能发展报告发布', '介绍研究进展。')
    assert ids(retrieve(store, 'SRT什么时候截止？需要怎么操作？', [])) == ['srt']
    assert ids(retrieve(store, '总结具身智能发展报告主要观点', [])) == ['report']


def test_comparison_matches_each_project_and_inherits_shared_university(store):
    add(store, 'winter', '2027年韩国汉阳大学冬季实习项目', '填写报名材料。')
    add(store, 'course', '2027年韩国汉阳大学寒假课程项目', '填写报名材料。')
    add(store, 'paris', '2027年法国巴黎政治学院寒假课程项目', '填写报名材料。')
    result = retrieve(store, '汉阳冬季实习和寒假课程有什么区别？', [])
    assert set(ids(result)) == {'winter', 'course'}
    assert result[1]['intent'] == 'comparison'
    assert [x['matched_count'] for x in result[1]['comparison_groups']] == [1, 1]
    assert [x['returned_count'] for x in result[1]['comparison_groups']] == [1, 1]


def test_comparison_inherits_shared_contest_name(store):
    add(store, 'basketball', '2026-2027无限杯篮球赛开始报名', '填写报名表。')
    add(store, 'football', '2026无限杯足球赛报名启动', '填写报名表。')
    add(store, 'other', '新生杯足球报名', '填写报名表。')
    result = retrieve(store, '无限杯篮球和足球哪个更紧急？', [])
    assert set(ids(result)) == {'basketball', 'football'}
    assert result[1]['comparison_groups'][1]['label'] == '无限杯足球'


def test_comparison_does_not_fill_missing_side_with_unrelated_articles(store):
    add(store, 'basketball', '无限杯篮球报名', '填写报名表。')
    add(store, 'other', '校级足球报名', '填写报名表。')
    result = retrieve(store, '无限杯篮球和量子橡皮鸭足球有什么区别？', [])
    assert ids(result) == ['basketball']
    assert result[1]['comparison_groups'][1]['matched_count'] == 0
    assert result[1]['comparison_groups'][1]['returned_count'] == 0


def test_electronic_department_overview_recognizes_sources_without_literal_title(store):
    add(store, 'department', '学生会活动通知', '欢迎参加。', source_id='voice')
    add(store, 'other', '其他院系活动通知', '欢迎参加。', source_id='info')
    result = retrieve(store, '帮我概括电子系最近值得关注的事', [])
    assert ids(result) == ['department']
    assert result[1]['organization'] == '电子系'
    assert ids(retrieve(store, '帮我概括电子系最近值得关注的事', ['voice'])) == []


def test_comparison_with_both_topics_missing_returns_no_unrelated_results(store):
    add(store, 'basketball', '无限杯篮球报名', '填写报名表。')
    result = retrieve(store, '量子橡皮鸭竞赛和火星南瓜杯有什么区别？', [])
    assert ids(result) == []
    assert [group['matched_count'] for group in result[1]['comparison_groups']] == [0, 0]


def test_academic_year_is_not_erased_as_a_date_and_ascii_topics_need_boundaries(store):
    add(store, 'year', '2026-2027无限杯篮球赛', '填写报名表。')
    assert ids(retrieve(store, '2026-2027无限杯篮球赛怎么报名？', [])) == ['year']
    add(store, 'srt', '秋季学期SRT报名通知', '填写报名表。')
    add(store, 'unrelated', '关于BSRTZ技术的介绍', '技术内容。')
    assert ids(retrieve(store, 'SRT什么时候截止？', [])) == ['srt']
