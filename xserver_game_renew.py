#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
from datetime import timedelta
from html.parser import HTMLParser
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from seleniumbase import SB
import requests

# ============================================================
# 配置与环境变量解析（兼顾 XSMA_ACCOUNT 与 XSERVER_GAME_ACCOUNT）
# ============================================================

XSERVER_GAME_ACCOUNT = os.environ.get("XSMA_ACCOUNT") or os.environ.get(
    "XSERVER_GAME_ACCOUNT", ""
)
if not XSERVER_GAME_ACCOUNT:
  print(
      "❌ 请设置 GitHub Secret: XSMA_ACCOUNT（格式: 自定义名称,email,password）"
  )
  sys.exit(1)

_parts = XSERVER_GAME_ACCOUNT.split(",", 2)
if len(_parts) < 3:
  print("❌ XSMA_ACCOUNT 格式错误，应为: 自定义名称,email,password")
  sys.exit(1)

SERVER_NAME = _parts[0].strip()
GH_EMAIL = _parts[1].strip()
GH_PASSWORD = _parts[2].strip()
GH_EMAIL_MASKED = (
    f"***@{GH_EMAIL.split('@')[1]}" if "@" in GH_EMAIL else "***"
)

BASE_URL = "https://secure.xserver.ne.jp"
LOGIN_URL = f"{BASE_URL}/xapanel/login/xvps"
XMGAME_INDEX_URL = f"{BASE_URL}/xapanel/xmgame/index"
ONETIMELOGIN_URL = f"{BASE_URL}/xmgame/onetimelogin"
INFO_URL = f"{BASE_URL}/xmgame/game/index"
EXTEND_URL = f"{BASE_URL}/xmgame/game/freeplan/extend/index"
RENEW_URL = f"{BASE_URL}/xmgame/game/freeplan/extend/input"
CONF_URL = f"{BASE_URL}/xmgame/game/freeplan/extend/conf"
DO_URL = f"{BASE_URL}/xmgame/game/freeplan/extend/do"
IP_CHECK_URL = "https://api.ipify.org?format=json"

RENEW_THRESHOLD_HOURS = 4

LOCAL_PROXY = "http://127.0.0.1:8080"
GOST_PROXY = os.environ.get("GOST_PROXY", "")
PROXIES = {"http": LOCAL_PROXY, "https": LOCAL_PROXY} if GOST_PROXY else {}

TG_BOT = os.environ.get("TG_BOT", "")
EXIT_IP = ""  # 本次运行实际出口 IP，用于 Telegram 通知
CRON_JOB = os.environ.get("CRON_JOB", "")

BASE_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}

SCRIPT_NAME = os.path.basename(__file__)
_start_time = time.time()


# ============================================================
# 工具函数与基础设置
# ============================================================


def log(msg):
  print(msg, flush=True)


def human_pause(lo=0.8, hi=2.2):
  time.sleep(random.uniform(lo, hi))


def human_pause_short(lo=0.3, hi=0.8):
  time.sleep(random.uniform(lo, hi))


def divider(label):
  width = 60
  inner = f" {{{label}}} "
  pad_total = width - len(inner)
  pad_l = pad_total // 2
  pad_r = pad_total - pad_l
  log("=" * pad_l + inner + "=" * pad_r)


def elapsed():
  return f"{time.time() - _start_time:.2f}s"


def now_str():
  return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# Cloudflare Turnstile 工具（采用 renew.py 登录页专属定位）
# ============================================================

EXPAND_POPUP_JS = """
(function() {
    var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (!turnstileInput) return;
    var el = turnstileInput;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflowY === 'hidden') {
            el.style.overflow = 'visible';
        }
        el.style.minWidth = 'max-content';
    }
})();
"""

INJECT_TOKEN_LISTENER_JS = """
(function() {
    if (window.__cf_token_listener_injected__) return;
    window.__cf_token_listener_injected__ = true;
    window.__cf_turnstile_token__ = '';
    window.addEventListener('message', function(e) {
        if (!e.origin || e.origin.indexOf('cloudflare.com') === -1) return;
        var d = e.data;
        if (!d || d.event !== 'complete' || !d.token) return;
        window.__cf_turnstile_token__ = d.token;
        var inputs = document.querySelectorAll(
            'input[name="cf-turnstile-response"], input[name="cf_turnstile_response"]'
        );
        for (var i = 0; i < inputs.length; i++) {
            try {
                var nativeSet = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                ).set;
                nativeSet.call(inputs[i], d.token);
                inputs[i].dispatchEvent(new Event('input',  {bubbles: true}));
                inputs[i].dispatchEvent(new Event('change', {bubbles: true}));
            } catch(err) {
                inputs[i].value = d.token;
            }
        }
    });
})();
"""

READ_CAPTURED_TOKEN_JS = (
    "(function(){ return window.__cf_turnstile_token__ || ''; })()"
)


