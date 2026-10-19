"""小范围公开数据源探测，只保存验证结果，不保存会话凭据。"""
import json
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
session = requests.Session()
session.headers['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'
urls = [
    ('ee', 'https://www.ee.tsinghua.edu.cn/info/1076/4651.htm'),
    ('ee_home', 'https://www.ee.tsinghua.edu.cn/'),
    ('sports', 'https://www.thsports.tsinghua.edu.cn/'),
    ('info', 'https://info.tsinghua.edu.cn/f/info/portal_fg/common/yyfwsearch?searchParam=%E5%85%AC%E7%A4%BA'),
    ('wechat', 'https://mp.weixin.qq.com/s/2Zq8CYQknLCN-S7Z7lbnLg'),
]
results = []
for name, url in urls:
    try:
        response = session.get(url, timeout=25)
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        body = soup.select_one('.v_news_content') or soup.select_one('#js_content')
        result = {
            'name': name, 'url': url, 'status': response.status_code,
            'final_host': urlparse(response.url).netloc,
            'title': soup.title.get_text(strip=True) if soup.title else None,
            'body_chars': len(body.get_text(strip=True)) if body else 0,
            'cookie_names': list(session.cookies.keys()),
            'links': [{'title': a.get_text(' ', strip=True), 'url': urljoin(url, a['href'])}
                      for a in soup.select('a[href]') if a.get_text(strip=True)][:35],
        }
        if name == 'info' and response.status_code == 200:
            token = next((c.value for c in session.cookies if c.name == 'XSRF-TOKEN'), None)
            if token:
                params = {'params': {}, 'filterParams': {}, 'orderMap': {}, 'departmentSearchFlag': False, 'dwnm': '', 'currentPage': 1}
                endpoint = 'https://info.tsinghua.edu.cn/b/xnzx/search/info/xxfb_fg/teacher/getMobilePageList'
                api = session.post(endpoint, params={'_csrf': token}, data={'esParamClass': json.dumps(params)}, headers={'Referer': url, 'X-Requested-With': 'XMLHttpRequest'}, timeout=25)
                result['api_http'] = api.status_code
                try:
                    payload = api.json()
                    obj = payload.get('object') or {}
                    result['api_result'] = payload.get('result')
                    result['total'] = obj.get('total')
                    result['sample_titles'] = [x.get('bt') for x in obj.get('resultsList', [])[:3]]
                    result['sample_fields'] = list(obj.get('resultsList', [{}])[0].keys())
                    if payload.get('result') == 'success':
                        (DATA / 'info_first_page.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
                except ValueError:
                    result['api_result'] = '非 JSON 响应'
        print(json.dumps(result, ensure_ascii=False), flush=True)
        results.append(result)
    except requests.RequestException as error:
        result = {'name': name, 'url': url, 'error': type(error).__name__}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    time.sleep(1)
(DATA / 'probe_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
