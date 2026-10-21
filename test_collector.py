import datetime as dt
import unittest
from unittest.mock import patch
from collector import ApiError, KST, SeoulClient, grade_match, normalize, plain, probe_image

class CollectorTests(unittest.TestCase):
    def test_grade_ranges_and_uncertainty(self):
        for target in ("초등학교 1~6학년 자녀를 동반한 가족", "초등 1~3학년", "초등 저학년"):
            self.assertEqual(grade_match(target), "explicit_match")
        for target in ("초등학교 4~6학년", "성인(50세 이상)", "유아"):
            self.assertEqual(grade_match(target), "excluded")
        for target in ("가족", "어린이", "8~10세", ""):
            self.assertEqual(grade_match(target), "needs_review")

    def test_closed_and_period_are_not_available_seats(self):
        now = dt.datetime(2026, 10, 21, 12, tzinfo=KST)
        row = {"SVCID": "a", "USETGTINFO": "초등 1~3학년", "SVCSTATNM": "예약마감",
               "RCPTBGNDT": "2026-10-01 00:00:00.0", "RCPTENDDT": "2026-10-30 00:00:00.0",
               "SVCOPNBGNDT": "2026-10-01 00:00:00.0", "SVCOPNENDDT": "2026-10-31 00:00:00.0",
               "IMGURL": "javascript:alert(1)"}
        record = normalize(row, "ListPublicReservationEducation", now)
        self.assertEqual(record["registration_status"], "closed")
        self.assertFalse(record["candidate"])
        self.assertTrue(record["period_overlaps_weekend"])
        self.assertFalse(record["weekend_session_verified"])
        self.assertIsNone(record["remaining_seats"])
        self.assertIsNone(record["official_image_url"])
        row["SVCSTATNM"] = "접수중"
        self.assertFalse(normalize(row, "", now)["candidate"])
        row["RCPTENDDT"] = "2026-10-20 00:00:00"
        self.assertEqual(normalize(row, "", now)["registration_status"], "closed")

    def test_upcoming_registration_window_not_event_week(self):
        now = dt.datetime(2026, 10, 21, 12, tzinfo=KST)
        row = {"USETGTINFO": "초등 1~3학년", "SVCSTATNM": "접수예정",
               "RCPTBGNDT": "2026-10-22 10:00:00",
               "RCPTENDDT": "2026-10-30 10:00:00",
               "SVCOPNBGNDT": "2026-11-07 10:00:00",
               "SVCOPNENDDT": "2026-11-07 12:00:00"}
        self.assertTrue(normalize(row, "", now)["candidate"])
        self.assertFalse(normalize(row, "", now)["period_overlaps_weekend"])
        row["RCPTBGNDT"] = "2026-10-29 10:00:00"
        self.assertFalse(normalize(row, "", now)["candidate"])
        row["RCPTBGNDT"] = "2026-10-28 12:00:00"
        self.assertTrue(normalize(row, "", now)["candidate"])
        row["RCPTBGNDT"] = now.isoformat()
        self.assertFalse(normalize(row, "", now)["candidate"])
        row["RCPTBGNDT"] = ""
        self.assertFalse(normalize(row, "", now)["candidate"])

    def test_sample_does_not_paginate(self):
        client = SeoulClient()
        with patch.object(client, "page", return_value=([{"SVCID": "a"}], 1000)) as call:
            self.assertEqual(len(list(client.collect("ListPublicReservationEducation"))), 1)
            call.assert_called_once_with("ListPublicReservationEducation", 1, 5)

    def test_errors_do_not_expose_key(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("secret-url")):
            with self.assertRaises(ApiError) as ctx:
                SeoulClient("secret-key").page("ListPublicReservationEducation", 1, 5)
        self.assertNotIn("secret", str(ctx.exception))

    def test_html_and_image_host(self):
        self.assertEqual(plain("<script>bad</script><p>A &amp; B</p>"), "A & B")
        with patch("urllib.request.build_opener") as opener:
            self.assertEqual(probe_image("https://example.org/a.jpg")["status"], "not_probed")
            opener.assert_not_called()

if __name__ == "__main__":
    unittest.main()