def xdotool_click(x, y):
  x, y = int(x), int(y)
  try:
    result = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    wids = [w for w in result.stdout.strip().split("\n") if w]
    if wids:
      subprocess.run(
          ["xdotool", "windowactivate", wids[-1]],
          timeout=2,
          stderr=subprocess.DEVNULL,
      )
      time.sleep(0.2)
    subprocess.run(
        ["xdotool", "mousemove", str(x), str(y)], timeout=2, check=True
    )
    time.sleep(random.uniform(0.1, 0.25))
    subprocess.run(["xdotool", "click", "1"], timeout=2, check=True)
    log("📐 坐标点击成功")
    return True
  except Exception as e:
    log(f"⚠️ xdotool 点击失败：{e}")
    return False


def get_turnstile_coords(sb):
  """通用定位法：供续期等非登录页面使用"""
  try:
    return sb.execute_script("""
            (function(){
                function visibleRect(el) {
                    if (!el) return null;
                    var r = el.getBoundingClientRect();
                    var s = window.getComputedStyle(el);
                    if (r.width < 10 || r.height < 10) return null;
                    if (s.display === 'none' || s.visibility === 'hidden' ||
                        s.opacity === '0') return null;
                    return {
                        click_x: Math.round(r.left + r.width / 2),
                        click_y: Math.round(r.top + r.height / 2)
                    };
                }
                var iframes = Array.from(document.querySelectorAll(
                    'iframe[src*="challenges.cloudflare.com"], iframe[title*="Cloudflare" i]'
                ));
                for (var i = 0; i < iframes.length; i++) {
                    var r = visibleRect(iframes[i]);
                    if (r) return r;
                }
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                if (input) {
                    var el = input;
                    for (var j = 0; j < 12 && el; j++, el = el.parentElement) {
                        var r2 = visibleRect(el);
                        if (r2 && r2.width >= 250 && r2.height >= 50) return r2;
                    }
                }
                return null;
            })()
        """)
  except Exception:
    return None


def get_turnstile_coords_login(sb):
  """来自 renew.py 的登录页专用方法：优先定位 iframe 的 left + 22 处（即 checkbox 准确位置）"""
  try:
    return sb.execute_script("""
            (function(){
                var ifr = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                var r = null, mode = '';
                if (ifr) {
                    ifr.scrollIntoView({block: 'center'});
                    r = ifr.getBoundingClientRect();
                    mode = 'iframe';
                    if (r.width > 0 && r.height > 0) {
                        return {
                            click_x: Math.round(r.left + 22),
                            click_y: Math.round(r.top + r.height / 2),
                            mode: mode
                        };
                    }
                }
                var anchor = document.querySelector('input[name="cf-turnstile-response"]');
                if (!anchor || !anchor.parentElement) return null;
                var box = anchor.parentElement;
                box.scrollIntoView({block: 'center'});
                r = box.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) return null;
                return {
                    click_x: Math.round(r.left + r.width / 2 - 150 + 22),
                    click_y: Math.round(r.top + r.height / 2),
                    mode: 'parent'
                };
            })()
        """)
  except Exception:
    return None


def get_window_offset(sb):
  try:
    result = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    wids = [w for w in result.stdout.strip().split("\n") if w]
    if wids:
      geo = subprocess.run(
          ["xdotool", "getwindowgeometry", "--shell", wids[-1]],
          capture_output=True,
          text=True,
          timeout=3,
      ).stdout
      geo_dict = {}
      for line in geo.strip().split("\n"):
        if "=" in line:
          k, v = line.split("=", 1)
          geo_dict[k.strip()] = int(v.strip())
      win_x = geo_dict.get("X", 0)
      win_y = geo_dict.get("Y", 0)
      info = sb.execute_script(
          "(function(){ return { outer: window.outerHeight, inner:"
          " window.innerHeight }; })()"
      )
      toolbar = info["outer"] - info["inner"]
      if not (30 <= toolbar <= 200):
        toolbar = 87
      return win_x, win_y, toolbar
  except Exception:
    pass
  try:
    info = sb.execute_script("""
            (function(){
                return { screenX: window.screenX || 0, screenY: window.screenY || 0,
                         outer: window.outerHeight, inner: window.innerHeight };
            })()
        """)
    toolbar = info["outer"] - info["inner"]
    if not (30 <= toolbar <= 200):
      toolbar = 87
    return info["screenX"], info["screenY"], toolbar
  except Exception:
    return 0, 0, 87


def check_token(sb) -> bool:
  try:
    if sb.execute_script("""
            (function(){
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                return input && input.value && input.value.length > 20;
            })()
        """):
      return True
  except Exception:
    pass
  try:
    token = sb.execute_script(READ_CAPTURED_TOKEN_JS)
    if token and len(token) > 20:
      return True
  except Exception:
    pass
  return False


def get_token_value(sb) -> str:
  try:
    token = sb.execute_script(READ_CAPTURED_TOKEN_JS)
    if token and len(token) > 20:
      return token
  except Exception:
    pass
  try:
    token = sb.execute_script("""
            (function(){
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                return (input && input.value) ? input.value : '';
            })()
        """)
    if token and len(token) > 20:
      return token
  except Exception:
    pass
  return ""


def turnstile_exists(sb) -> bool:
  try:
    return sb.execute_script(
        '(function(){ return'
        ' document.querySelector(\'input[name="cf-turnstile-response"]\') !=='
        " null; })()"
    )
  except Exception:
    return False


