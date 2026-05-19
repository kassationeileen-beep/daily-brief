from main import _extract_earnings_requests, _merge_manual_ipo_section


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
