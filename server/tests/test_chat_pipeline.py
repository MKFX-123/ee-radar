"""问答可靠性回归：只用替身服务，不访问真实模型或用户数据。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.ai import AIConfig, AIError, ModelClient
from server.chat import CHAT_ATTEMPT_SECONDS, CHAT_TOTAL_SECONDS, generate_answer, source_references
from server.app import create_app
from server.domain import now


REFS = [{'number': 1, 'id': 'a', 'title': '通知', 'excerpt': '每队4人，至少1名女生。'}]
VALID = {'statements': [{'text': '每队4人，至少1名女生。', 'evidence_ids': ['s1p1']}]}


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete_chat(self, system, payload, **options):
        self.calls.append((payload, options))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def test_invalid_citations_get_one_fully_validated_correction():
    model = ScriptedModel([{'answer': '错误格式', 'citations': []}, VALID])
    result = generate_answer(model, '参赛要求', REFS, [], {})
    assert result['mode'] == 'ai' and result['model_attempts'] == 2
    assert 'format_feedback' in model.calls[1][0]
    assert 0 < model.calls[1][1]['timeout'] <= CHAT_ATTEMPT_SECONDS


def test_two_invalid_answers_keep_retrieval_and_report_reason():
    model = ScriptedModel([{'answer': '错误格式'}, {'answer': '仍然错误'}])
    result = generate_answer(model, '参赛要求', REFS, [], {})
    assert 'mode' not in result and result['fallback_reason'] == 'model_error'
    assert result['model_error_code'] == 'citation_validation'
    assert result['model_attempts'] == 2


@pytest.mark.parametrize('code', ['authentication_failed', 'rate_limited', 'endpoint_not_found', 'upstream_timeout', 'sensitive_response'])
def test_non_retryable_failures_never_duplicate_requests(code):
    model = ScriptedModel([AIError('安全错误说明', code=code), VALID])
    result = generate_answer(model, '问题', REFS, [], {})
    assert len(model.calls) == 1 and result['model_error_code'] == code


def test_transient_failure_retries_once():
    model = ScriptedModel([AIError('连接失败', code='upstream_connection', retryable=True), VALID])
    result = generate_answer(model, '问题', REFS, [], {})
    assert result['mode'] == 'ai' and len(model.calls) == 2


def test_retry_stops_when_total_budget_is_almost_spent(monkeypatch):
    ticks = iter([0, 0, CHAT_TOTAL_SECONDS - 8, CHAT_TOTAL_SECONDS - 8])
    monkeypatch.setattr('server.chat.time.monotonic', lambda: next(ticks))
    model = ScriptedModel([AIError('暂时失败', code='upstream_connection', retryable=True), VALID])
    result = generate_answer(model, '问题', REFS, [], {})
    assert len(model.calls) == 1 and result['model_attempts'] == 1


def test_relevant_passage_beyond_article_introduction_is_available():
    item = {'id': 'a', 'title': '新生竞赛', 'content': '项目介绍。' * 150 + '\n报名方式：到党课小组长处报名。\n每队4人。', 'source_url': 'https://example.invalid/a', 'source_name': '测试来源'}
    refs = source_references([item], '怎么报名', {'keywords': ['竞赛']})
    assert '报名方式：到党课小组长处报名。' in refs[0]['excerpt']
    assert len(refs[0]['excerpt']) <= 1900 + len(item['title']) + 1
    assert refs[0]['excerpt'].startswith(item['title'] + '\n')


@pytest.mark.parametrize('content,reason,code', [
    ('', 'stop', 'empty_response'),
    (None, 'stop', 'empty_response'),
    ('{"answer":', 'length', 'output_truncated'),
    ('not-json', 'stop', 'invalid_json'),
])
def test_model_empty_truncated_and_invalid_outputs_are_distinct(monkeypatch, content, reason, code):
    model = ModelClient(AIConfig('https://example.invalid/v1', 'test-secret', 'model'))
    monkeypatch.setattr(model, '_request', lambda *args, **kwargs: {'choices': [{'finish_reason': reason, 'message': {'content': content}}]})
    with pytest.raises(AIError) as error:
        model.complete_chat('测试', {})
    assert error.value.code == code


def test_chat_budget_and_output_limit_are_passed_to_model(monkeypatch):
    model = ModelClient(AIConfig('https://example.invalid/v1', 'test-secret', 'model'))
    requests = []
    def request(config, body, **options):
        requests.append((body, options))
        return {'choices': [{'message': {'content': json.dumps(VALID)}}]}
    monkeypatch.setattr(model, '_request', request)
    assert model.complete_chat('测试', {}, timeout=24) == VALID
    assert requests[0][0]['stream'] is False
    assert requests[0][0]['max_tokens'] == 2800
    assert requests[0][1]['timeout'] == 24


def test_http_read_timeout_has_safe_distinct_error(monkeypatch):
    constructor = httpx.Client
    def handler(request):
        raise httpx.ReadTimeout('test-secret upstream detail')
    monkeypatch.setattr('server.ai.httpx.Client', lambda **kwargs: constructor(transport=httpx.MockTransport(handler), **kwargs))
    model = ModelClient(AIConfig('https://example.invalid/v1', 'test-secret', 'model'))
    with pytest.raises(AIError) as error:
        model.complete_chat('测试', {})
    assert error.value.code == 'upstream_timeout'
    assert 'test-secret' not in str(error.value)


@pytest.mark.parametrize('response', [{'choices': [None]}, {'choices': ['text']}, {'choices': [{'message': None}]}, {'choices': {}}, None])
def test_invalid_upstream_message_structure_never_escapes_as_server_error(monkeypatch, response):
    model = ModelClient(AIConfig('https://example.invalid/v1', 'test-secret', 'model'))
    monkeypatch.setattr(model, '_request', lambda *args, **kwargs: response)
    with pytest.raises(AIError) as error:
        model.complete_chat('测试', {})
    assert error.value.code == 'invalid_response'


def test_quote_cannot_bridge_an_omitted_source_passage():
    refs = [{'number': 1, 'id': 'a', 'excerpt': '欢迎参加。\n（中间原文已省略）\n资料详见原文。'}]
    refs[0]['evidence'] = [{'id': 's1p1', 'text': refs[0]['excerpt']}]
    value = {'statements': [{'text': '欢迎参加。', 'evidence_ids': ['s1p1']}]}
    result = generate_answer(ScriptedModel([value, value]), '问题', refs, [], {})
    assert result['fallback_reason'] == 'model_error' and result['model_error_code'] == 'citation_validation'


def test_chat_total_deadline_returns_cached_evidence_instead_of_http_error(tmp_path, monkeypatch):
    import asyncio
    async def expired(awaitable, timeout):
        assert timeout == CHAT_TOTAL_SECONDS
        awaitable.close()
        raise asyncio.TimeoutError()
    monkeypatch.setattr('server.app.asyncio.wait_for', expired)
    model = ScriptedModel([VALID])
    model.config = AIConfig('https://example.invalid/v1', 'test-secret', 'model')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'missing.json', model)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        app.state.store.upsert({'id': 'a', 'source_id': 'info', 'source': '测试', 'source_url': 'https://example.invalid/a', 'title': '机器人竞赛', 'content': '每队4人，至少1名女生。', 'published_at': now()})
        response = client.post('/api/chat', json={'message': '机器人竞赛怎么报名'})
        result = response.json()
    assert response.status_code == 200 and result['mode'] == 'retrieval'
    assert result['answer'] and result['references']
    assert result['model_error_code'] == 'upstream_timeout'
    assert result['fallback_reason'] == 'model_error'


def test_evidence_ids_expand_to_original_contiguous_quotes():
    refs = source_references([{'id': 'a', 'title': '参赛通知', 'source_url': 'https://example.invalid/a', 'source_name': '测试', 'content': '每队4人，至少1名女生。'}], '参赛要求', {})
    evidence_id = next(part['id'] for part in refs[0]['evidence'] if part.get('field') == 'content')
    model = ScriptedModel([{'statements': [{'text': '每队4人，至少1名女生。', 'evidence_ids': [evidence_id]}]}])
    result = generate_answer(model, '参赛要求', refs, [], {})
    assert result['mode'] == 'ai'
    assert result['citations'][0]['source_excerpt'] in refs[0]['excerpt']
    assert 'excerpt' not in model.calls[0][0]['sources'][0]
    assert model.calls[0][0]['sources'][0]['evidence']


@pytest.mark.parametrize('evidence_ids', [['invented'], ['s2p1'], [], 's1p1', [None]])
def test_unknown_or_malformed_evidence_ids_never_pass(evidence_ids):
    refs = source_references([{'id': 'a', 'title': '通知', 'source_url': 'https://example.invalid/a', 'source_name': '测试', 'content': '每队4人。'}], '参赛要求', {})
    value = {'statements': [{'text': '每队4人。', 'evidence_ids': evidence_ids}]}
    result = generate_answer(ScriptedModel([value, value]), '参赛要求', refs, [], {})
    assert result['model_error_code'] == 'citation_validation'


def test_short_notice_keeps_final_deadline_role_in_evidence():
    content = '申请条件说明。' * 70 + '\n各院系须在10月21日前报送初评结果，学生个人时间由院系安排。'
    refs = source_references([{'id': 'a', 'title': '奖学金通知', 'source_url': 'https://example.invalid/a', 'source_name': '测试', 'content': content}], '有哪些奖学金快截止', {'keywords': ['奖学金'], 'time_kind': 'deadline'})
    assert content in refs[0]['excerpt']
    assert '学生个人时间由院系安排' in ''.join(part['text'] for part in refs[0]['evidence'])


def test_scholarship_unknown_deadline_still_invokes_model_and_discloses_gap(tmp_path):
    class ScholarshipModel:
        config = AIConfig('https://example.invalid/v1', 'test-secret', 'model')
        calls = []
        def complete_chat(self, system, payload, **options):
            self.calls.append(payload)
            ref = payload['sources'][0]
            return {'statements': [{'text': '院系初评结果须在10月21日前报送；学生个人申请时间由院系安排，不能把它当作个人截止。', 'evidence_ids': [part['id'] for part in ref['evidence']]}]}
    model = ScholarshipModel()
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'missing.json', model)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        app.state.store.upsert({'id': 'scholarship', 'source_id': 'info', 'source_url': 'https://example.invalid/scholarship', 'title': '国家励志奖学金评选通知', 'content': '学生向院系提交书面申请。各院系10\n月\n21\n日前报送初评结果，学生个人申请时间由院系安排。', 'published_at': now()})
        result = client.post('/api/chat', json={'message': '有哪些奖学金快截止？'}).json()
    assert result['mode'] == 'ai' and not result['no_results']
    assert len(model.calls) == 1
    assert model.calls[0]['retrieval_context']['constraint_status'] == 'evidence_only'
    assert result['search_scope']['strict_match_count'] == 0
    assert '不能' in result['answer'] and result['citations']


def test_single_report_summary_receives_more_than_intro_with_coverage_label():
    content = '研究背景。' * 80 + '主要观点：需要关注真实场景和数据质量。' + '研究细节。' * 80 + '总结：完善验证体系。'
    refs = source_references([{'id': 'a', 'title': '具身智能发展报告', 'source_url': 'https://example.invalid/a', 'source_name': '测试', 'content': content}], '总结具身智能发展报告主要观点', {'intent': 'overview', 'keywords': ['具身智能']})
    assert content in refs[0]['excerpt']
    assert refs[0]['content_coverage'] == '完整收录正文'
    assert all(block['text'] in refs[0]['excerpt'] for block in refs[0]['evidence'])


def test_overview_never_claims_exhaustive_coverage_of_truncated_candidates():
    model = ScriptedModel([VALID])
    result = generate_answer(model, '概括近期通知', [dict(REFS[0])], [], {'intent': 'overview', 'truncated': True})
    assert result['mode'] == 'ai'
    assert '并非全部通知的完整清单' in result['answer']
    assert model.calls[0][0]['retrieval_context']['truncated'] is True


def test_title_and_body_evidence_remain_separate_original_fields():
    item = {'id': 'a', 'title': '通知标题', 'source_url': 'https://example.invalid/a', 'source_name': '测试', 'content': '报名截止时间另行通知。'}
    refs = source_references([item], '报名截止', {})
    assert refs[0]['evidence'][0]['field'] == 'title'
    for block in refs[0]['evidence']:
        assert block['text'] in item[block['field']]


def test_structured_claims_render_citations_per_sentence_and_verify_each_claim():
    refs = source_references([{'id': 'a', 'title': '报名通知', 'source_url': 'https://example.invalid/a', 'source_name': '测试', 'content': '每队4人。至少1名女生。'}], '要求', {})
    key = next(part['id'] for part in refs[0]['evidence'] if part['field'] == 'content')
    model = ScriptedModel([{'statements': [{'text': '每队4人。至少1名女生。', 'evidence_ids': [key]}]}])
    result = generate_answer(model, '要求', refs, [], {})
    assert result['mode'] == 'ai'
    assert '每队4人[1]。至少1名女生[1]。' in result['answer']


def test_structured_claim_cannot_borrow_number_from_another_claims_evidence():
    refs = [{'id': 'a', 'number': 1, 'title': '通知', 'excerpt': '每队4人。材料2份。', 'evidence': [{'id': 's1p1', 'text': '每队4人。'}, {'id': 's1p2', 'text': '材料2份。'}]}]
    value = {'statements': [{'text': '每队4人', 'evidence_ids': ['s1p1']}, {'text': '材料4份', 'evidence_ids': ['s1p2']}]}
    result = generate_answer(ScriptedModel([value, value]), '要求', refs, [], {})
    assert result['mode'] == 'ai' and result['answer_partial'] is True
    assert '材料4份' not in result['answer'] and result['answer'].count('每队4人') == 1


@pytest.mark.parametrize('statement', [{'text':'','evidence_ids':['s1p1']}, {'text':'每队4人','evidence_ids':['missing']}, {'text':'每队9人','evidence_ids':['s1p1']}])
def test_structured_bad_claims_keep_original_fallback(statement):
    result = generate_answer(ScriptedModel([{'statements':[statement]},{'statements':[statement]}]), '要求', [dict(REFS[0])], [], {})
    assert result['model_error_code'] == 'citation_validation'


def test_one_bad_claim_does_not_discard_other_verified_claims():
    refs = [{'id': 'a', 'number': 1, 'title': '通知', 'excerpt': '每队4人。材料2份。', 'evidence': [{'id': 's1p1', 'text': '每队4人。'}, {'id': 's1p2', 'text': '材料2份。'}]}]
    good = {'text': '每队4人', 'evidence_ids': ['s1p1']}
    bad = {'text': '材料9份', 'evidence_ids': ['s1p2']}
    model = ScriptedModel([{'statements': [good, bad]}, {'statements': [bad]}])
    result = generate_answer(model, '要求', refs, [], {})
    assert result['mode'] == 'ai' and result['answer_partial'] is True
    assert '每队4人' in result['answer'] and '9份' not in result['answer']
    assert '部分细节尚未完成原文核验' in result['answer']
    assert len(model.calls[1][0]['repair_only']) == 1


def test_repaired_claim_combines_with_saved_claim_without_duplicates():
    refs = [{'id': 'a', 'number': 1, 'title': '通知', 'excerpt': '每队4人。材料2份。', 'evidence': [{'id': 's1p1', 'text': '每队4人。'}, {'id': 's1p2', 'text': '材料2份。'}]}]
    good = {'text': '每队4人', 'evidence_ids': ['s1p1']}
    bad = {'text': '材料9份', 'evidence_ids': ['s1p2']}
    fixed = {'id': 'c2', 'text': '材料2份', 'evidence_ids': ['s1p2']}
    result = generate_answer(ScriptedModel([{'statements': [good, bad]}, {'statements': [fixed]}]), '要求', refs, [], {})
    assert result['mode'] == 'ai' and result['answer_partial'] is False
    assert result['answer'].count('每队4人') == 1 and '材料2份' in result['answer']


def test_legacy_answer_cannot_borrow_another_claims_numbers():
    refs = [{'id': 'a', 'number': 1, 'excerpt': '每队4人。材料2份。'}]
    legacy = {'answer': '每队4人。[1]材料4份。[1]', 'citations': [{'id': 'a', 'source_excerpt': '每队4人。'}, {'id': 'a', 'source_excerpt': '材料2份。'}]}
    result = generate_answer(ScriptedModel([legacy, legacy]), '参赛要求', refs, [], {})
    assert result['model_error_code'] == 'citation_validation'
    assert result['model_attempts'] == 2 and 'answer' not in result


@pytest.mark.parametrize('legacy', [
    {'answer': '每队4人。[1]', 'citations': [{'id': 'a', 'source_excerpt': '每队4人。'}]},
    {'answer': '每队4人。[1]', 'citations': [{'id': 'a', 'evidence_ids': ['s1p1']}]},
])
def test_legacy_protocol_must_retry_with_statements(legacy):
    result = generate_answer(ScriptedModel([legacy, VALID]), '参赛要求', [dict(REFS[0])], [], {})
    assert result['mode'] == 'ai' and result['model_attempts'] == 2
    assert result['answer_partial'] is False


def repair_fixture():
    refs = [{'id': 'a', 'number': 1, 'title': '通知', 'excerpt': '每队4人。材料2份。需缴费3元。', 'evidence': [
        {'id': 's1p1', 'text': '每队4人。'}, {'id': 's1p2', 'text': '材料2份。'}, {'id': 's1p3', 'text': '需缴费3元。'},
    ]}]
    first = {'statements': [
        {'text': '每队4人', 'evidence_ids': ['s1p1']},
        {'text': '材料9份', 'evidence_ids': ['s1p2']},
        {'text': '需缴费8元', 'evidence_ids': ['s1p3']},
    ]}
    return refs, first


@pytest.mark.parametrize('repairs', [
    [{'text': '材料2份', 'evidence_ids': ['s1p2']}],
    [{'id': 'c2', 'text': '材料2份', 'evidence_ids': ['s1p2']}],
    [{'id': 'c1', 'text': '每队4人', 'evidence_ids': ['s1p1']}],
    [{'id': 'c2', 'text': '每队4人', 'evidence_ids': ['s1p1']}, {'id': 'c3', 'text': '每队4人', 'evidence_ids': ['s1p1']}],
    [{'id': 'c2', 'text': '材料2份', 'evidence_ids': ['s1p2']}, {'id': 'c2', 'text': '需缴费3元', 'evidence_ids': ['s1p3']}],
])
def test_missing_duplicate_or_repeated_repairs_remain_partial(repairs):
    refs, first = repair_fixture()
    model = ScriptedModel([first, {'statements': repairs}])
    result = generate_answer(model, '参赛要求', refs, [], {})
    assert result['mode'] == 'ai' and result['answer_partial'] is True
    assert result['answer'].count('每队4人') == 1
    assert '9份' not in result['answer'] and '8元' not in result['answer']
    assert {entry['id'] for entry in model.calls[1][0]['repair_only']} == {'c2', 'c3'}


def test_all_failed_ids_must_be_repaired_before_marking_complete():
    refs, first = repair_fixture()
    repairs = [{'id': 'c3', 'text': '需缴费3元', 'evidence_ids': ['s1p3']}, {'id': 'c2', 'text': '材料2份', 'evidence_ids': ['s1p2']}]
    result = generate_answer(ScriptedModel([first, {'statements': repairs}]), '要求', refs, [], {})
    assert result['answer_partial'] is False
    assert result['answer'].index('每队4人') < result['answer'].index('材料2份') < result['answer'].index('需缴费3元')


@pytest.mark.parametrize('registration,scope', [
    ('unknown', {}), ('closed', {}), ('open', {'constraint_status': 'evidence_only'}),
])
@pytest.mark.parametrize('claim', [
    '目前仍可报名', '现在可以申请', '报名仍然开放', '仍在接受申请', '尚未截止',
    '所有本科生均可申请', '任何人都具备申请资格', '申请没有任何资格限制',
    '无法确认，但现在可以申请', '是否可以申请尚不清楚，不过所有本科生均可申请',
])
def test_unverified_availability_and_universal_eligibility_are_rejected(registration, scope, claim):
    refs = source_references([{'id': 'a', 'title': '申请通知', 'source_name': '测试', 'source_url': 'https://example.invalid/a', 'content': '个人申请时间由院系另行安排。', 'registration': registration}], '申请条件', scope)
    value = {'statements': [{'text': claim, 'evidence_ids': ['s1p2']}]}
    result = generate_answer(ScriptedModel([value, value]), '申请条件', refs, [], scope)
    assert result['model_error_code'] == 'citation_validation' and 'answer' not in result


@pytest.mark.parametrize('claim', [
    '通知面向本科生，申请人须提交材料',
    '申请人应填写申请表，由院系另行公布个人申请时间',
    '原文没有说明个人报名截止时间，无法确认仍可报名',
    '不能据此认定所有本科生均可申请',
    '是否仍可报名需核实',
    '原文介绍当时可通过院系报名，当前是否开放仍需核实',
])
def test_eligibility_requirements_history_and_uncertainty_are_preserved(claim):
    refs = source_references([{'id': 'a', 'title': '申请通知', 'source_name': '测试', 'source_url': 'https://example.invalid/a', 'content': '通知面向本科生，申请人须填写申请表、提交材料。院系另行公布个人申请时间。', 'registration': 'unknown'}], '申请条件', {})
    result = generate_answer(ScriptedModel([{'statements': [{'text': claim, 'evidence_ids': ['s1p2']}]}]), '申请条件', refs, [], {'constraint_status': 'evidence_only'})
    assert result['mode'] == 'ai' and result['answer_partial'] is False
    assert claim.split('，')[0] in result['answer']


def test_confirmed_open_source_can_describe_current_registration():
    refs = source_references([{'id': 'a', 'title': '报名通知', 'source_name': '测试', 'source_url': 'https://example.invalid/a', 'content': '目前接受报名。', 'registration': 'open'}], '报名', {})
    result = generate_answer(ScriptedModel([{'statements': [{'text': '目前可以报名', 'evidence_ids': ['s1p2']}]}]), '报名', refs, [], {'constraint_status': 'matched'})
    assert result['mode'] == 'ai' and result['answer_partial'] is False


def test_progress_callback_publishes_verified_partial_before_retry():
    refs, first = repair_fixture()
    snapshots = []
    class Model(ScriptedModel):
        def complete_chat(self, *args, **options):
            if self.calls:
                assert len(snapshots) == 1
                assert snapshots[0]['answer_partial'] is True
                assert '部分细节尚未完成原文核验' in snapshots[0]['answer']
                assert '尚未确认有严格满足' in snapshots[0]['answer']
            return super().complete_chat(*args, **options)
    result = generate_answer(Model([first, AIError('超时', code='upstream_timeout')]), '要求', refs, [], {'constraint_status': 'evidence_only'}, progress_callback=snapshots.append)
    assert result['answer_partial'] is True and snapshots[0]['citations']
    assert '9份' not in snapshots[0]['answer'] and '8元' not in snapshots[0]['answer']


def test_cancellation_before_first_request_never_calls_model():
    from threading import Event
    event = Event()
    event.set()
    model = ScriptedModel([])
    result = generate_answer(model, '要求', [dict(REFS[0])], [], {}, cancel_event=event)
    assert model.calls == [] and result['model_attempts'] == 0
    assert result['model_error_code'] == 'request_cancelled'


def test_cancellation_after_progress_keeps_partial_without_retry():
    from threading import Event
    event, snapshots = Event(), []
    refs, first = repair_fixture()
    model = ScriptedModel([first])
    def publish(result):
        snapshots.append(result)
        event.set()
    result = generate_answer(model, '要求', refs, [], {}, progress_callback=publish, cancel_event=event)
    assert len(model.calls) == 1 and model.calls[0][1]['cancel_event'] is event
    assert len(snapshots) == 1 and result['answer_partial'] is True
    assert '每队4人' in result['answer'] and '9份' not in result['answer']


def test_none_cancel_event_is_not_forwarded_to_existing_model_signature():
    class ExistingModel:
        def complete_chat(self, system, payload, *, timeout):
            return VALID
    assert generate_answer(ExistingModel(), '要求', [dict(REFS[0])], [], {})['mode'] == 'ai'