def solve_turnstile(sb, login: bool = False) -> bool:
  """来自 renew.py 的解 Turnstile 主逻辑：根据 login 参数使用不同定位法，并防 Google 误点击"""
  for _ in range(3):
    sb.execute_script(EXPAND_POPUP_JS)
    human_pause_short()

  try:
    sb.execute_script(INJECT_TOKEN_LISTENER_JS)
    log("📡 开始监控 Cloudflare Turnstile Token...")
  except Exception as e:
    log(f"⚠️ 监听器注入失败：{e}")

  if check_token(sb):
    log("✅ 验证已自动通过")
    return True

  if login:
    # 登录页：不要滚到底部（底部是 Google 登录，易误触），采用专用的精准定位
    human_pause(1.0, 2.0)
    coords = get_turnstile_coords_login(sb)
    if coords:
      log(f"📍 登录页验证定位方式：{coords.get('mode')}")
  else:
    # 非登录页：滚动到底部确保可见
    sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    human_pause(1.0, 2.0)
    coords = get_turnstile_coords(sb)

  if not coords:
    log("❌ 验证坐标获取失败")
    sb.save_screenshot("turnstile_no_coords.png")
    return False

  win_x, win_y, toolbar = get_window_offset(sb)
  abs_x = coords["click_x"] + win_x
  abs_y = coords["click_y"] + win_y + toolbar
  log("📐 坐标计算完成")
  xdotool_click(abs_x, abs_y)

  for _ in range(30):
    human_pause_short(0.4, 0.6)
    if login:
      try:
        cur = sb.get_current_url()
      except Exception:
        cur = ""
      if "accounts.google.com" in cur or "google.com/o/oauth" in cur:
        log("❌ 误点到 Google 登录，已跳转到谷歌页面")
        sb.save_screenshot("login_misclick_google.png")
        return False
    if check_token(sb):
      token = get_token_value(sb)
      log(f"✅ Cloudflare Turnstile 验证通过！token：{token[:50]}...")
      return True

  log("❌ 人机验证超时")
  sb.save_screenshot("turnstile_fail.png")
  return False


# ============================================================
# 登录逻辑（采用 renew.py 的登录流）
# ============================================================


def _get_cdp_debugger_address(sb):
    """从 Chrome capability 获取独立 CDP 地址，不依赖 Selenium cookie API。"""
    try:
        caps = getattr(sb.driver, "capabilities", {}) or {}
        opts = caps.get("goog:chromeOptions", {}) or {}
        addr = opts.get("debuggerAddress")
        if addr:
            return addr
    except Exception:
        pass
    return None


