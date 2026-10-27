import datetime as dt
import unittest
from seongnam_collector import date_range, months_from, parse_detail, parse_list, KST, SourceError

NOW = dt.datetime(2026, 10, 27, 12, tzinfo=KST)

def detail(audience="어린이", period="2026.11.01(일) ~ 11.08(일)", description=None):
    description = description or "어린이와 가족이 함께 자연을 관찰하고 나만의 작품을 만드는 체험 프로그램입니다. 재료를 살펴보고 직접 만들어 봅니다."
    return f"""<div class="board-view"><div class="board-view-head">자연 체험</div>
    <dl><dt>참여대상</dt><dd>{audience}</dd></dl>
    <dl><dt>분류</dt><dd>체험</dd></dl>
    <dl><dt>행사기간</dt><dd>{period}</dd></dl>
    <dl><dt>접수기간</dt><dd>2026.10.28 09:00 ~ 2026.10.30 18:00</dd></dl>
    <img src="/pm010201/getCropImage?evntSn=1">
    <div class="board-view-content">{description}</div></div>
    <div class="content-license"><img alt="공공누리 제4유형 : 출처표시 + 상업적 이용금지 + 변경금지"></div>"""

class CollectorTests(unittest.TestCase):
    def test_abbreviated_end(self):
        self.assertEqual(date_range("2026.10.23(금) ~ 11.09(월)"), ("2026-10-23","2026-11-09"))
    def test_registration_time(self):
        self.assertEqual(date_range("2026.10.23 00:00 ~ 2026.11.09 23:00")[1], "2026-11-09T23:00:00+09:00")
    def test_invalid_or_ambiguous_dates(self):
        for value in ("2026.12.31 ~ 01.02", "2026.02.30", "", "10.27 ~ 11.01"):
            self.assertEqual(date_range(value), (None,None))
    def test_year_rollover(self):
        self.assertEqual(months_from(dt.datetime(2026,12,1,tzinfo=KST),2), ["202612","202701"])
    def test_list_pagination(self):
        rows,pages=parse_list("""<a onclick="fn_move_form('123');"><div class="tit">체험</div><span class="badge">체험</span></a><a href="?curPage=6&amp;nowym=202610">6</a>""")
        self.assertEqual((rows[0]["source_id"],pages),("123",6))
    def test_candidate_is_never_ready(self):
        r=parse_detail(detail(),{"source_id":"1"},NOW)
        self.assertTrue(r["candidate"])
        self.assertFalse(r["ready_for_draft"])
        self.assertFalse(r["weekend_session_verified"])
        self.assertIsNone(r["booking_url"])
        self.assertEqual(r["image_rights_status"],"restricted_noncommercial_no_derivatives")
    def test_adult_ended_and_image_only_excluded(self):
        for raw in (detail(audience="성인"),detail(period="2026.10.01 ~ 10.02"),detail(description="<img src='/poster.png'>")):
            self.assertFalse(parse_detail(raw,{"source_id":"1"},NOW)["candidate"])
    def test_schema_change_fails(self):
        with self.assertRaises(SourceError):
            parse_detail("<div>blocked</div>",{"source_id":"1"},NOW)

if __name__ == "__main__":
    unittest.main()
