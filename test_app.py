#!/usr/bin/env python3
"""쿠폰함 · 기프티콘·쿠폰 보관함 PWA — 자동 검증

    LD_LIBRARY_PATH=/workspace/.browserlibs/lib python3 test_app.py

검증 항목
  · PWA 자원(manifest · sw · 꽉 찬 사각형 아이콘), 첫 화면 2열 그리드와 첫 칸 ‘＋ 쿠폰 사진등록’
  · 사진 등록(압축 1600px JPEG · 3장 제한) · 등록·수정 폼 값 · IndexedDB 저장 · 새로고침 유지
  · 사진을 고르면 열리는 자르기 창 — 비율 칩(자유·1:1·3:4·4:3) · 모서리 드래그 · ↺ 90° 회전 · 자르기 적용/원본 그대로
  · 유효기간 D-day 자동 정렬(임박 → 먼 기한 → 기한 없음 → 기한 지남)과 임박 배지 색
  · 사용 가능 / 사용완료 탭 분리 · 사용완료 이동과 되돌리기
  · 상세 · 흰 배경 고대비 바코드 크게 보기 · 금액권 남은 잔액 수정 · 삭제(확인 창)
  · 카테고리 칩 · 검색 · 결과 없음 안내
  · JSON 백업 내려받기 / 복원(사진 포함) / 잘못된 파일 안내
  · 390 / 1280 가로 넘침 0 · 모든 input font-size ≥ 16px · touch-action·overscroll 고정
  · 콘솔 오류 0 · 외부 전송 0 · 오프라인 실행
  · 스크린샷 downloads/coupon_box/01_home_390.png · 02_add_390.png · 03_detail_390.png · 04_crop_modal_390.png
"""
import asyncio
import base64
import io
import json
import os
import socket
import sys
import threading
from datetime import date, datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BASE))
SHOTS = os.path.join(ROOT, "downloads", "coupon_box")
FIX = os.path.join(BASE, ".dev", "fixtures")
TODAY = datetime.now(ZoneInfo("Asia/Seoul")).date()

PASS, FAIL, WARN, ERRORS, EXTERNAL = [], [], [], [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  ✓ {name}")
    else:
        FAIL.append(f"{name} — {detail}")
        print(f"  ✗ {name} — {detail}")


def warn(name, detail=""):
    WARN.append(f"{name} — {detail}")
    print(f"  ! {name} — {detail}")


def iso(days=0):
    return (TODAY + timedelta(days=days)).isoformat()


def kdate(days=0):
    d = TODAY + timedelta(days=days)
    return f"{d.year}년 {d.month}월 {d.day}일"


def comma(n):
    return f"{int(n):,}"


# ── 테스트 사진 ─────────────────────────
def font(size):
    path = "/usr/share/fonts/pickle-hwp/NotoSansCJKkr-Regular.otf"
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except Exception:
            return ImageFont.load_default()


def make_coupon(path, brand, title, sub, color="#0F6E3F", size=(1400, 1050), noisy=True):
    """쿠폰(기프티콘) 화면을 찍은 사진처럼 그린다."""
    W, H = size
    img = Image.new("RGB", (W, H), "#F4F6F8")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, int(H * 0.20)], fill=color)
    d.text((int(W * 0.06), int(H * 0.055)), brand, fill="#FFFFFF", font=font(int(H * 0.062)))
    d.text((int(W * 0.06), int(H * 0.135)), "GIFTICON", fill="#E8F5EE", font=font(int(H * 0.030)))

    card = [int(W * 0.05), int(H * 0.26), int(W * 0.95), int(H * 0.95)]
    d.rounded_rectangle(card, radius=36, fill="#FFFFFF", outline="#DFE4EA", width=4)
    d.text((int(W * 0.10), int(H * 0.31)), title, fill="#191F28", font=font(int(H * 0.055)))
    d.text((int(W * 0.10), int(H * 0.40)), sub, fill="#6B7684", font=font(int(H * 0.034)))

    bx0, by0, bx1, by1 = int(W * 0.10), int(H * 0.52), int(W * 0.90), int(H * 0.76)
    d.rectangle([bx1 - 4, by1 + 10, bx1, by1 + 10], fill="#FFFFFF")
    x = bx0
    i = 0
    while x < bx1 - 6:
        bw = 5 + (i * 7) % 13
        if i % 4 != 3:
            d.rectangle([x, by0, min(x + bw, bx1), by1], fill="#111418")
        x += bw + 6
        i += 1
    d.text((int(W * 0.10), int(H * 0.80)), "8801 2345 6789 0", fill="#4E5968", font=font(int(H * 0.030)))

    if noisy:
        grain = Image.effect_noise((W, H), 12).convert("RGB")
        img = Image.blend(img, grain, 0.08)
    img.save(path, "PNG")
    return path


CARD_BOX = (220, 180, 1180, 1020)   # 자동 맞춤 확인용: 어두운 배경 위 밝은 쿠폰 카드 위치


def make_card_on_dark(path, W=1400, H=1200, box=CARD_BOX):
    """어두운 책상 위에 놓인 밝은 기프티콘 화면 — 자동 맞춤(영역 감지) 확인용."""
    img = Image.new("RGB", (W, H), "#22262B")
    px = img.load()
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            v = 30 + ((x * 5 + y * 11) % 9)
            px[x, y] = (v, v + 2, v + 6)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=28, fill="#FFFFFF")
    d.rectangle([box[0], box[1], box[2], box[1] + 140], fill="#0F6E3F")
    d.text((box[0] + 40, box[1] + 40), "STARBUCKS", fill="#FFFFFF", font=font(52))
    d.text((box[0] + 60, box[1] + 210), "아메리카노 Tall 1잔", fill="#191F28", font=font(44))
    d.text((box[0] + 60, box[1] + 290), "유효기간 2026.12.31까지", fill="#6B7684", font=font(30))
    bx = box[0] + 90
    i = 0
    while bx < box[2] - 120:
        bw = 6 + (i * 7) % 13
        d.rectangle([bx, box[1] + 380, bx + bw, box[1] + 600], fill="#111418")
        bx += bw + 8
        i += 1
    d.text((box[0] + 60, box[1] + 640), "8801 2345 6789 0", fill="#4E5968", font=font(30))
    img.save(path, "JPEG", quality=88)
    return path


def make_uniform(path, W=1200, H=900, color="#6E6E6E"):
    """전체가 고른 색 — 자동 맞춤이 포기하고 전체로 폴백하는지 확인용."""
    Image.new("RGB", (W, H), color).save(path, "JPEG", quality=88)
    return path


def data_url_of(path):
    with open(path, "rb") as fh:
        return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()


# ── 서버 ─────────────────────────
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    handler = lambda *a, **kw: QuietHandler(*a, directory=BASE, **kw)
    srv = HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


# ── 시드 ─────────────────────────
def build_seed(starbucks_url):
    return [
        dict(id="seed-starbucks", brand="스타벅스", name="아메리카노 Tall 1잔", category="cafe",
             expiry=iso(1), giver="김안숙", memo="장모님 생신 선물",
             photos=[starbucks_url], createdAt=100, updatedAt=100, used=False),
        dict(id="seed-bhc", brand="BHC", name="뿌링클 + 콜라 1.25L", category="food",
             expiry=iso(3), giver="정윤선", memo="", photos=[], createdAt=110, updatedAt=110, used=False),
        dict(id="seed-cu", brand="CU", name="모바일 상품권 5,000원", category="mart",
             expiry=iso(7), giver="", memo="현금영수증 필요", photos=[],
             amount=5000, balance=3500, createdAt=120, updatedAt=120, used=False),
        dict(id="seed-mega", brand="메가MGC커피", name="아이스 아메리카노 1잔", category="cafe",
             expiry=iso(15), giver="", memo="", photos=[], createdAt=130, updatedAt=130, used=False),
        dict(id="seed-baskin", brand="배스킨라빈스", name="파인트 교환권", category="cafe",
             expiry=None, giver="엄마", memo="", photos=[], createdAt=140, updatedAt=140, used=False),
        dict(id="seed-paris", brand="파리바게뜨", name="생일 케이크 10% 할인", category="cafe",
             expiry=iso(-3), giver="", memo="", photos=[], createdAt=150, updatedAt=150, used=False),
        dict(id="seed-tous", brand="투썸플레이스", name="카페라떼 1잔", category="cafe",
             expiry=iso(20), giver="김하랑", memo="", photos=[], createdAt=160, updatedAt=160,
             used=True, usedAt=iso(-2)),
        dict(id="seed-domino", brand="도미노피자", name="포테이토 피자 L", category="food",
             expiry=iso(2), giver="", memo="", photos=[], createdAt=170, updatedAt=170,
             used=True, usedAt=iso(-5)),
    ]


