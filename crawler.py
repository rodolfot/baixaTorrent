import asyncio
import json
import queue as stdlib_queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import Download, Page, async_playwright

OUTPUT_DIR = Path("evidencias")
TORRENTS_DIR = OUTPUT_DIR / "torrents"
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"
STATES_DIR = OUTPUT_DIR / "estados"

WORKERS = 50
SAVE_INTERVAL = 30          # salva estado a cada N páginas
MAX_PAGES = 5000
MAX_HOPS = 5

SKIP_PATTERN = re.compile(
    r"\b(skip|pular|continuar|continue|fechar|close|ir para|go to|proceed|download|baixar)\b",
    re.IGNORECASE,
)
AD_DOMAINS = {
    "doubleclick.net", "googlesyndication.com", "adnxs.com",
    "advertising.com", "popads.net", "popcash.net", "adcash.com",
    "propellerads.com", "trafficjunky.com",
}
IGNORE_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".css", ".js",
    ".svg", ".ico", ".woff", ".woff2", ".mp4", ".avi",
    ".mkv", ".zip", ".rar", ".exe", ".pdf",
}
TORRENT_PAGE_HINT = re.compile(
    r"torrent|magnet|download|baixar|detail|info|item|ficha", re.IGNORECASE
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
}


# ─────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────

def setup_dirs():
    for d in (TORRENTS_DIR, SCREENSHOTS_DIR, STATES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]', "_", name)[:120]


def normalize(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def domain_of(url: str) -> str:
    return urlparse(url).netloc


def same_domain(url: str, domain: str) -> bool:
    return urlparse(url).netloc == domain


def ignorable(url: str) -> bool:
    return Path(urlparse(url).path).suffix.lower() in IGNORE_EXT


def is_ad(url: str) -> bool:
    return any(ad in url for ad in AD_DOMAINS)


def parse_links(html: str, base: str) -> tuple[list[str], list[str], list[str]]:
    """Retorna (torrent_urls, magnets, page_links)."""
    soup = BeautifulSoup(html, "html.parser")
    torrents, magnets, pages = [], [], []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        raw = tag["href"].strip()
        if not raw or raw.startswith("javascript:") or raw == "#":
            continue
        if raw.startswith("magnet:"):
            if raw not in seen:
                magnets.append(raw)
                seen.add(raw)
            continue
        full = normalize(urljoin(base, raw))
        if full in seen or not full.startswith("http"):
            continue
        seen.add(full)
        if full.endswith(".torrent"):
            torrents.append(full)
        elif not ignorable(full) and not is_ad(full):
            pages.append(full)
    return torrents, magnets, pages


# ─────────────────────────────────────────────
# Estado persistido
# ─────────────────────────────────────────────

class State:
    """Estado thread-safe com persistência em disco."""

    def __init__(self, start_url: str, session_id: str):
        self.start_url = start_url
        self.domain = domain_of(start_url)
        self.session_id = session_id
        self.visited: set[str] = set()
        self.site_map: dict[str, dict] = {}   # url -> {torrents, magnets, links}
        self.downloaded: list[str] = []
        self.magnets_seen: set[str] = set()
        self._lock = threading.Lock()
        safe = self.domain.replace(".", "_").replace(":", "_")
        self._file = STATES_DIR / f"{safe}.json"

    @property
    def state_file(self) -> Path:
        return self._file

    # ── mutação thread-safe ──

    def mark_visited(self, url: str):
        with self._lock:
            self.visited.add(url)

    def is_visited(self, url: str) -> bool:
        with self._lock:
            return url in self.visited

    def add_page(self, url: str, torrents: list, magnets: list, links: list):
        with self._lock:
            self.site_map[url] = {
                "torrents": torrents,
                "magnets": magnets,
                "links": links,
            }

    def add_downloaded(self, path: str):
        with self._lock:
            if path not in self.downloaded:
                self.downloaded.append(path)

    def add_magnet(self, link: str) -> bool:
        """Retorna True se for novo."""
        with self._lock:
            if link in self.magnets_seen:
                return False
            self.magnets_seen.add(link)
            return True

    def pending_urls(self) -> list[str]:
        """Reconstrói fila a partir do mapa salvo."""
        with self._lock:
            pending: set[str] = set()
            for data in self.site_map.values():
                for link in data.get("links", []):
                    if link not in self.visited:
                        pending.add(link)
            if not pending:
                pending.add(normalize(self.start_url))
            return list(pending)

    def stats(self) -> tuple[int, int, int]:
        with self._lock:
            t = sum(len(v["torrents"]) for v in self.site_map.values())
            m = len(self.magnets_seen)
            return len(self.visited), t, m

    # ── persistência ──

    def save(self):
        with self._lock:
            data = {
                "start_url": self.start_url,
                "session_id": self.session_id,
                "visited": list(self.visited),
                "site_map": self.site_map,
                "downloaded": self.downloaded,
                "magnets_seen": list(self.magnets_seen),
            }
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._file)   # atômico

    def delete(self):
        if self._file.exists():
            self._file.unlink()

    @classmethod
    def load(cls, start_url: str) -> "State | None":
        domain = domain_of(start_url)
        safe = domain.replace(".", "_").replace(":", "_")
        path = STATES_DIR / f"{safe}.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            s = cls(start_url, raw["session_id"])
            s.visited = set(raw.get("visited", []))
            s.site_map = raw.get("site_map", {})
            s.downloaded = raw.get("downloaded", [])
            s.magnets_seen = set(raw.get("magnets_seen", []))
            return s
        except Exception:
            return None

    @staticmethod
    def exists(start_url: str) -> bool:
        domain = domain_of(start_url)
        safe = domain.replace(".", "_").replace(":", "_")
        return (STATES_DIR / f"{safe}.json").exists()


