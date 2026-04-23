from playwright.sync_api import sync_playwright
import winreg
import time

ATTENTION_JS = r"""
(() => {
    if (window.__DY_NOTICE__) return;
    window.__DY_NOTICE__ = true;

    function createNotice() {
        const box = document.createElement("div");

        box.style.position = "fixed";
        box.style.top = "0";
        box.style.left = "50%";
        box.style.transform = "translateX(-50%)"; 

        box.style.zIndex = "9999999";
        box.style.background = "#ffffff";
        box.style.color = "#000000";

        box.style.padding = "12px 20px";
        box.style.borderRadius = "0 0 12px 12px";
        box.style.fontSize = "15px";
        box.style.fontWeight = "600";
        box.style.fontFamily = "sans-serif";

        box.style.boxShadow = "0 4px 12px rgba(0,0,0,0.2)";
        box.style.lineHeight = "1.6";

        box.style.textAlign = "center";

        box.innerText = "请登录抖音\n登录完成后直接关闭浏览器即可";

        document.body.appendChild(box);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", createNotice);
    } else {
        createNotice();
    }
})();
"""

def save_state(context):
    try:
        context.storage_state(path="state.json")
        print("state 保存成功")
        return True
    except Exception as e:
        print("state 保存失败:", str(e))
        return False

def get_chrome_path():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
        )
        path, _ = winreg.QueryValueEx(key, None)
        return path
    except:
        return None

def login_and_save():
    with sync_playwright() as p:
        path = get_chrome_path()
        browser = p.chromium.launch(path,headless=False) 
        context = browser.new_context()

        page = context.new_page()
        page.add_init_script(ATTENTION_JS)
        page.goto("https://douyin.com/") 
        
        input("👉 请完成登录后按回车继续...")
        context.storage_state(path="state.json")

        page.wait_for_event("close", timeout=0)

login_and_save()  
