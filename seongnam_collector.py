"""Read-only Seongnam City Hall event-board collector; not an official Open API."""
import argparse
import calendar
import datetime as dt
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = "https://www.seongnam.go.kr"
BOARD = BASE + "/pm010201"
KST = ZoneInfo("Asia/Seoul")
VOID = {"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}

class SourceError(RuntimeError):
    pass

class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []
    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()
    def text(self):
        return " ".join(" ".join(c.text() if isinstance(c, Node) else c for c in self.children).split())
    def has(self, token):
        return token in self.attrs.get("class", "").split()

class Document(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(raw)
    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in VOID:
            self.stack.append(n)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break
    def handle_data(self, data):
        if not any(n.tag in ("script", "style") for n in self.stack):
            self.stack[-1].children.append(data)

def first(nodes, predicate):
    return next((n for n in nodes if predicate(n)), None)

def field(node, cls):
    found = first(node.walk(), lambda n: n.has(cls))
    return found.text() if found else ""

def fetch(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname != "www.seongnam.go.kr":
        raise SourceError("Only the official City Hall host may be collected")
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"kids-weekend-api/1.0 public-event-research"})
        # City Hall requires RSA cipher compatibility on OpenSSL 3.
        # Keep certificate/hostname verification and TLS 1.2 minimum enabled.
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("DEFAULT")
        with urllib.request.urlopen(req, timeout=30, context=context) as res:
            final = urllib.parse.urlsplit(res.url)
            if final.hostname != p.hostname:
                raise SourceError("Unexpected redirect")
            raw = res.read().decode("utf-8")
    except (OSError, UnicodeError) as err:
        raise SourceError("City Hall request failed: " + type(err).__name__ + ": " + str(err)) from None
    if len(raw) < 500 or "행사/강좌/공모" not in raw:
        raise SourceError("City Hall response is not the expected event page")
    return raw

def parse_list(raw):
    root = Document(raw).root
    rows = {}
    for a in root.walk():
        match = re.fullmatch(r"fn_move_form\('(\d+)'\)", a.attrs.get("onclick", "").strip().rstrip(";"))
        if a.tag != "a" or not match:
            continue
        title = field(a, "tit")
        if not title:
            raise SourceError("Event-list title schema changed")
        rows[match[1]] = {"source_id":match[1], "title":title,
                         "category":field(a, "badge"), "provider_status":field(a, "receipt"),
                         "event_period_raw":field(a, "date")}
    pages = [int(x) for x in re.findall(r"curPage=(\d+)", raw)]
    if not rows and "총" not in raw:
        raise SourceError("Event-list schema changed")
    return list(rows.values()), max(pages, default=1)

def date_range(value):
    clean = re.sub(r"\([^)]*\)", "", value or "")
    found = re.findall(r"(?:(20\d{2})[./-])?(\d{1,2})[./-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", clean)
    if not found or not found[0][0]:
        return None, None
    result, year = [], int(found[0][0])
    try:
        for y,m,d,h,minute in found[:2]:
            if y:
                year = int(y)
            # Never guess a rollover into next year from abbreviated dates.
            stamp = dt.datetime(year,int(m),int(d),int(h or 0),int(minute or 0),tzinfo=KST)
            result.append(stamp.isoformat() if h else stamp.date().isoformat())
    except ValueError:
        return None, None
    if len(result)==1:
        result.append(result[0])
    if result[1] < result[0]:
        return None, None
    return tuple(result)


SELECTION_POLICY = "seongnam-tomorrow-or-month-v1"

def next_month_day(day):
    year, month = (day.year+1, 1) if day.month==12 else (day.year, day.month+1)
    return dt.date(year, month, min(day.day, calendar.monthrange(year,month)[1]))

def selection_rule(info, description, start, end, reg_start, reg_end, now):
    """Dates in KST. Missing registration metadata is never proof of walk-in access."""
    today = now.astimezone(KST).date()
    tomorrow = today + dt.timedelta(days=1)
    until = next_month_day(today)
    if info.get("접수기간"):
        if not reg_start or not reg_end:
            return False, "registration_dates_need_review", "unknown"
        opens = dt.datetime.fromisoformat(reg_start)
        opens = opens.replace(tzinfo=KST) if opens.tzinfo is None else opens.astimezone(KST)
        return (opens.date()==tomorrow,
                "registration_opens_tomorrow" if opens.date()==tomorrow else "not_tomorrow_registration",
                "required")
    explicit_none = bool(re.search(r"(?:사전\s*)?(?:예약|접수|신청)\s*(?:없이|없음|불필요|필요\s*없)|자유\s*(?:관람|입장)", description))
    mentions_registration = bool(re.search(r"(?:예약|접수|신청|예매)", description))
    if mentions_registration and not explicit_none:
        return False, "registration_method_needs_review", "unknown"
    in_window = bool(start and end and start[:10] <= until.isoformat() and end[:10] >= today.isoformat())
    return (in_window, "event_within_one_month" if in_window else "outside_one_month",
            "not_required" if explicit_none else "not_listed")


def parse_detail(raw, summary, now):
    root = Document(raw).root
    view = first(root.walk(), lambda n: n.has("board-view"))
    if not view:
        raise SourceError("Event-detail schema changed")
    title = field(view, "board-view-head")
    body = first(view.walk(), lambda n: n.has("board-view-content"))
    if not title or body is None:
        raise SourceError("Event-detail content schema changed")
    info = {}
    for dl in view.walk():
        if dl.tag == "dl":
            key = first(dl.walk(),lambda n:n.tag=="dt")
            value = first(dl.walk(),lambda n:n.tag=="dd")
            if key and value:
                info[key.text()] = value.text()
    if "참여대상" not in info or "행사기간" not in info:
        raise SourceError("Event-detail required fields missing")
    event_start,event_end = date_range(info.get("행사기간"))
    reg_start,reg_end = date_range(info.get("접수기간"))
    description = body.text()
    imgs = [urllib.parse.urljoin(BASE,n.attrs["src"]) for n in view.walk()
            if n.tag=="img" and n.attrs.get("src") and "/pm010201/" in n.attrs["src"]]
    license_node = first(root.walk(),lambda n:n.has("content-license"))
    license_text = " ".join(n.attrs.get("alt","") for n in license_node.walk() if n.tag=="img") if license_node else ""
    links = []
    for n in body.walk():
        if n.tag=="a":
            url = urllib.parse.urljoin(BASE,n.attrs.get("href",""))
            if urllib.parse.urlsplit(url).scheme in ("https","http"):
                links.append(url)
    audience = info["참여대상"]
    child = any(w in audience+title for w in ("어린이","초등","가족","아동"))
    adult_only = not child and any(w in audience for w in ("성인","청소년","청년","어르신","여성","남성"))
    broad = any(w in audience for w in ("누구나","전체","제한없"))
    category = info.get("분류",summary.get("category",""))
    ended = bool(event_end and event_end[:10] < now.date().isoformat())
    relevant = category in ("체험","축제","전시","공연","강좌") and (child or broad) and not adult_only
    reason = ("event_ended" if ended else "audience_excluded" if adult_only else
              "image_only_or_sparse_details" if len(description)<40 else
              "family_event_review" if relevant else "not_child_experience")
    selected, policy_reason, registration_mode = selection_rule(info, description, event_start, event_end, reg_start, reg_end, now)
    weekend_closed = bool(re.search(r"주말\s*[·ㆍ,/및\s]*공휴일\s*(?:휴관|휴무)|주말\s*(?:휴관|휴무)", description))
    candidate = relevant and not ended and not weekend_closed and len(description)>=40 and bool(event_start) and selected
    if weekend_closed:
        reason = "weekend_closed"
    elif relevant and not ended and len(description)>=40:
        reason = policy_reason
    registration_priority = "opening_tomorrow" if registration_mode=="required" else "no_registration_month"

    return {
        "id":"seongnam-"+summary["source_id"], "source":"seongnam_city_hall_html",
        "source_id":summary["source_id"], "title":title, "category":category,
        "audience":audience, "grade_status":"needs_review", "place":info.get("장소") or "장소는 공식 안내 확인",
        "district":"성남시", "fee_label":info.get("체험비용") or info.get("관람료") or "비용은 공식 안내 확인",
        "description":description, "source_fields":info,
        "event_start":event_start, "event_end":event_end,
        "registration_start":reg_start, "registration_end":reg_end,
        "registration_priority":registration_priority, "registration_mode":registration_mode,
        "selection_policy":SELECTION_POLICY,
        "event_period_raw":info.get("행사기간"),"registration_period_raw":info.get("접수기간"),
        "source_url":BOARD+"/"+summary["source_id"],
        "booking_url":None, "reference_links":list(dict.fromkeys(links)),
        "official_image_url":imgs[0] if imgs else None,
        "image_rights_status":"restricted_noncommercial_no_derivatives" if "제4유형" in license_text else "review_required",
        "license_raw":license_text, "checked_at":now.isoformat(),
        "candidate":candidate, "candidate_reason":reason, "ready_for_draft":False,
        "review_required":["실제 체험 내용","초1~3 참여 가능 여부","주말 회차","접수기간과 본문 일치 여부","예약 경로","이미지 이용조건"],
        "remaining_seats":None,"weekend_session_verified":False,
    }

def months_from(now,count):
    start = now.year*12+now.month-1
    return [f"{(start+i)//12:04d}{(start+i)%12+1:02d}" for i in range(count)]

def collect(now=None, months=2, max_pages=10, delay=0.3):
    now = now or dt.datetime.now(KST)
    summaries, scans, truncated = {}, [], False
    for month in months_from(now,months):
        page = 1
        while True:
            raw = fetch(BOARD+"?"+urllib.parse.urlencode({"nowym":month,"curPage":page}))
            rows,total_pages = parse_list(raw)
            scans.append({"month":month,"page":page,"pages":total_pages,"records":len(rows)})
            for row in rows:
                summaries.setdefault(row["source_id"],row)
            if page>=total_pages:
                break
            if page>=max_pages:
                truncated = True
                break
            page += 1
            time.sleep(delay)
    if not summaries:
        raise SourceError("No events collected; preserve previous snapshot for review")
    records=[]
    errors=[]
    for summary in summaries.values():
        time.sleep(delay)
        try:
            records.append(parse_detail(fetch(BOARD+"/"+summary["source_id"]),summary,now))
        except SourceError as err:
            errors.append({"source_id":summary["source_id"],"title":summary["title"],"error":str(err)})
    if not records:
        raise SourceError("No detail records collected; preserve previous snapshot")
    return {"source":"seongnam_city_hall_html","checked_at":now.isoformat(),
            "selection":{"policy":SELECTION_POLICY,"timezone":"Asia/Seoul","registration_day":(now.astimezone(KST).date()+dt.timedelta(days=1)).isoformat(),"event_window_start":now.astimezone(KST).date().isoformat(),"event_window_end":next_month_day(now.astimezone(KST).date()).isoformat()},
            "sample_is_not_complete":False, "partial_scan":bool(truncated or errors),
            "scan_complete":not truncated and not errors,"scanned_pages":scans,"errors":errors,
            "notice":"공식 API가 아닌 공개 게시판 수집. 후보는 검토용이며 예약가능·게시가능을 의미하지 않습니다.",
            "candidates":sorted([r for r in records if r["candidate"]], key=lambda r: ({"opening_tomorrow":0,"no_registration_month":1}[r["registration_priority"]], r["registration_start"] or r["event_start"] or "")),"records":records}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--months",type=int,default=2)
    parser.add_argument("--max-pages",type=int,default=10)
    parser.add_argument("--output",default="output/seongnam.json")
    args=parser.parse_args()
    if not 1<=args.months<=3 or not 1<=args.max_pages<=20:
        parser.error("months 1..3, max-pages 1..20")
    result=collect(months=args.months,max_pages=args.max_pages)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"records":len(result["records"]),"review_candidates":len(result["candidates"]),"scan_complete":result["scan_complete"]}))
if __name__=="__main__":
    main()
