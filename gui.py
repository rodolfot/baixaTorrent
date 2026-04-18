import os
import queue
import threading
import tkinter.messagebox as msgbox
from pathlib import Path

import customtkinter as ctk

from crawler import has_saved_state, start_crawl

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

GREEN  = "#2ecc71"
RED    = "#e74c3c"
YELLOW = "#f1c40f"
BLUE   = "#3498db"
GRAY   = "#95a5a6"
BG_CARD = "#1e2a38"
BG_MAIN = "#141d27"


class DownloadCard(ctk.CTkFrame):
    def __init__(self, master, filename: str, filepath: str, source_url: str, **kw):
        super().__init__(master, fg_color=BG_CARD, corner_radius=8, **kw)
        self.filepath = filepath

        ctk.CTkLabel(self, text="📥", font=ctk.CTkFont(size=20), width=36).grid(
            row=0, column=0, rowspan=2, padx=(10, 6), pady=8, sticky="ns"
        )
        ctk.CTkLabel(
            self, text=filename, font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w", text_color="white",
        ).grid(row=0, column=1, padx=0, pady=(8, 0), sticky="ew")
        ctk.CTkLabel(
            self,
            text=(source_url[:80] + "..." if len(source_url) > 80 else source_url),
            font=ctk.CTkFont(size=10), anchor="w", text_color=GRAY,
        ).grid(row=1, column=1, padx=0, pady=(0, 8), sticky="ew")
        ctk.CTkButton(
            self, text="Abrir", width=60, height=28,
            fg_color="#2980b9", hover_color="#3498db",
            command=self._open,
        ).grid(row=0, column=2, rowspan=2, padx=10, pady=8)
        self.columnconfigure(1, weight=1)

    def _open(self):
        p = Path(self.filepath)
        if p.parent.exists():
            os.startfile(str(p.parent))


class MagnetCard(ctk.CTkFrame):
    def __init__(self, master, magnet: str, **kw):
        super().__init__(master, fg_color=BG_CARD, corner_radius=8, **kw)

        ctk.CTkLabel(self, text="🧲", font=ctk.CTkFont(size=18), width=36).grid(
            row=0, column=0, padx=(10, 6), pady=8
        )
        ctk.CTkLabel(
            self,
            text=(magnet[:90] + "..." if len(magnet) > 90 else magnet),
            font=ctk.CTkFont(size=10), anchor="w", text_color=GRAY,
        ).grid(row=0, column=1, padx=0, pady=8, sticky="ew")
        ctk.CTkButton(
            self, text="Copiar", width=60, height=28,
            fg_color="#8e44ad", hover_color="#9b59b6",
            command=lambda: (self.clipboard_clear(), self.clipboard_append(magnet)),
        ).grid(row=0, column=2, padx=10, pady=8)
        self.columnconfigure(1, weight=1)


