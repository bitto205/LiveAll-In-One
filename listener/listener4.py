"""
listener4.py — 直播伴侣代理注入方案

修改直播伴侣 Electron 应用的 index.js，注入 --proxy-server 启动参数，
使直播伴侣的所有流量经过本地 mitmproxy 代理。

优势（对比 listener3 WinDivert 方案）：
- 不依赖 WinDivert，无"Cannot spawn more than one"问题
- 直播伴侣启动后就一直走代理，任何时候点连接都能抓到新建的 WSS
- 不占用系统代理，不影响其他应用

使用流程：
1. 软件里点"Patch 直播伴侣"（自动找安装路径并修改 index.js）
2. 重启直播伴侣一次
3. 之后正常开播，点线路四连接即可

直播伴侣更新后会覆盖 index.js，需重新 patch（软件启动时自动检测）。
"""
import asyncio
import logging
import os
import re
import shutil
import subprocess
import winreg
from typing import Callable, Optional

from mitmproxy import http, options
from mitmproxy.tools.dump import DumpMaster

try:
    from listener.listener2 import _parse_frame
except ImportError:
    from listener2 import _parse_frame

logger = logging.getLogger(__name__)

PROXY_PORT   = 8888
_PROXY_VALUE = f"127.0.0.1:{PROXY_PORT},direct://"
HOST_FILTER  = ("webcast",)
_TIMEOUT     = 60.0


# ─────────────────────────────────────────────
# 直播伴侣路径查找
# ─────────────────────────────────────────────
def _find_install_dir() -> Optional[str]:
    subkeys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for subkey in subkeys:
            try:
                with winreg.OpenKey(hive, subkey) as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, i)) as entry:
                                try:
                                    name = winreg.QueryValueEx(entry, "DisplayName")[0]
                                    if "直播伴侣" not in name:
                                        continue
                                    loc = winreg.QueryValueEx(entry, "InstallLocation")[0]
                                    if loc and os.path.isdir(loc):
                                        return loc.rstrip("\\")
                                except FileNotFoundError:
                                    pass
                        except Exception:
                            continue
            except Exception:
                continue
    return None


def find_index_js() -> Optional[str]:
    """返回直播伴侣 index.js 的完整路径，找不到返回 None。

    直播伴侣使用 Launcher 模式：InstallLocation 是 launcher 目录，
    实际版本在子目录里，路径由 launcher_config.json 的 cur_path 字段决定。
    """
    root = _find_install_dir()
    if not root:
        return None

    # 候选路径列表，按优先级排列
    candidates: list[str] = []

    # 1. Launcher 模式：读 launcher_config.json 找当前版本子目录
    config_path = os.path.join(root, "launcher_config.json")
    if os.path.isfile(config_path):
        try:
            import json
            cfg = json.load(open(config_path, encoding="utf-8", errors="ignore"))
            for key in ("cur_path", "new_path"):
                ver = cfg.get(key, "")
                if ver:
                    for rel in ("index.js", os.path.join("app.asar.unpacked", "index.js")):
                        candidates.append(os.path.join(root, ver, "resources", "app", rel))
        except Exception:
            pass

    # 2. 直接安装模式（无 launcher）
    for rel in (
        os.path.join("resources", "app", "index.js"),
        os.path.join("resources", "app.asar.unpacked", "index.js"),
    ):
        candidates.append(os.path.join(root, rel))

    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


# ─────────────────────────────────────────────
# Patch / Unpatch
# ─────────────────────────────────────────────
def is_patched() -> bool:
    """index.js 中已含我们的代理地址则视为已 patch。"""
    path = find_index_js()
    if not path:
        return False
    try:
        return f"127.0.0.1:{PROXY_PORT}" in open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return False


def patch_companion() -> tuple[bool, str]:
    """
    向直播伴侣 index.js 注入代理设置并绕过完整性校验。
    返回 (成功, 提示信息)。
    """
    path = find_index_js()
    if not path:
        return False, "未找到直播伴侣安装目录，请确认已安装"

    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return False, f"读取 index.js 失败: {e}"

    if f"127.0.0.1:{PROXY_PORT}" in content:
        return True, "已经是 patch 状态"

    # 备份原文件（仅首次）
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)

    # 1. 替换已有的 proxy-server appendSwitch 参数值
    proxy_re = re.compile(
        r'(\.commandLine\.appendSwitch\s*\(\s*["\']proxy-server["\'],\s*["\'])([^"\']*?)(["\'])'
    )
    if proxy_re.search(content):
        new_content = proxy_re.sub(rf'\g<1>{_PROXY_VALUE}\g<3>', content)
    else:
        # 2. 在 app.on('ready'…) 前注入新的 appendSwitch 调用
        ready_re = re.compile(r'(\b(\w+)\.on\s*\(\s*["\']ready["\'])')
        m = ready_re.search(content)
        if m:
            app_var = m.group(2)
            inject = f'{app_var}.commandLine.appendSwitch("proxy-server","{_PROXY_VALUE}");'
            new_content = content[:m.start()] + inject + content[m.start():]
        else:
            return False, "未找到合适的注入点，index.js 结构可能已变更"

    # 3. 绕过直播伴侣自身的完整性校验（避免 patch 后弹"文件损坏"并退出）
    # 实际 pattern：})(E),!E.ok)return ... app.quit()
    # 把 ,!VARNAME.ok) 改成 ,false) 让条件永远不成立
    new_content, n_subs = re.subn(r',!\w+\.ok\)', ',false)', new_content, count=1)
    if n_subs == 0:
        logger.warning("未找到完整性校验 pattern，直播伴侣启动时可能弹出文件损坏提示")

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True, "Patch 成功！请重启直播伴侣使设置生效"
    except Exception as e:
        return False, f"写入 index.js 失败: {e}"