def _get_cdp_cookies(debugger_address: str, timeout: float = 8.0):
    """通过 Chrome DevTools Protocol 从当前页面 Target 读取 Cookie。

    注意：/json/version 返回的是 Browser 级 CDP WebSocket，
    Network.getAllCookies 属于页面 Target，因此必须连接 /json/list
    中当前 page Target 的 websocket，而不能直接对 Browser websocket
    调用 Network.getAllCookies。
    """
    import json as _json
    import urllib.request as _urlreq

    if not debugger_address:
        raise RuntimeError("未获取到 Chrome debuggerAddress")

    try:
        import websocket
    except ImportError as e:
        raise RuntimeError("缺少 websocket-client，请在环境中安装 websocket-client") from e

    list_url = f"http://{debugger_address}/json/list"
    with _urlreq.urlopen(list_url, timeout=timeout) as r:
        targets = _json.loads(r.read().decode("utf-8", "replace"))

    # 优先当前 XServer GAME 页面，其次任意正常 page target。
    page = None
    for target in targets:
        if target.get("type") != "page":
            continue
        url = target.get("url", "") or ""
        if "/xmgame/game/" in url or "/xapanel/xmgame/" in url:
            page = target
            break
    if page is None:
        for target in targets:
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                page = target
                break

    if not page:
        raise RuntimeError("Chrome CDP 未找到可用的 page Target")

    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("当前 page Target 未返回 webSocketDebuggerUrl")

    ws = websocket.create_connection(
        ws_url,
        timeout=timeout,
        origin=f"http://{debugger_address}",
    )
    try:
        ws.send(_json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = ws.recv()
            msg = _json.loads(raw)
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                cookies = msg.get("result", {}).get("cookies", [])
                if not cookies:
                    raise RuntimeError("CDP 返回 Cookie 为空")
                return cookies
        raise RuntimeError("CDP 获取 Cookie 超时")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def login_with_cf() -> requests.Session:
    """正常完成 XServer 登录/Turnstile/Game 管理，然后通过独立 CDP 读取 Cookie。"""
    global EXIT_IP
    log(f"🔑 正在登录... 账号: {GH_EMAIL_MASKED}")
    session = requests.Session()

    with SB(
        uc=True,
        proxy=LOCAL_PROXY if GOST_PROXY else None,
        chromium_arg="--remote-allow-origins=*",
    ) as sb:
        log("🚀 浏览器就绪！")

        log("🌐 验证出口 IP...")
        try:
            sb.open("https://api.ipify.org/?format=json")
            ip_text = sb.get_text("body")
            ip_match = re.search(r"(\d+\.\d+\.\d+)\d+", ip_text)
            if ip_match:
                EXIT_IP = ip_match.group(0)
            masked = (
                re.sub(r"(\d+\.\d+\.\d+)\d+", r"\1**", ip_match.group(0))
                if ip_match else ip_text
            )
            log(f"✅ 出口 IP 确认：{masked}")
        except Exception as e:
            log(f"⚠️ 出口 IP 检测失败: {e}")

        log("🔑 打开登录页面...")
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
        human_pause(4.0, 6.0)

        log("✏️ 填写账号密码...")
        try:
            sb.wait_for_element_visible("input[name='memberid']", timeout=40)
            human_pause_short()
            sb.type("input[name='memberid']", GH_EMAIL)
            human_pause_short(0.5, 1.2)
            sb.type("input[name='user_password']", GH_PASSWORD)
            human_pause_short(0.8, 1.5)
        except Exception as e:
            sb.save_screenshot("login_fail.png")
            fail_exit(f"❌ 登录框加载失败: {e}")

        log("⏳ 检查登录页验证组件...")
        for _ in range(8):
            if turnstile_exists(sb):
                log("✅ 登录页验证组件就绪")
                break
            human_pause_short(0.8, 1.2)
        else:
            log("ℹ️ 登录页未检测到验证组件，直接登录")

        if turnstile_exists(sb):
            if not solve_turnstile(sb, login=True):
                sb.save_screenshot("login_turnstile_fail.png")
                fail_exit("❌ 登录页 Turnstile 验证失败")
            human_pause_short(0.8, 1.5)

        log("📤 提交登录请求...")
        try:
            sb.click("input[type='submit']")
        except Exception as e:
            sb.save_screenshot("login_submit_fail.png")
            fail_exit(f"❌ 登录按钮不可用: {e}")

        log("⏳ 等待登录跳转...")
        login_ok = False
        for _ in range(40):
            try:
                current_url = sb.get_current_url()
                if any(x in current_url for x in (
                    "/xvps/index", "/xapanel/index", "/xmgame/index", "/xserver/index"
                )):
                    log("✅ 登录成功！")
                    login_ok = True
                    break
            except Exception:
                pass
            human_pause_short(0.4, 0.6)

        if not login_ok:
            sb.save_screenshot("login_timeout.png")
            fail_exit("❌ 登录等待超时")

        # 在 Selenium 仍正常时保存 Chrome 的独立 CDP 地址。
        debugger_address = _get_cdp_debugger_address(sb)
        if debugger_address:
            log(f"🔌 Chrome CDP 已就绪：{debugger_address}")
        else:
            log("⚠️ 未读取到 Chrome CDP 地址")

        log("🎮 打开 XServer GAME 页面...")
        try:
            sb.open(XMGAME_INDEX_URL)
            human_pause(3.0, 5.0)
            current_game_url = sb.get_current_url()
            log(f"📍 GAME 页面地址: {current_game_url}")
        except Exception as e:
            sb.save_screenshot("xmgame_index_fail.png")
            fail_exit(f"❌ 打开 XServer GAME 页面失败: {e}")

        log("🖱️ 查找「ゲーム管理」对应的 jumpvps 链接...")
        jump_url = None
        server_id = None
        for _ in range(20):
            try:
                links = sb.find_elements('a[href*="/xapanel/xmgame/jumpvps/"]')
                for a in links:
                    href = a.get_attribute("href") or ""
                    m = re.search(r"/xapanel/xmgame/jumpvps/\?id=(\d+)", href)
                    if not m:
                        continue
                    jump_url = href
                    server_id = m.group(1)
                    break
                if jump_url:
                    break
            except Exception:
                pass
            human_pause_short(0.5, 1.0)

        if not jump_url:
            try:
                sb.save_screenshot("game_manage_link_not_found.png")
            except Exception:
                pass
            fail_exit("❌ 未找到「ゲーム管理」对应的 jumpvps 链接")

        log(f"🔑 找到动态服务器 ID: {server_id[:2]}****{server_id[-2:]}")
        log(f"🔗 游戏管理地址: {jump_url}")

        try:
            clicked = False
            links = sb.find_elements('a[href*="/xapanel/xmgame/jumpvps/"]')
            for a in links:
                href = a.get_attribute("href") or ""
                if href == jump_url:
                    try:
                        sb.scroll_to_element(a)
                    except Exception:
                        pass
                    a.click()
                    clicked = True
                    break
            if not clicked:
                sb.click(f'a[href="{jump_url}"]', timeout=15)
                clicked = True
            human_pause(3.0, 5.0)
            log(f"✅ 已点击「ゲーム管理」，当前页面: {sb.get_current_url()}")
        except Exception as e:
            try:
                sb.save_screenshot("game_manage_click_fail.png")
            except Exception:
                pass
            fail_exit(f"❌ 点击「ゲーム管理」失败: {e}")

        # 不再调用 sb.driver.get_cookies()。
        # 通过独立 Chrome DevTools Protocol 读取当前浏览器会话 Cookie。
        selenium_cookies = None
        cookie_error = None
        for cookie_attempt in range(3):
            try:
                selenium_cookies = _get_cdp_cookies(debugger_address, timeout=10)
                if selenium_cookies:
                    log(f"🍪 CDP 浏览器 Cookie 获取成功：{len(selenium_cookies)} 个")
                    break
                cookie_error = "CDP 返回空 Cookie"
            except Exception as e:
                cookie_error = e

            if cookie_attempt < 2:
                log(f"⚠️ CDP Cookie 获取失败，第 {cookie_attempt + 1}/3 次，等待后重试...")
                time.sleep(2)

        if not selenium_cookies:
            fail_exit(f"❌ 登录成功，但无法通过 CDP 获取 Cookie：{cookie_error}")

        for cookie in selenium_cookies:
            try:
                session.cookies.set(
                    cookie["name"], cookie["value"],
                    domain=cookie.get("domain", ""),
                    path=cookie.get("path", "/"),
                )
            except Exception:
                session.cookies.set(cookie["name"], cookie["value"])

        log("🍪 Cookie 已通过 CDP 继承到 requests Session")

    if not session.cookies.get("X2SESSID"):
        fail_exit("❌ 登录成功但未获取到 X2SESSID")

    log("✅ 登录成功，Cookie 继承完毕")
    return session


# ============================================================
# Game 面板续期业务逻辑
# ============================================================


def parse_remaining(page_html: str) -> tuple:
  deadline = re.search(r'<span class="dateLimit">\(([^)]+)\)</span>', page_html)
  dl_str = deadline.group(1) if deadline else "未知"

  if "limitOverTxt" in page_html and "期限切れ" in page_html:
    return -1, -1, dl_str, True

  numbers = re.findall(r'<span class="numberTxt">(\d+)</span>', page_html)
  if len(numbers) >= 2:
    return int(numbers[0]), int(numbers[1]), dl_str, False

  return -2, -2, dl_str, False


def format_remaining(h: int, m: int, is_expired: bool) -> str:
  if is_expired or h == -1:
    return "期限切れ"
  if h == -2:
    return "不明"
  return f"{h}時間{m}分"


def can_renew(page_html: str) -> bool:
  return "残り契約時間が2時間を切るまで" not in page_html


def notify_tg(result: str, remaining_str: str, use_proxy: bool = True):
    if not TG_BOT:
        return
    parts = TG_BOT.split(",", 1)
    if len(parts) != 2:
        return
    chat_id, bot_token = parts[0].strip(), parts[1].strip()
    message = (
        f"🟢 XServer Game 续期通知\n"
        f"🕐 运行时间: {now_str()}\n"
        f"🖥 服务器: {SERVER_NAME}\n"
        f"👤 用户名: {GH_EMAIL}\n"
        f"🌐 代理IP: {EXIT_IP or '未知'}\n"
        f"📅 残り時間: {remaining_str}\n"
        f"📊 续期结果: {result}"
    )
    tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}

    try:
        # 正常情况下保持原逻辑：TG 跟随原来的代理设置。
        requests.post(
            tg_url,
            json=payload,
            timeout=10,
            proxies=PROXIES if use_proxy else None,
        )
        log("📨 TG 推送成功")
    except Exception as e:
        # 如果原本通过代理发送失败，说明代理可能不可用。
        # 此时仅将 TG 切换为直连；续期业务的其它请求保持原样。
        if use_proxy and PROXIES:
            log(f"⚠️ TG 代理发送失败，切换直连：{e}")
            try:
                requests.post(
                    tg_url,
                    json=payload,
                    timeout=10,
                    proxies=None,
                )
                log("📨 TG 推送成功（直连）")
            except Exception as direct_e:
                log(f"⚠️ TG 直连推送也失败: {direct_e}")
        else:
            log(f"⚠️ TG 推送失败: {e}")