class PhaseBadge(ctk.CTkLabel):
    PHASES = {
        "idle":     ("Aguardando",       GRAY),
        "map":      ("Fase 1 — Mapeando", YELLOW),
        "download": ("Fase 2 — Baixando", BLUE),
        "done":     ("Concluído",         GREEN),
        "stopped":  ("Parado",            RED),
    }

    def __init__(self, master, **kw):
        super().__init__(master, text="● Aguardando", font=ctk.CTkFont(size=12), text_color=GRAY, **kw)

    def set(self, phase: str):
        label, color = self.PHASES.get(phase, ("", GRAY))
        self.configure(text=f"● {label}", text_color=color)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("BaixaTorrent")
        self.geometry("1080x740")
        self.minsize(860, 600)
        self.configure(fg_color=BG_MAIN)

        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = ""
        self._n_pages = self._n_dl = self._n_mag = 0
        self._empty_lbl: ctk.CTkLabel | None = None

        self._build_ui()
        self._poll()

    # ── construção da interface ──────────────────────

    def _build_ui(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=64)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="⬇  BaixaTorrent",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left", padx=20)
        self._badge = PhaseBadge(hdr)
        self._badge.pack(side="right", padx=20)

        # URL bar
        ubar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=58)
        ubar.pack(fill="x", pady=(1, 0))
        ubar.pack_propagate(False)
        ctk.CTkLabel(ubar, text="URL:", font=ctk.CTkFont(size=13),
                     text_color=GRAY).pack(side="left", padx=(16, 6))
        self._url = ctk.CTkEntry(ubar, placeholder_text="https://site-de-torrents.com",
                                 font=ctk.CTkFont(size=13), height=36, corner_radius=8)
        self._url.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=10)
        self._url.bind("<Return>", lambda _: self._toggle())
        self._btn = ctk.CTkButton(
            ubar, text="Iniciar", width=110, height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=GREEN, hover_color="#27ae60", text_color="black",
            command=self._toggle,
        )
        self._btn.pack(side="left", padx=(0, 8), pady=10)
        self._reset_btn = ctk.CTkButton(
            ubar, text="Resetar", width=80, height=36,
            font=ctk.CTkFont(size=12),
            fg_color="#2c3e50", hover_color="#34495e",
            command=self._reset_state,
        )
        self._reset_btn.pack(side="left", padx=(0, 16), pady=10)

        # Stats bar
        sbar = ctk.CTkFrame(self, fg_color="#182330", corner_radius=0, height=38)
        sbar.pack(fill="x")
        sbar.pack_propagate(False)
        self._sl_pages = self._stat(sbar, "Páginas: 0")
        self._sl_dl     = self._stat(sbar, "Torrents: 0", GREEN)
        self._sl_mag    = self._stat(sbar, "Magnets: 0", "#9b59b6")
        self._sl_queue  = self._stat(sbar, "Na fila: 0", YELLOW)
        self._sl_workers = self._stat(sbar, "Workers: 50", BLUE)

        # Content
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=12, pady=10)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        # Log
        lf = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=10)
        lf.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        ctk.CTkLabel(lf, text="Atividade", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=GRAY).pack(anchor="w", padx=12, pady=(10, 4))
        self._log = ctk.CTkTextbox(
            lf, fg_color="#0f1923", text_color="#aed6f1",
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word", state="disabled", corner_radius=8,
        )
        self._log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Downloads
        df = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=10)
        df.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        hr = ctk.CTkFrame(df, fg_color="transparent")
        hr.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(hr, text="Arquivos Encontrados",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=GRAY).pack(side="left")
        ctk.CTkButton(
            hr, text="📂 Abrir pasta", width=110, height=26,
            fg_color="#2c3e50", hover_color="#34495e", font=ctk.CTkFont(size=11),
            command=self._open_dir,
        ).pack(side="right")
        self._dl_scroll = ctk.CTkScrollableFrame(df, fg_color="transparent", corner_radius=8)
        self._dl_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._show_empty()

    def _stat(self, parent, text, color="white"):
        lbl = ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=12), text_color=color)
        lbl.pack(side="left", padx=14, pady=8)
        return lbl

    # ── ações do usuário ──────────────────────────────

    def _toggle(self):
        if self._thread and self._thread.is_alive():
            self._stop_crawl()
        else:
            self._start_crawl()

    def _start_crawl(self):
        url = self._url.get().strip()
        if not url:
            self._url.configure(border_color=RED)
            return
        if not url.startswith("http"):
            url = "https://" + url
        self._url.configure(border_color=GREEN)

        resume = False
        if has_saved_state(url):
            resume = msgbox.askyesno(
                "Sessão anterior encontrada",
                "Existe uma sessão salva para este site.\n\nDeseja retomar de onde parou?",
                icon="question",
            )

        if not resume:
            self._clear_ui()

        self._stop.clear()
        self._thread = threading.Thread(
            target=start_crawl,
            args=(url, self._q, self._stop, resume),
            daemon=True,
        )
        self._thread.start()
        self._btn.configure(text="Parar", fg_color=RED, hover_color="#c0392b", text_color="white")

    def _stop_crawl(self):
        self._stop.set()
        self._btn.configure(text="Parando...", state="disabled", fg_color=GRAY)
        self._badge.set("stopped")

    def _reset_state(self):
        url = self._url.get().strip()
        if not url:
            return
        if not url.startswith("http"):
            url = "https://" + url
        from crawler import State
        s = State(url, "")
        if s.state_file.exists():
            if msgbox.askyesno("Confirmar", "Apagar estado salvo para este site?"):
                s.delete()
                self._log_append("🗑 Estado apagado.")
        else:
            self._log_append("Nenhum estado salvo encontrado.")

    # ── polling da fila ───────────────────────────────

    def _poll(self):
        try:
            while True:
                self._handle(self._q.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _handle(self, msg):
        kind = msg[0]

        if kind == "log":
            self._log_append(msg[1])

        elif kind == "session":
            self._session = msg[1]

        elif kind == "phase":
            self._badge.set(msg[1])

        elif kind == "map_progress":
            _, visited, qsize, t_found, m_found = msg
            self._n_pages = visited
            self._update_stats()
            self._sl_queue.configure(text=f"Na fila: {qsize}")

        elif kind == "map_done":
            _, pages, t, m = msg
            self._n_pages = pages
            self._update_stats()
            self._sl_queue.configure(text="Na fila: 0")

        elif kind == "queue_size":
            self._sl_queue.configure(text=f"Na fila: {msg[1]}")

        elif kind == "download":
            _, fname, fpath, src = msg
            self._n_dl += 1
            self._update_stats()
            self._remove_empty()
            DownloadCard(self._dl_scroll, fname, fpath, src).pack(fill="x", pady=3)
            self._log_append(f"✅ {fname}")

        elif kind == "magnet":
            magnet = msg[1]
            self._n_mag += 1
            self._update_stats()
            self._remove_empty()
            MagnetCard(self._dl_scroll, magnet).pack(fill="x", pady=3)

        elif kind == "done":
            _, pages, torrents, magnets, report = msg
            self._badge.set("done")
            self._btn.configure(
                text="Iniciar", fg_color=GREEN, hover_color="#27ae60",
                text_color="black", state="normal",
            )
            self._log_append(f"\n{'─'*42}")
            self._log_append(f"✔ Concluído!")
            self._log_append(f"  Páginas mapeadas : {pages}")
            self._log_append(f"  Torrents baixados: {torrents}")
            self._log_append(f"  Magnets coletados: {magnets}")
            if report:
                self._log_append(f"  Relatório        : {report}")

    # ── helpers de UI ─────────────────────────────────

    def _log_append(self, text: str):
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _update_stats(self):
        self._sl_pages.configure(text=f"Páginas: {self._n_pages}")
        self._sl_dl.configure(text=f"Torrents: {self._n_dl}")
        self._sl_mag.configure(text=f"Magnets: {self._n_mag}")

    def _show_empty(self):
        self._empty_lbl = ctk.CTkLabel(
            self._dl_scroll,
            text="Nenhum arquivo encontrado ainda.\nInicie o rastreamento para começar.",
            font=ctk.CTkFont(size=12), text_color=GRAY,
        )
        self._empty_lbl.pack(pady=40)

    def _remove_empty(self):
        if self._empty_lbl and self._empty_lbl.winfo_exists():
            self._empty_lbl.destroy()
            self._empty_lbl = None

    def _clear_ui(self):
        self._n_pages = self._n_dl = self._n_mag = 0
        self._update_stats()
        self._sl_queue.configure(text="Na fila: 0")
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        for w in self._dl_scroll.winfo_children():
            w.destroy()
        self._show_empty()

    def _open_dir(self):
        p = Path("evidencias")
        if p.exists():
            os.startfile(str(p.resolve()))


if __name__ == "__main__":
    app = App()
    app.mainloop()
