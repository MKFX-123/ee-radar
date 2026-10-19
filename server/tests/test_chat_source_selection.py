"""来源摘录优先保留活动信息，所有证据仍是连续的真实原文。"""

from server.chat import source_references


def article(item_id, content, **changes):
    return {'id': item_id, 'title': '前沿学术讲座', 'content': content,
            'source_url': 'https://example.invalid/' + item_id, 'source_name': '测试来源', **changes}


def long_lecture():
    introduction = '学术讲座介绍与研究背景。' * 70
    middle = ''.join(('研究挑战与发展结果，申请报名条件和材料要求。' + '专家研究背景介绍。' * 40) for _ in range(5))
    ending = ('活动信息\nInformation\n活动时间\nTime\n2026年10月18日（周日）16:00—18:00\n'
              'Sunday, October 18, 2026, 4:00–6:00 p.m.\n活动地点\nLocation\n清华大学建华楼LG1-21\n'
              'Room LG1-21, Jianhua Building\n活动形式\n现场讲座与互动对话\n观众报名方式\nRegistration\n'
              '扫描下方二维码，进入水木汇报名后加入微信群。活动名额有限，先到先得！\n')
    return introduction + middle + ending + '主办单位及嘉宾简介。' * 100, ending


def test_tail_activity_time_location_and_registration_survive_multi_source_budget():
    content, ending = long_lecture()
    items = [article('lecture', content)] + [article(str(index), '其他通知。') for index in range(3)]
    refs = source_references(items, '本周有什么学术讲座？', {'intent': 'overview', 'time_kind': 'event', 'keywords': ['讲座']})
    excerpt = refs[0]['excerpt']
    assert '2026年10月18日（周日）16:00—18:00' in excerpt
    assert '清华大学建华楼LG1-21' in excerpt
    assert '扫描下方二维码，进入水木汇报名后加入微信群。' in excerpt
    assert len(excerpt) <= 1900 + len(items[0]['title']) + 1
    assert refs[0]['content_coverage'] == '相关原文节选，未提供全文'


def test_existing_event_evidence_and_tail_fields_are_both_available():
    content, ending = long_lecture()
    evidence = '分会场安排：10月19日9:00，电子馆报告厅。'
    content = content[:900] + evidence + content[900:]
    item = article('lecture', content, events=[{'event_type': 'event', 'source_excerpt': evidence}])
    refs = source_references([item, article('b', '其他通知。'), article('c', '其他通知。')], '下周讲座在哪里？', {'time_kind': 'event', 'keywords': ['讲座']})
    assert evidence in refs[0]['excerpt']
    assert '清华大学建华楼LG1-21' in refs[0]['excerpt']
    assert '扫描下方二维码，进入水木汇报名后加入微信群。' in refs[0]['excerpt']


def test_excerpt_blocks_are_continuous_source_slices_with_unique_source_ids():
    content, _ = long_lecture()
    items = [article('first', content), article('second', content)]
    refs = source_references(items, '学术讲座时间地点和报名方式', {'time_kind': 'event', 'keywords': ['讲座']})
    all_ids = []
    for index, (item, ref) in enumerate(zip(items, refs), 1):
        assert ref['number'] == index and ref['id'] == item['id']
        body = ref['excerpt'][len(item['title']) + 1:]
        regions = body.split('\n（中间原文已省略）\n')
        starts = [content.index(region) for region in regions]
        assert starts == sorted(starts)
        for block in ref['evidence']:
            assert block['text'] in item[block['field']]
            assert '（中间原文已省略）' not in block['text']
            assert block['id'].startswith('s' + str(index) + 'p')
            all_ids.append(block['id'])
    assert len(all_ids) == len(set(all_ids))


def test_short_source_remains_complete():
    content = '活动时间：10月20日14:00。活动地点：电子馆。观众报名方式：向负责人登记。'
    ref = source_references([article('a', content)], '讲座安排', {})[0]
    assert ref['excerpt'] == ref['title'] + '\n' + content
    assert ref['content_coverage'] == '完整收录正文'