# ─────────────────────────────────────────────
# FASE 1 — Mapeamento paralelo com requests
# ─────────────────────────────────────────────

def _fetch_worker(
    work_q: stdlib_queue.Queue,
    state: State,
    msg_q: stdlib_queue.Queue,
    stop: threading.Event,
    counter: list,          # [int] compartilhado
    counter_lock: threading.Lock,
):
    session = requests.Session()
    session.headers.update(HEADERS)

    while not stop.is_set():
        try:
            url = work_q.get(timeout=2)
        except stdlib_queue.Empty:
            break

        try:
            if state.is_visited(url) or ignorable(url):
                continue
            if not same_domain(url, state.domain):
                continue

            state.mark_visited(url)

            with counter_lock:
                counter[0] += 1
                n = counter[0]

            msg_q.put(("log", f"[{n:04d}] {url}"))

            try:
                resp = session.get(url, timeout=15, allow_redirects=True)
                ct = resp.headers.get("Content-Type", "")
                if "text/html" not in ct:
                    continue

                torrents, magnets, pages = parse_links(resp.text, url)
                state.add_page(url, torrents, magnets, pages)

                if torrents or magnets:
                    msg_q.put(("log", f"  ✔ {len(torrents)} torrent(s) | {len(magnets)} magnet(s)"))

                for link in pages:
                    if not state.is_visited(link):
                        work_q.put(link)

            except Exception as e:
                msg_q.put(("log", f"  ✗ {e}"))

            # Salva e reporta progresso
            with counter_lock:
                if counter[0] % SAVE_INTERVAL == 0:
                    state.save()
                    v, t, m = state.stats()
                    msg_q.put(("log", f"  💾 Estado salvo — {v} páginas | {t} torrents | {m} magnets"))

            v, t, m = state.stats()
            msg_q.put(("map_progress", v, work_q.qsize(), t, m))

        finally:
            work_q.task_done()


def map_site(state: State, msg_q: stdlib_queue.Queue, stop: threading.Event, resume: bool):
    work_q: stdlib_queue.Queue = stdlib_queue.Queue()

    if resume:
        pending = state.pending_urls()
        msg_q.put(("log", f"  Retomando — {len(state.visited)} já visitadas, {len(pending)} na fila"))
        for u in pending:
            work_q.put(u)
    else:
        work_q.put(normalize(state.start_url))

    counter = [len(state.visited)]
    counter_lock = threading.Lock()

    msg_q.put(("phase", "map"))
    msg_q.put(("log", "━━━ FASE 1: Mapeando o site ━━━"))
    msg_q.put(("log", f"Domínio: {state.domain} | Workers: {WORKERS}"))

    threads = []
    for _ in range(WORKERS):
        t = threading.Thread(
            target=_fetch_worker,
            args=(work_q, state, msg_q, stop, counter, counter_lock),
            daemon=True,
        )
        t.start()
        threads.append(t)

    work_q.join()           # aguarda fila zerar
    stop_workers = threading.Event()
    stop_workers.set()      # garante que threads paradas saem

    for t in threads:
        t.join(timeout=5)

    state.save()
    v, t_count, m_count = state.stats()
    msg_q.put(("log", f"\n━━━ Mapeamento concluído ━━━"))
    msg_q.put(("log", f"  Páginas visitadas : {v}"))
    msg_q.put(("log", f"  Torrents encontrados: {t_count}"))
    msg_q.put(("log", f"  Magnets encontrados : {m_count}"))
    msg_q.put(("map_done", v, t_count, m_count))


