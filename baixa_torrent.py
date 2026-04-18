import queue
import sys
import threading

from crawler import start_crawl


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL do site: ").strip()
    if not url.startswith("http"):
        url = "https://" + url

    q: queue.Queue = queue.Queue()
    stop = threading.Event()

    def printer():
        while True:
            msg = q.get()
            kind = msg[0]
            if kind == "log":
                print(msg[1])
            elif kind == "download":
                print(f"[BAIXADO] {msg[1]} → {msg[2]}")
            elif kind == "magnet":
                print(f"[MAGNET] {msg[1][:80]}...")
            elif kind == "done":
                print(f"\n✔ Concluído. Páginas={msg[1]} Torrents={msg[2]} Magnets={msg[3]}")
                break

    t = threading.Thread(target=printer, daemon=True)
    t.start()
    start_crawl(url, q, stop)
    t.join()


if __name__ == "__main__":
    main()
