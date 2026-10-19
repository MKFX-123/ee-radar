"""所有配置位于临时目录；模型请求使用替身，不调用真实服务。"""
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server.ai import AIConfig, AIError, ModelClient
from server.app import create_app
from server.model_config import ConfigError, ConfigStorageError, ModelSettings, validate_base_url


@pytest.fixture
def configured_client(tmp_path, monkeypatch):
    for name in ('EE_RADAR_AI_BASE_URL', 'EE_RADAR_AI_API_KEY', 'EE_RADAR_AI_MODEL'):
        monkeypatch.delenv(name, raising=False)
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'missing.json', model_config_path=tmp_path / 'model-config.json')
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        yield client, app, tmp_path


def configuration(**changes):
    return {'base_url': 'https://provider.example/v1', 'model': 'campus-model', 'api_key': 'unit-test-secret', **changes}


def test_save_permissions_and_never_return_secret(configured_client, caplog):
    client, app, path = configured_client
    saved = client.post('/api/model/config', json=configuration())
    assert saved.status_code == 200
    assert saved.json() == {'configured': True, 'base_url': 'https://provider.example/v1', 'model': 'campus-model', 'has_api_key': True, 'source': 'local'}
    file = path / 'model-config.json'
    assert file.stat().st_mode & 0o777 == 0o600
    assert 'unit-test-secret' not in saved.text
    assert 'unit-test-secret' not in client.get('/api/model/config').text
    assert 'unit-test-secret' not in client.get('/api/health').text
    assert 'unit-test-secret' not in caplog.text
    assert 'unit-test-secret' not in repr(app.state.model.config)
    assert b'unit-test-secret' not in (path / 'db.sqlite3').read_bytes()
    assert client.get('/api/model/config').headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('key', [None, '', '   '])
def test_blank_key_preserves_previous(configured_client, key):
    client, app, _ = configured_client
    client.post('/api/model/config', json=configuration())
    body = {'base_url': 'https://provider.example/api/v1', 'model': 'replacement'}
    if key is not None:
        body['api_key'] = key
    assert client.post('/api/model/config', json=body).status_code == 200
    assert app.state.model.config.api_key == 'unit-test-secret'
    assert app.state.model.config.model == 'replacement'


def test_changed_provider_requires_key(configured_client):
    client, app, _ = configured_client
    client.post('/api/model/config', json=configuration())
    for base in ('https://other.example/v1', 'https://provider.example:8443/v1'):
        assert client.post('/api/model/config', json={'base_url': base, 'model': 'replacement'}).status_code == 422
    assert app.state.model.config.base_url == 'https://provider.example/v1'


def test_clear_reverts_environment_without_modifying_it(tmp_path, monkeypatch):
    monkeypatch.setenv('EE_RADAR_AI_BASE_URL', 'https://environment.example/v1')
    monkeypatch.setenv('EE_RADAR_AI_MODEL', 'environment-model')
    monkeypatch.setenv('EE_RADAR_AI_API_KEY', 'environment-test-secret')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'missing.json', model_config_path=tmp_path / 'config.json')
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        assert client.get('/api/model/config').json()['source'] == 'environment'
        client.post('/api/model/config', json=configuration())
        assert app.state.model.config.model == 'campus-model'
        cleared = client.delete('/api/model/config')
        assert cleared.status_code == 200
        assert cleared.json()['source'] == 'environment'
        assert app.state.model.config.api_key == 'environment-test-secret'
        assert not (tmp_path / 'config.json').exists()
        assert os.environ['EE_RADAR_AI_API_KEY'] == 'environment-test-secret'
        assert client.post('/api/model/config/reset').status_code == 200


def test_missing_initial_key_cannot_save(configured_client):
    client, _, path = configured_client
    assert client.post('/api/model/config', json={'base_url': 'https://provider.example', 'model': 'model'}).status_code == 422
    assert not (path / 'model-config.json').exists()


