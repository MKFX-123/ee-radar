import pytest

from server.ai import AIError
from server.chat_validation import validate_chat_answer


SOURCE = '面向本科生开放申请。报名截止：2026年10月20日17:00。名额30人。'
REFERENCES = [{'id': 'a', 'number': 1, 'excerpt': SOURCE}]


def payload(answer, quote=SOURCE, source_id='a'):
    return {'answer': answer, 'citations': [{'id': source_id, 'source_excerpt': quote}]}


@pytest.mark.parametrize('answer', [
    '报名截止：2026年10月20日17:00[1]。',
    '报名截止：2026年10月20日17:00。[1]',
    '报名截止：2026年10月20日17:00。 **[1]**',
    '报名截止：2026年10月20日17:00。\n[1]',
    '报名截止：2026年10月20日17:00\n[1]',
    '1. **申请对象**：本科生。[1]\n2. **报名截止**：2026年10月20日17:00。[1]',
    '1、面向本科生开放申请。[1]\n2、名额30人。[1]',
    '(1) 面向本科生开放申请。[1]\n(2) 名额30人。[1]',
    '## 1. **报名截止**\n2026年10月20日17:00。[1]',
    '面向本科生开放申请。[1]\n报名截止：2026年10月20日17:00！[1]',
    '名额30人[1]。报名截止为2026年10月20日[1]。',
    '截止2026-10-20 17:00。[1]',
    '截止2026.10.20 17:00。[1]',
])
def test_layout_does_not_turn_supported_answer_into_model_failure(answer):
    validated, citations = validate_chat_answer(payload(answer), REFERENCES)
    assert validated == answer
    assert citations == [{'id': 'a', 'source_excerpt': SOURCE}]


@pytest.mark.parametrize('answer', [
    '报名截止为2099年12月31日[1]。',
    '报名截止为2026年10月30日17:00。[1]',
    '报名截止为2026年20月10日17:00。[1]',
    '报名截止为10月30日17:00。[1]',
    '报名截止为2026年10月20日17:30。[1]',
    '名额300人。[1]',
    '1. 名额300人。[1]',
    '3.5万元资助。[1]',
    '报名截止：2026年10月20日17:00。',
    '面向本科生。[1]报名截止：2026年10月20日17:00。',
    '报名截止：2026年10月20日17:00。面向本科生。[1]',
    '报名截止：2026年10月20日17:00。\n\n[1]',
    '面向本科生[99]。',
])
def test_wrong_facts_and_missing_sentence_citations_remain_rejected(answer):
    with pytest.raises(AIError):
        validate_chat_answer(payload(answer), REFERENCES)


def test_quote_whitespace_is_resolved_to_contiguous_original_text():
    original = '通知\n面向 本科生开放申请。\n报名截止：2026年10月20日 17:00。\n请关注。'
    quote = '面向本科生开放申请。报名截止：2026年10月20日17:00。'
    refs = [{'id': 'a', 'number': 1, 'excerpt': original}]
    answer, citations = validate_chat_answer(payload('报名截止：2026年10月20日17:00。[1]', quote), refs)
    assert answer
    assert citations[0]['source_excerpt'] == '面向 本科生开放申请。\n报名截止：2026年10月20日 17:00。'
    assert citations[0]['source_excerpt'] in original


@pytest.mark.parametrize('original,quote', [
    ('Not eligible for students.', 'Noteligible for students.'),
    ('名额 1 00 人。', '名额100人。'),
    (SOURCE, '面向研究生开放申请。'),
    (SOURCE, '面向本科生开放申请。名额30人。'),
    (SOURCE, '   '),
])
def test_quote_matching_does_not_rewrite_or_join_disjoint_evidence(original, quote):
    with pytest.raises(AIError, match='原文核对'):
        validate_chat_answer(payload('可申请。[1]', quote), [{'id': 'a', 'number': 1, 'excerpt': original}])


