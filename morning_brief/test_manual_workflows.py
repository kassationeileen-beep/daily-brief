from main import _extract_earnings_requests, _merge_manual_ipo_section
from llm.refiner import _condense_macro_bullets
from fetchers.telegram_input import _parse_message


def test_extract_earnings_requests_removes_search_reminder():
    cleaned, requests = _extract_earnings_requests({
        "0700": ["提醒查一下騰訊是否發業績", "騰訊回購 100 萬股"],
        "9988": ["阿里發布新產品"],
    })

    assert requests == [{"code": "0700", "name": "0700", "source": "telegram"}]
    assert cleaned == {
        "0700": ["騰訊回購 100 萬股"],
        "9988": ["阿里發布新產品"],
    }


def test_merge_manual_ipo_replaces_empty_auto_section():
    section = "▶️五、*今日招股（新股認購）*\n• 今日暫無新股認購"

    merged = _merge_manual_ipo_section(section, ["#ipo 01234 測試公司今日招股"])

    assert "今日暫無新股認購" not in merged
    assert "人工補充（Telegram）" in merged
    assert "01234 測試公司今日招股" in merged


def test_condense_macro_bullets_keeps_three_short_lines():
    text = "\n".join([
        "• 这是一条很长很长的宏观描述，里面有很多背景说明和修饰语",
        "• 第二条也很长很长，需要截断保留核心",
        "• 第三条",
        "• 第四条不应出现",
    ])
    compact = _condense_macro_bullets(text, max_items=3, max_chars=12)
    lines = compact.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("• ")
    assert lines[-1] == "• 第三条"


def test_industry_tag_classified_as_macro():
    parsed = _parse_message("#行业\n补充航运指数和BDI变动")
    assert parsed["section"] == "macro"