# ─────────────────────────────────────────────
# FASE 2 — Download paralelo
# ─────────────────────────────────────────────

def download_direct(url: str, state: State, msg_q: stdlib_queue.Queue, index: int) -> str | None:
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS, stream=True)
        resp.raise_for_status()
        fname = sanitize(Path(urlparse(url).path).name or f"torrent_{state.session_id}_{index}.torrent")
        if not fname.endswith(".torrent"):
            fname += ".torrent"
        dest = TORRENTS_DIR / fname
        if dest.exists():
            return str(dest)    # já baixado
        dest.write_bytes(resp.content)
        state.add_downloaded(str(dest))
        state.save()
        msg_q.put(("download", fname, str(dest), url))
        return str(dest)
    except Exception as e:
        msg_q.put(("log", f"  ✗ requests falhou {url}: {e}"))
        return None


def _download_worker(
    urls: list[tuple[str, str]],   # [(torrent_url, page_url)]
    state: State,
    msg_q: stdlib_queue.Queue,
    stop: threading.Event,
    base_index: int,
):
    for i, (turl, _) in enumerate(urls):
        if stop.is_set():
            break
        if turl in state.downloaded:
            continue
        msg_q.put(("log", f"  ↓ {turl}"))
        download_direct(turl, state, msg_q, base_index + i)


async def playwright_chain(page: Page, context, url: str, state: State, msg_q: stdlib_queue.Queue, hop: int = 0):
    if hop >= MAX_HOPS:
        return

    async def on_dl(dl: Download):
        fname = sanitize(dl.suggested_filename or f"torrent_{state.session_id}_{hop}.torrent")
        if not fname.endswith(".torrent"):
            fname += ".torrent"
        dest = TORRENTS_DIR / fname
        await dl.save_as(str(dest))
        state.add_downloaded(str(dest))
        state.save()
        msg_q.put(("download", fname, str(dest), dl.url))

    page.on("download", on_dl)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
    except Exception:
        pass
    finally:
        page.remove_listener("download", on_dl)

    # Verifica links diretos após renderização
    try:
        hrefs: list[str] = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        for href in hrefs:
            href = href.strip()
            if href.endswith(".torrent"):
                download_direct(href, state, msg_q, hop)
            elif href.startswith("magnet:"):
                if state.add_magnet(href):
                    msg_q.put(("magnet", href))
    except Exception:
        pass

    # Tenta pular anúncio/contador
    try:
        for _ in range(20):
            text = await page.inner_text("body")
            nums = [int(n) for n in re.findall(r"\b([1-9][0-9]?)\b", text) if 1 <= int(n) <= 30]
            if nums:
                await asyncio.sleep(1)
            else:
                break
        soup = BeautifulSoup(await page.content(), "html.parser")
        for tag in soup.find_all(["a", "button"]):
            text = tag.get_text(strip=True) or tag.get("title", "")
            if SKIP_PATTERN.search(text):
                locator = page.get_by_text(re.compile(re.escape(text[:30]), re.IGNORECASE))
                await locator.first.click(timeout=4000)
                await page.wait_for_load_state("domcontentloaded", timeout=8000)
                await asyncio.sleep(1)
                break
    except Exception:
        pass

    # Fecha abas de anúncio, segue abas legítimas
    for tab in list(context.pages[1:]):
        tab_url = tab.url
        if tab_url.endswith(".torrent"):
            download_direct(tab_url, state, msg_q, hop)
        elif same_domain(tab_url, state.domain):
            await playwright_chain(tab, context, tab_url, state, msg_q, hop + 1)
        else:
            try:
                await tab.close()
            except Exception:
                pass


