from web_content_agent.onepoint3acres.chrome_bridge import _validated_web_evidence


def test_web_evidence_rejects_banks_ai_and_nonforum_domains() -> None:
    good = {
        "title": "社区规则",
        "url": "https://www.1point3acres.com/bbs/rules.html",
        "snippet": "请清晰表达。",
    }
    rejected = [
        {**good, "url": "https://example.org/quiz-bank"},
        {**good, "url": "https://support.google.com/websearch/answer/1"},
        {**good, "url": "https://1point3acres.com.evil.example/rules"},
        {**good, "url": "https://user:secret@www.1point3acres.com/rules"},
        {**good, "title": "论坛题库"},
        {**good, "title": "每日答题-答案"},
        {
            **good,
            "snippet": "下面哪个说法是错误的？ B B √ 1.0 系统正确答案为B 抖包袱看帖，不会消耗积分",
        },
        {
            **good,
            "snippet": "【题目】 下面哪种方法，可以妥妥拿到积分？ "
            "【题目】 给手机应用留评价如何获取50 大米？",
        },
        {**good, "snippet": "AI 概览：这是生成的回答"},
        {**good, "snippet": "AI Overview: generated text"},
        {**good, "url": "https://[invalid"},
    ]
    for item in rejected:
        assert _validated_web_evidence({"web_evidence": [item, good]}) == [good]


def test_web_evidence_preserves_limits_and_allows_forum_subdomains() -> None:
    item = {"title": "规则", "url": "https://blog.1point3acres.com/rules", "snippet": "说明"}
    assert _validated_web_evidence({"web_evidence": [item] * 8}) == [item] * 5
    assert _validated_web_evidence({"web_evidence": []}) == []


def test_web_evidence_rejects_current_quiz_repost_without_answer_bank_title() -> None:
    repost = {
        "title": "Mark|一亩三分地聊聊版- 手机版",
        "url": "https://www.1point3acres.com/bbs/thread-549949-1-1.html",
        "snippet": "【题目】 在论坛发slack 群，qq 群，微信群，任何站外讨论方式，会如何？ "
        "如果发在求职面经大区，申请大区，都会被删帖扣分 举报这些群，可能得到加分 ...",
    }
    choices = [
        "如果发在版聚，或者本地版块，是允许的",
        "如果发在求职面经大区，申请大区，都会被删帖扣分",
        "以上都正确",
        "举报这些群，可能得到加分奖励",
    ]
    # A truncated second option must still be identified as an echo.
    assert _validated_web_evidence({"choices": choices, "web_evidence": [repost]}) == []
    ordinary_rule = {**repost, "snippet": repost["snippet"].replace("【题目】", "管理员说明：")}
    assert _validated_web_evidence({"choices": choices, "web_evidence": [ordinary_rule]}) == [
        ordinary_rule
    ]
    assert _validated_web_evidence({"choices": choices[:2], "web_evidence": [repost]}) == [repost]
