"""取消信号的本地替身测试，不访问模型服务。"""
import json
from threading import Event

import httpx
import pytest

from server.ai import AIConfig, AIError, ModelClient


def model():
    return ModelClient(AIConfig('https://example.invalid/v1', 'test-secret', 'test-model'))


def assert_cancelled(error):
    assert error.value.code == 'request_cancelled'
    assert error.value.retryable is False
    assert 'test-secret' not in str(error.value)


@pytest.mark.parametrize('method', ['complete_chat', 'complete', '_request'])
def test_cancelled_before_call_never_constructs_http_client(monkeypatch, method):
    def unexpected(**kwargs):
        pytest.fail('已取消请求不应创建网络客户端')
    monkeypatch.setattr('server.ai.httpx.Client', unexpected)
    event = Event()
    event.set()
    client = model()
    args = (client.config, {}) if method == '_request' else ('测试', {})
    with pytest.raises(AIError) as error:
        getattr(client, method)(*args, cancel_event=event)
    assert_cancelled(error)


def test_optional_cancellation_preserves_complete_monkeypatch_signature(monkeypatch):
    client = model()
    calls = []
    def complete(system, payload, *, timeout, max_tokens):
        calls.append((timeout, max_tokens))
        return {'answer': '成功'}
    monkeypatch.setattr(client, 'complete', complete)
    assert client.complete_chat('测试', {}) == {'answer': '成功'}
    assert calls == [(55, 2800)]


def test_optional_cancellation_preserves_request_monkeypatch_signature(monkeypatch):
    client = model()
    def request(config, body, *, timeout):
        return {'choices': [{'message': {'content': json.dumps({'answer': '成功'})}}]}
    monkeypatch.setattr(client, '_request', request)
    assert client.complete('测试', {}) == {'answer': '成功'}


def test_cancel_event_reaches_request_and_discards_returned_payload(monkeypatch):
    client, event = model(), Event()
    def request(config, body, *, timeout, cancel_event):
        assert cancel_event is event
        event.set()
        return {'choices': [{'message': {'content': json.dumps({'answer': '不应返回'})}}]}
    monkeypatch.setattr(client, '_request', request)
    with pytest.raises(AIError) as error:
        client.complete_chat('测试', {}, cancel_event=event)
    assert_cancelled(error)


def test_cancellation_after_response_skips_body_and_http_error(monkeypatch):
    client, event = model(), Event()
    constructor = httpx.Client
    def handler(request):
        event.set()
        return httpx.Response(503, content=b'not-json')
    monkeypatch.setattr('server.ai.httpx.Client', lambda **kwargs: constructor(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(AIError) as error:
        client.complete_chat('测试', {}, cancel_event=event)
    assert_cancelled(error)


@pytest.mark.parametrize('cancel_when', ['chunk', 'end'])
def test_cancellation_at_chunk_or_before_parse_closes_stream(monkeypatch, cancel_when):
    client, event = model(), Event()
    constructor = httpx.Client
    state = {'closed': False, 'yielded': 0}
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            state['yielded'] += 1
            if cancel_when == 'chunk':
                event.set()
            yield json.dumps({'choices': [{'message': {'content': json.dumps({'answer': '成功'})}}]}).encode()
            if cancel_when == 'end':
                event.set()
        def close(self):
            state['closed'] = True
    monkeypatch.setattr('server.ai.httpx.Client', lambda **kwargs: constructor(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream())), **kwargs))
    with pytest.raises(AIError) as error:
        client.complete_chat('测试', {}, cancel_event=event)
    assert_cancelled(error)
    assert state == {'closed': True, 'yielded': 1}
