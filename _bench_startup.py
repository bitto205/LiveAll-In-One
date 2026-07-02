import time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browsers")

marks = []

def mark(s):
    marks.append((time.perf_counter(), s))

mark("start")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
mark("qt")
from main_page import MainPage
mark("main_page import")
app = QApplication([])
mark("QApplication")
win = MainPage()
mark("MainPage()")
base = marks[0][0]
for t, s in marks:
    print(f"{(t - base) * 1000:7.0f}ms  {s}")