AVAILABLE_ORDER = ["seed-starbucks", "seed-bhc", "seed-cu", "seed-mega", "seed-baskin", "seed-paris"]
DONE_ORDER = ["seed-tous", "seed-domino"]


# ── 페이지 ─────────────────────────
async def new_page(browser, width=390, height=844, accept_downloads=True, dsf=1):
    last = None
    for attempt in range(3):
        try:
            ctx = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=dsf,
                accept_downloads=accept_downloads,
                locale="ko-KR",
                timezone_id="Asia/Seoul",
            )
            page = await ctx.new_page()
            page.on("console", lambda m: ERRORS.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
            page.on("pageerror", lambda e: ERRORS.append(f"[pageerror] {e}"))
            page.on("request", lambda r: EXTERNAL.append(r.url)
                    if r.url.startswith("http") and "127.0.0.1" not in r.url else None)
            return ctx, page
        except Exception as exc:  # 환경이 잠깐 막힐 때 한 번 더
            last = exc
            await asyncio.sleep(1.5)
    raise last


async def goto(page, port):
    await page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="networkidle")
    await page.wait_for_function("document.documentElement.dataset.ready === '1'")


async def seed(page, items):
    await page.evaluate("(items) => window.__couponbox.replaceAll(items)", items)
    await page.wait_for_timeout(120)


async def idb_items(page):
    return await page.evaluate("""async () => {
        const db = await new Promise((res, rej) => {
            const r = indexedDB.open('coupon-box', 1);
            r.onsuccess = () => res(r.result);
            r.onerror = () => rej(r.error);
        });
        return await new Promise((res, rej) => {
            const req = db.transaction('coupons', 'readonly').objectStore('coupons').getAll();
            req.onsuccess = () => res(req.result);
            req.onerror = () => rej(req.error);
        });
    }""")


async def card_ids(page):
    return await page.evaluate("() => [...document.querySelectorAll('.coupon')].map(c => c.dataset.id)")


async def open_detail(page, cid):
    await page.click(f'.coupon[data-id="{cid}"]')
    await page.wait_for_selector("#detailWrap:not([hidden])")
    await page.wait_for_timeout(220)


async def open_add(page):
    await page.click("#addCard")
    await page.wait_for_selector("#addWrap:not([hidden])")
    await page.wait_for_timeout(220)


async def pick_photo(page, paths, mode="apply", wait=680):
    """사진 선택 → 자르기 창 확인 → ‘자르기 적용’ 또는 ‘원본 그대로’"""
    if isinstance(paths, (str, os.PathLike)):
        paths = [str(paths)]
    await page.set_input_files("#photoInput", paths)
    await page.wait_for_selector("#cropWrap:not([hidden])")
    await page.wait_for_timeout(280)
    await page.click("#cropApply" if mode == "apply" else "#cropKeep")
    await page.wait_for_timeout(wait)


async def font_sizes(page):
    return await page.evaluate("""() => [...document.querySelectorAll('input, textarea, select')].map(el => ({
        id: el.id || el.getAttribute('name') || el.type,
        type: el.type || el.tagName.toLowerCase(),
        size: parseFloat(getComputedStyle(el).fontSize),
    }))""")


def all_big(rows):
    bad = [r for r in rows if not (r["size"] >= 16)]
    return (len(bad) == 0), bad