def test_english_line_wraps_are_allowed_without_joining_words():
    original = 'Applications are\nopen to students.'
    _, citations = validate_chat_answer(payload('接受学生申请。[1]', 'Applications are open to students.'), [{'id': 'a', 'number': 1, 'excerpt': original}])
    assert citations[0]['source_excerpt'] == original


def test_zero_padded_dates_keep_same_calendar_and_clock_values():
    original = '报名截止：2026年01月02日09:00。'
    answer, _ = validate_chat_answer(payload('报名截止为2026年1月2日9:00。[1]', original), [{'id': 'a', 'number': 1, 'excerpt': original}])
    assert answer


@pytest.mark.parametrize('original,answer', [
    ('截止2026-10-20 17:00。', '报名截止为2026年10月20日17:00。[1]'),
    ('截止2026.10.20 17:00。', '报名截止为10月20日17:00。[1]'),
    ('截止2026年10月20日下午5:00。', '报名截止为2026年10月20日17:00。[1]'),
    ('截止2026年10月20日17:00。', '报名截止为2026年10月20日下午5:00。[1]'),
])
def test_calendar_formats_and_twelve_hour_clocks_preserve_same_facts(original, answer):
    validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


def test_calendar_numbers_are_not_evidence_for_unrelated_quantities():
    with pytest.raises(AIError, match='数字'):
        validate_chat_answer(payload('名额20人。[1]'), REFERENCES)


@pytest.mark.parametrize('original,answer', [
    ('讲座：2026年10月14日 晚上19:30—21:00。', '讲座：10月14日晚上7:30—9:00。[1]'),
    ('讲座：2026年10月14日晚上7:30—9:00。', '讲座：10月14日19:30—21:00。[1]'),
    ('讲座：2026年10月14日晚上7:30至9:00。', '讲座：10月14日19:30—21:00。[1]'),
])
def test_clock_range_inherits_one_shared_time_of_day(original, answer):
    validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


@pytest.mark.parametrize('answer', [
    '讲座：10月14日晚上7:30—9:30。[1]',
    '讲座：10月14日上午7:30—9:00。[1]',
    '讲座：10月14日晚上7:30—上午9:00。[1]',
    '讲座：10月14日晚上7:30，9:00结束。[1]',
])
def test_clock_range_does_not_invent_periods_or_apply_them_to_other_clauses(answer):
    original = '讲座：2026年10月14日晚上19:30—21:00。'
    with pytest.raises(AIError):
        validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


def test_scholarship_amount_and_date_line_breaks_are_layout_only():
    original = '奖励标准：每人每年\n6\n000\n元。各院系需在\n10\n月\n21\n日前报送。学生处\n20\n2\n6\n年\n9\n月\n1\n4\n日'
    answer = '奖励标准为每人每年6000元。[1]各院系应在10月21日前报送。[1]通知落款2026年9月14日。[1]'
    _, citations = validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])
    assert citations[0]['source_excerpt'] == original


def test_normalized_date_quote_still_returns_original_contiguous_slice():
    original = '学生处\n20\n2\n6\n年\n9\n月\n1\n4\n日'
    _, citations = validate_chat_answer(payload('通知落款2026年9月14日。[1]', '2026年9月14日'), [{'id': 'a', 'number': 1, 'excerpt': original}])
    assert citations[0]['source_excerpt'] == '20\n2\n6\n年\n9\n月\n1\n4\n日'


def test_normalized_amount_quote_requires_money_context_and_returns_original():
    original = '奖励标准为每人每年\n6\n000\n元。'
    _, citations = validate_chat_answer(payload('每人每年6000元。[1]', '每人每年6000元。'), [{'id': 'a', 'number': 1, 'excerpt': original}])
    assert citations[0]['source_excerpt'] == '每人每年\n6\n000\n元。'