def test_failed_atomic_save_keeps_old_file_and_snapshot(configured_client, monkeypatch):
    client, app, path = configured_client
    client.post('/api/model/config', json=configuration())
    previous = (path / 'model-config.json').read_bytes()
    def fail(*args):
        raise OSError('错误可能包含敏感值 unit-test-secret')
    monkeypatch.setattr('server.model_config.os.replace', fail)
    response = client.post('/api/model/config', json=configuration(model='new-model', api_key='new-secret'))
    assert response.status_code == 500
    assert 'secret' not in response.text
    assert app.state.model.config.model == 'campus-model'
    assert (path / 'model-config.json').read_bytes() == previous
    assert not list(path.glob('.model-config-*.tmp'))


def test_saved_config_reloads_on_new_instance(tmp_path):
    settings = ModelSettings(tmp_path / 'config.json', AIConfig())
    settings.save(**configuration())
    reloaded = ModelSettings(tmp_path / 'config.json', AIConfig())
    assert reloaded.snapshot().api_key == 'unit-test-secret'
    assert reloaded.public()['source'] == 'local'


@pytest.mark.parametrize('url', ['ftp://provider.example', 'http://provider.example', 'https://u:p@provider.example', 'https://provider.example?q=secret', 'https://provider.example#key', 'https://provider.example:99999', 'https://provider.example?'])
def test_invalid_provider_url_rejected(url):
    with pytest.raises(ConfigError):
        validate_base_url(url)


@pytest.mark.parametrize('url', ['http://127.0.0.1:11434/v1', 'http://localhost:8001/v1', 'http://[::1]:8080/v1', 'https://provider.example/v1'])
def test_supported_provider_urls(url):
    assert validate_base_url(url) == url


def test_validation_errors_do_not_echo_api_key(configured_client):
    client, _, _ = configured_client
    for body in (configuration(api_key=['DO-NOT-ECHO']), configuration(unexpected='DO-NOT-ECHO'), configuration(api_key='x' * 9000)):
        response = client.post('/api/model/config', json=body)
        assert response.status_code == 422
        assert 'DO-NOT-ECHO' not in response.text and 'x' * 20 not in response.text


@pytest.mark.parametrize('path,method', [('/api/model/config', 'get'), ('/api/model/config', 'post'), ('/api/model/config', 'delete'), ('/api/model/test', 'post'), ('/api/chat', 'post'), ('/api/items/a/analyze', 'post')])
def test_model_endpoints_reject_external_origin_and_cross_site(configured_client, path, method):
    client, _, _ = configured_client
    request = getattr(client, method)
    assert request(path, headers={'Origin': 'https://external.example'}).status_code == 403
    assert request(path, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_non_loopback_client_rejected(tmp_path):
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'missing.json', model_config_path=tmp_path / 'config.json')
    with TestClient(app, client=('192.168.1.4', 50000)) as client:
        assert client.get('/api/model/config').status_code == 403


def test_explicit_test_is_minimal_and_uses_current_snapshot(configured_client, monkeypatch):
    client, app, _ = configured_client
    requests = []
    def fake(self, config, body, **options):
        requests.append((config, body, options))
        return {'choices': [{'message': {'content': '连接成功'}}]}
    monkeypatch.setattr(ModelClient, '_request', fake)
    client.post('/api/model/config', json=configuration())
    assert requests == []
    assert client.post('/api/model/test').json() == {'success': True, 'message': '模型连接测试成功。'}
    config, body, options = requests[0]
    assert body['model'] == 'campus-model'
    assert body['messages'] == [{'role': 'user', 'content': '请只回复：连接成功'}]
    assert body['max_tokens'] == 16 and options['timeout'] <= 25
    assert 'unit-test-secret' not in json.dumps(body)
    client.post('/api/model/config', json=configuration(base_url='https://second.example/v1', api_key='second-secret', model='second-model'))
    client.post('/api/model/test')
    assert requests[-1][0] == AIConfig('https://second.example/v1', 'second-secret', 'second-model')


def test_request_snapshots_config_once_during_concurrent_change(tmp_path, monkeypatch):
    settings = ModelSettings(tmp_path / 'config.json', AIConfig())
    settings.save(**configuration())
    client = ModelClient(AIConfig(), config_provider=settings.snapshot)
    captured = []
    def fake(config, body, **options):
        settings.save('https://second.example/v1', 'second-model', 'second-secret')
        captured.append((config, body))
        return {'choices': [{'message': {'content': '成功'}}]}
    monkeypatch.setattr(client, '_request', fake)
    client.test_connection()
    assert captured[0][0].api_key == 'unit-test-secret'
    assert captured[0][0].base_url == 'https://provider.example/v1'
    assert captured[0][1]['model'] == 'campus-model'
    assert client.config.model == 'second-model'