def update_cronjob_schedule(delay_minutes: int):
    """写回 cron-job.org 调度，格式: api_key,job_id"""

    if not CRON_JOB or "," not in CRON_JOB:
        log("⚠️ 未配置 CRON_JOB，跳过写回调度")
        return False

    api_key, job_id = [x.strip() for x in CRON_JOB.split(",", 1)]

    # 以北京时间计算下次触发时间
    bj_now = (
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        + timedelta(hours=8)
    )
    next_run = bj_now + timedelta(minutes=delay_minutes)

    data = {
        "job": {
            "schedule": {
                "timezone": "Asia/Shanghai",
                "expiresAt": 0,
                "hours": [next_run.hour],
                "minutes": [next_run.minute],
                "mdays": [next_run.day],
                "months": [next_run.month],
                "wdays": [-1],
            }
        }
    }

    url = f"https://api.cron-job.org/jobs/{job_id}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "XServer-Renew/1.0",
    }

    payload = json.dumps(data).encode("utf-8")

    # 最多 3 次。429 时优先遵循 Retry-After，否则采用递增等待。
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            data=payload,
            headers=headers,
            method="PATCH",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = resp.getcode()

                if 200 <= status < 300:
                    log(
                        f"🔁 Cron 写回成功：下次触发 "
                        f"{next_run.month:02d}月{next_run.day:02d}日 "
                        f"{next_run.hour:02d}:{next_run.minute:02d}"
                        f"（北京时间）"
                    )
                    return True

                log(f"⚠️ Cron 写回返回 HTTP {status}")

        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")

                if retry_after:
                    try:
                        wait_seconds = max(5, int(retry_after))
                    except (TypeError, ValueError):
                        wait_seconds = 15
                else:
                    wait_seconds = 15 * (attempt + 1)

                if attempt < 2:
                    log(
                        f"⚠️ Cron API 限流（429），"
                        f"第{attempt + 1}次失败，"
                        f"{wait_seconds}秒后重试..."
                    )
                    time.sleep(wait_seconds)
                    continue

                log("❌ Cron API 连续 3 次被限流（429），放弃本次写回")
                return False

            if attempt < 2:
                wait_seconds = 5 * (attempt + 1)
                log(
                    f"⚠️ Cron 写回第{attempt + 1}次失败："
                    f"HTTP {e.code}，{wait_seconds}秒后重试..."
                )
                time.sleep(wait_seconds)
            else:
                log(f"❌ Cron 写回失败：HTTP {e.code} {e.reason}")
                return False

    return False

