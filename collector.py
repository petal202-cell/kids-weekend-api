"""Seoul public reservation collector. No publishing or booking actions."""
import argparse
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SERVICES = ("ListPublicReservationEducation", "ListPublicReservationCulture")
BASE = "http://openapi.seoul.go.kr:8088"
SOURCE = "https://data.seoul.go.kr/dataList/4/literacyView.do"

class ApiError(RuntimeError):
    pass

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

def plain(value):
    p = TextParser()
    p.feed(str(value or ""))
    return " ".join(html.unescape(" ".join(p.parts)).split())

def parse_time(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)
    except ValueError:
        return None

def grade_match(target):
    """Use declared audience, never convert age to grade or infer eligibility from title."""
    text = re.sub(r"\s+", "", plain(target))
    if re.search(r"초(?:등학교|등)?[1-6](?:학년)?", text):
        grades = set()
        for a, b in re.findall(r"([1-6])(?:학년)?[~∼～\-–]([1-6])학년", text):
            grades.update(range(int(a), int(b) + 1))
        grades.update(int(n) for n in re.findall(r"([1-6])학년", text))
        if grades:
            return "explicit_match" if grades & {1, 2, 3} else "excluded"
    if "저학년" in text and "초" in text:
        return "explicit_match"
    if not any(w in text for w in ("초등", "어린이", "아동", "가족", "누구나", "제한없", "모든", "전체")) and any(w in text for w in ("성인", "청소년", "유아", "미취학")):
        return "excluded"
    return "needs_review"

def safe_url(value):
    value = html.unescape(str(value or "")).strip()
    p = urllib.parse.urlsplit(value)
    return value if p.scheme in ("http", "https") and p.hostname and not p.username else None

def normalize(row, service, now, opening_days=7):
    start, end = parse_time(row.get("SVCOPNBGNDT")), parse_time(row.get("SVCOPNENDDT"))
    begin, close = parse_time(row.get("RCPTBGNDT")), parse_time(row.get("RCPTENDDT"))
    sat = now.date() + dt.timedelta(days=(5 - now.weekday()) % 7)
    sun = sat + dt.timedelta(days=1)
    status = plain(row.get("SVCSTATNM"))
    if any(w in status for w in ("종료", "마감", "취소", "중지")) or (close and now > close):
        registration = "closed"
    elif begin and now < begin:
        registration = "upcoming"
    elif status in ("접수중", "예약접수중", "예약가능") and begin and close and begin <= now <= close:
        registration = "accepting"
    else:
        registration = "unknown"
    overlap = None if not start or not end else start.date() <= sun and end.date() >= sat
    opens_soon = bool(begin and now < begin <= now + dt.timedelta(days=opening_days))
    candidate = (grade_match(row.get("USETGTINFO")) != "excluded"
                 and registration == "upcoming" and opens_soon
                 and (end is None or end >= begin))
    image = safe_url(row.get("IMGURL"))
    return {
        "id": str(row.get("SVCID", "")), "service": service,
        "title": plain(row.get("SVCNM")), "category": plain(row.get("MINCLASSNM")),
        "audience": plain(row.get("USETGTINFO")), "grade_status": grade_match(row.get("USETGTINFO")),
        "place": plain(row.get("PLACENM")), "district": plain(row.get("AREANM")),
        "fee_label": plain(row.get("PAYATNM")), "provider_status": status,
        "registration_status": registration,
        "registration_start": begin.isoformat() if begin else None,
        "registration_end": close.isoformat() if close else None,
        "event_start": start.isoformat() if start else None,
        "event_end": end.isoformat() if end else None,
        "target_weekend": [sat.isoformat(), sun.isoformat()],
        "period_overlaps_weekend": overlap,
        "weekend_session_verified": False, "remaining_seats": None,
        "booking_url": safe_url(row.get("SVCURL")),
        "description": plain(row.get("DTLCONT")),
        "official_image_url": image,
        "image_kind": "official_image_unclassified" if image else None,
        "image_rights_status": "review_required" if image else None,
        "attribution": "서울특별시 공공서비스예약 / 서울 열린데이터광장",
        "source_url": SOURCE, "checked_at": now.isoformat(),
        "candidate": candidate,
        "candidate_reason": "registration_opens_soon" if candidate else None,
    }

