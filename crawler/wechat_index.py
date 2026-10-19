"""从公开索引发现链接，文章账号、正文与发布日期仍以微信原文为准。"""
from urllib.parse import urlencode, urlsplit

if __package__:
    from .policy import within_publication_window
else:
    from policy import within_publication_window

BASE_URL = 'https://pkuknow.cn/thu'


def discover(client, config, since, limit):
    source_id = config['public_index_source']
    landing = BASE_URL + '?' + urlencode({'source': source_id, 'group': 'wechat'})
    # 正常访问公开页面取得访客会话，不调用登录接口、不处理验证码。
    client.request('GET', landing)
    links, seen = [], set()
    pages = max(1, min(20, int(config.get('index_pages', 5))))
    for page in range(1, pages + 1):
        response = client.request('GET', BASE_URL + '/api/notices', params={
            'source': source_id, 'group': 'wechat', 'category': '全部通知',
            'general': '1', 'view': 'list', 'page': page,
        }, headers={'Referer': landing})
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            raise ValueError('公开索引未返回文章列表')
        items = payload['items']
        for item in items:
            if not isinstance(item, dict) or item.get('source_name') != config['expected_account']:
                continue
            url = item.get('url', '')
            if urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'mp.weixin.qq.com':
                continue
            if url in seen or not within_publication_window(item, since):
                continue
            seen.add(url)
            # 不把索引中的标题/时间用作原文验证失败后的替代数据。
            links.append((url, '', None))
            if len(links) >= limit:
                return links
        size, total = payload.get('page_size', 30), payload.get('total', 0)
        if not items or (isinstance(size, int) and isinstance(total, int) and page * size >= total):
            break
        if all(isinstance(item, dict) and item.get('published_at') and item['published_at'][:10] < since for item in items):
            break
    return links