def schedule_next_run(h: int, m: int):
    """
    根据当前剩余时间计算下次触发时间：
    delay = 剩余分钟 - RENEW_THRESHOLD_HOURS * 60 + 随机 1～60 分钟
    最少 5 分钟。
    """
    try:
        remaining_minutes = h * 60 + m

        # 随机延后 1～60 分钟
        random_delay = random.randint(1, 60)

        delay_min = max(
            5,
            remaining_minutes - RENEW_THRESHOLD_HOURS * 60 + random_delay
        )

        log(f"🎲 本次随机延后 {random_delay} 分钟")
        success = update_cronjob_schedule(delay_minutes=delay_min)

        if not success:
            log("⚠️ Cron 写回失败，本次运行结束，但不影响当前续期结果")

    except Exception as e:
        log(f"❌ 计算下次触发时间失败: {e}")

def fail_exit(msg: str):
  log(msg)
  use_proxy = "代理连接失败" not in msg
  notify_tg(msg, "未知", use_proxy=use_proxy)
  divider(f"{SCRIPT_NAME} failed in {elapsed()}")
  sys.exit(1)


def finish(success: bool, result: str, remaining_str: str):
  notify_tg(result, remaining_str)
  tag = "passed" if success else "failed"
  divider(f"{SCRIPT_NAME} {tag} in {elapsed()}")
  sys.exit(0 if success else 1)


def jump_to_xmgame(session: requests.Session):
  log("🔗 跳转到游戏面板...")
  last_error = None
  for attempt in range(3):
    try:
      resp = session.get(
          XMGAME_INDEX_URL,
          headers={**BASE_HEADERS, "referer": BASE_URL},
          timeout=15,
          proxies=PROXIES,
          allow_redirects=True,
      )
      resp.encoding = "EUC-JP"
      break
    except Exception as e:
      last_error = e
      if attempt < 2:
        time.sleep(30)
      else:
        fail_exit(f"❌ 获取 xmgame index 失败（已重试 3 次）: {last_error}")

  jumpvps_match = re.search(r"/xapanel/xmgame/jumpvps/\?id=(\d+)", resp.text)
  if not jumpvps_match:
    fail_exit("❌ 未找到 jumpvps 链接")
  server_id = jumpvps_match.group(1)
  log(f"✅ 找到服务器 ID: {server_id[:2]}****{server_id[-2:]}")

  try:
    resp2 = session.get(
        f"{BASE_URL}/xapanel/xmgame/jumpvps/?id={server_id}",
        headers={**BASE_HEADERS, "referer": XMGAME_INDEX_URL},
        timeout=15,
        proxies=PROXIES,
        allow_redirects=True,
    )
    resp2.encoding = "EUC-JP"
  except Exception as e:
    fail_exit(f"❌ 获取 jumpvps 失败: {e}")

  username = re.search(r'name="username"\s+value="([^"]+)"', resp2.text)
  server_identify = re.search(
      r'name="server_identify"\s+value="([^"]+)"', resp2.text
  )
  password = re.search(r'name="password"\s+value="([^"]+)"', resp2.text)
  service = re.search(r'name="service"\s+value="([^"]+)"', resp2.text)
  master_panel = re.search(
      r'name="master_panel_username"\s+value="([^"]*)"', resp2.text
  )
  back = re.search(r'name="back"\s+value="([^"]+)"', resp2.text)

  if not all([username, server_identify, password, service]):
    fail_exit("❌ 解析 onetimelogin 表单失败")

  try:
    session.post(
        ONETIMELOGIN_URL,
        headers={
            **BASE_HEADERS,
            "content-type": "application/x-www-form-urlencoded",
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/xapanel/xmgame/jumpvps/?id={server_id}",
        },
        data={
            "username": username.group(1),
            "server_identify": server_identify.group(1),
            "password": password.group(1),
            "service": service.group(1),
            "master_panel_username": (
                master_panel.group(1) if master_panel else ""
            ),
            "back": back.group(1) if back else "",
        },
        allow_redirects=True,
        timeout=15,
        proxies=PROXIES,
    )
  except Exception as e:
    fail_exit(f"❌ onetimelogin 请求失败: {e}")

  xmgame_sessid = session.cookies.get(
      "X2%2Fxmgame_SESSID"
  ) or session.cookies.get("X2/xmgame_SESSID")
  if xmgame_sessid:
    log("✅ 游戏面板 Session 获取成功")
  else:
    log("⚠️ 未检测到 xmgame_SESSID，但继续尝试...")


