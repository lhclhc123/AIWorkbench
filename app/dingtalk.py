# -*- coding: utf-8 -*-
"""钉钉接入（全免费）。

三种通道：
  1. webhook —— 群自定义机器人 Webhook（最简单：群里加个机器人，复制 Webhook 即可）
  2. app     —— 企业内部应用机器人（AppKey/AppSecret，可发单聊/群聊）
  3. stream  —— 官方 Stream 长连接收消息（不需要公网 IP），可实现「在钉钉里和 AI 工作台对话」

发送方向对三种通道都支持；接收方向需要 stream。
"""
import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request

UA = "AIWorkbench/9.0"
TIMEOUT = 15

# 自定义机器人限流：每分钟最多 20 条
_RATE_LIMIT = 20
_send_times = []
_rate_lock = threading.Lock()


class DingTalkError(Exception):
    pass


def _throttle():
    """简单限流，避免触发钉钉「每分钟 20 条」被禁 10 分钟。"""
    with _rate_lock:
        now = time.time()
        while _send_times and now - _send_times[0] > 60:
            _send_times.pop(0)
        if len(_send_times) >= _RATE_LIMIT:
            wait = 60 - (now - _send_times[0])
            raise DingTalkError(f"发送太频繁（{_RATE_LIMIT} 条/分钟），请 {wait:.0f} 秒后再试")
        _send_times.append(now)


def _requests():
    """优先用 requests（本机 urllib 会被系统代理拦），拿不到再退回 urllib。"""
    try:
        import requests
        return requests
    except Exception:
        return None


def _explain_http_error(code, body):
    """把钉钉的错误体翻译成人话——否则用户只看到一句 HTTP 400，完全没法排查。"""
    tips = {
        400: "参数不对：最常见是「接收人 userId」填错了（不能填昵称/显示名）。",
        401: "鉴权失败：AppKey/AppSecret 不对，或 access_token 过期。",
        403: "没权限：应用没开通对应权限（发消息要「机器人」能力，查通讯录要「通讯录」权限）。",
        404: "接口地址不对或应用类型不支持该接口。",
        429: "被限流了，等一会儿再试。",
    }
    extra = ""
    if isinstance(body, dict):
        c = str(body.get("code") or body.get("errcode") or "")
        m = str(body.get("message") or body.get("errmsg") or "")
        if c or m:
            extra = f"（钉钉说：{c} {m}）"
    return f"钉钉返回 HTTP {code}{extra}。{tips.get(code, '')}"


def _post_json(url, payload, headers=None, timeout=TIMEOUT):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8", "User-Agent": UA}
    h.update(headers or {})
    rq = _requests()
    if rq is not None:
        try:
            r = rq.post(url, data=data, headers=h, timeout=timeout)
        except Exception as e:
            raise DingTalkError(f"网络请求失败：{type(e).__name__}: {e}")
        body = r.text or ""
        try:
            j = json.loads(body)
        except Exception:
            j = None
        if r.status_code >= 400:
            raise DingTalkError(_explain_http_error(r.status_code, j)
                                + ("" if j else " 原始返回：" + body[:300]))
        if j is None:
            raise DingTalkError("返回不是合法 JSON：" + body[:300])
        return j
    # ---- urllib 兜底（要把错误体读出来，否则只有一句 HTTP 400）----
    import urllib.error
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "ignore")
        except Exception:
            raw = ""
        try:
            j = json.loads(raw)
        except Exception:
            j = None
        raise DingTalkError(_explain_http_error(e.code, j)
                            + ("" if j else " 原始返回：" + raw[:300]))
    try:
        return json.loads(body)
    except Exception:
        raise DingTalkError("返回不是合法 JSON：" + body[:200])