class SeoulClient:
    def __init__(self, key="sample"):
        self.key = key or "sample"
    def page(self, service, start, end):
        if service not in SERVICES:
            raise ValueError("Unsupported Seoul service")
        # Official documented endpoint is HTTP. Never include its key-bearing URL in logs.
        url = f"{BASE}/{urllib.parse.quote(self.key, safe='')}/json/{service}/{start}/{end}/"
        try:
            with urllib.request.urlopen(url, timeout=45) as res:
                data = json.load(res)
        except (urllib.error.URLError, ValueError, TimeoutError):
            raise ApiError("Seoul API request failed; key-bearing URL suppressed") from None
        node = data.get(service, data)
        result = node.get("RESULT", {})
        if result.get("CODE") == "INFO-200":
            return [], 0
        if result.get("CODE") != "INFO-000":
            raise ApiError("Seoul API returned code " + str(result.get("CODE", "missing")))
        if not isinstance(node.get("row"), list):
            raise ApiError("Seoul API row schema changed")
        return node["row"], int(node.get("list_total_count", 0))
    def collect(self, service):
        start = 1
        while True:
            size = 5 if self.key == "sample" else 500
            rows, total = self.page(service, start, start + size - 1)
            yield from rows
            if self.key == "sample" or start + size > total or not rows:
                return
            start += size

def probe_image(url):
    """Check metadata only, never download images or contact arbitrary third-party hosts."""
    p = urllib.parse.urlsplit(url or "")
    if p.scheme != "https" or p.hostname != "yeyak.seoul.go.kr":
        return {"status": "not_probed"}
    try:
        req = urllib.request.Request(url, method="HEAD")
        # Do not follow redirects to unknown hosts.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as res:
            mime = res.headers.get("Content-Type", "").split(";")[0]
            return {"status": "accessible" if mime.startswith("image/") else "not_image",
                    "content_type": mime}
    except (urllib.error.URLError, TimeoutError):
        return {"status": "unavailable"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true", help="Public sample key; only first five rows per service")
    parser.add_argument("--probe-images", action="store_true", help="HEAD-check at most ten official image URLs")
    parser.add_argument("--output", default="output/reservations.json")
    parser.add_argument("--opening-days", type=int, default=7, help="Upcoming registration window in days")
    args = parser.parse_args()
    if args.opening_days < 1:
        parser.error("--opening-days must be positive")
    key = "sample" if args.sample else os.environ.get("SEOUL_API_KEY")
    if not key:
        parser.error("Set SEOUL_API_KEY or use --sample")
    now = dt.datetime.now(KST)
    records = {}
    client = SeoulClient(key)
    for service in SERVICES:
        for row in client.collect(service):
            record = normalize(row, service, now, args.opening_days)
            if not record["id"]:
                raise ApiError("Missing reservation ID")
            records.setdefault(record["id"], record)
    probes = 0
    for record in records.values():
        if args.probe_images and record["official_image_url"] and probes < 10:
            record["image_probe"] = probe_image(record["official_image_url"])
            probes += 1
    candidates = sorted((r for r in records.values() if r["candidate"]),
                        key=lambda r: (r["registration_start"], r["id"]))
    result = {"selection": {"rule": "registration_opens_soon", "opening_days": args.opening_days},
              "candidates": candidates, "mode": "sample" if key == "sample" else "full", "checked_at": now.isoformat(),
              "sample_is_not_complete": key == "sample",
              "notice": "접수 중은 잔여석 보장이 아닙니다. 운영기간 중 실제 주말 회차와 대상 학년은 원문 확인 필요.",
              "records": list(records.values())}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": result["mode"], "records": len(records),
                     "candidates": sum(r["candidate"] for r in records.values()),
                     "with_official_image": sum(bool(r["official_image_url"]) for r in records.values())}))

if __name__ == "__main__":
    try:
        main()
    except ApiError as err:
        raise SystemExit(str(err)) from None
