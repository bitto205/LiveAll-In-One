import time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browsers")

from PySide6.QtWidgets import QApplication
app = QApplication([])

def bench(name, fn):
    t0 = time.perf_counter()
    fn()
    print(f"{(time.perf_counter()-t0)*1000:7.0f}ms  {name}")

bench("import tools", lambda: __import__("tools"))
bench("HomePage", lambda: __import__("pages.home_page").home_page.HomePage())
bench("ToolsPage", lambda: __import__("pages.tools_page").tools_page.ToolsPage())
bench("SettingsPage", lambda: __import__("pages.settings_page").settings_page.SettingsPage())
