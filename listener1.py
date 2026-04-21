from playwright.sync_api import sync_playwright
import json

LIVE_URL = "https://live.douyin.com/69164788651"


HOOK_JS = r"""
(() => {
    if (window.__DY_HOOK__) return;
    window.__DY_HOOK__ = true;

    console.log("HOOK_READY");

    const oldPush = Array.prototype.push;

    Array.prototype.push = function (...args) {

        for (const msg of args) {
            if (!msg || typeof msg !== "object") continue;

            const method = msg.method;
            if (!method) continue;

            const payload = msg.payload || {};

            const user = payload.user?.desensitized_nickname;

            let data = null;

            if (method === "WebcastChatMessage") {
                const content = payload.content;

                if (user && content) {
                    data = {
                        type: "chat",
                        user,
                        content
                    };
                }
            }

            else if (method === "WebcastGiftMessage") {
                const gift = payload.gift?.name;
                const count = payload.total_count;

                if (user && gift) {
                    data = {
                        type: "gift",
                        user,
                        gift,
                        count
                    };
                }
            }

            else if (method === "WebcastMemberMessage") {
                if (user) {
                    data = {
                        type: "enter",
                        user
                    };
                }
            }

            if (data) {
                console.log("DY_MSG:" + JSON.stringify(data));
            }
        }

        return oldPush.apply(this, args);
    };
})();
"""


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context()

        page = context.new_page()

        page.add_init_script(HOOK_JS)

        def handle_console(msg):
            text = msg.text

            if text.startswith("DY_MSG:"):
                try:
                    data = json.loads(text[7:])
                    print(data)
                except:
                    pass

        page.on("console", handle_console)

        page.goto(LIVE_URL)

        print("已进入直播间（游客模式），等待数据流...")

        page.wait_for_timeout(999999)

if __name__ == "__main__":
    run()