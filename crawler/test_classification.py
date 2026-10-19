"""分类覆盖真实资讯主题，并防止正文推荐和再次采集污染分类。"""
import unittest

from classification import CATEGORIES, assign_category, classify
from collect import merge_articles, record


class ClassificationTests(unittest.TestCase):
    def test_topics_and_other_fallback(self):
        cases = [
            ('化学系胡亚杰获2026年瑞士乔诺法青年研究奖', 'research'),
            ('电子系方璐团队提出物理梯度训练架构，实现光子芯片原位学习', 'research'),
            ('联合发布《具身智能发展报告（2026年）》', 'research'),
            ('校园招聘正式启动', 'career'),
            ('教学实习训练中心来访', 'academics'),
            ('课程记P/F及退课安排', 'academics'),
            ('国家励志奖学金评选通知', 'awards'),
            ('海外学习项目报名', 'international'),
            ('校长访问英国、法国', 'international'),
            ('普林斯顿大学研究生院院长专场宣讲会暨校友交流分享会', 'international'),
            ('社会实践志愿招募', 'practice'),
            ('原创舞台剧专场展演', 'campus'),
            ('体测时间更改通知', 'campus'),
            ('首届虚拟运动会即将开赛', 'campus'),
            ('新生赤足运动会', 'campus'),
            ('东操音乐跑预告', 'campus'),
            ('阳光憩岛集章兑奖攻略', 'campus'),
            ('举办退休职工重阳节活动', 'campus'),
            ('百花创新影像单元展映片单', 'campus'),
            ('国家社会科学基金年度项目立项公示', 'research'),
            ('计算服务器采购项目公开招标公告', 'other'),
            ('退休教师讣告', 'other'),
            ('机关工作人员作风能力专项培训专题报告举行', 'other'),
            ('新闻学院信息简报', 'other'),
            ('相关情况说明', 'other'),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(classify(title), expected)

    def test_previous_article_recommendations_do_not_classify_current_article(self):
        self.assertEqual(classify('工作动态', '', '日常工作情况。\n往期精选\n学术讲座与科研竞赛'), 'other')

    def test_legacy_pending_is_reclassified_and_valid_ai_is_preserved(self):
        old = {'title': '体测时间更改通知', 'category': 'uncategorized'}
        self.assertEqual(assign_category(old)['category'], 'campus')
        self.assertEqual(old['category'], 'uncategorized')
        ai = {'title': '校园体育动态', 'category': 'research', 'category_method': 'ai'}
        self.assertEqual(assign_category(ai), ai)

    def test_metadata_merge_classifies_preserved_full_text(self):
        old = record('https://mp.weixin.qq.com/s/test', 'wechat', '测试', '新的安排', '校园体育活动现已公布。')
        new = record('https://mp.weixin.qq.com/s/test', 'wechat', '测试', '新的安排')
        merged = assign_category(merge_articles(old, new))
        self.assertEqual(merged['category'], 'campus')
        self.assertIn(merged['category'], CATEGORIES)


if __name__ == '__main__':
    unittest.main()