def _get_json(url, headers=None, timeout=TIMEOUT, params=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    rq = _requests()
    if rq is not None:
        try:
            r = rq.get(url, headers=h, timeout=timeout, params=params)
        except Exception as e:
            raise DingTalkError(f"网络请求失败：{type(e).__name__}: {e}")
        try:
            return r.json()
        except Exception:
            if r.status_code >= 400:
                raise DingTalkError(_explain_http_error(
                    r.status_code, None) + " 原始返回：" + (r.text or "")[:300])
            raise DingTalkError("返回不是合法 JSON：" + (r.text or "")[:300])
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(url + qs, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


DEFAULT_CONFIG = {
    "enabled": False,
    "mode": "webhook",              # webhook | app
    "webhook": "",                  # https://oapi.dingtalk.com/robot/send?access_token=xxx
    "secret": "",                   # 加签密钥（安全设置选"加签"时填）
    "keyword": "",                  # 安全设置选"自定义关键词"时填（会自动加在消息开头）
    "client_id": "",                # 应用机器人 AppKey
    "client_secret": "",            # 应用机器人 AppSecret
    "robot_code": "",               # 应用机器人 robotCode（一般同 AppKey）
    "agent_id": "",                 # 微应用的 AgentId（发「工作通知」必须）
    "open_conversation_id": "",     # 群聊会话 ID（应用机器人发群消息用）
    "user_ids": "",                 # 单聊接收人 userId，多个用逗号分隔
    "receive_enabled": False,       # Stream 收消息
    "at_all": False,
    "push_tag": "【AI工作台】",
    # —— 扫码授权登录（OAuth）——
    "login_user_id": "",            # 上次授权登录拿到的用户（仅显示用）
    "login_user_name": "",
    "oauth_port": 8765,             # 本地回调端口（redirect_uri 必须与开发者后台一致）
}


# ---------------------------------------------------------------------------
# 扫码授权登录：本地回调服务器
# ---------------------------------------------------------------------------
class OAuthCallbackServer:
    """在 127.0.0.1 起一个一次性 HTTP 服务，接住钉钉回调里的 code。

    用法：先 server.start()，把 server.redirect_uri 配到钉钉开发者后台的
    「登录与分享 → 回调域名」，再打开浏览器；钉钉跳回来时自动拿到 code。
    """

    def __init__(self, port=8765, path="/callback", timeout=300):
        self.port = int(port or 8765)
        self.path = path or "/callback"
        self.timeout = timeout
        self.code = None
        self.state = ""
        self.error = ""
        self.done = threading.Event()
        self._httpd = None
        self._thread = None

    @property
    def redirect_uri(self):
        return f"http://127.0.0.1:{self.port}{self.path}"

    def start(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        outer = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):      # 别往控制台刷日志
                pass

            def do_GET(self):
                q = urllib.parse.urlparse(self.path).query
                params = dict(urllib.parse.parse_qsl(q))
                if params.get("code"):
                    outer.code = params["code"]
                    outer.state = params.get("state", "")
                    msg = "<h2>授权成功 ✅</h2><p>可以关掉这个页面，回到 AI 工作台。</p>"
                else:
                    outer.error = params.get("error") or "回调里没有 code"
                    msg = f"<h2>授权失败 ❌</h2><p>{outer.error}</p>"
                body = ("<html><meta charset='utf-8'><body style='font-family:"
                        "Microsoft YaHei;padding:40px'>" + msg + "</body></html>")
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                outer.done.set()

        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _H)
        except OSError as e:
            raise DingTalkError(
                f"本地端口 {self.port} 起不来（{e}）。换一个端口再试。")
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def wait(self):
        self.done.wait(self.timeout)
        self.stop()
        if self.code:
            return True, self.code
        return False, self.error or "等待超时（没等到钉钉回调）"

    def stop(self):
        try:
            if self._httpd is not None:
                self._httpd.shutdown()
                self._httpd.server_close()
        except Exception:
            pass
        self._httpd = None


class DingTalkClient:
    def __init__(self, cfg=None):
        self.cfg = dict(DEFAULT_CONFIG)
        if cfg:
            self.cfg.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
        self._token = ""
        self._token_expire = 0
        self._stream_client = None
        self._stream_thread = None
        self._stream_handler = None
        self._stream_running = False

    # ---------------- 配置 ----------------
    def update(self, cfg):
        self.cfg.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG})

    @property
    def mode(self):
        return self.cfg.get("mode") or "webhook"

    def configured(self):
        if not self.cfg.get("enabled"):
            return False
        if self.mode == "app":
            return bool(self.cfg.get("client_id") and self.cfg.get("client_secret"))
        return bool(self.cfg.get("webhook"))

    def status_text(self):
        if not self.cfg.get("enabled"):
            return "未启用"
        if self.mode == "app":
            ok = bool(self.cfg.get("client_id") and self.cfg.get("client_secret"))
            s = "应用机器人 · " + ("已配置" if ok else "缺少 AppKey/AppSecret")
            bad = self.looks_like_nickname()
            if bad:
                s += f" · ⚠️ 接收人「{bad}」像是昵称，必须填 userId"
            if self._stream_running:
                s += " · 收消息已连接"
            return s
        return "群机器人 Webhook · " + ("已配置" if self.cfg.get("webhook") else "未配置")

    def looks_like_nickname(self):
        """接收人里看起来不像 userId 的项（含中文、空格，或以 self 结尾）。

        踩过的坑：把「昵称」当成 userId 填进去（如「张三」），发消息直接 400
        staffId.notExisted，而界面上看不出任何异常。
        """
        for u in (self.cfg.get("user_ids") or "").split(","):
            u = u.strip()
            if not u:
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in u) or " " in u \
                    or u.lower().endswith("self"):
                return u
        return ""

    # ---------------- 签名 ----------------
    def _signed_webhook(self):
        url = self.cfg.get("webhook", "").strip()
        if not url:
            raise DingTalkError("没有配置钉钉 Webhook 地址")
        secret = (self.cfg.get("secret") or "").strip()
        if secret:
            ts = str(round(time.time() * 1000))
            string_to_sign = f"{ts}\n{secret}"
            digest = hmac.new(secret.encode("utf-8"),
                              string_to_sign.encode("utf-8"),
                              digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(digest))
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}timestamp={ts}&sign={sign}"
        return url

    def _decorate(self, content):
        """关键词安全设置：消息里必须含关键词，否则会被拒。"""
        kw = (self.cfg.get("keyword") or "").strip()
        tag = (self.cfg.get("push_tag") or "").strip()
        prefix = tag
        if kw and kw not in content:
            prefix = f"{tag} {kw}" if tag else kw
        return (prefix + " " + content) if prefix else content

    # ---------------- 应用机器人 access_token ----------------
    def _app_token(self):
        now = time.time()
        if self._token and now < self._token_expire - 60:
            return self._token
        cid = self.cfg.get("client_id", "").strip()
        sec = self.cfg.get("client_secret", "").strip()
        if not (cid and sec):
            raise DingTalkError("缺少 AppKey / AppSecret")
        r = _post_json("https://api.dingtalk.com/v1.0/oauth2/accessToken",
                       {"appKey": cid, "appSecret": sec})
        tok = r.get("accessToken")
        if not tok:
            raise DingTalkError(f"获取 access_token 失败：{r}")
        self._token = tok
        self._token_expire = now + int(r.get("expireIn") or 7200)
        return tok

    # ---------------- 发送 ----------------
    def send_text(self, content, at_all=None):
        return self.send(content, title=None, at_all=at_all)

    def send_markdown(self, title, text, at_all=None):
        return self.send(text, title=title, at_all=at_all)

    def send(self, content, title=None, at_all=None):
        """统一发送入口。content 为正文，title 非空则发 markdown。"""
        content = (content or "").strip()
        if not content:
            raise DingTalkError("消息内容为空")
        at_all = self.cfg.get("at_all") if at_all is None else at_all
        _throttle()
        if self.mode == "app":
            return self._send_app(content, title, at_all)
        return self._send_webhook(content, title, at_all)

    def _send_webhook(self, content, title, at_all):
        body_text = self._decorate(content)
        if title:
            payload = {"msgtype": "markdown",
                       "markdown": {"title": self._decorate(title),
                                    "text": body_text},
                       "at": {"isAtAll": bool(at_all)}}
        else:
            payload = {"msgtype": "text",
                       "text": {"content": body_text},
                       "at": {"isAtAll": bool(at_all)}}
        r = _post_json(self._signed_webhook(), payload)
        if r.get("errcode") not in (0, None):
            raise DingTalkError(f"钉钉返回错误 {r.get('errcode')}：{r.get('errmsg')}")
        return {"ok": True, "raw": r}

    # ---------------- 通讯录：帮用户查出可用的 userId ----------------
    def list_user_ids(self, dept_id=1, max_n=50):
        """列出本组织里当前应用能看到的 userId。

        用途：用户经常把「昵称」当成 userId 填进来（如"张三"），
        发消息就会报 staffId.notExisted。这里直接去通讯录查真名。
        """
        tok = self._app_token()
        out = []
        r = _post_json(
            f"https://oapi.dingtalk.com/topapi/user/listid?access_token={tok}",
            {"dept_id": int(dept_id or 1)})
        if r.get("errcode") not in (0, None):
            raise DingTalkError(
                f"查接收人失败：{r.get('errcode')} {r.get('errmsg')}。"
                "（应用需要「通讯录」里的成员信息读权限）")
        res = r.get("result") or {}
        for uid in (res.get("userid_list") or []):
            if uid:
                out.append(str(uid))
            if len(out) >= max_n:
                break
        if not out:
            # 子部门里再找一层
            sub = _post_json(
                f"https://oapi.dingtalk.com/topapi/v2/department/listsub?access_token={tok}",
                {"dept_id": int(dept_id or 1)})
            for d in (sub.get("result") or []):
                try:
                    out.extend(self.list_user_ids(d.get("dept_id"), max_n - len(out)))
                except Exception:
                    pass
                if len(out) >= max_n:
                    break
        return out[:max_n]

    def lookup_user_name(self, user_id):
        """查某个 userId 的真实姓名（用来给用户看"这是谁"）。"""
        try:
            tok = self._app_token()
            r = _post_json(
                f"https://oapi.dingtalk.com/topapi/v2/user/get?access_token={tok}",
                {"userid": user_id})
            if r.get("errcode") in (0, None):
                return (r.get("result") or {}).get("name") or ""
        except Exception:
            pass
        return ""

    def auto_fix_users(self):
        """把配置里填错的接收人换成通讯录里真实存在的 userId。

        返回 (新列表, 说明文字)。查不到就返回原来的。
        """
        try:
            valid = self.list_user_ids()
        except Exception as e:
            return [], f"自动查接收人失败：{e}"
        if not valid:
            return [], ("通讯录里没查到任何成员。请确认应用已开通「通讯录」"
                        "成员信息读权限，或改用「群自定义机器人 Webhook」方式。")
        names = []
        for u in valid[:5]:
            nm = self.lookup_user_name(u)
            names.append(f"{u}（{nm}）" if nm else u)
        return valid, "通讯录里可用的接收人：" + "、".join(names)

    # ---------------- 通讯录：部门 / 成员 ----------------
    def list_departments(self, dept_id=1):
        """列出某个部门下的子部门。dept_id=1 是根部门。"""
        tok = self._app_token()
        r = _post_json(
            f"https://oapi.dingtalk.com/topapi/v2/department/listsub?access_token={tok}",
            {"dept_id": int(dept_id or 1)})
        if r.get("errcode") not in (0, None):
            raise DingTalkError(
                f"查部门失败：{r.get('errcode')} {r.get('errmsg')}。"
                "（应用需要「通讯录」部门信息读权限）")
        return [{"id": d.get("dept_id"), "name": d.get("name") or f"部门{d.get('dept_id')}"}
                for d in (r.get("result") or [])]

    def list_members(self, dept_id=1, limit=100):
        """列出某个部门下的成员（含真实姓名）。"""
        tok = self._app_token()
        r = _post_json(
            f"https://oapi.dingtalk.com/topapi/v2/user/list?access_token={tok}",
            {"dept_id": int(dept_id or 1), "cursor": 0,
             "size": max(1, min(100, int(limit or 100)))})
        if r.get("errcode") not in (0, None):
            raise DingTalkError(
                f"查成员失败：{r.get('errcode')} {r.get('errmsg')}。"
                "（应用需要「通讯录」成员信息读权限）")
        res = r.get("result") or {}
        out = []
        for u in (res.get("list") or []):
            if not u.get("userid"):
                continue
            out.append({"userid": str(u.get("userid")),
                        "name": u.get("name") or "",
                        "title": u.get("title") or "",
                        "dept": u.get("dept_id_list") or []})
        return out

    # ---------------- 工作通知（不依赖机器人的推送通道） ----------------
    def send_work_notice(self, content, user_ids=None, dept_ids=None, to_all=False):
        """发工作通知（钉钉「工作通知」消息）。

        比机器人更稳：只要应用有 AgentId 就能发给组织成员，不需要机器人能力。
        """
        agent = str(self.cfg.get("agent_id") or "").strip()
        if not agent.isdigit():
            raise DingTalkError("发工作通知需要填「AgentId」（开发者后台 → 应用信息里能看到）")
        tok = self._app_token()
        if to_all:
            body = {"agent_id": int(agent), "to_all_user": True,
                    "msgtype": "text", "text": {"content": self._decorate(content)}}
        else:
            users = user_ids if isinstance(user_ids, (list, tuple)) else \
                [u.strip() for u in str(user_ids or self.cfg.get("user_ids") or "")
                 .split(",") if u.strip()]
            depts = dept_ids if isinstance(dept_ids, (list, tuple)) else []
            if not users and not depts:
                raise DingTalkError("工作通知没有指定接收人（user_ids / dept_ids / to_all）")
            body = {"agent_id": int(agent), "msgtype": "text",
                    "text": {"content": self._decorate(content)}}
            if users:
                body["userid_list"] = ",".join(str(u) for u in users[:100])
            if depts:
                body["dept_id_list"] = ",".join(str(d) for d in depts[:20])
        r = _post_json(
            f"https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2"
            f"?access_token={tok}", body)
        if r.get("errcode") not in (0, None):
            raise DingTalkError(f"发工作通知失败：{r.get('errcode')} {r.get('errmsg')}")
        return {"ok": True, "task_id": r.get("task_id"), "raw": r}

    # ---------------- 群会话 ----------------
    def get_group(self, chat_id):
        """按 chatId 查群信息（群名、群主、成员数）。"""
        tok = self._app_token()
        r = _post_json(f"https://oapi.dingtalk.com/topapi/chat/get?access_token={tok}",
                       {"chatid": chat_id})
        if r.get("errcode") not in (0, None):
            raise DingTalkError(f"查群失败：{r.get('errcode')} {r.get('errmsg')}")
        return r.get("chat_info") or r.get("result") or {}

    def send_group(self, content, open_conversation_id=None, title=None):
        """按 openConversationId 往群里发消息（需要机器人在该群）。"""
        conv = str(open_conversation_id or self.cfg.get("open_conversation_id") or "").strip()
        if not conv:
            raise DingTalkError("需要「群会话 ID」（openConversationId）")
        token = self._app_token()
        robot = (self.cfg.get("robot_code") or self.cfg.get("client_id") or "").strip()
        text = self._decorate(content)
        if title:
            msg_key, msg_param = "sampleMarkdown", {"title": self._decorate(title), "text": text}
        else:
            msg_key, msg_param = "sampleText", {"content": text}
        r = _post_json("https://api.dingtalk.com/v1.0/robot/groupMessages/send",
                       {"robotCode": robot, "openConversationId": conv,
                        "msgKey": msg_key,
                        "msgParam": json.dumps(msg_param, ensure_ascii=False)},
                       {"x-acs-dingtalk-access-token": token})
        return {"ok": True, "raw": r, "target": "群 " + conv[:14]}

    # ---------------- 扫码授权登录（OAuth2） ----------------
    def auth_url(self, redirect_uri, state="aiworkbench"):
        """拼出钉钉扫码授权页地址（用系统浏览器打开它）。"""
        cid = (self.cfg.get("client_id") or "").strip()
        if not cid:
            raise DingTalkError("扫码登录需要先填 AppKey（client_id）")
        return ("https://login.dingtalk.com/oauth2/auth?"
                + urllib.parse.urlencode({
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "client_id": cid,
                    "scope": "openid",
                    "state": state,
                    "prompt": "consent"}))

    def exchange_user_token(self, code):
        """用回调里的 code 换用户级 accessToken。"""
        cid = (self.cfg.get("client_id") or "").strip()
        sec = (self.cfg.get("client_secret") or "").strip()
        if not (cid and sec):
            raise DingTalkError("扫码登录需要 AppKey + AppSecret")
        r = _post_json("https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
                       {"clientId": cid, "clientSecret": sec, "code": code,
                        "grantType": "authorization_code"})
        tok = r.get("accessToken")
        if not tok:
            raise DingTalkError(f"换 token 失败：{r}")
        return tok, r

    def get_login_user(self, user_token):
        """拿授权登录用户的信息（昵称、unionId、头像）。"""
        r = _get_json("https://api.dingtalk.com/v1.0/contact/users/me",
                      headers={"x-acs-dingtalk-access-token": user_token})
        return r or {}

    # ---------------- 一键自检：逐项真实调用，把结果摊开给用户看 ----------------
    def self_test(self):
        """按顺序真调一遍钉钉接口，返回 [(项目, ok, 说明)]。

        这就是"适配调试"的落点：哪一项不通、钉钉原话是什么、要开哪个权限，
        全部摆出来，用户照着开就行了。
        """
        out = []

        def add(name, ok, msg):
            out.append((name, bool(ok), str(msg)[:300]))

        cid = (self.cfg.get("client_id") or "").strip()
        sec = (self.cfg.get("client_secret") or "").strip()

        # 1) access_token
        tok = None
        if not (cid and sec):
            add("1. 应用凭据（AppKey/AppSecret）", False, "没填 AppKey / AppSecret")
        else:
            try:
                tok = self._app_token()
                add("1. 应用凭据（AppKey/AppSecret）", True,
                    "拿到 access_token，鉴权通过")
            except Exception as e:
                add("1. 应用凭据（AppKey/AppSecret）", False, e)

        # 2) 通讯录 - 部门
        if tok:
            try:
                depts = self.list_departments(1)
                add("2. 通讯录 · 部门列表", True,
                    f"根部门下有 {len(depts)} 个子部门"
                    + ("：" + "、".join(d["name"] for d in depts[:5]) if depts else ""))
            except Exception as e:
                add("2. 通讯录 · 部门列表", False, e)
            # 3) 通讯录 - 成员
            try:
                mem = self.list_members(1, 50)
                if mem:
                    add("3. 通讯录 · 成员列表", True,
                        f"{len(mem)} 人，例：" + "、".join(
                            f"{m['name']}({m['userid']})" for m in mem[:3]))
                else:
                    add("3. 通讯录 · 成员列表", False,
                        "查到了接口但没人。通常是根部门下没有直属成员——"
                        "试试去子部门里找，或确认成员已加入组织。")
            except Exception as e:
                add("3. 通讯录 · 成员列表", False, e)

        # 4) 机器人能力（拿机器人所在会话）
        if tok:
            try:
                robot = (self.cfg.get("robot_code") or cid or "").strip()
                _post_json("https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                           {"robotCode": robot, "userIds": ["__probe__"],
                            "msgKey": "sampleText",
                            "msgParam": json.dumps({"content": "probe"}, ensure_ascii=False)},
                           {"x-acs-dingtalk-access-token": tok}, timeout=10)
                add("4. 机器人 · 单聊发消息", True, "接口可用")
            except Exception as e:
                msg = str(e)
                if "staffId" in msg or "notExisted" in msg or "不存在" in msg:
                    add("4. 机器人 · 单聊发消息", True,
                        "机器人能力正常（探测用的假 userId 被如实拒绝，说明接口通了）")
                else:
                    add("4. 机器人 · 单聊发消息", False, msg)

        # 5) 工作通知
        if tok and str(self.cfg.get("agent_id") or "").strip():
            try:
                self.send_work_notice(
                    f"AI 工作台自检 {time.strftime('%H:%M:%S')}",
                    user_ids=["__probe__"])
                add("5. 工作通知 · 发送", True, "已受理")
            except Exception as e:
                msg = str(e)
                if "不存在" in msg or "invalid" in msg.lower() or "userid" in msg.lower():
                    add("5. 工作通知 · 发送", True, "接口可用（假用户被如实拒绝）")
                else:
                    add("5. 工作通知 · 发送", False, msg)
        else:
            add("5. 工作通知 · 发送", False,
                "没填 AgentId（不填也能用机器人/Webhook，填了多一条推送通道）")

        # 6) 群消息能力：只做参数检查，避免真的发消息打扰别人
        conv = str(self.cfg.get("open_conversation_id") or "").strip()
        add("6. 群会话 · 发群消息", bool(conv) and bool(tok),
            f"群会话 ID：{conv[:14]}…" if conv else
            "没填 openConversationId（要往群里发消息就填它）")

        # 7) Stream 长连接（收消息）——用户最容易"看着像连不上"的一项，
        #    这里把真实状态和常见原因说清楚，别让用户自己猜。
        if not (cid and sec):
            add("7. Stream 长连接（收消息）", False,
                "没填 AppKey/AppSecret，Stream 起不来。"
                "到「钉钉开放平台 → 应用 → 机器人」确认已创建并开启了"
                "「Stream 模式」的机器人。")
        elif self._stream_running:
            add("7. Stream 长连接（收消息）", True,
                "已连接（正在监听机器人收到的消息）")
        else:
            try:
                import dingtalk_stream  # noqa: F401
                add("7. Stream 长连接（收消息）", False,
                    "当前**没在运行**。到集成页勾选「接收消息」后点"
                    "「连接/断开 Stream」，或重启本程序（启动时会自动连）。\n"
                    "若点了还是连不上，多半是：① 应用没开「机器人」能力的 Stream 模式；"
                    "② 版本是旧 exe（请换成 dist_v9\\AIWorkbench\\AIWorkbench.exe）。")
            except Exception:
                add("7. Stream 长连接（收消息）", False,
                    "缺少 dingtalk_stream 依赖（程序打包异常，请重新下载最新版）")

        # 8) 平台边界说明（不重要但必须讲清楚）
        add("8. 读取个人聊天会话列表", False,
            "钉钉开放平台**不提供**读取个人聊天记录/会话列表的接口（这是平台限制）。"
            "能拿到的是：机器人收发的消息（Stream 长连接）、通讯录、群信息。")

        return out

    # ---------------- 发送（应用机器人：单聊 / 群聊） ----------------
    def _send_app(self, content, title, at_all):
        token = self._app_token()
        robot = (self.cfg.get("robot_code") or self.cfg.get("client_id") or "").strip()
        headers = {"x-acs-dingtalk-access-token": token}
        conv = (self.cfg.get("open_conversation_id") or "").strip()
        users = [u.strip() for u in (self.cfg.get("user_ids") or "").split(",") if u.strip()]
        text = self._decorate(content)
        if title:
            msg_key, msg_param = "sampleMarkdown", {"title": self._decorate(title), "text": text}
        else:
            msg_key, msg_param = "sampleText", {"content": text}
        if conv:
            body = {"robotCode": robot, "openConversationId": conv,
                    "msgKey": msg_key,
                    "msgParam": json.dumps(msg_param, ensure_ascii=False)}
            r = _post_json("https://api.dingtalk.com/v1.0/robot/groupMessages/send",
                           body, headers)
            return {"ok": True, "raw": r, "target": "群 " + conv[:12]}
        if users:
            body = {"robotCode": robot, "userIds": users, "msgKey": msg_key,
                    "msgParam": json.dumps(msg_param, ensure_ascii=False)}
            try:
                r = _post_json("https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                               body, headers)
            except DingTalkError as e:
                # 最常见：userId 填成了昵称 -> 自动去通讯录查真名并重发一次
                if "staffId" not in str(e) and "staff" not in str(e):
                    raise
                fixed, note = self.auto_fix_users()
                if not fixed:
                    raise DingTalkError(
                        "接收人 userId 不存在（不能填昵称）。" + note) from e
                body["userIds"] = fixed[:20]
                r = _post_json("https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                               body, headers)
                return {"ok": True, "raw": r,
                        "target": f"{len(fixed)} 个用户（已自动纠正为 {'、'.join(fixed[:3])}）"}
            return {"ok": True, "raw": r, "target": f"{len(users)} 个用户"}
        raise DingTalkError("应用机器人模式需要填「群会话 ID」或「接收人 userId」")

    def test(self):
        """发一条测试消息，返回 (成功?, 说明)。"""
        if not self.cfg.get("enabled"):
            return False, "钉钉推送还没启用"
        if not self.configured():
            return False, "配置不完整：" + self.status_text()
        bad = self.looks_like_nickname()
        if bad:
            return False, (f"接收人「{bad}」填的是昵称（或带 self），"
                           f"必须填 userId。点下面的「自动查接收人」按钮，"
                           f"我把通讯录里真实可用的 userId 查出来。")
        try:
            r = self.send(
                f"✅ 这是一条来自 AI 工作台的测试消息。\n"
                f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"通道：{self.status_text()}\n"
                f"收到说明配置成功，之后可以让 AI 把结果、简报、告警推到钉钉。",
                title="AI 工作台 · 测试消息")
            return True, f"已发送（{r.get('target') or '群机器人'}）"
        except Exception as e:
            msg = str(e)
            if "staffId" in msg or "staff" in msg:
                try:
                    fixed, note = self.auto_fix_users()
                except Exception:
                    fixed, note = [], ""
                if fixed:
                    return False, ("接收人 userId 不存在。" + note
                                   + "　→ 建议把接收人改成上面第一个。")
            return False, msg

    # ---------------- Stream 收消息 ----------------
    def stream_available(self):
        try:
            import dingtalk_stream  # noqa: F401
            return True
        except Exception:
            return False

    def start_stream(self, on_message, on_log=None):
        """启动 Stream 长连接，接收钉钉消息。

        on_message(text, sender, chat_type, raw_msg) -> 回复文本（str）或 None
        """
        if self._stream_running:
            return True, "已经在运行"
        if not self.stream_available():
            return False, "缺少 dingtalk-stream 库"
        cid = (self.cfg.get("client_id") or "").strip()
        sec = (self.cfg.get("client_secret") or "").strip()
        if not (cid and sec):
            return False, "Stream 模式需要填 AppKey / AppSecret"

        def _log(msg):
            if on_log:
                try:
                    on_log(msg)
                except Exception:
                    pass

        def _run():
            try:
                import dingtalk_stream

                credential = dingtalk_stream.Credential(cid, sec)
                client = dingtalk_stream.DingTalkStreamClient(credential)

                outer = self

                class _Handler(dingtalk_stream.ChatbotHandler):
                    async def process(self, callback):
                        try:
                            msg = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                            text = ""
                            try:
                                text = (msg.text.content or "").strip()
                            except Exception:
                                text = ""
                            sender = getattr(msg, "sender_nick", "") or getattr(
                                msg, "sender_staff_id", "") or "钉钉用户"
                            ctype = "群" if getattr(msg, "conversation_type", "") == "2" else "单聊"
                            _log(f"收到{ctype}消息（{sender}）：{text[:60]}")
                            if not text:
                                self.reply_text("（我暂时只认文字消息）", msg)
                                return dingtalk_stream.AckMessage.STATUS_OK, "OK"
                            reply = on_message(text, sender, ctype, msg)
                            if reply:
                                outer._reply_long(reply, msg)
                            else:
                                self.reply_text("已收到，正在处理…", msg)
                        except Exception as e:
                            _log(f"处理消息出错：{e}")
                        return dingtalk_stream.AckMessage.STATUS_OK, "OK"

                client.register_callback_handler(
                    dingtalk_stream.ChatbotMessage.TOPIC, _Handler())
                self._stream_client = client
                self._stream_running = True
                _log("钉钉 Stream 已启动，等待消息…")
                client.start_forever()
            except Exception as e:
                _log(f"Stream 连接结束/失败：{e}")
            finally:
                self._stream_running = False

        self._stream_thread = threading.Thread(target=_run, daemon=True)
        self._stream_thread.start()
        return True, "正在连接…"

    @staticmethod
    def _reply_long(text, msg):
        """钉钉单条消息有长度限制，过长自动切段回复。"""
        limit = 4000
        text = text or ""
        chunks = [text[i:i + limit] for i in range(0, len(text), limit)] or [""]
        for c in chunks[:5]:
            try:
                DingTalkClient._handler_reply(c, msg)
            except Exception:
                break

    @staticmethod
    def _handler_reply(text, msg):
        # ChatbotHandler 是 SDK 的实例方法，这里走它自带的 reply_text 需要 handler 实例；
        # 为简化，直接用 sessionWebhook 自己 POST。
        hook = getattr(msg, "session_webhook", "") or ""
        if not hook:
            return
        _post_json(hook, {"msgtype": "text", "text": {"content": text}})

    def stop_stream(self):
        try:
            if self._stream_client is not None:
                self._stream_client.stop()
        except Exception:
            pass
        self._stream_running = False


def config_from_settings(settings):
    """从 settings.json 里取出钉钉配置（兼容扁平与嵌套两种写法）。"""
    cfg = dict(DEFAULT_CONFIG)
    nested = (settings or {}).get("dingtalk") or {}
    cfg.update({k: v for k, v in nested.items() if k in DEFAULT_CONFIG})
    flat = (settings or {}).get("dingtalk_webhook")
    if flat and not cfg.get("webhook"):
        cfg["webhook"] = flat
    return cfg
