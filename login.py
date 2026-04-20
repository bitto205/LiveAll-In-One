from playwright.sync_api import sync_playwright
import winreg

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
        browser = p.chromium.launch(executable_path= path ,headless=False)  # 一定要False
        context = browser.new_context()

        page = context.new_page()
        page.goto("https://anchor.douyin.com/")  # 你的目标页面

        page.wait_for_url("https://anchor.douyin.com/anchor/dashboard/home", timeout=120000)
        
        context.storage_state(path="state.json")

        browser.close()    