def unpatch_companion() -> tuple[bool, str]:
    """从备份还原 index.js。"""
    path = find_index_js()
    if not path:
        return False, "未找到直播伴侣"
    bak = path + ".bak"
    if not os.path.exists(bak):
        return False, "未找到备份文件"
    try:
        shutil.copy2(bak, path)
        return True, "已还原原始 index.js"
    except Exception as e:
        return False, f"还原失败: {e}"


# ─────────────────────────────────────────────
# mitmproxy CA 证书安装
# ─────────────────────────────────────────────
def _install_cert() -> None:
    cert = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.cer")
    if not os.path.exists(cert):
        logger.warning("mitmproxy CA 证书未找到，TLS 解密可能失败")
        return
    r = subprocess.run(
        ["certutil", "-addstore", "-f", "ROOT", cert],
        capture_output=True, text=True, errors="ignore",
    )
    if r.returncode == 0:
        logger.info("mitmproxy CA 证书已安装")
    else:
        logger.debug(f"certutil 返回 {r.returncode}（证书可能已存在）")


# ─────────────────────────────────────────────
# mitmproxy addon
# ─────────────────────────────────────────────
class _DouyinWsAddon:
    def __init__(self, callback: Callable, on_status: Optional[Callable],
                 connected: asyncio.Event):
        self.callback    = callback
        self.on_status   = on_status
        self._connected  = connected
        self._seen_first = False

    def request(self, flow: http.HTTPFlow):
        if flow.request.headers.get("upgrade", "").lower() == "websocket":
            logger.info(f"WS 升级: {flow.request.host}{flow.request.path[:80]}")

    def websocket_message(self, flow: http.HTTPFlow):
        host = flow.request.host or ""
        if not any(k in host for k in HOST_FILTER):
            return
        assert flow.websocket is not None
        msg = flow.websocket.messages[-1]
        if msg.from_client:
            return
        if not self._seen_first:
            self._seen_first = True
            self._connected.set()
            logger.info("✅ 监听到弹幕 WSS 连接，开始解析")
            if self.on_status:
                self.on_status(True)
        try:
            for m in _parse_frame(msg.content):
                self.callback(m)
        except Exception as e:
            logger.debug(f"帧解析失败: {e}")

    def websocket_end(self, flow: http.HTTPFlow):
        if not any(k in (flow.request.host or "") for k in HOST_FILTER):
            return
        logger.info("WSS 连接断开")
        if self.on_status:
            self.on_status(False)


# ─────────────────────────────────────────────
# 对外接口
# ─────────────────────────────────────────────
async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
    port: int = PROXY_PORT,
):
    """
    以普通 HTTP 代理模式启动 mitmproxy，等待直播伴侣的 WSS 弹幕连接。
    需提前执行 patch_companion() 并重启直播伴侣。
    """
    if not is_patched():
        logger.error("直播伴侣未 patch，请先执行 Patch 操作")
        if on_status:
            on_status(False)
        return

    connected = asyncio.Event()
    opts = options.Options(listen_host="0.0.0.0", listen_port=port)
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    _install_cert()
    master.addons.add(_DouyinWsAddon(callback, on_status, connected))

    logger.info(f"代理已启动，监听 0.0.0.0:{port}（{_TIMEOUT:.0f}s 超时）")

    master_task = asyncio.create_task(master.run())

    async def _stop():
        master.shutdown()
        master_task.cancel()
        try:
            await master_task
        except (asyncio.CancelledError, Exception):
            pass

    try:
        await asyncio.wait_for(connected.wait(), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"{_TIMEOUT:.0f}s 内未检测到 WSS，请确认直播伴侣已重启并开播")
        await _stop()
        if on_status:
            on_status(False)
        return
    except asyncio.CancelledError:
        await _stop()
        raise

    try:
        await master_task
    except asyncio.CancelledError:
        await _stop()
        raise
    finally:
        if on_status:
            on_status(False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")

    def _cb(msg): print(f"[消息] {msg}")
    def _st(c):   print(f"[状态] {'已连接' if c else '已断开'}")

    asyncio.run(start_listener(_cb, on_status=_st))