@pytest.mark.parametrize('original,answer', [
    ('编号1\n2', '编号12。[1]'),
    ('人数1\n2人。', '人数12人。[1]'),
    ('金额1\n2元。', '金额12元。[1]'),
    ('奖励标准每人每年6\n000元。', '每人每年6001元。[1]'),
    ('日期20\n2\n6年9月1\n4日。', '人数14人。[1]'),
    ('日期20\n2\n6年9月1\n4日。', '日期2026年9月15日。[1]'),
])
def test_numeric_line_break_fix_does_not_join_lists_or_invent_facts(original, answer):
    with pytest.raises(AIError):
        validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


@pytest.mark.parametrize('original,answer', [
    ('截止2026年10月18日24:00。', '截止2026年10月18日24:00。[1]'),
    ('截止2026年10月18日24:00。', '截止2026年10月19日00:00。[1]'),
    ('截止2026年10月19日00:00。', '截止2026年10月18日24:00。[1]'),
    ('截止2026年10月31日24:00。', '截止2026年11月1日00:00。[1]'),
    ('截止2026年12月31日24:00。', '截止2027年1月1日00:00。[1]'),
    ('截止2028年2月29日24:00。', '截止2028年3月1日00:00。[1]'),
    ('截止2026年10月18日24:00:00。', '截止2026年10月19日00:00。[1]'),
])
def test_end_of_day_midnight_has_same_instant_across_calendar_boundaries(original, answer):
    validated, citations = validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])
    assert validated == answer
    assert citations[0]['source_excerpt'] == original


@pytest.mark.parametrize('answer', [
    '截止2026年10月18日24:01。[1]',
    '截止2026年10月18日24:30。[1]',
    '截止2026年10月18日24:00:01。[1]',
    '截止2026年10月18日25:00。[1]',
    '截止2026年10月18日上午24:00。[1]',
    '截止2026年10月18日00:00。[1]',
    '截止2026年10月19日24:00。[1]',
    '截止2026年10月20日00:00。[1]',
    '截止2026年10月18日24:00。',
    '面向学生。[1]截止2026年10月18日24:00。',
])
def test_end_of_day_support_does_not_accept_invalid_times_wrong_dates_or_uncited_facts(answer):
    original = '面向学生。截止2026年10月18日24:00。'
    with pytest.raises(AIError):
        validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


@pytest.mark.parametrize('original,answer', [
    ('截止2027年2月29日24:00。', '截止2027年3月1日00:00。[1]'),
    ('截止2026年10月18日24:30。', '截止2026年10月19日00:30。[1]'),
])
def test_invalid_source_calendar_times_do_not_support_new_normalized_dates(original, answer):
    with pytest.raises(AIError):
        validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


@pytest.mark.parametrize('original,answer', [
    ('截止2026年10月18日24:00。', '截止10月18日24:00。[1]'),
    ('截止2026年10月18日24:00。', '截止10月19日00:00。[1]'),
    ('截止2026年12月31日24:00。', '截止1月1日00:00。[1]'),
])
def test_partial_date_midnight_preserves_day_time_pair(original, answer):
    validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


@pytest.mark.parametrize('original,answer', [
    ('截止2026年10月18日00:00。', '截止10月18日24:00。[1]'),
    ('截止2026年10月18日24:00。', '截止10月18日00:00。[1]'),
    ('截止2026年10月18日24:00。', '截止10月18日24:30。[1]'),
])
def test_partial_date_midnight_does_not_move_deadline_a_day(original, answer):
    with pytest.raises(AIError):
        validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


@pytest.mark.parametrize('answer,expected', [
    ('名额300人。[1]', '300'),
    ('报名截止2026年10月21日17:00。[1]', '2026年10月21日17:00'),
    ('报名截止10月21日17:00。[1]', '10月21日17:00'),
    ('报名截止10月21日。[1]', '10月21日'),
    ('报名截止17:30。[1]', '17:30'),
])
def test_validation_feedback_identifies_unsupported_numbers_or_times(answer, expected):
    with pytest.raises(AIError) as error:
        validate_chat_answer(payload(answer), REFERENCES)
    assert expected in str(error.value)
    assert error.value.unsupported['values']
    assert len(str(error.value)) < 400
    assert '面向本科生' not in str(error.value)


