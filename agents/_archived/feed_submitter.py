"""Feed Directories Auto-Submit — pings WebSub hubs and logs directories for manual claim."""
import httpx

FEED_URL = "https://gworky.com/rss.xml"
HUBS = ["https://pubsubhubbub.appspot.com/", "https://pubsubhubbub.superfeedr.com/"]

def ping_hubs():
    for hub in HUBS:
        try:
            r = httpx.post(hub, data={"hub.mode":"publish","hub.url":FEED_URL}, timeout=10)
            print(f"hub {hub} → {r.status_code} {r.text[:120]}")
        except Exception as e:
            print(f"hub {hub} error {e}")

if __name__ == "__main__":
    ping_hubs()
    print("Manual directories: Feedspot, Blogarama, Alltop, Flipboard — submit via browser form (see docs/FEED-DIRECTORIES-SUBMISSION.md)")