async def playwright_phase(candidates: list[str], state: State, msg_q: stdlib_queue.Queue, stop: threading.Event):
    if not candidates:
        return

    msg_q.put(("log", f"\n  Investigando {len(candidates)} página(s) com Playwright ({WORKERS} tabs)..."))

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            accept_downloads=True,
        )
        await context.route("**/*", lambda r: (
            r.abort() if is_ad(r.request.url) else r.continue_()
        ))

        sem = asyncio.Semaphore(WORKERS)

        async def process(url: str):
            if stop.is_set():
                return
            async with sem:
                msg_q.put(("log", f"  🔍 {url}"))
                tab = await context.new_page()
                try:
                    await playwright_chain(tab, context, url, state, msg_q)
                    ss = SCREENSHOTS_DIR / f"{state.session_id}_{sanitize(url)[:40]}.png"
                    await tab.screenshot(path=str(ss), full_page=True)
                except Exception as e:
                    msg_q.put(("log", f"    ✗ {e}"))
                finally:
                    try:
                        await tab.close()
                    except Exception:
                        pass

        await asyncio.gather(*[process(u) for u in candidates])
        await browser.close()


async def _download_phase(state: State, msg_q: stdlib_queue.Queue, stop: threading.Event):
    msg_q.put(("phase", "download"))
    msg_q.put(("log", "\n━━━ FASE 2: Baixando arquivos ━━━"))

    # Coleta todos os magnets do mapa
    for data in state.site_map.values():
        for m in data.get("magnets", []):
            if state.add_magnet(m):
                msg_q.put(("magnet", m))

    # Todos os torrents diretos do mapa
    all_torrents: list[tuple[str, str]] = []
    for page_url, data in state.site_map.items():
        for t in data.get("torrents", []):
            all_torrents.append((t, page_url))

    msg_q.put(("log", f"  {len(all_torrents)} torrent(s) direto(s) | {len(state.magnets_seen)} magnet(s)"))

    # Divide entre workers e baixa em paralelo via threads
    if all_torrents:
        chunk = max(1, len(all_torrents) // WORKERS + 1)
        chunks = [all_torrents[i:i+chunk] for i in range(0, len(all_torrents), chunk)]
        threads = []
        for idx, ch in enumerate(chunks):
            t = threading.Thread(
                target=_download_worker,
                args=(ch, state, msg_q, stop, idx * chunk),
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    # Páginas candidatas para investigação com Playwright
    candidates = [
        u for u, data in state.site_map.items()
        if not data.get("torrents") and not data.get("magnets")
        and TORRENT_PAGE_HINT.search(u)
        and u not in state.downloaded
    ]
    await playwright_phase(candidates[:200], state, msg_q, stop)

    state.save()


# ─────────────────────────────────────────────
# Entrada pública
# ─────────────────────────────────────────────

async def _run(start_url: str, msg_q: stdlib_queue.Queue, stop: threading.Event, resume: bool):
    setup_dirs()

    if resume:
        state = State.load(start_url) or State(start_url, datetime.now().strftime("%Y%m%d_%H%M%S"))
        msg_q.put(("log", "▶ Retomando sessão anterior..."))
    else:
        state = State(start_url, datetime.now().strftime("%Y%m%d_%H%M%S"))
        state.delete()

    msg_q.put(("session", state.session_id))

    # Fase 1 em executor para não bloquear o event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, map_site, state, msg_q, stop, resume)

    if stop.is_set():
        msg_q.put(("log", "⏹ Parado pelo usuário. Estado salvo — retome quando quiser."))
        msg_q.put(("done", *state.stats(), ""))
        return

    # Fase 2
    await _download_phase(state, msg_q, stop)

    v, t, m = state.stats()
    report = OUTPUT_DIR / f"relatorio_{state.session_id}.json"
    report.write_text(json.dumps({
        "session": state.session_id,
        "start_url": start_url,
        "pages": v, "torrents": t, "magnets": m,
        "site_map": state.site_map,
        "downloaded": state.downloaded,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    state.delete()   # limpa estado ao concluir com sucesso
    msg_q.put(("done", v, t, m, str(report)))


def start_crawl(url: str, msg_q: stdlib_queue.Queue, stop: threading.Event, resume: bool = False):
    asyncio.run(_run(url, msg_q, stop, resume))


def has_saved_state(url: str) -> bool:
    return State.exists(url)