def test_validation_feedback_is_bounded_and_does_not_echo_full_statement():
    answer = '不应回显的整段说明，数字：' + '、'.join(str(i) for i in range(301, 321)) + '。[1]'
    with pytest.raises(AIError) as error:
        validate_chat_answer(payload(answer), REFERENCES)
    assert len(error.value.unsupported['values']) == 6
    assert '不应回显' not in str(error.value)
    assert '320' not in str(error.value)


def test_validation_feedback_does_not_echo_very_long_numeric_tokens():
    long_number = '9' * 120
    with pytest.raises(AIError) as error:
        validate_chat_answer(payload('人数' + long_number + '。[1]'), REFERENCES)
    assert long_number not in str(error.value)
    assert '未回显' in str(error.value)


@pytest.mark.parametrize('original,answer', [
    ('退课：2026-11-16（周一）8:00 ～ 2026-11-20（周五）16:00。', '退课时间2026年11月16日至20日。[1]'),
    ('学生报名10月14日至10月25日。', '学生报名10月14日至25日。[1]'),
    ('学生报名10月14日至25日。', '学生报名10月14日至10月25日。[1]'),
    ('退课：2026年11月16日至20日。', '退课时间2026-11-16至2026-11-20。[1]'),
    ('活动2026年10月14日（周三）晚上19:30。', '活动2026年10月14日19:30。[1]'),
    ('活动2026年10月14日19:30。', '活动2026年10月14日（星期三）19:30。[1]'),
    ('讲座2026-10-16（周五）09:00。', '讲座10月16日9:00。[1]'),
])
def test_calendar_layout_ranges_and_weekday_parentheses_preserve_supported_facts(original, answer):
    validated, citations = validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])
    assert validated == answer
    assert citations[0]['source_excerpt'] == original


@pytest.mark.parametrize('original,answer', [
    ('报名2026年11月16日至20日。', '报名2026年11月16日至21日。[1]'),
    ('报名2026年11月16日至20日。', '报名2026年12月16日至20日。[1]'),
    ('报名2026年12月16日至20日。', '报名2027年12月16日至20日。[1]'),
    ('报名2026年12月31日至2027年1月2日。', '报名2026年12月31日至2日。[1]'),
    ('报名2026年12月31日至2027年1月2日。每队2人。', '报名2026年12月31日至2日。[1]'),
    ('报名10月14日至25日。', '报名10月14日至26日。[1]'),
    ('活动2026年10月14日（周三）19:30。', '活动2026年10月14日19:31。[1]'),
    ('活动2026年10月14日（周三）19:30。', '活动2026年10月15日19:30。[1]'),
    ('报名2026年2月16日至28日。', '报名2026年2月16日至29日。[1]'),
])
def test_calendar_layout_does_not_create_new_range_endpoints_months_years_or_times(original, answer):
    with pytest.raises(AIError):
        validate_chat_answer(payload(answer, original), [{'id': 'a', 'number': 1, 'excerpt': original}])


def test_each_sentence_uses_only_the_sources_it_cites():
    refs = REFERENCES + [{'id': 'b', 'number': 2, 'excerpt': '名额300人。'}]
    data = {'answer': '名额300人。[1]另一项目有名额300人。[2]', 'citations': [
        {'id': 'a', 'source_excerpt': SOURCE}, {'id': 'b', 'source_excerpt': '名额300人。'},
    ]}
    with pytest.raises(AIError, match='数字'):
        validate_chat_answer(data, refs)


@pytest.mark.parametrize('source_id', ['unknown', 1, {}, []])
def test_invalid_source_ids_are_validation_errors(source_id):
    with pytest.raises(AIError):
        validate_chat_answer(payload('面向本科生。[1]', source_id=source_id), REFERENCES)


@pytest.mark.parametrize('data', [None, [], {}, {'answer': '', 'citations': []}, {'answer': '不知道', 'citations': []}])
def test_malformed_payloads_remain_validation_errors(data):
    with pytest.raises(AIError):
        validate_chat_answer(data, REFERENCES)