async def run():
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(FIX, exist_ok=True)
    sb_photo = make_coupon(os.path.join(FIX, "starbucks.png"), "STARBUCKS", "아메리카노 Tall 1잔", "유효기간 2026.12.31까지", "#0F6E3F")
    big_photo = make_coupon(os.path.join(FIX, "big.png"), "BHC", "뿌링클 + 콜라", "큰 사진 압축 확인용", "#B4212C", size=(2400, 1800))
    auto_dark = make_card_on_dark(os.path.join(FIX, "auto_dark.jpg"))
    auto_flat = make_uniform(os.path.join(FIX, "auto_flat.jpg"))
    starbucks_url = data_url_of(sb_photo)
    SEED = build_seed(starbucks_url)

    srv, port = start_server()
    print(f"· 검증 서버 http://127.0.0.1:{port}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])

        # ═════════════════════════════════════════════
        print("\n[1] PWA 자원 · 첫 화면 구조")
        ctx0, page0 = await new_page(browser)
        await goto(page0, port)
        check("앱 제목", await page0.title() == "쿠폰함", await page0.title())
        check("브랜드 제목", await page0.inner_text(".brand-title") == "쿠폰함",
              await page0.inner_text(".brand-title"))
        meta = await page0.get_attribute('meta[name="viewport"]', "content")
        check("아이폰 화면 고정 viewport",
              meta == "width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover",
              str(meta))
        check("PWA 타이틀 메타",
              await page0.get_attribute('meta[name="apple-mobile-web-app-title"]', "content") == "쿠폰함")
        check("standalone 메타",
              await page0.get_attribute('meta[name="apple-mobile-web-app-capable"]', "content") == "yes")
        check("빈 상태 안내", await page0.is_visible("#emptyState"))
        check("빈 상태 문구", "아직 담아 둔 쿠폰이 없어요" in await page0.inner_text("#emptyTitle"),
              await page0.inner_text("#emptyTitle"))
        check("처음 목록 개수", await page0.inner_text("#listCount") == "쿠폰 0개",
              await page0.inner_text("#listCount"))
        check("처음 탭 개수 0/0",
              await page0.inner_text("#countAvailable") == "0" and await page0.inner_text("#countDone") == "0")

        tabs = await page0.locator("#tabTabs button").all_inner_texts()
        check("탭 두 개", len(tabs) == 2, str(tabs))
        check("탭: 사용 가능", "사용 가능" in tabs[0], tabs[0])
        check("탭: 사용완료", "사용완료" in tabs[1], tabs[1])
        check("첫 탭 선택", await page0.get_attribute('#tabTabs button[data-tab="available"]', "aria-selected") == "true")

        chips = await page0.locator("#catChips button").all_inner_texts()
        check("카테고리 칩 5개", chips == ["전체", "카페·간식", "외식·치킨", "마트·편의점", "기타"], str(chips))

        layout = await page0.evaluate("""() => {
            const g = document.querySelector('#couponGrid');
            const first = g.firstElementChild;
            const r = first.getBoundingClientRect();
            return {
                cols: getComputedStyle(g).gridTemplateColumns.split(' ').length,
                firstTag: first.tagName,
                firstId: first.id,
                firstText: first.innerText,
                width: Math.round(r.width),
                gap: getComputedStyle(g).gap,
            };
        }""")
        check("첫 화면 2열 그리드", layout["cols"] == 2, str(layout["cols"]))
        check("첫 칸이 쿠폰 사진등록 카드", layout["firstId"] == "addCard" and layout["firstTag"] == "BUTTON", str(layout))
        check("등록 카드 문구 ‘＋ 쿠폰 사진등록’",
              "쿠폰 사진등록" in layout["firstText"] and "＋" in layout["firstText"], layout["firstText"])
        check("등록 카드가 첫 줄 첫 칸", layout["width"] > 140, str(layout))

        manifest = await page0.evaluate("fetch('./manifest.webmanifest').then(r => r.json())")
        check("manifest 이름", manifest.get("short_name") == "쿠폰함", str(manifest.get("short_name")))
        check("manifest 전체 이름", manifest.get("name") == "쿠폰함")
        check("manifest standalone", manifest.get("display") == "standalone")
        check("manifest 아이콘 3종", len(manifest.get("icons", [])) == 3, str(len(manifest.get("icons", []))))
        check("manifest 테마 #F2F4F6", manifest.get("theme_color") == "#F2F4F6", str(manifest.get("theme_color")))
        check("manifest 시작 주소", manifest.get("start_url") == "./")
        sw = await page0.evaluate("fetch('./sw.js').then(r => r.text())")
        check("sw 캐시 이름 coupon-box-v4", "coupon-box-v4" in sw)
        html = await page0.evaluate("fetch('./index.html').then(r => r.text())")
        check("index 버전 쿼리", "styles.css?v=4" in html and "app.js?v=4" in html)
        for asset in ["icon.svg", "icon-180.png", "icon-192.png", "icon-512.png",
                      "icon-maskable-512.png", "manifest.webmanifest", "app.js", "styles.css"]:
            ok = await page0.evaluate(f"fetch('./{asset}').then(r => r.ok)")
            check(f"{asset} 제공", ok)
        with Image.open(os.path.join(BASE, "icon-512.png")) as im:
            check("icon-512 512×512", im.size == (512, 512), str(im.size))
            corners = [im.convert("RGB").getpixel(xy) for xy in [(2, 2), (509, 2), (2, 509), (509, 509)]]
            check("아이콘 꽉 찬 사각형(모서리 파랑)",
                  all(c[2] > 200 and c[0] < 100 for c in corners), str(corners))
            core = im.convert("RGB").crop((190, 160, 330, 350))
            plain = sum(1 for px in core.getdata() if px[0] > 235 and px[1] > 235 and px[2] > 235)
            check("아이콘 가운데 흰 티켓", plain > 3000, f"{plain}px")
        with Image.open(os.path.join(BASE, "icon-180.png")) as im:
            check("icon-180 180×180", im.size == (180, 180), str(im.size))
        app_src = open(os.path.join(BASE, "app.js"), encoding="utf-8").read()
        check("실시간 공유 없음", "firebase" not in app_src.lower() and "websocket" not in app_src.lower())
        check("IndexedDB 저장", "indexedDB" in app_src and "indexedDB.open(DB_NAME" in app_src)
        check("백업 내려받기 구현", "coupon-box-backup-" in app_src)

        styles = await page0.evaluate("""() => {
            const cs = getComputedStyle(document.body);
            const hs = getComputedStyle(document.documentElement);
            return {
                overscroll: cs.overscrollBehaviorY,
                overflowX: hs.overflowX,
                textAdjust: hs.webkitTextSizeAdjust || hs.textSizeAdjust,
                touch: getComputedStyle(document.querySelector('#addCard')).touchAction,
            };
        }""")
        check("body overscroll-behavior-y: contain", styles["overscroll"] == "contain", str(styles["overscroll"]))
        check("html overflow-x: hidden", styles["overflowX"] == "hidden", str(styles["overflowX"]))
        check("text-size-adjust 100%", styles["textAdjust"] in ("100%", "100% auto"), str(styles["textAdjust"]))
        check("touch-action: manipulation", styles["touch"] == "manipulation", str(styles["touch"]))

        rows = await font_sizes(page0)
        ok, bad = all_big(rows)
        check("모든 입력 칸 font-size ≥ 16px (홈)", ok, str(bad[:3]))
        await ctx0.close()

        # ═════════════════════════════════════════════
        print("\n[2] 사진 등록 · 저장 · IndexedDB")
        ctx1, page1 = await new_page(browser)
        await goto(page1, port)
        await open_add(page1)
        check("등록 시트 열림", await page1.is_visible("#addWrap"))
        check("등록 제목", await page1.inner_text("#addTitle") == "쿠폰 등록", await page1.inner_text("#addTitle"))
        check("사진 영역 먼저", await page1.evaluate(
            "() => document.querySelector('#photoGrid').getBoundingClientRect().top < document.querySelector('#fName').getBoundingClientRect().top"))
        check("유효기간 기본 비어 있음", await page1.input_value("#fExpiry") == "")
        check("카페·간식 기본 선택",
              await page1.get_attribute('#catSeg button[data-cat="cafe"]', "aria-pressed") == "true")

        await page1.click("#addSave")
        await page1.wait_for_timeout(300)
        check("이름 없이 저장하면 안내", "쿠폰명을 적어 주세요" in await page1.inner_text("#toast"),
              await page1.inner_text("#toast"))
        check("이름 없으면 시트 유지", await page1.is_visible("#addWrap"))

        await pick_photo(page1, big_photo, "keep")
        src = await page1.evaluate("() => window.__couponbox.form.photos[0]")
        check("사진은 data URL JPEG", src.startswith("data:image/jpeg;base64,"), src[:32])
        raw = base64.b64decode(src.split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as im:
            check("사진 1600px 이하로 압축", max(im.size) <= 1600, str(im.size))
            check("사진 비율 유지(4:3)", abs(im.size[0] / im.size[1] - 4 / 3) < 0.02, str(im.size))
        check("원본보다 작게 저장", len(raw) < os.path.getsize(big_photo) / 3,
              f"{len(raw)} vs {os.path.getsize(big_photo)}")

        await pick_photo(page1, big_photo, "keep")
        await page1.wait_for_timeout(200)
        await pick_photo(page1, big_photo, "keep")
        check("사진 3장까지", await page1.locator(".photo-cell").count() == 3,
              str(await page1.locator(".photo-cell").count()))
        await page1.set_input_files("#photoInput", big_photo)
        await page1.wait_for_timeout(400)
        check("4장째는 안내", "3장까지" in await page1.inner_text("#toast"), await page1.inner_text("#toast"))
        check("4장째는 담기지 않음", await page1.locator(".photo-cell").count() == 3)
        await page1.click('.photo-del[data-remove="2"]')
        await page1.wait_for_timeout(150)
        check("사진 빼기", await page1.locator(".photo-cell").count() == 2)
        await page1.click('.photo[data-photo="0"]')
        await page1.wait_for_timeout(200)
        check("폼에서 사진 크게 보기", await page1.is_visible("#zoomWrap"))
        await page1.click("#zoomClose")
        await page1.wait_for_timeout(150)

        await page1.click("#fNoExpiry")
        check("기한 없음 켜기", await page1.get_attribute("#fNoExpiry", "aria-checked") == "true")
        check("기한 없음이면 날짜 잠김", await page1.is_disabled("#fExpiry"))
        await page1.click("#fNoExpiry")
        check("기한 없음 끄기", await page1.is_enabled("#fExpiry"))

        await page1.fill("#fName", "아메리카노 Tall 1잔")
        await page1.fill("#fBrand", "스타벅스")
        await page1.click('#catSeg button[data-cat="food"]')
        check("카테고리 선택", await page1.get_attribute('#catSeg button[data-cat="food"]', "aria-pressed") == "true")
        await page1.fill("#fExpiry", iso(10))
        await page1.fill("#fGiver", "김안숙")
        await page1.fill("#fMemo", "생일 선물 · 유효기간 확인")
        await page1.click("#fIsAmount")
        check("금액권 켜면 금액 칸", await page1.is_visible("#fBalance"))
        await page1.fill("#fAmount", "10000")
        await page1.fill("#fBalance", "4000")
        check("금액 콤마 표시", await page1.input_value("#fAmount") == "10,000", await page1.input_value("#fAmount"))
        check("잔액 콤마 표시", await page1.input_value("#fBalance") == "4,000", await page1.input_value("#fBalance"))

        await page1.click("#addSave")
        await page1.wait_for_timeout(600)
        check("저장 후 시트 닫힘", await page1.is_hidden("#addWrap"))
        check("저장 안내", "쿠폰을 담았어요" in await page1.inner_text("#toast"), await page1.inner_text("#toast"))
        check("카드 1장", await page1.locator(".coupon").count() == 1, str(await page1.locator(".coupon").count()))
        card = await page1.inner_text(".coupon")
        check("카드 브랜드", "스타벅스" in card, card)
        check("카드 쿠폰명", "아메리카노 Tall 1잔" in card, card)
        check("카드 D-day", "D-10" in card, card)
        check("카드 준 사람·잔액", "김안숙" in card and "잔액 4,000원" in card, card)
        check("카드 썸네일", await page1.locator(".coupon .thumb img").count() == 1)
        check("탭 개수 반영 1/0",
              await page1.inner_text("#countAvailable") == "1" and await page1.inner_text("#countDone") == "0")

        stored = await idb_items(page1)
        check("IndexedDB에 1건", len(stored) == 1, str(len(stored)))
        if stored:
            it = stored[0]
            check("저장 이름", it["name"] == "아메리카노 Tall 1잔", it["name"])
            check("저장 브랜드", it["brand"] == "스타벅스", it["brand"])
            check("저장 카테고리", it["category"] == "food", it["category"])
            check("저장 유효기간", it["expiry"] == iso(10), str(it["expiry"]))
            check("저장 준 사람", it["giver"] == "김안숙", it["giver"])
            check("저장 메모", it["memo"] == "생일 선물 · 유효기간 확인", it["memo"])
            check("저장 금액 10000", it["amount"] == 10000, str(it["amount"]))
            check("저장 잔액 4000", it["balance"] == 4000, str(it["balance"]))
            check("저장 사진 2장", len(it["photos"]) == 2, str(len(it["photos"])))
            check("사진도 IndexedDB에", it["photos"][0].startswith("data:image/jpeg"))

        await page1.reload(wait_until="networkidle")
        await page1.wait_for_function("document.documentElement.dataset.ready === '1'")
        await page1.wait_for_timeout(250)
        check("새로고침해도 남아 있음", await page1.locator(".coupon").count() == 1,
              str(await page1.locator(".coupon").count()))
        check("새로고침 후 썸네일", await page1.locator(".coupon .thumb img").count() == 1)
        rows = await font_sizes(page1)
        ok, bad = all_big(rows)
        check("모든 입력 칸 font-size ≥ 16px (홈·검색)", ok, str(bad[:3]))
        await ctx1.close()

        # ═════════════════════════════════════════════
        print("\n[3] D-day 자동 정렬 · 임박 배지")
        ctx2, page2 = await new_page(browser)
        await goto(page2, port)
        await seed(page2, SEED)
        check("사용 가능 6장", await page2.locator(".coupon").count() == 6,
              str(await page2.locator(".coupon").count()))
        ids = await card_ids(page2)
        check("사용 가능 탭은 쓴 쿠폰 제외", "seed-tous" not in ids and "seed-domino" not in ids, str(ids))
        check("기한 임박 순 정렬", ids == AVAILABLE_ORDER, str(ids))
        check("기한 없음은 뒤", ids.index("seed-baskin") == 4, str(ids.index("seed-baskin") if "seed-baskin" in ids else -1))
        check("기한 지난 쿠폰은 맨 뒤", ids[-1] == "seed-paris", str(ids[-1]))

        badges = await page2.evaluate("""() => [...document.querySelectorAll('.coupon')].map(c => ({
            id: c.dataset.id,
            label: c.querySelector('.dday').textContent.trim(),
            cls: c.querySelector('.dday').className,
            right: Math.round(c.querySelector('.dday').getBoundingClientRect().right),
            cardRight: Math.round(c.getBoundingClientRect().right),
        }))""")
        by_id = {b["id"]: b for b in badges}
        check("D-1 배지", by_id["seed-starbucks"]["label"] == "D-1", by_id["seed-starbucks"]["label"])
        check("D-1 빨강 임박", "urgent" in by_id["seed-starbucks"]["cls"], by_id["seed-starbucks"]["cls"])
        check("D-3 빨강 임박", by_id["seed-bhc"]["label"] == "D-3" and "urgent" in by_id["seed-bhc"]["cls"],
              by_id["seed-bhc"])
        check("D-7 주황 임박", by_id["seed-cu"]["label"] == "D-7" and "soon" in by_id["seed-cu"]["cls"],
              by_id["seed-cu"])
        check("D-15 일반", by_id["seed-mega"]["label"] == "D-15" and "normal" in by_id["seed-mega"]["cls"],
              by_id["seed-mega"])
        check("기한 없음 배지", by_id["seed-baskin"]["label"] == "기한 없음" and "none" in by_id["seed-baskin"]["cls"],
              by_id["seed-baskin"])
        check("기한 지남 배지", by_id["seed-paris"]["label"] == "기한 지남" and "expired" in by_id["seed-paris"]["cls"],
              by_id["seed-paris"])
        urgent_color = await page2.evaluate(
            "() => getComputedStyle(document.querySelector('.coupon[data-id=\"seed-starbucks\"] .dday')).color")
        soon_color = await page2.evaluate(
            "() => getComputedStyle(document.querySelector('.coupon[data-id=\"seed-cu\"] .dday')).color")
        check("임박 배지는 붉은색", urgent_color == "rgb(228, 41, 57)", urgent_color)
        check("D-7 배지는 주황색", soon_color == "rgb(194, 103, 10)", soon_color)
        check("배지는 카드 오른쪽 위",
              by_id["seed-starbucks"]["cardRight"] - by_id["seed-starbucks"]["right"] < 20,
              str(by_id["seed-starbucks"]))

        note = await page2.inner_text("#soonNote")
        check("7일 안 쿠폰 안내", "7일 안에 쓸 쿠폰이 3장" in note, note)
        check("목록 개수", await page2.inner_text("#listCount") == "쿠폰 6개", await page2.inner_text("#listCount"))
        check("탭 개수 6/2",
              await page2.inner_text("#countAvailable") == "6" and await page2.inner_text("#countDone") == "2",
              f"{await page2.inner_text('#countAvailable')}/{await page2.inner_text('#countDone')}")
        await page2.click("#tabTabs button[data-tab=\"done\"]")
        await page2.wait_for_timeout(250)
        check("사용완료 탭엔 등록 카드 없음", await page2.locator("#addCard").count() == 0)
        check("사용완료 정렬(최근 사용 순)", await card_ids(page2) == DONE_ORDER, str(await card_ids(page2)))
        done_badge = await page2.evaluate(
            "() => document.querySelector('.coupon[data-id=\"seed-tous\"] .dday').textContent.trim()")
        check("사용완료 배지", done_badge == "사용완료", done_badge)
        check("사용완료 도장", await page2.locator('.coupon[data-id="seed-tous"] .used-stamp').count() == 1)
        check("사용완료 개수 문구", await page2.inner_text("#listCount") == "사용완료 2개",
              await page2.inner_text("#listCount"))
        await page2.click("#tabTabs button[data-tab=\"available\"]")
        await page2.wait_for_timeout(250)
        await ctx2.close()

        # ═════════════════════════════════════════════
        print("\n[4] 사용완료 이동 · 되돌리기")
        ctx3, page3 = await new_page(browser)
        await goto(page3, port)
        await seed(page3, SEED)
        await open_detail(page3, "seed-mega")
        check("상세 사용완료 버튼", await page3.inner_text("#detailUse") == "사용완료",
              await page3.inner_text("#detailUse"))
        await page3.click("#detailUse")
        await page3.wait_for_timeout(500)
        check("사용완료 안내", "사용완료로 넘겼어요" in await page3.inner_text("#toast"),
              await page3.inner_text("#toast"))
        check("사용완료 후 상세 닫힘", await page3.is_hidden("#detailWrap"))
        check("사용 가능에서 사라짐", "seed-mega" not in await card_ids(page3), str(await card_ids(page3)))
        check("탭 개수 5/3",
              await page3.inner_text("#countAvailable") == "5" and await page3.inner_text("#countDone") == "3",
              f"{await page3.inner_text('#countAvailable')}/{await page3.inner_text('#countDone')}")
        stored = await idb_items(page3)
        mega = [i for i in stored if i["id"] == "seed-mega"]
        check("사용완료 저장", mega and mega[0]["used"] is True and mega[0]["usedAt"] == iso(0),
              str(mega[0]["usedAt"] if mega else None))
        await page3.click("#tabTabs button[data-tab=\"done\"]")
        await page3.wait_for_timeout(300)
        check("사용완료 탭 맨 앞", (await card_ids(page3))[0] == "seed-mega", str(await card_ids(page3)))
        await open_detail(page3, "seed-mega")
        check("되돌리기 버튼", await page3.inner_text("#detailUse") == "되돌리기", await page3.inner_text("#detailUse"))
        await page3.click("#detailUse")
        await page3.wait_for_timeout(500)
        check("되돌리기 안내", "사용 가능으로 되돌렸어요" in await page3.inner_text("#toast"),
              await page3.inner_text("#toast"))
        check("탭 개수 6/2 복귀",
              await page3.inner_text("#countAvailable") == "6" and await page3.inner_text("#countDone") == "2")
        await page3.click("#tabTabs button[data-tab=\"available\"]")
        await page3.wait_for_timeout(300)
        check("사용 가능으로 복귀", "seed-mega" in await card_ids(page3), str(await card_ids(page3)))
        mega_card = await page3.inner_text('.coupon[data-id="seed-mega"]')
        check("복귀 후 D-day 그대로", "D-15" in mega_card, mega_card)
        stored = await idb_items(page3)
        mega = [i for i in stored if i["id"] == "seed-mega"][0]
        check("되돌리기 저장", mega["used"] is False and mega["usedAt"] is None, str(mega["usedAt"]))
        await ctx3.close()

        # ═════════════════════════════════════════════
        print("\n[5] 상세 · 바코드 흰 배경 크게 보기")
        ctx4, page4 = await new_page(browser)
        await goto(page4, port)
        await seed(page4, SEED)
        await open_detail(page4, "seed-starbucks")
        check("상세 제목 브랜드", await page4.inner_text("#detailTitle") == "스타벅스", await page4.inner_text("#detailTitle"))
        body = await page4.inner_text("#detailBody")
        check("상세 쿠폰명", "아메리카노 Tall 1잔" in body, body[:120])
        check("상세 D-day", "D-1" in body, body[:60])
        check("상세 남은 날", "1일 남았어요" in body and kdate(1) in body, body[:200])
        check("상세 카테고리", "카페·간식" in body)
        check("상세 준 사람", "김안숙" in body)
        check("상세 메모", "장모님 생신 선물" in body)
        check("상세 사진 1장", await page4.locator("#detailBody .shot").count() == 1)
        await page4.click('#detailBody .shot[data-zoom="0"]')
        await page4.wait_for_selector("#zoomWrap:not([hidden])")
        await page4.wait_for_timeout(250)
        zoom = await page4.evaluate("""() => {
            const w = document.querySelector('#zoomWrap');
            const img = document.querySelector('#zoomImg');
            const cs = getComputedStyle(w);
            const ics = getComputedStyle(img);
            return {
                bg: cs.backgroundColor,
                src: img.getAttribute('src') || '',
                fit: ics.objectFit,
                w: Math.round(img.getBoundingClientRect().width),
                h: Math.round(img.getBoundingClientRect().height),
                natural: [img.naturalWidth, img.naturalHeight],
                viewport: window.innerWidth,
            };
        }""")
        check("확대 화면 흰 배경", zoom["bg"] == "rgb(255, 255, 255)", zoom["bg"])
        check("확대 사진 = 등록 사진", zoom["src"] == starbucks_url)
        check("확대 사진 비율 유지", zoom["fit"] == "contain", zoom["fit"])
        check("확대 사진이 화면 폭을 채움", zoom["w"] >= zoom["viewport"] - 36, str(zoom["w"]))
        check("확대 사진 원본 화질", zoom["natural"][0] >= 1200, str(zoom["natural"]))
        await page4.screenshot(path=os.path.join(FIX, "_zoom_check.png"))
        await page4.click("#zoomClose")
        await page4.wait_for_timeout(200)
        check("확대 닫기", await page4.is_hidden("#zoomWrap"))
        await page4.click("#detailClose")
        await page4.wait_for_timeout(200)

        await page4.click('#tabTabs button[data-tab="done"]')
        await page4.wait_for_timeout(300)
        await open_detail(page4, "seed-tous")
        body = await page4.inner_text("#detailBody")
        check("쓴 쿠폰 상세 문구", "사용완료했어요" in body, body[:160])
        check("쓴 쿠폰 날짜", kdate(-2) in body, body[:160])
        check("사진 없는 쿠폰 안내", "등록된 사진이 없어요" in body)
        await page4.click("#detailClose")
        await page4.wait_for_timeout(200)
        rows = await font_sizes(page4)
        ok, bad = all_big(rows)
        check("모든 입력 칸 font-size ≥ 16px (홈)", ok, str(bad[:3]))
        await ctx4.close()

        # ═════════════════════════════════════════════
        print("\n[6] 금액권 남은 잔액")
        ctx5, page5 = await new_page(browser)
        await goto(page5, port)
        await seed(page5, SEED)
        await open_detail(page5, "seed-cu")
        body = await page5.inner_text("#detailBody")
        check("금액 표시", "5,000원" in body, body[:200])
        check("남은 잔액 기본", await page5.input_value("#detailBalance") == "3,500",
              await page5.input_value("#detailBalance"))
        await page5.fill("#detailBalance", "2000")
        check("잔액 콤마 자동", await page5.input_value("#detailBalance") == "2,000",
              await page5.input_value("#detailBalance"))
        await page5.click("#balanceSave")
        await page5.wait_for_timeout(500)
        check("잔액 저장 안내", "남은 잔액을 저장했어요" in await page5.inner_text("#toast"),
              await page5.inner_text("#toast"))
        body = await page5.inner_text("#detailBody")
        check("상세 잔액 갱신",
              "남은 잔액" in body and await page5.input_value("#detailBalance") == "2,000",
              f"{body[:120]} / {await page5.input_value('#detailBalance')}")
        check("상세 금액은 그대로", "5,000원" in body, body[:120])
        await page5.click("#detailClose")
        await page5.wait_for_timeout(250)
        cu_card = await page5.inner_text('.coupon[data-id="seed-cu"]')
        check("카드에 잔액 반영", "잔액 2,000원" in cu_card, cu_card)
        stored = await idb_items(page5)
        cu = [i for i in stored if i["id"] == "seed-cu"][0]
        check("잔액 저장됨", cu["balance"] == 2000 and cu["amount"] == 5000, str(cu["balance"]))
        await open_detail(page5, "seed-cu")
        await page5.fill("#detailBalance", "99999")
        await page5.click("#balanceSave")
        await page5.wait_for_timeout(400)
        check("금액보다 큰 잔액 거절", "금액보다 클 수 없어요" in await page5.inner_text("#toast"),
              await page5.inner_text("#toast"))
        stored = await idb_items(page5)
        check("거절된 잔액은 저장 안 함",
              [i for i in stored if i["id"] == "seed-cu"][0]["balance"] == 2000)
        await ctx5.close()

        # ═════════════════════════════════════════════
        print("\n[7] 검색 · 카테고리 칩")
        ctx6, page6 = await new_page(browser)
        await goto(page6, port)
        await seed(page6, SEED)
        await page6.fill("#searchInput", "스타벅스")
        await page6.wait_for_timeout(300)
        check("브랜드 검색", await card_ids(page6) == ["seed-starbucks"], str(await card_ids(page6)))
        await page6.fill("#searchInput", "엄마")
        await page6.wait_for_timeout(300)
        check("준 사람 검색", await card_ids(page6) == ["seed-baskin"], str(await card_ids(page6)))
        await page6.fill("#searchInput", "현금영수증")
        await page6.wait_for_timeout(300)
        check("메모 검색", await card_ids(page6) == ["seed-cu"], str(await card_ids(page6)))
        await page6.fill("#searchInput", "없는쿠폰")
        await page6.wait_for_timeout(300)
        check("검색 결과 없음", await card_ids(page6) == [], str(await card_ids(page6)))
        check("검색 결과 없음 안내", "찾는 쿠폰이 없어요" in await page6.inner_text("#emptyTitle"),
              await page6.inner_text("#emptyTitle"))
        await page6.click("#searchClear")
        await page6.wait_for_timeout(300)
        check("검색 지우기", await card_ids(page6) == AVAILABLE_ORDER, str(await card_ids(page6)))

        await page6.click('#catChips button[data-cat="cafe"]')
        await page6.wait_for_timeout(300)
        cafe_ids = await card_ids(page6)
        check("카페·간식 필터", cafe_ids == ["seed-starbucks", "seed-mega", "seed-baskin", "seed-paris"], str(cafe_ids))
        check("칩 선택 표시", await page6.get_attribute('#catChips button[data-cat="cafe"]', "aria-pressed") == "true")
        check("필터 중 등록 카드 유지", await page6.locator("#addCard").count() == 1)
        await page6.click('#catChips button[data-cat="food"]')
        await page6.wait_for_timeout(300)
        check("외식·치킨 필터", await card_ids(page6) == ["seed-bhc"], str(await card_ids(page6)))
        await page6.click('#tabTabs button[data-tab="done"]')
        await page6.wait_for_timeout(300)
        check("탭+카테고리 함께", await card_ids(page6) == ["seed-domino"], str(await card_ids(page6)))
        await page6.click('#catChips button[data-cat="mart"]')
        await page6.wait_for_timeout(300)
        check("결과 없음 문구(카테고리)", "이 카테고리에 쿠폰이 없어요" in await page6.inner_text("#emptyTitle"),
              await page6.inner_text("#emptyTitle"))
        await page6.click('#catChips button[data-cat="all"]')
        await page6.click('#tabTabs button[data-tab="available"]')
        await page6.wait_for_timeout(300)
        check("전체로 복귀", await card_ids(page6) == AVAILABLE_ORDER)
        await ctx6.close()

        # ═════════════════════════════════════════════
        print("\n[8] 수정 · 삭제")
        ctx7, page7 = await new_page(browser)
        await goto(page7, port)
        await seed(page7, SEED)
        await open_detail(page7, "seed-bhc")
        await page7.click("#detailEdit")
        await page7.wait_for_selector("#addWrap:not([hidden])")
        await page7.wait_for_timeout(250)
        check("수정 제목", await page7.inner_text("#addTitle") == "쿠폰 수정", await page7.inner_text("#addTitle"))
        check("수정 값 채움", await page7.input_value("#fName") == "뿌링클 + 콜라 1.25L", await page7.input_value("#fName"))
        check("수정 유효기간 채움", await page7.input_value("#fExpiry") == iso(3), await page7.input_value("#fExpiry"))
        check("수정 카테고리 표시", await page7.get_attribute('#catSeg button[data-cat="food"]', "aria-pressed") == "true")
        await page7.fill("#fName", "뿌링클 순살 + 콜라 1.25L")
        await page7.fill("#fExpiry", iso(2))
        await page7.click("#addSave")
        await page7.wait_for_timeout(600)
        check("수정 안내", "수정했어요" in await page7.inner_text("#toast"), await page7.inner_text("#toast"))
        bhc = await page7.inner_text('.coupon[data-id="seed-bhc"]')
        check("카드에 수정 반영", "뿌링클 순살" in bhc and "D-2" in bhc, bhc)
        stored = await idb_items(page7)
        bhc_item = [i for i in stored if i["id"] == "seed-bhc"][0]
        check("수정 저장", bhc_item["name"] == "뿌링클 순살 + 콜라 1.25L" and bhc_item["expiry"] == iso(2),
              str(bhc_item["expiry"]))
        check("등록 시각 유지", bhc_item["createdAt"] == 110, str(bhc_item["createdAt"]))

        await open_detail(page7, "seed-paris")
        await page7.click("#detailDelete")
        await page7.wait_for_timeout(300)
        check("삭제 확인 창", await page7.is_visible("#confirmWrap"))
        check("확인 창에 쿠폰명", "생일 케이크" in await page7.inner_text("#confirmDesc"),
              await page7.inner_text("#confirmDesc"))
        await page7.click("#confirmCancel")
        await page7.wait_for_timeout(300)
        check("취소하면 그대로", "seed-paris" in await card_ids(page7), str(await card_ids(page7)))
        await page7.click("#detailDelete")
        await page7.wait_for_timeout(250)
        await page7.click("#confirmOk")
        await page7.wait_for_timeout(500)
        check("삭제 후 상세 닫힘", await page7.is_hidden("#detailWrap"))
        check("삭제 안내", "쿠폰을 지웠어요" in await page7.inner_text("#toast"), await page7.inner_text("#toast"))
        check("목록에서 사라짐", "seed-paris" not in await card_ids(page7), str(await card_ids(page7)))
        stored = await idb_items(page7)
        check("저장소에서도 삭제", not any(i["id"] == "seed-paris" for i in stored))
        check("남은 쿠폰 5장", await page7.locator(".coupon").count() == 5,
              str(await page7.locator(".coupon").count()))
        await ctx7.close()

        # ═════════════════════════════════════════════
        print("\n[9] JSON 백업 · 복원")
        ctx8, page8 = await new_page(browser)
        await goto(page8, port)
        await seed(page8, SEED)
        await page8.click("#moreBtn")
        await page8.wait_for_selector("#moreWrap:not([hidden])")
        await page8.wait_for_timeout(250)
        info = await page8.inner_text("#moreInfo")
        check("백업 정보 표시",
              "쿠폰 8개" in info and "사용 가능 6" in info and "사용완료 2" in info and "사진 1장" in info, info)
        check("저장 위치 표시", "IndexedDB" in info, info)

        backup_path = os.path.join(FIX, "coupon-backup.json")
        try:
            async with page8.expect_download(timeout=15000) as dl_info:
                await page8.click("#backupBtn")
            dl = await dl_info.value
            await dl.save_as(backup_path)
            check("백업 파일 이름", dl.suggested_filename == f"coupon-box-backup-{iso(0)}.json",
                  dl.suggested_filename)
            data = json.load(open(backup_path, encoding="utf-8"))
            check("백업 app 표시", data.get("app") == "coupon-box", str(data.get("app")))
            check("백업 쿠폰 수", len(data.get("items", [])) == 8, str(len(data.get("items", []))))
            check("백업에 사진 포함", any(it.get("photos") for it in data["items"]))
            check("백업 후 시트 닫힘", await page8.is_hidden("#moreWrap"))
        except Exception as exc:
            backup_path = None
            check("백업 내려받기", False, str(exc))

        if backup_path:
            ctx9, page9 = await new_page(browser)
            await goto(page9, port)
            check("복원 전 빈 목록", await page9.locator(".coupon").count() == 0)
            await page9.click("#moreBtn")
            await page9.wait_for_timeout(250)
            await page9.set_input_files("#restoreInput", backup_path)
            await page9.wait_for_timeout(700)
            check("복원 확인 창", await page9.is_visible("#confirmWrap"))
            check("복원 건수 안내", "8개" in await page9.inner_text("#confirmTitle"),
                  await page9.inner_text("#confirmTitle"))
            await page9.click("#confirmOk")
            await page9.wait_for_timeout(800)
            check("복원 후 목록", await page9.locator(".coupon").count() == 6,
                  str(await page9.locator(".coupon").count()))
            check("복원 후 정렬", await card_ids(page9) == AVAILABLE_ORDER, str(await card_ids(page9)))
            restored = await idb_items(page9)
            sb = [i for i in restored if i["id"] == "seed-starbucks"][0]
            check("복원 사진 동일", sb["photos"][0] == starbucks_url)
            check("복원 금액권", [i for i in restored if i["id"] == "seed-cu"][0]["balance"] == 3500)
            check("복원 사용완료", [i for i in restored if i["id"] == "seed-tous"][0]["used"] is True)
            check("복원 후 사용완료 탭", True)
            await page9.click("#tabTabs button[data-tab=\"done\"]")
            await page9.wait_for_timeout(300)
            check("복원 사용완료 2장", await page9.locator(".coupon").count() == 2,
                  str(await page9.locator(".coupon").count()))

            await page9.click("#moreBtn")
            await page9.wait_for_timeout(250)
            bad = os.path.join(FIX, "bad.json")
            open(bad, "w", encoding="utf-8").write('{"hello": "world"}')
            await page9.set_input_files("#restoreInput", bad)
            await page9.wait_for_timeout(600)
            check("잘못된 백업 안내", "백업 파일이 아니에요" in await page9.inner_text("#toast"),
                  await page9.inner_text("#toast"))
            check("잘못된 백업은 그대로", await page9.locator(".coupon").count() == 2)
            await ctx9.close()
        await ctx8.close()

        # ═════════════════════════════════════════════
        print("\n[10] 사진 자르기(크롭)")
        ctx7, page7 = await new_page(browser, width=390, height=844, dsf=2)
        await goto(page7, port)
        await open_add(page7)
        await page7.fill("#fName", "크롭 테스트 쿠폰")

        await page7.set_input_files("#photoInput", [sb_photo])
        await page7.wait_for_selector("#cropWrap:not([hidden])")
        await page7.wait_for_timeout(320)
        check("사진을 고르면 자르기 창이 먼저 열림", await page7.is_visible("#cropWrap"))
        check("자르기 전에는 담기지 않음", await page7.locator(".photo-cell").count() == 0)
        check("자르기 창 제목", "사진 자르기" in (await page7.inner_text(".crop-title")), await page7.inner_text(".crop-title"))
        ratios = await page7.locator("#cropRatios button").all_inner_texts()
        check("비율 칩 자유·1:1·3:4·4:3", ratios == ["자유", "1:1", "3:4", "4:3"], str(ratios))
        check("기본 비율은 자유", await page7.get_attribute('#cropRatios button[data-ratio="free"]', "aria-pressed") == "true")
        check("회전 버튼 있음", "90°" in (await page7.inner_text("#cropRotate")), await page7.inner_text("#cropRotate"))
        check("원본 그대로 버튼 있음", (await page7.inner_text("#cropKeep")) == "원본 그대로")
        check("자르기 적용 버튼 있음", (await page7.inner_text("#cropApply")) == "자르기 적용")
        check("자르기 중 body.modal-open", await page7.evaluate("() => document.body.classList.contains('modal-open')"))
        check("자르기 무대 touch-action none",
              await page7.evaluate("() => getComputedStyle(document.getElementById('cropStage')).touchAction") == "none")
        check("자를 영역 박스 드래그 가능(touch-action none)",
              await page7.evaluate("() => getComputedStyle(document.getElementById('cropFrame')).touchAction") == "none")
        overflow = await page7.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("390 자르기 창 가로 넘침 0", overflow <= 1, f"{overflow}px")

        st = await page7.evaluate("() => window.__couponbox.crop")
        check("자르기 소스 = 원본 크기", (st["source"]["w"], st["source"]["h"]) == (1400, 1050), str(st["source"]))
        check("처음엔 사진 전체가 자를 영역", st["rect"]["w"] >= 300 and st["rect"]["h"] >= 200, str(st["rect"]))
        check("모서리 핸들 4개", await page7.locator(".crop-handle").count() == 4)


        # 3:4 세로로 잘라 담기
        await page7.click('#cropRatios button[data-ratio="3:4"]')
        await page7.wait_for_timeout(240)
        st = await page7.evaluate("() => window.__couponbox.crop")
        check("3:4 비율 선택 표시", await page7.get_attribute('#cropRatios button[data-ratio="3:4"]', "aria-pressed") == "true")
        check("3:4 자를 영역 비율", abs(st["rect"]["w"] / st["rect"]["h"] - 0.75) < 0.02,
              f"{st['rect']}")
        want_w = round(st["rect"]["w"] * st["scale"])
        want_h = round(st["rect"]["h"] * st["scale"])
        check("자를 영역 크기 안내", f"{want_w} × {want_h}" in (await page7.inner_text("#cropSize")), await page7.inner_text("#cropSize"))
        await page7.click("#cropApply")
        await page7.wait_for_timeout(750)
        check("자르기 적용하면 담김", await page7.locator(".photo-cell").count() == 1,
              str(await page7.locator(".photo-cell").count()))
        check("적용 뒤 자르기 창 닫힘", await page7.is_hidden("#cropWrap"))
        saved = await page7.evaluate("() => window.__couponbox.form.photos[0]")
        check("자른 사진은 data URL JPEG", saved.startswith("data:image/jpeg;base64,"), saved[:32])
        raw = base64.b64decode(saved.split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as im:
            check("자른 사진은 3:4 세로", abs(im.size[0] / im.size[1] - 0.75) < 0.02, str(im.size))
            check("자른 사진 = 고른 영역 크기", abs(im.size[0] - want_w) <= 2 and abs(im.size[1] - want_h) <= 2,
                  f"{im.size} vs ({want_w}, {want_h})")
            check("자른 사진도 1600px 이하", max(im.size) <= 1600, str(im.size))
        check("자른 사진이 원본보다 작음", len(raw) < os.path.getsize(sb_photo), f"{len(raw)} vs {os.path.getsize(sb_photo)}")

        # 회전 · 모서리 드래그 · 자유 비율
        await page7.click('.photo-del[data-remove="0"]')
        await page7.wait_for_timeout(200)
        await page7.set_input_files("#photoInput", [big_photo])
        await page7.wait_for_selector("#cropWrap:not([hidden])")
        await page7.wait_for_timeout(340)
        before = await page7.evaluate("() => window.__couponbox.crop.source")
        await page7.click("#cropRotate")
        await page7.wait_for_timeout(320)
        after = await page7.evaluate("() => window.__couponbox.crop.source")
        check("↺ 90° 회전하면 가로·세로가 바뀜", (after["w"], after["h"]) == (before["h"], before["w"]),
              f"{before} → {after}")
        overflow = await page7.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("회전 뒤에도 가로 넘침 0", overflow <= 1, f"{overflow}px")

        r1 = await page7.evaluate("() => window.__couponbox.crop.rect")
        hb = await page7.locator("#cropFrame .crop-handle.br").bounding_box()
        await page7.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
        await page7.mouse.down()
        await page7.mouse.move(hb["x"] + hb["width"] / 2 - 80, hb["y"] + hb["height"] / 2 - 80, steps=10)
        await page7.mouse.up()
        await page7.wait_for_timeout(260)
        r2 = await page7.evaluate("() => window.__couponbox.crop.rect")
        check("모서리를 끌면 자를 영역이 줄어듦", r2["w"] < r1["w"] - 20 and r2["h"] < r1["h"] - 20, f"{r1} → {r2}")
        view = await page7.locator("#cropView").bounding_box()
        check("모서리 드래그 뒤에도 사진 안쪽",
              r2["x"] >= -1 and r2["y"] >= -1
              and r2["x"] + r2["w"] <= view["width"] + 1
              and r2["y"] + r2["h"] <= view["height"] + 1, str(r2))
        await page7.click("#cropApply")
        await page7.wait_for_timeout(750)
        crop2 = await page7.evaluate("() => window.__couponbox.form.photos[0]")
        with Image.open(io.BytesIO(base64.b64decode(crop2.split(",", 1)[1]))) as im:
            check("회전한 사진은 세로가 김", im.size[1] > im.size[0], str(im.size))
            want = r2["w"] / r2["h"]
            check("자른 사진 비율 = 고른 영역 비율", abs(im.size[0] / im.size[1] - want) < 0.02,
                  f"{im.size} vs {want:.3f}")
            check("회전·자른 사진도 1600px 이하", max(im.size) <= 1600, str(im.size))

        # 원본 그대로 · 취소
        await page7.click('.photo-del[data-remove="0"]')
        await page7.wait_for_timeout(200)
        await pick_photo(page7, sb_photo, "keep")
        check("원본 그대로 담기", await page7.locator(".photo-cell").count() == 1)
        keep = await page7.evaluate("() => window.__couponbox.form.photos[0]")
        with Image.open(io.BytesIO(base64.b64decode(keep.split(",", 1)[1]))) as im:
            check("원본 그대로는 자르지 않음", im.size == (1400, 1050), str(im.size))
        await page7.set_input_files("#photoInput", [big_photo])
        await page7.wait_for_selector("#cropWrap:not([hidden])")
        await page7.wait_for_timeout(280)
        await page7.click("#cropCancel")
        await page7.wait_for_timeout(420)
        check("취소하면 자르기 창 닫힘", await page7.is_hidden("#cropWrap"))
        check("취소하면 사진도 안 담힘", await page7.locator(".photo-cell").count() == 1)

        await page7.fill("#fBrand", "스타벅스")
        await page7.click("#addSave")
        await page7.wait_for_timeout(700)
        check("자른 사진으로 쿠폰 저장", await page7.locator(".coupon").count() == 1, str(await page7.locator(".coupon").count()))
        stored_crop = await page7.evaluate("() => window.__couponbox.items[0].photos[0]")
        with Image.open(io.BytesIO(base64.b64decode(stored_crop.split(",", 1)[1]))) as im:
            check("저장된 사진이 원본 그대로", im.size == (1400, 1050), str(im.size))

        # ✨ 자동 맞춤 — 어두운 배경 위 밝은 쿠폰 카드
        await open_add(page7)
        await page7.set_input_files("#photoInput", [auto_dark])
        await page7.wait_for_selector("#cropWrap:not([hidden])")
        await page7.wait_for_timeout(420)
        check("✨ 자동 맞춤 버튼 있음", "자동 맞춤" in (await page7.inner_text("#cropAuto")),
              await page7.inner_text("#cropAuto"))
        st = await page7.evaluate("() => window.__couponbox.crop")
        det = st["detected"]
        check("자동 맞춤: 어두운 배경에서 밝은 카드를 찾음", det is not None, str(det))
        r0 = st["rect"]
        sc = st["scale"]
        bx0, by0 = r0["x"] * sc, r0["y"] * sc
        bx1, by1 = (r0["x"] + r0["w"]) * sc, (r0["y"] + r0["h"]) * sc
        check("자동 맞춤: 카드 위치·크기와 맞음",
              abs(bx0 - CARD_BOX[0]) <= 40 and abs(by0 - CARD_BOX[1]) <= 40
              and abs(bx1 - CARD_BOX[2]) <= 40 and abs(by1 - CARD_BOX[3]) <= 40,
              f"({bx0:.0f},{by0:.0f})-({bx1:.0f},{by1:.0f}) vs {CARD_BOX}")
        check("자동 맞춤: 사진 전체보다 작게 잡음", r0["w"] * sc < 1400 * 0.95, str(r0))
        await page7.wait_for_timeout(2600)  # 앞선 토스트가 사라진 뒤 찍는다
        await page7.screenshot(path=os.path.join(SHOTS, "04_crop_modal_390.png"))
        check("04_crop_modal_390.png", os.path.getsize(os.path.join(SHOTS, "04_crop_modal_390.png")) > 20000)

        hb = await page7.locator("#cropFrame .crop-handle.br").bounding_box()
        await page7.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
        await page7.mouse.down()
        await page7.mouse.move(hb["x"] + hb["width"] / 2 - 90, hb["y"] + hb["height"] / 2 - 90, steps=10)
        await page7.mouse.up()
        await page7.wait_for_timeout(240)
        r_manual = await page7.evaluate("() => window.__couponbox.crop.rect")
        check("손으로 줄인 뒤", r_manual["w"] < r0["w"] - 20, str(r_manual))
        await page7.click("#cropAuto")
        await page7.wait_for_timeout(320)
        r_auto = await page7.evaluate("() => window.__couponbox.crop.rect")
        check("✨ 자동 맞춤을 다시 누르면 감지 영역으로 돌아옴",
              abs(r_auto["w"] - r0["w"]) <= 1.5 and abs(r_auto["h"] - r0["h"]) <= 1.5
              and abs(r_auto["x"] - r0["x"]) <= 1.5, f"{r_manual} → {r_auto}")

        await page7.click("#cropRotate")
        await page7.wait_for_timeout(420)
        st_rot = await page7.evaluate("() => window.__couponbox.crop")
        check("회전 뒤 자동 맞춤이 다시 감지", st_rot["detected"] is not None, str(st_rot["detected"]))
        await page7.click("#cropCancel")
        await page7.wait_for_timeout(300)

        await page7.set_input_files("#photoInput", [auto_flat])
        await page7.wait_for_selector("#cropWrap:not([hidden])")
        await page7.wait_for_timeout(400)
        st_flat = await page7.evaluate("() => window.__couponbox.crop")
        check("균일한 사진은 감지하지 않음", st_flat["detected"] is None, str(st_flat["detected"]))
        view = await page7.locator("#cropView").bounding_box()
        rf = st_flat["rect"]
        check("감지 실패하면 사진 전체로 폴백",
              abs(rf["w"] - view["width"]) <= 1.5 and abs(rf["h"] - view["height"]) <= 1.5, str(rf))
        await page7.click("#cropAuto")
        await page7.wait_for_timeout(300)
        check("못 찾으면 안내 문구", "찾지 못해" in (await page7.inner_text("#toast")),
              await page7.inner_text("#toast"))
        check("폴백 상태에서도 모서리 조절 가능", await page7.locator(".crop-handle").count() == 4)
        await page7.click("#cropCancel")
        await page7.wait_for_timeout(250)
        await page7.click("#addClose")
        await page7.wait_for_timeout(250)
        await ctx7.close()

        # ═════════════════════════════════════════════
        print("\n[11] 390 / 1280 레이아웃 · 입력 글자 크기 · 스크린샷")
        ctxA, pageA = await new_page(browser, width=390, height=844, dsf=2)
        await goto(pageA, port)
        await seed(pageA, SEED)
        await pageA.wait_for_timeout(300)
        overflow = await pageA.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("390 홈 가로 넘침 0", overflow <= 1, f"{overflow}px")
        geo = await pageA.evaluate("""() => {
            const g = document.querySelector('#couponGrid');
            const cs = getComputedStyle(g);
            const cards = [...g.children].slice(0, 4).map(el => {
                const r = el.getBoundingClientRect();
                return { id: el.id || el.dataset.id, top: Math.round(r.top), left: Math.round(r.left), w: Math.round(r.width) };
            });
            return { cols: cs.gridTemplateColumns, gap: cs.gap, cards };
        }""")
        check("390 2열 그리드", len(geo["cols"].split(" ")) == 2, geo["cols"])
        check("첫 줄에 등록 카드 + 첫 쿠폰", len(geo["cards"]) >= 2 and geo["cards"][0]["top"] == geo["cards"][1]["top"],
              str(geo["cards"]))
        check("카드 폭 절반", abs(geo["cards"][0]["w"] - geo["cards"][1]["w"]) <= 1 and geo["cards"][0]["w"] > 150,
              str(geo["cards"]))
        await pageA.screenshot(path=os.path.join(SHOTS, "01_home_390.png"))
        check("01_home_390.png", os.path.getsize(os.path.join(SHOTS, "01_home_390.png")) > 30000)

        await open_add(pageA)
        await pick_photo(pageA, big_photo, "keep")
        await pageA.wait_for_timeout(300)
        await pageA.fill("#fName", "뿌링클 순살 + 콜라 1.25L")
        await pageA.fill("#fBrand", "BHC")
        await pageA.click('#catSeg button[data-cat="food"]')
        await pageA.fill("#fExpiry", iso(4))
        await pageA.fill("#fGiver", "정윤선")
        await pageA.click("#fIsAmount")
        await pageA.fill("#fAmount", "23000")
        await pageA.fill("#fBalance", "23000")
        await pageA.wait_for_timeout(400)
        overflow = await pageA.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("390 등록 시트 가로 넘침 0", overflow <= 1, f"{overflow}px")
        body_overflow = await pageA.evaluate(
            "() => { const b = document.querySelector('#addBody'); return b.scrollWidth - b.clientWidth; }")
        check("390 등록 시트 내용 넘침 0", body_overflow <= 1, f"{body_overflow}px")
        rows = await font_sizes(pageA)
        ok, bad = all_big(rows)
        check("모든 입력 칸 font-size ≥ 16px (등록 시트)", ok, str(bad[:4]))
        await pageA.screenshot(path=os.path.join(SHOTS, "02_add_390.png"))
        check("02_add_390.png", os.path.getsize(os.path.join(SHOTS, "02_add_390.png")) > 30000)
        await pageA.click("#addClose")
        await pageA.wait_for_timeout(250)

        await open_detail(pageA, "seed-starbucks")
        overflow = await pageA.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("390 상세 가로 넘침 0", overflow <= 1, f"{overflow}px")
        await pageA.screenshot(path=os.path.join(SHOTS, "03_detail_390.png"))
        check("03_detail_390.png", os.path.getsize(os.path.join(SHOTS, "03_detail_390.png")) > 30000)
        await pageA.click("#detailClose")
        await pageA.wait_for_timeout(250)

        await pageA.click("#moreBtn")
        await pageA.wait_for_timeout(300)
        rows = await font_sizes(pageA)
        ok, bad = all_big(rows)
        check("모든 입력 칸 font-size ≥ 16px (백업 시트)", ok, str(bad[:4]))
        await pageA.click("#moreClose")
        await pageA.wait_for_timeout(200)
        await ctxA.close()

        ctxB, pageB = await new_page(browser, width=1280, height=900)
        await goto(pageB, port)
        await seed(pageB, SEED)
        await pageB.wait_for_timeout(300)
        overflow = await pageB.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("1280 가로 넘침 0", overflow <= 1, f"{overflow}px")
        wide = await pageB.evaluate("""() => {
            const app = document.querySelector('.app').getBoundingClientRect();
            const g = document.querySelector('#couponGrid');
            return {
                appLeft: Math.round(app.left),
                appWidth: Math.round(app.width),
                cols: getComputedStyle(g).gridTemplateColumns.split(' ').length,
                viewport: window.innerWidth,
            };
        }""")
        check("1280 가운데 정렬", abs(wide["appLeft"] - (wide["viewport"] - wide["appWidth"]) / 2) < 2, str(wide))
        check("1280에서도 2열", wide["cols"] == 2, str(wide["cols"]))
        await pageB.click("#moreBtn")
        await pageB.wait_for_selector("#moreWrap:not([hidden])")
        await pageB.wait_for_timeout(300)
        overflow = await pageB.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("1280 시트 가로 넘침 0", overflow <= 1, f"{overflow}px")
        await pageB.screenshot(path=os.path.join(SHOTS, "04_desktop_1280.png"))
        await pageB.click("#moreClose")
        await ctxB.close()

        # ═════════════════════════════════════════════
        print("\n[12] 오프라인 실행")
        ctxC, pageC = await new_page(browser)
        await goto(pageC, port)
        await seed(pageC, SEED)
        try:
            await asyncio.wait_for(
                pageC.evaluate("navigator.serviceWorker.ready.then(r => !!(r && r.active))"), 20)
            await pageC.wait_for_timeout(900)
            await ctxC.set_offline(True)
            await pageC.reload(wait_until="domcontentloaded")
            await pageC.wait_for_function("document.documentElement.dataset.ready === '1'", timeout=15000)
            check("오프라인에서도 앱이 열림", True)
            check("오프라인 목록", await pageC.locator(".coupon").count() == 6,
                  f"{await pageC.locator('.coupon').count()}건")
            check("오프라인에서도 사진", await pageC.locator('.coupon[data-id="seed-starbucks"] .thumb img').count() == 1)
            await open_detail(pageC, "seed-starbucks")
            check("오프라인 상세 열림", await pageC.is_visible("#detailBody"))
        except Exception as exc:
            check("오프라인 실행", False, str(exc))
        finally:
            try:
                await ctxC.set_offline(False)
            except Exception:
                pass
            await ctxC.close()

        await browser.close()
    srv.shutdown()

    # ═════════════════════════════════════════════
    print("\n[13] 외부 전송 · 콘솔 오류")
    check("외부 서버로 보내는 요청 0", len(EXTERNAL) == 0, " | ".join(EXTERNAL[:3]))
    real_errors = [e for e in ERRORS if "favicon" not in e.lower()
                   and "ERR_INTERNET_DISCONNECTED" not in e]
    check("콘솔 오류 0", len(real_errors) == 0, " | ".join(real_errors[:4]))

    print("\n" + "─" * 58)
    print(f"통과 {len(PASS)} · 실패 {len(FAIL)} · 경고 {len(WARN)}")
    if WARN:
        for w in WARN:
            print("  ! ", w)
    if FAIL:
        print("\n실패 항목:")
        for f in FAIL:
            print("  ✗ ", f)
        sys.exit(1)
    print("🎉 모든 검증 통과!")
    print("스크린샷:", SHOTS)


if __name__ == "__main__":
    asyncio.run(run())