def fetch_info_page(session: requests.Session) -> str:
  try:
    resp = session.get(
        INFO_URL,
        headers={**BASE_HEADERS, "referer": BASE_URL},
        timeout=15,
        proxies=PROXIES,
        allow_redirects=True,
    )
    resp.encoding = "EUC-JP"
    return resp.text
  except Exception as e:
    fail_exit(f"❌ 获取游戏首页失败: {e}")


def fetch_extend_page(session: requests.Session) -> str:
  try:
    resp = session.get(
        EXTEND_URL,
        headers={**BASE_HEADERS, "referer": INFO_URL},
        timeout=15,
        proxies=PROXIES,
        allow_redirects=True,
    )
    resp.encoding = "EUC-JP"
    return resp.text
  except Exception as e:
    fail_exit(f"❌ 获取续期页面失败: {e}")


class ConfirmFormParser(HTMLParser):

  def __init__(self):
    super().__init__(convert_charrefs=True)
    self.forms = []
    self._form = None
    self._select = None
    self._option = None
    self._textarea = None
    self._button = None
    self._button_text = []

  @staticmethod
  def _attrs(attrs):
    return {k.lower(): (v if v is not None else "") for k, v in attrs}

  def handle_starttag(self, tag, attrs):
    tag = tag.lower()
    a = self._attrs(attrs)
    if tag == "form":
      self._form = {
          "action": a.get("action", ""),
          "method": a.get("method", "get").lower(),
          "enctype": a.get(
              "enctype", "application/x-www-form-urlencoded"
          ).lower(),
          "controls": [],
          "submits": [],
      }
      self.forms.append(self._form)
      return
    if self._form is None:
      return
    if tag == "input":
      name = a.get("name", "")
      typ = a.get("type", "text").lower()
      if typ in {"submit", "button", "image", "reset"}:
        if typ == "submit":
          self._form["submits"].append({
              "name": name,
              "value": a.get("value", ""),
              "text": a.get("value", ""),
              "formaction": a.get("formaction", ""),
              "formmethod": a.get("formmethod", ""),
          })
        return
      if not name:
        return
      if typ in {"checkbox", "radio"} and "checked" not in a:
        return
      self._form["controls"].append((name, a.get("value", "")))
      return
    if tag == "button":
      self._button = {
          "name": a.get("name", ""),
          "value": a.get("value", ""),
          "text": "",
          "formaction": a.get("formaction", ""),
          "formmethod": a.get("formmethod", ""),
          "type": a.get("type", "submit").lower(),
      }
      self._button_text = []
      return

  def handle_data(self, data):
    if self._button is not None:
      self._button_text.append(data)

  def handle_endtag(self, tag):
    tag = tag.lower()
    if tag == "button" and self._button is not None and self._form is not None:
      button = dict(self._button)
      button["text"] = re.sub(r"\s+", "", "".join(self._button_text))
      if button.get("type", "submit") in {"submit", "button"}:
        self._form["submits"].append(button)
      self._button = None
      self._button_text = []


def _find_extend_confirm_form(html: str, page_url: str):
  parser = ConfirmFormParser()
  parser.feed(html)
  target = "期限を延長する"
  candidates = []
  for form in parser.forms:
    for submit in form["submits"]:
      label = submit.get("text", "") or submit.get("value", "")
      if target in re.sub(r"\s+", "", label):
        candidates.append((form, submit))

  if not candidates:
    for form in parser.forms:
      form_action = urllib.parse.urljoin(page_url, form["action"] or page_url)
      if urllib.parse.urlparse(form_action).path.rstrip("/") == urllib.parse.urlparse(DO_URL).path.rstrip("/"):
        candidates.append((form, None))
        break
      for submit in form["submits"]:
        submit_action = urllib.parse.urljoin(
            page_url, submit.get("formaction", "") or form["action"] or page_url
        )
        if urllib.parse.urlparse(submit_action).path.rstrip("/") == urllib.parse.urlparse(DO_URL).path.rstrip("/"):
          candidates.append((form, submit))
          break
      if candidates:
        break

  if not candidates:
    return None

  form, submit = candidates[0]
  data = list(form["controls"])

  if submit and submit.get("name"):
    data.append((submit["name"], submit.get("value", "")))

  action = submit.get("formaction") if submit else ""
  action = urllib.parse.urljoin(page_url, action or form["action"] or page_url)
  method = (submit.get("formmethod") if submit else "") or form["method"] or "get"
  enctype = form["enctype"] or "application/x-www-form-urlencoded"

  return action, method.lower(), enctype.lower(), data, submit, form