def test_http_failure_does_not_return_provider_body_or_key(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def stream(self, *args, **kwargs):
            raise httpx.ConnectError('unit-test-secret upstream private response')
    monkeypatch.setattr('server.ai.httpx.Client', FakeClient)
    client = ModelClient(AIConfig('https://provider.example', 'unit-test-secret', 'model'))
    with pytest.raises(AIError) as error:
        client.test_connection()
    assert 'secret' not in str(error.value)


def test_404_explains_endpoint_without_leaking_provider_body(monkeypatch):
    constructor = httpx.Client
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(404, json={'error': 'unit-test-secret upstream private response'})

    monkeypatch.setattr('server.ai.httpx.Client', lambda **kwargs: constructor(transport=httpx.MockTransport(handler), **kwargs))
    client = ModelClient(AIConfig('https://provider.example/prefix', 'unit-test-secret', 'model'))
    with pytest.raises(AIError) as error:
        client.test_connection()
    assert requests == ['/prefix/chat/completions']
    assert 'HTTP 404' in str(error.value)
    assert '/chat/completions' in str(error.value) and '模型 ID' in str(error.value)
    assert 'unit-test-secret' not in str(error.value) and 'private' not in str(error.value)


def test_oversized_test_response_rejected(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def raise_for_status(self):
            pass
        def iter_bytes(self):
            yield b'x' * 32769
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False
            assert kwargs['trust_env'] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def stream(self, *args, **kwargs):
            return FakeResponse()
    monkeypatch.setattr('server.ai.httpx.Client', FakeClient)
    client = ModelClient(AIConfig('https://provider.example', 'unit-test-secret', 'model'))
    with pytest.raises(AIError, match='超过允许大小'):
        client.test_connection()


def test_model_echo_of_key_never_enters_analysis_payload(monkeypatch):
    client = ModelClient(AIConfig('https://provider.example', 'unit-test-secret', 'model'))
    monkeypatch.setattr(client, '_request', lambda *args, **kwargs: {'choices': [{'message': {'content': '{"summary":"unit-test-secret"}'}}]})
    with pytest.raises(AIError) as error:
        client.complete('仅作测试', {'content': '普通公开通知'})
    assert 'unit-test-secret' not in str(error.value)


@pytest.mark.parametrize('payload', [
    {'answer': 'unit-test-secret'},
    {'answer': [{'details': ['前缀 unit-test-secret 后缀']}]},
    {'unit-test-secret': '普通文本'},
    {'answer': [{'unit-test-secret': ['普通文本']}]},
])
def test_unicode_escaped_key_never_enters_analysis_payload(monkeypatch, payload):
    secret = 'unit-test-secret'
    escaped = ''.join(f'\\u{ord(character):04x}' for character in secret)
    content = json.dumps(payload, ensure_ascii=False).replace(secret, escaped)
    assert secret not in content
    client = ModelClient(AIConfig('https://provider.example', secret, 'model'))
    monkeypatch.setattr(client, '_request', lambda *args, **kwargs: {'choices': [{'message': {'content': content}}]})
    with pytest.raises(AIError, match='敏感配置') as error:
        client.complete('仅作测试', {'content': '普通公开通知'})
    assert secret not in str(error.value)


def test_unicode_public_text_is_accepted_after_parsing(monkeypatch):
    payload = {'answer': [{'通知': '欢迎参加公开讲座', 'count': 1, 'deadline': None, 'open': True}]}
    content = json.dumps(payload, ensure_ascii=True)
    client = ModelClient(AIConfig('https://provider.example', 'unit-test-secret', 'model'))
    monkeypatch.setattr(client, '_request', lambda *args, **kwargs: {'choices': [{'message': {'content': content}}]})
    assert client.complete('仅作测试', {'content': '普通公开通知'}) == payload
