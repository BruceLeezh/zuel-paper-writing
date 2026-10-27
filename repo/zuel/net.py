"""网络取数：统一处理代理、证书、编码与错误提示。"""
from __future__ import annotations

import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

MAX_BYTES = 8 * 1024 * 1024      # 单次响应上限，防止拉爆内存

# 命令行开关落到这里，各模块不必逐层传参
DEFAULTS = {"direct": False, "skip_verify": False}

# 单进程内按站点节流：D 级 1 秒/次，arXiv 按其官方建议 3 秒/次。
# 这是"不加循环批量"的代码层保障，不是装饰。
_INTERVAL: dict[str, float] = {}
_LAST: dict[str, float] = {}

# 关证书校验只应出现在调试场景，用过一次就提示，避免被当成默认设置
_WARNED_INSECURE = False

# 瞬时故障：同一地址重试一次往往就通（跨网段、代理首次握手、对端限速）。
# 只重试这类错误，4xx 与数据错误不重试——重试解决不了它们，只会拖慢报错。
_RETRY = 1
_RETRY_CODES = (502, 503, 504)
_TRANSIENT = ("timed out", "timeout", "remote end closed", "connection reset",
              "connection aborted", "temporarily unavailable",
              "network is unreachable", "eof occurred")


def retryable(message: str) -> bool:
    """判断错误是否值得重试。"""
    low = (message or "").lower()
    return any(k in low for k in _TRANSIENT)


TIPS = {
    "502": "代理转发失败，加 --direct 直连",
    "403": "站点拒绝脚本请求，改用网页导出",
    "406": "缺少 Accept 头，或站点只接受特定类型",
    "429": "被限流：等几分钟重试，或换 --source crossref / arxiv",
    "CERTIFICATE_VERIFY_FAILED": "本机缺根证书：pip install certifi，或临时加 --skip-verify",
    "timed out": "网络不可达：先加 --direct 绕开代理试一次；仍超时说明该站此刻不可达，换线或稍后重试",
}


class NetError(RuntimeError):
    """取数失败。"""


def _trust() -> ssl.SSLContext | None:
    """优先用 certifi 根证书；未安装则交给系统默认。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """重定向守卫：跨域、或 https 降级到 http 时，拒绝跟并丢弃请求头。

    默认处理器会原样带着请求头跟过去。当前代码不发任何认证头，风险有限；
    但 net.fetch 是公共入口，将来有人加 Key 就会变成凭据外泄，所以提前堵住。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        cross_host = (old.hostname or "").lower() != (new.hostname or "").lower()
        downgrade = old.scheme == "https" and new.scheme != "https"
        if cross_host or downgrade:
            raise NetError(
                f"重定向被拒绝：{old.scheme}://{old.hostname} → "
                f"{new.scheme}://{new.hostname}（{'跨域' if cross_host else '明文降级'}）。"
                f"如确需访问，请直接改用目标地址"
            )
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req.headers.pop("Authorization", None)
        return new_req


def _throttle(url: str, interval: float) -> None:
    """同一站点两次请求之间至少间隔 interval 秒。"""
    if interval <= 0:
        return
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    wait = _LAST.get(host, 0.0) + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _LAST[host] = time.monotonic()


def fetch(url: str, *, head: dict | None = None, body: bytes | None = None,
          timeout: float = 15.0, codec: str = "utf-8", interval: float = 0.0,
          skip_verify: bool | None = None, direct: bool | None = None) -> str:
    """取回文本。失败抛 NetError，并附排查方向。

    interval 是站点最小请求间隔（秒），由调用方按数据源等级给出。
    """
    if direct is None:
        direct = DEFAULTS["direct"]
    if skip_verify is None:
        skip_verify = DEFAULTS["skip_verify"]

    _throttle(url, interval)

    headers = {"User-Agent": UA}
    if head:
        headers.update(head)

    handlers: list = [_SafeRedirect()]
    if direct:
        handlers.append(urllib.request.ProxyHandler({}))
    if skip_verify:
        global _WARNED_INSECURE
        if not _WARNED_INSECURE:
            sys.stderr.write("[警告] 已关闭 TLS 证书校验（--skip-verify），仅限本机调试。\n")
            _WARNED_INSECURE = True
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    else:
        ctx = _trust()
        if ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))

    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers=headers, data=body)
    attempt = 0
    while True:
        try:
            with opener.open(req, timeout=timeout) as resp:
                buf = resp.read(MAX_BYTES + 1)
                if len(buf) > MAX_BYTES:
                    raise NetError(
                        f"响应超过 {MAX_BYTES // 1024 // 1024}MB 上限，已中止：{url[:60]}")
                return buf.decode(codec, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRY_CODES and attempt < _RETRY:
                attempt += 1
                time.sleep(1.2)
                continue
            tip = TIPS.get(str(exc.code), "")
            raise NetError(f"HTTP {exc.code}：{url[:70]}"
                           + (f"（{tip}）" if tip else "")) from exc
        except NetError:
            raise
        except Exception as exc:
            msg = str(exc)
            if attempt < _RETRY and retryable(msg):
                attempt += 1
                time.sleep(1.2)
                continue
            tip = next((v for k, v in TIPS.items() if k.lower() in msg.lower()), "")
            raise NetError(f"{type(exc).__name__}：{msg[:110]}"
                           + (f"（{tip}）" if tip else "")) from exc