def do_renew(session: requests.Session) -> bool:
  log("📝 获取续期表单...")
  try:
    resp = session.get(
        RENEW_URL,
        headers={**BASE_HEADERS, "referer": EXTEND_URL},
        timeout=15,
        proxies=PROXIES,
        allow_redirects=True,
    )
    resp.encoding = "EUC-JP"
  except Exception as e:
    log(f"❌ 获取续期表单失败: {e}")
    return False

  uniqid = re.search(r'name="uniqid"\s+value="([^"]+)"', resp.text)
  login_token = re.search(r'name="login_token"\s+value="([^"]+)"', resp.text)
  period = re.search(r'name="period"[^>]*value="(\d+)"', resp.text)

  if not uniqid or not login_token:
    log("❌ 解析续期表单失败")
    return False

  period_val = period.group(1) if period else "12"

  log("📤 提交确认页...")
  try:
    resp2 = session.post(
        CONF_URL,
        headers={
            **BASE_HEADERS,
            "content-type": "application/x-www-form-urlencoded",
            "origin": BASE_URL,
            "referer": RENEW_URL,
        },
        data={
            "uniqid": uniqid.group(1),
            "ethna_csrf": "",
            "login_token": login_token.group(1),
            "period": period_val,
        },
        timeout=15,
        proxies=PROXIES,
        allow_redirects=True,
    )
    resp2.encoding = "EUC-JP"
  except Exception as e:
    log(f"❌ 提交确认页失败: {e}")
    return False

  parsed = _find_extend_confirm_form(resp2.text, resp2.url)
  if not parsed:
    log("❌ 确认页未找到「期限を延長する」按钮对应的 form")
    return False

  action, method, enctype, form_data, submit, form = parsed
  submit_label = (submit or {}).get("text", "") or (submit or {}).get(
      "value", "期限を延長する"
  )
  log(f"🖱️ 自动解析最终按钮：{submit_label.strip() or '期限を延長する'}")

  log("📤 点击「期限を延長する」...")
  try:
    resp3 = session.post(
        action,
        headers={
            **BASE_HEADERS,
            "origin": BASE_URL,
            "referer": resp2.url,
        },
        data=form_data,
        timeout=15,
        proxies=PROXIES,
        allow_redirects=True,
    )
    resp3.encoding = "EUC-JP"
  except Exception as e:
    log(f"❌ 执行续期失败: {e}")
    return False

  body = resp3.text
  success_markers = (
      "更新が完了",
      "更新完了",
      "期限延長が完了",
      "期限を延長しました",
      "期限延長しました",
  )
  error_markers = (
      "エラー",
      "失敗しました",
      "不正なリクエスト",
      "期限を延長できません",
      "CSRF",
  )

  if any(x in body for x in success_markers):
    log("✅ Xserver 返回续期完成信息")
    return True

  if any(x in body for x in error_markers):
    log("❌ Xserver 返回了续期错误信息")
    return False

  log("⚠️ 最终提交未发现明确的成功/失败文字，将以服务器实际期限为准")
  return True


def main():
  log(f"{'🛡️ 使用代理模式' if PROXIES else '🌐 直连模式'}")
  divider(f"{SCRIPT_NAME} starts")
  log(f"🕐 运行时间: {now_str()}")
  log(f"🖥 服务器: {SERVER_NAME}")

  session = login_with_cf()
  jump_to_xmgame(session)

  log("📋 读取服务器信息...")
  page_info = fetch_info_page(session)
  h_before, m_before, dl_before, is_expired = parse_remaining(page_info)

  if h_before == -2:
    fail_exit("❌ 解析剩余时间失败，页面结构异常")

  remaining_before = format_remaining(h_before, m_before, is_expired)

  if is_expired:
    log(f"⚠️ 服务器已过期（{dl_before}），直接尝试续期...")
  else:
    log(f"📅 当前利用期限：{dl_before}")
    log(f"⏳ 当前剩余时间：{h_before}時間{m_before}分")

    if h_before >= RENEW_THRESHOLD_HOURS:
      log(f"ℹ️ 剩余 {h_before} 小时，未低于阈值，无需续期")
      schedule_next_run(h_before, m_before)
      finish(True, "⌛️ 期限未至！", remaining_before)

    page_extend = fetch_extend_page(session)
    if not can_renew(page_extend):
      log("⚠️ 页面提示暂不可续期")
      schedule_next_run(h_before, m_before)
      finish(True, "⌛️ 期限未至！", remaining_before)

  log("🔄 开始续期...")
  if not do_renew(session):
    finish(False, "❌ 续期失败！", remaining_before)

  page_info_after = fetch_info_page(session)
  h_after, m_after, dl_after, expired_after = parse_remaining(page_info_after)
  remaining_after = format_remaining(h_after, m_after, expired_after)

  log(f"📅 续期后利用期限：{dl_after}")
  if not expired_after:
    log(f"⏳ 续期后剩余时间：{h_after}時間{m_after}分")

  if not expired_after and (
      is_expired or dl_after != dl_before or h_after > h_before
  ):
    log("✅ 续期成功！")
    schedule_next_run(h_after, m_after)
    finish(True, "✅ 续期成功！", remaining_after)
  else:
    log("❌ 续期失败，时间未变化")
    finish(False, "❌ 续期失败！", remaining_after or remaining_before)


if __name__ == "__main__":
  main()
