#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
우리 아기 유아식 가이드 - 로컬 스크래퍼 서버 (표준 라이브러리만 사용, 설치 불필요)

실행:  python3 유아식-서버.py
접속:  http://localhost:8770

동작:
  - '/'            유아식 가이드 HTML을 같은 출처(localhost)로 서빙 → CORS 문제 없음
  - '/api/scrape'  매장별 refresh 버튼이 호출. 그때그때 스크래핑해서 가격 JSON 반환

매장 상태:
  - kurly   (컬리)        : 공개 검색 API로 실제 조회됨
  - ssg     (이마트몰)     : 페이지 임베드 데이터 파싱으로 실제 조회됨
  - coupang (쿠팡)        : 봇 차단(403)으로 자동 조회 불가 → 안내 메시지 반환
  - gsfresh (GS프레쉬)    : 로그인/매장선택 필요 → 안내 메시지 반환
"""
import json, re, urllib.request, urllib.parse, os, sys
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", 8770))  # 클라우드(Render/Railway 등)는 PORT 환경변수를 줌
HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "index.html")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def http_get(url, accept="application/json,text/html,*/*", timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


# ---------- 매장별 어댑터 ----------
def scrape_kurly(q):
    enc = urllib.parse.quote(q)
    url = (f"https://api.kurly.com/search/v4/sites/market/normal-search"
           f"?keyword={enc}&page=1&per_page=8&sort_type=1")
    data = json.loads(http_get(url))
    items = []
    for sec in data.get("data", {}).get("listSections", []):
        for it in sec.get("data", {}).get("items", []):
            name = it.get("name")
            if not name:
                continue
            base = it.get("salesPrice")
            disc = it.get("discountedPrice")
            price = disc if disc else base
            items.append({
                "name": name,
                "price": won(price),
                "orig": won(base) if disc and disc != base else "",
                "soldout": bool(it.get("isSoldOut")),
            })
            if len(items) >= 6:
                break
        if len(items) >= 6:
            break
    return {"ok": True, "items": items}


def scrape_ssg(q):
    enc = urllib.parse.quote(q)
    html = http_get(f"https://www.ssg.com/search.ssg?target=all&query={enc}", accept="text/html")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return {"ok": False, "note": "페이지 구조가 바뀐 것 같아요(임베드 데이터 없음). 셀렉터 확인 필요."}
    j = json.loads(m.group(1))
    items = []

    def walk(o):
        if len(items) >= 6:
            return
        if isinstance(o, dict):
            if o.get("itemName") and (o.get("finalPrice") or o.get("priceInfo")):
                pi = o.get("priceInfo") or {}
                price = o.get("finalPrice") or pi.get("primaryPrice") or ""
                unit = pi.get("unitPriceDescription", "")
                items.append({
                    "name": o["itemName"],
                    "price": price if str(price).endswith("원") else (str(price) + "원" if price else ""),
                    "orig": "",
                    "unit": unit,
                    "soldout": False,
                })
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(j)
    if not items:
        return {"ok": False, "note": "상품을 찾지 못했어요. 검색어를 바꿔보거나 셀렉터를 확인해주세요."}
    return {"ok": True, "items": items}


def scrape_coupang(q):
    # 쿠팡은 봇 차단이 강해 단순 요청이 403으로 막힘. 시도는 하되 실패 시 안내.
    try:
        enc = urllib.parse.quote(q)
        http_get(f"https://www.coupang.com/np/search?q={enc}&listSize=6", accept="text/html")
        return {"ok": False, "note": "쿠팡 응답을 받았지만 상품 파싱은 미구현이에요. (안정적 조회엔 헤드리스 브라우저 필요)"}
    except Exception:
        return {"ok": False, "note": "쿠팡은 봇 차단(403)으로 자동 조회가 막혀요. 수동 입력으로 관리하세요.\n(원하면 헤드리스 브라우저 연동을 별도로 시도할 수 있어요)"}


def scrape_gsfresh(q):
    return {"ok": False, "note": "GS프레쉬(우리동네GS)는 로그인·배송지 매장 선택이 있어야 가격이 보여요.\n자동 조회 대신 수동 입력으로 관리하는 걸 권장합니다."}


def scrape_trends(q):
    """만개의레시피 인기순 검색으로 최신 유아식 레시피 트렌드를 가져온다."""
    enc = urllib.parse.quote(q)
    html = http_get(f"https://www.10000recipe.com/recipe/list.html?q={enc}&order=reco", accept="text/html")
    chunks = html.split('class="common_sp_list_li"')[1:]
    items = []
    for c in chunks:
        mlink = re.search(r'href="(/recipe/\d+)"', c)
        mtitle = re.search(r'common_sp_caption_tit line2">(.*?)</div>', c, re.S)
        mimg = re.search(r'<img[^>]+src="([^"]+)"', c)
        mname = re.search(r'common_sp_caption_rv_name[^>]*>(.*?)</div>', c, re.S)
        if mlink and mtitle:
            title = unescape(re.sub(r'<[^>]+>', '', mtitle.group(1)).strip())
            author = unescape(re.sub(r'<[^>]+>', '', mname.group(1)).strip()) if mname else ""
            items.append({
                "title": title,
                "url": "https://www.10000recipe.com" + mlink.group(1),
                "img": mimg.group(1) if mimg else "",
                "author": author,
            })
        if len(items) >= 12:
            break
    if not items:
        return {"ok": False, "note": "레시피를 불러오지 못했어요. 검색어를 바꾸거나 잠시 후 다시 시도해주세요."}
    return {"ok": True, "items": items}


ADAPTERS = {
    "kurly": scrape_kurly,
    "ssg": scrape_ssg,
    "coupang": scrape_coupang,
    "gsfresh": scrape_gsfresh,
}


def won(v):
    try:
        return f"{int(v):,}원"
    except (TypeError, ValueError):
        return str(v) if v else ""


# ---------- HTTP 핸들러 ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # 조용히

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/유아식-가이드.html"):
            try:
                with open(HTML_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, b"HTML file not found next to server.", "text/plain; charset=utf-8")
            return

        if parsed.path == "/api/ping":
            self._send(200, b'{"ok": true}', "application/json; charset=utf-8")
            return

        if parsed.path == "/ads.txt":
            self._send(200, b"google.com, pub-2685692811563574, DIRECT, f08c47fec0942fa0\n",
                       "text/plain; charset=utf-8")
            return

        if parsed.path == "/api/scrape":
            qs = urllib.parse.parse_qs(parsed.query)
            store = (qs.get("store", [""])[0]).strip()
            q = (qs.get("q", [""])[0]).strip()
            result = {"ok": False, "note": "알 수 없는 매장이에요."}
            if not q:
                result = {"ok": False, "note": "검색어를 입력해주세요."}
            elif store in ADAPTERS:
                try:
                    result = ADAPTERS[store](q)
                except Exception as e:
                    result = {"ok": False, "note": f"조회 실패: {type(e).__name__}. 잠시 후 다시 시도하거나 검색어를 바꿔보세요."}
            result["store"] = store
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if parsed.path == "/api/trends":
            qs = urllib.parse.parse_qs(parsed.query)
            q = (qs.get("q", ["유아식"])[0]).strip() or "유아식"
            try:
                result = scrape_trends(q)
            except Exception as e:
                result = {"ok": False, "note": f"조회 실패: {type(e).__name__}. 잠시 후 다시 시도해주세요."}
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")


def get_lan_ip():
    """같은 와이파이의 폰에서 접속할 수 있는 이 컴퓨터의 IP를 찾는다."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # 실제로 데이터를 보내지는 않음
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def main():
    if not os.path.exists(HTML_PATH):
        print(f"⚠️  '{HTML_PATH}' 를 찾을 수 없어요. 서버와 같은 폴더에 HTML이 있어야 합니다.")
    # 0.0.0.0 로 열면 이 컴퓨터뿐 아니라 같은 와이파이의 폰에서도 접속 가능
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    lan = get_lan_ip()
    print("=" * 56, flush=True)
    print("  🍚 우리 아기 유아식 가이드 서버 시작", flush=True)
    print(f"  이 컴퓨터   →  http://localhost:{PORT}", flush=True)
    if lan:
        print(f"  폰/아내폰   →  http://{lan}:{PORT}   (같은 와이파이)", flush=True)
    print("  종료: Ctrl + C  (이 창을 닫아도 종료)", flush=True)
    print("=" * 56, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다. 안녕히!")
        srv.shutdown()


if __name__ == "__main__":
    main()
