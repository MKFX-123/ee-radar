"""采集器与 API 共用的资讯分类；未匹配的内容统一归入其他。"""
import re

RULE_VERSION = 3
CATEGORIES = {
    'academics': '学业教务', 'research': '科研科创', 'awards': '奖助评优',
    'international': '国际交流', 'practice': '实践公益', 'career': '实习就业',
    'campus': '校园生活', 'other': '其他',
}
CATEGORY_TERMS = {
    'awards': ('奖学金', '助学金', '评优', '荣誉评选', '优秀学生', '困难补助'),
    'international': ('国际交流', '出国', '境外', '海外', '交换生', '暑校', '访学', '全球战略', '国际化提升', '寒假课程项目'),
    'practice': ('社会实践', '志愿', '支教', '公益', '社会调研'),
    'career': ('招聘', '实习', '就业', '校招', '职业', '管培生'),
    'academics': ('教务', '选课', '退课', '记P/F', '考试', '学分', '培养方案', '推免', '免试攻读', '研究生招生', '录取', '综合考核', '通识课程', '教学实习'),
    'research': ('科研', '学术', '讲座', '报告会', '实验室', 'SRT', '竞赛', '黑客松', '科创', '创新创业', '创业大赛', '作品大赛', '研究奖', '科研基金', '科学基金', '科技重大专项', '征文', '征稿', '研讨会', '学者', '科考', '自主研发', '期刊创刊'),
    'campus': ('校园', '体育', '田径', '马约翰', '马杯', '社团', '后勤', '假期', '文艺', '校内服务', '体测', '体质健康', '运动会', '赤足', '光脚', '音乐', '舞台剧', '话剧', '独奏', '观影', '影像', '展映', '领票', '开票', '原创赛', '想唱就唱', '夜现场', '工作坊', '亚运', '图书馆', '生权', '校会', '学生区', '快递', '占道', '施工', '活动中心', '商贸中心', '心理健康', '游园会', '集章兑奖', '时节', '国庆', '祖国', '建系', '建院', '金婚庆典', '重阳节'),
}


def classify(title: str, source_category: str = '', content: str = '') -> str:
    # 行政与采购信息不会因为设备名称或正文提及研究而误入科研类别。
    if re.search(r'采购|招标|中标|废标|成交结果|讣告|党支部|党委理论|理论中心组|党员集中培训|作风能力|务虚会|工作简报|信息简报|校领导调研', title):
        return 'other'
    if re.search(r'教学实习|实习训练中心', title):
        return 'academics'
    if re.search(r'访问.{0,16}(?:英国|法国|马来西亚|印度尼西亚|海外|境外)|(?:海外|境外).{0,12}访问', title):
        return 'international'
    if re.search(r'(?:普林斯顿|哈佛|斯坦福|牛津|剑桥|汉阳)大学.{0,25}(?:宣讲|交流|课程)', title):
        return 'international'
    if re.search(r'团队.{0,30}(?:提出|揭示|研制|发现)|发布.{0,30}发展报告|学科.{0,12}论坛|工程领袖大会|社会科学学院院长论坛', title):
        return 'research'
    for text in (title, source_category):
        scores = {key: sum(term.lower() in text.lower() for term in terms) for key, terms in CATEGORY_TERMS.items()}
        if max(scores.values(), default=0):
            return max(scores, key=scores.get)
    # 只用正文开头辅助判断，排除文末往期推荐对主题的干扰。
    lead = re.split(r'往期精选|往期回顾|往期推荐|推荐阅读|相关阅读', content, maxsplit=1)[0][:1800]
    if re.search(r'师生综合服务大厅|校园卡|游园会|建[系院]\s*\d+周年', lead[:700]):
        return 'campus'
    scores = {key: sum(term.lower() in lead.lower() for term in terms) for key, terms in CATEGORY_TERMS.items()}
    return max(scores, key=scores.get) if max(scores.values(), default=0) >= 2 else 'other'


def assign_category(article: dict) -> dict:
    result = dict(article)
    if result.get('category_method') == 'ai' and result.get('category') in CATEGORIES:
        return result
    result.update(
        category=classify(result.get('title') or '', result.get('source_category') or '', result.get('content') or result.get('source_excerpt') or result.get('content_preview') or ''),
        category_method='rule', rule_version=RULE_VERSION,
    )
    return result
