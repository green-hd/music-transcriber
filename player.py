"""Player de desktop opcional. Execute: python player.py"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

import transcriber as core

BG = "#121412"
PANEL = "#1c201b"
SIDEBAR = "#171a16"
CONTROL = "#2b3229"
BORDER = "#383f34"
GREEN = "#c3f275"
TEXT = "#f2f4ee"
MUTED = "#afb8a7"
SELECTED = "#34442c"


def display_word(word, stretch=False):
    """Alongamento visual aproximado; nunca altera a letra salva."""
    text = word["text"]
    duration = max(0, word["end"] - word["start"])
    if not stretch or duration < max(1.0, len(text) * 0.11):
        return text
    last = next((i for i in range(len(text) - 1, -1, -1) if text[i].isalpha()), None)
    if last is None:
        return text
    count = min(3, max(1, int((duration - len(text) * 0.07) / 0.32)))
    return text[:last + 1] + text[last] * count + text[last + 1:]


@dataclass(frozen=True)
class WordCue:
    start: float
    end: float
    offset: int
    text: str


def word_cues(data, stretch=False):
    cues = []
    offset = 0
    for segment in data["segments"]:
        for index, word in enumerate(segment["words"] or [segment]):
            offset += bool(index)
            text = display_word(word, stretch and bool(segment["words"]))
            cues.append(WordCue(word["start"], word["end"], offset, text))
            offset += len(text)
        offset += 2
    return cues


def lyric_events(data, styled=False, stretch=False):
    """Tempos e caracteres para a janela usar o mesmo ritmo do terminal."""
    events = []
    for segment in data["segments"]:
        for index, word in enumerate(segment["words"] or [segment]):
            if index:
                events.append((word["start"], " ", ""))
            tag = "impact" if word.get("emphasis", False) else ""
            text = display_word(word, stretch and bool(segment["words"]))
            interval = min(0.06, max(0, word["end"] - word["start"]) / max(1, len(text)))
            for offset, char in enumerate(text):
                events.append((word["start"] + offset * interval, char, tag))
        if events:
            events.append((max(events[-1][0], word["end"]), "\n\n", ""))
    # Impede regressão do relógio em timestamps de palavras sobrepostas.
    previous = 0.0
    result = []
    for position, char, tag in events:
        previous = max(previous, position)
        result.append((previous, char, tag) if styled else (previous, char))
    return result


def clock_label(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02}:{seconds:02}"


class Player:
    def __init__(self, root, tk, ttk, filedialog):
        self.root, self.tk, self.ttk, self.filedialog = root, tk, ttk, filedialog
        self.messages = queue.Queue()
        self.process = None
        self.process_lock = threading.Lock()
        self.closed = threading.Event()
        self.pygame = None
        self.audio = None
        self.busy = False
        self.playing = False
        self.paused = False
        self.events = []
        self.event_times = []
        self.event_index = 0
        self.lyric_data = None
        self.cues = []
        self.cue_times = []
        self.rendered_chars = 0
        self.duration = 0.0
        self.position = 0.0
        self.files = []
        root.title("green · music & lyrics")
        root.geometry("1020x730")
        root.minsize(820, 600)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self.refresh_library()
        root.after(40, self.tick)

    def label(self, parent, text, size=12, color=TEXT, weight="normal", **kwargs):
        return self.tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                             font=("Segoe UI", size, weight), **kwargs)

    def draw_brand(self, parent):
        mark = self.tk.Canvas(parent, width=48, height=48, bg=SIDEBAR, highlightthickness=0)
        mark.create_oval(2, 2, 46, 46, fill=SELECTED, outline=BORDER)
        mark.create_oval(7, 7, 41, 41, outline=CONTROL, width=2)
        for x, height in ((14, 8), (19, 15), (24, 23), (29, 15), (34, 8)):
            mark.create_line(x, 24 - height / 2, x, 24 + height / 2,
                             fill=GREEN, width=3, capstyle="round")
        mark.pack(side="left")

    def button(self, parent, text, command, primary=False):
        return self.tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                              bg=GREEN if primary else CONTROL, fg=BG if primary else TEXT,
                              activebackground="#d7fa9f" if primary else SELECTED,
                              activeforeground=BG if primary else TEXT,
                              highlightthickness=1, highlightbackground=parent.cget("bg"),
                              highlightcolor=GREEN, disabledforeground="#737d6e",
                              font=("Segoe UI", 10, "bold"), padx=16, pady=9, cursor="hand2")

    def build(self):
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Green.Horizontal.TProgressbar", troughcolor=CONTROL, background=GREEN,
                        borderwidth=0, lightcolor=GREEN, darkcolor=GREEN, thickness=5)
        style.configure("Green.TCombobox", arrowcolor=TEXT, bordercolor=BORDER,
                        lightcolor=CONTROL, darkcolor=CONTROL)
        style.map("Green.TCombobox", foreground=[("readonly", TEXT)],
                  fieldbackground=[("readonly", CONTROL)], background=[("readonly", CONTROL)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", SELECTED)
        self.root.option_add("*TCombobox*Listbox.selectForeground", GREEN)

        sidebar = tk.Frame(self.root, bg=SIDEBAR, width=255, padx=19, pady=24,
                           highlightthickness=1, highlightbackground=BORDER)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=SIDEBAR)
        brand.pack(fill="x", pady=(0, 27))
        self.draw_brand(brand)
        brand_text = tk.Frame(brand, bg=SIDEBAR)
        brand_text.pack(side="left", padx=10)
        self.label(brand_text, "green", 21, weight="bold", anchor="w").pack(fill="x")
        self.label(brand_text, "Música e letra", 9, MUTED, anchor="w").pack(fill="x")
        library_header = tk.Frame(sidebar, bg=SIDEBAR)
        library_header.pack(fill="x", pady=(0, 9))
        self.label(library_header, "Biblioteca", 11, weight="bold").pack(side="left")
        self.library_count = self.label(library_header, "", 9, MUTED)
        self.library_count.pack(side="right")
        self.library = tk.Listbox(sidebar, bg=PANEL, fg=TEXT, selectbackground=SELECTED,
                                  selectforeground=GREEN, highlightthickness=1, highlightbackground=BORDER,
                                  highlightcolor=GREEN, bd=0, font=("Segoe UI", 11),
                                  activestyle="none", exportselection=False)
        self.library.pack(fill="both", expand=True)
        self.library.bind("<<ListboxSelect>>", self.select_library)
        self.library.bind("<Double-Button-1>", lambda event: self.prepare())
        self.open_button = self.button(sidebar, "+ Abrir música", self.open_file)
        self.open_button.pack(fill="x", pady=(14, 0))

        main = tk.Frame(self.root, bg=BG, padx=26, pady=24)
        main.pack(side="left", fill="both", expand=True)
        self.title = self.label(main, "Escolha uma música", 28, weight="bold", anchor="w",
                                wraplength=600, justify="left")
        self.title.pack(fill="x", pady=(0, 3))
        self.status = self.label(main, "Selecione uma faixa na biblioteca ou abra um arquivo.", 10, MUTED,
                                 anchor="w", wraplength=600, justify="left")
        self.status.pack(fill="x", pady=(0, 10))

        def wrap_header(event):
            width = max(100, event.width - 56)
            self.title.configure(wraplength=width)
            self.status.configure(wraplength=width)

        main.bind("<Configure>", wrap_header)

        settings_visible = tk.BooleanVar(master=self.root, value=False)

        def toggle_settings():
            if settings_visible.get():
                settings.pack(fill="x", before=lyric_frame, pady=(8, 0))
            else:
                settings.pack_forget()
            settings_toggle.configure(text="Configurações  −" if settings_visible.get() else "Configurações  +")

        settings_toggle = tk.Checkbutton(main, text="Configurações  +", variable=settings_visible,
                                         command=toggle_settings, indicatoron=False, relief="flat", bd=0,
                                         bg=CONTROL, fg=TEXT, activebackground=SELECTED,
                                         activeforeground=TEXT, selectcolor=SELECTED, padx=10, pady=5,
                                         highlightbackground=BG, highlightcolor=GREEN, cursor="hand2")
        settings_toggle.pack(anchor="w")
        settings = tk.Frame(main, bg=BG)
        options = tk.Frame(settings, bg=BG)
        options.pack(fill="x")
        self.mode = tk.StringVar(value="Equilibrado · small")
        self.language = tk.StringVar(value="auto")
        for title, variable, values, width in (
            ("QUALIDADE", self.mode, ["Leve · base", "Equilibrado · small", "Preciso · medium",
                                      "Inglês · medium.en", "Máxima · large-v3"], 23),
            ("IDIOMA", self.language, ["auto", "en", "pt", "es", "fr", "ja"], 8),
        ):
            group = tk.Frame(options, bg=BG)
            group.pack(side="left", padx=(0, 16))
            self.label(group, title, 9, MUTED, anchor="w").pack(fill="x", pady=(0, 6))
            ttk.Combobox(group, textvariable=variable, values=values, state="readonly",
                         style="Green.TCombobox", width=width).pack()
        self.refazer = tk.BooleanVar(value=False)
        self.offline = tk.BooleanVar(value=False)
        flags = tk.Frame(options, bg=BG)
        flags.pack(side="left")
        for title, variable in (("Refazer", self.refazer), ("Offline", self.offline)):
            tk.Checkbutton(flags, text=title, variable=variable, bg=BG, fg=MUTED,
                           activebackground=BG, activeforeground=GREEN, selectcolor=PANEL).pack(anchor="w")
        self.label(settings, "CONTEXTO  ·  nomes ou expressões da música (opcional)", 9, MUTED,
                   anchor="w").pack(fill="x", pady=(14, 6))
        self.context = tk.Entry(settings, bg=PANEL, fg=TEXT, insertbackground=GREEN, relief="flat",
                                font=("Segoe UI", 11))
        self.context.pack(fill="x", ipady=7)

        lyric_frame = tk.Frame(main, bg=PANEL, padx=23, pady=16,
                               highlightthickness=1, highlightbackground=BORDER)
        lyric_frame.pack(fill="both", expand=True, pady=(14, 12))
        self.label(lyric_frame, "Letra", 10, GREEN, weight="bold", anchor="w").pack(fill="x", pady=(0, 8))
        tk.Frame(lyric_frame, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))
        effects = tk.Frame(settings, bg=BG)
        effects.pack(fill="x", pady=(10, 0))
        self.emphasis = tk.BooleanVar(value=False)
        tk.Checkbutton(effects, text="Ênfase", variable=self.emphasis, command=self.update_lyric_font,
                       bg=BG, fg=MUTED, selectcolor=PANEL, activebackground=BG,
                       activeforeground=GREEN).pack(side="left", padx=(0, 8))
        self.font_choice = tk.StringVar(value="Limpa")
        font_picker = ttk.Combobox(effects, textvariable=self.font_choice, state="readonly",
                                  style="Green.TCombobox", values=["Serifada", "Limpa", "Monoespaçada"], width=14)
        font_picker.pack(side="left", padx=(0, 8))
        font_picker.bind("<<ComboboxSelected>>", self.update_lyric_font)
        self.stretch = tk.BooleanVar(value=False)
        self.show_dot = tk.BooleanVar(value=False)
        for title, variable, callback in (("Alongar palavras", self.stretch, self.rebuild_animation),
                                          ("Bolinha karaokê", self.show_dot, self.update_dot)):
            tk.Checkbutton(effects, text=title, variable=variable, command=callback,
                           bg=BG, fg=MUTED, selectcolor=PANEL, activebackground=BG,
                           activeforeground=GREEN).pack(side="left", padx=(0, 8))
        self.lyrics = tk.Text(lyric_frame, bg=PANEL, fg=TEXT, wrap="word",
                              relief="flat", highlightthickness=0, spacing1=18, spacing3=8, spacing2=18,
                              state="disabled", cursor="arrow", padx=0, height=6)
        self.lyrics.pack(fill="both", expand=True)
        self.dot = tk.Canvas(self.lyrics, width=14, height=14, bg=PANEL, highlightthickness=0)
        self.dot.create_oval(1, 1, 13, 13, fill=SELECTED, outline="")
        self.dot.create_oval(4, 4, 10, 10, fill=GREEN, outline="")
        self.update_lyric_font()
        self.set_lyrics("A letra sincronizada aparece aqui.")
        playback = tk.Frame(main, bg=PANEL, padx=12, pady=10,
                            highlightthickness=1, highlightbackground=BORDER)
        playback.pack(side="bottom", before=lyric_frame, fill="x")
        timeline = tk.Frame(playback, bg=PANEL)
        timeline.pack(fill="x", pady=(0, 7))
        self.time_label = self.label(timeline, "00:00 / 00:00", 9, MUTED)
        self.time_label.pack(side="right", padx=(9, 0))
        self.progress = ttk.Progressbar(timeline, style="Green.Horizontal.TProgressbar", maximum=1)
        self.progress.pack(side="left", fill="x", expand=True)

        controls = tk.Frame(playback, bg=PANEL)
        controls.pack(fill="x")
        self.prepare_button = self.button(controls, "Preparar & tocar", self.prepare, True)
        self.prepare_button.pack(side="left")
        self.prepare_button.configure(state="disabled")
        self.pause_button = self.button(controls, "Pausar", self.toggle_pause)
        self.pause_button.pack(side="left", padx=6)
        self.pause_button.configure(state="disabled")
        self.volume = tk.Scale(controls, from_=0, to=100, orient="horizontal", length=100,
                               bg=PANEL, fg=MUTED, troughcolor=CONTROL, highlightthickness=0,
                               activebackground=GREEN, command=self.change_volume)
        self.volume.set(10)
        self.volume.pack(side="right")
        self.label(controls, "Volume", 9, MUTED).pack(side="right", padx=(6, 5))

    def update_lyric_font(self, event=None):
        from tkinter import font

        available = {family.casefold(): family for family in font.families(self.root)}
        choices = {
            "Serifada": ("Georgia", "Noto Serif", "DejaVu Serif", "Times New Roman"),
            "Limpa": ("Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"),
            "Monoespaçada": ("Cascadia Code", "Consolas", "DejaVu Sans Mono", "Courier New"),
        }
        family = next((available[name.casefold()] for name in choices[self.font_choice.get()]
                       if name.casefold() in available), "TkDefaultFont")
        self.lyric_font = font.Font(root=self.root, family=family, size=20)
        self.impact_font = font.Font(root=self.root, family=family, size=20,
                                    weight="bold" if self.emphasis.get() else "normal")
        self.lyrics.configure(font=self.lyric_font)
        self.lyrics.tag_configure("impact", font=self.impact_font,
                                 foreground=GREEN if self.emphasis.get() else TEXT)

    def set_lyrics(self, text, append=False, tag=""):
        self.lyrics.configure(state="normal")
        if not append:
            self.lyrics.delete("1.0", "end")
            self.rendered_chars = 0
        self.rendered_chars += len(text)
        self.lyrics.insert("end", text, (tag,) if tag else ())
        self.lyrics.see("end")
        self.lyrics.configure(state="disabled")

    def rebuild_animation(self):
        if self.lyric_data is None:
            return
        self.events = lyric_events(self.lyric_data, styled=True, stretch=self.stretch.get())
        self.event_times = [position for position, _, _ in self.events]
        self.cues = word_cues(self.lyric_data, self.stretch.get())
        self.cue_times = [cue.start for cue in self.cues]
        # Mantém o texto e a animação em ordem mesmo com tempos sobrepostos.
        for i in range(1, len(self.cue_times)):
            self.cue_times[i] = max(self.cue_times[i], self.cue_times[i - 1])
        self.event_index = 0
        self.set_lyrics("")
        self.render_pending()
        self.root.after_idle(self.update_dot)

    def render_pending(self):
        from itertools import groupby

        end = bisect_right(self.event_times, self.position)
        if end > self.event_index:
            for tag, group in groupby(self.events[self.event_index:end], key=lambda item: item[2]):
                self.set_lyrics("".join(char for _, char, _ in group), append=True, tag=tag)
            self.event_index = end

    def update_dot(self):
        if not self.show_dot.get() or not self.playing or not self.cues:
            self.dot.place_forget()
            return
        index = bisect_right(self.cue_times, self.position) - 1
        if index < 0:
            self.dot.place_forget()
            return
        cue = self.cues[index]
        if not cue.start <= self.position < cue.end or not cue.text:
            self.dot.place_forget()
            return
        fraction = (self.position - cue.start) / max(0.001, cue.end - cue.start)
        letter_position = fraction * len(cue.text)
        char = min(len(cue.text) - 1, int(letter_position),
                   self.rendered_chars - cue.offset - 1)
        if char < 0:
            self.dot.place_forget()
            return
        box = self.lyrics.bbox(f"1.0 + {cue.offset + char} chars")
        if box:
            x, y, width, _ = box
            hop = 4 * math.sin(math.pi * fraction)
            within_letter = min(1, max(0, letter_position - char))
            self.dot.place(x=x + width * within_letter - 7, y=max(0, y - 13 - hop))
        else:
            self.dot.place_forget()

    def refresh_library(self):
        self.files = sorted({p.resolve() for folder in (core.ROOT, core.ROOT / "musicas")
                             if folder.is_dir() for p in folder.iterdir()
                             if p.is_file() and p.suffix.lower() in core.AUDIO_EXTENSIONS},
                            key=lambda p: p.name.casefold())
        count = len(self.files)
        self.library_count.configure(text=f"{count} faixa" if count == 1 else f"{count} faixas")
        self.library.delete(0, "end")
        for path in self.files:
            self.library.insert("end", path.stem)

    def select_library(self, event=None):
        selected = self.library.curselection()
        if selected and not self.busy:
            self.select(self.files[selected[0]])

    def open_file(self):
        path = self.filedialog.askopenfilename(title="Escolha uma música", initialdir=core.ROOT / "musicas",
                    filetypes=[("Áudio", " ".join(f"*{ext}" for ext in sorted(core.AUDIO_EXTENSIONS))),
                               ("Todos os arquivos", "*")])
        if path:
            self.select(Path(path))

    def select(self, audio):
        self.stop()
        self.audio = audio.resolve()
        self.title.configure(text=audio.stem)
        self.status.configure(text="Pronta para preparar · usa o cache quando disponível.")
        self.set_lyrics("Clique em Preparar & tocar.\n\nA letra vai aparecer aos poucos, no ritmo da voz.")
        self.prepare_button.configure(text="Preparar & tocar", state="normal")
        self.progress["value"] = 0
        self.time_label.configure(text="00:00 / 00:00")

    def prepare(self):
        if self.busy or self.audio is None:
            return
        self.stop()
        mode = self.mode.get()
        argv = [str(self.audio), "--sem-reproducao", "--idioma", self.language.get(),
                "--contexto", self.context.get()]
        if mode.startswith("Leve"):
            argv.append("--leve")
        elif mode.startswith("Preciso"):
            argv.append("--preciso")
        elif mode.startswith("Inglês"):
            argv.extend(["--modelo", "medium.en", "--idioma", "en"])
        elif mode.startswith("Máxima"):
            argv.extend(["--modelo", "large-v3"])
        if self.refazer.get():
            argv.append("--refazer")
        if self.offline.get():
            argv.append("--offline")
        self.busy = True
        self.prepare_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.library.configure(state="disabled")
        self.status.configure(text="Preparando a faixa… o progresso detalhado também aparece no terminal.")
        self.set_lyrics("Preparando sua música…\n\n" +
                        ("Modo offline: usando apenas cache e modelos já baixados." if self.offline.get()
                         else "Na primeira vez, o modelo pode precisar ser baixado."))
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        threading.Thread(target=self.worker, args=(self.audio, argv), daemon=True).start()

    def worker(self, audio, argv):
        process = None
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            with self.process_lock:
                if self.closed.is_set():
                    return
                process = subprocess.Popen([sys.executable, "-u", str(core.ROOT / "transcriber.py"), *argv],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                            env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.process = process
            tail = []
            with process.stdout:
                for line in process.stdout:
                    line = line.strip()
                    if line:
                        tail.append(line)
                        tail = tail[-6:]
                        self.messages.put(("status", line))
                        print(line, flush=True)
            if process.wait() != 0:
                raise RuntimeError("\n".join(tail) or "Não foi possível preparar a música.")
            args = core.parser().parse_args(argv)
            key = core.cache_key(audio, core.recognition_settings(audio, args))
            base = args.saida / f"{audio.stem[:80]}.{key[:16]}"
            data = core.load_cache(Path(str(base) + ".json"), key)
            if data is None:
                raise RuntimeError("A transcrição salva não pôde ser lida.")
            self.messages.put(("status", "Analisando intensidade para os destaques da letra…"))
            from audio_style import audio_envelope, mark_emphasis
            try:
                levels, duration = audio_envelope(audio)
                mark_emphasis(data, levels)
            except Exception as error:
                # Uma falha no efeito visual não deve impedir ouvir a música.
                print(f"Ênfase indisponível: {error}", flush=True)
                import av
                with av.open(str(audio)) as container:
                    duration = container.duration / av.time_base if container.duration else data["segments"][-1]["end"]
            self.messages.put(("ready", (audio, data, duration)))
        except Exception as error:
            self.messages.put(("error", str(error)))
        finally:
            with self.process_lock:
                if self.process is process:
                    self.process = None

    def finish_preparing(self):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress["value"] = 0
        self.prepare_button.configure(state="normal")
        self.open_button.configure(state="normal")
        self.library.configure(state="normal")

    def start(self, audio, data, duration):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        self.pygame = pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(str(audio))
        pygame.mixer.music.set_volume(self.volume.get() / 100)
        self.lyric_data = data
        self.duration = duration
        self.position = 0
        self.progress.configure(maximum=max(1, duration))
        self.rebuild_animation()
        pygame.mixer.music.play()
        self.playing, self.paused = True, False
        self.pause_button.configure(state="normal", text="Pausar")
        self.prepare_button.configure(text="Reiniciar")
        self.status.configure(text=f"Tocando · {data['language'].upper()} · letra sincronizada")

    def stop(self):
        if self.pygame and self.pygame.mixer.get_init():
            self.pygame.mixer.music.stop()
            self.pygame.mixer.music.unload()
        self.playing = self.paused = False
        self.lyric_data = None
        self.dot.place_forget()
        self.pause_button.configure(state="disabled", text="Pausar")

    def toggle_pause(self):
        if not self.playing:
            return
        self.paused = not self.paused
        if self.paused:
            self.pygame.mixer.music.pause()
        else:
            self.pygame.mixer.music.unpause()
        self.pause_button.configure(text="Continuar" if self.paused else "Pausar")
        self.status.configure(text="Pausada" if self.paused else "Tocando · letra sincronizada")

    def change_volume(self, value):
        if self.pygame and self.pygame.mixer.get_init():
            self.pygame.mixer.music.set_volume(float(value) / 100)

    def tick(self):
        for _ in range(60):
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status.configure(text=value[-220:])
            elif kind == "error":
                self.finish_preparing()
                self.status.configure(text="Não foi possível preparar a faixa.")
                self.set_lyrics(value)
            elif kind == "ready":
                self.finish_preparing()
                try:
                    self.start(*value)
                except Exception as error:
                    self.stop()
                    self.status.configure(text="A letra foi salva, mas a reprodução falhou.")
                    self.set_lyrics(str(error))
        if self.playing and not self.paused:
            music = self.pygame.mixer.music
            if music.get_busy():
                self.position = max(self.position, music.get_pos() / 1000)
            else:
                self.playing = False
                self.pause_button.configure(state="disabled")
                self.status.configure(text="Faixa finalizada · clique em Reiniciar para ouvir de novo.")
            self.render_pending()
            self.progress["value"] = self.position
            self.time_label.configure(text=f"{clock_label(self.position)} / {clock_label(self.duration)}")
        self.update_dot()
        if not self.closed.is_set():
            self.root.after(25, self.tick)

    def close(self):
        self.closed.set()
        with self.process_lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
        self.stop()
        if self.pygame:
            self.pygame.mixer.quit()
        self.root.destroy()


def main():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog
    except ImportError:
        print("Tkinter não está instalado. No Ubuntu/Debian: sudo apt install python3-tk", file=sys.stderr)
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"Não foi possível abrir a janela: {error}. Execute em uma sessão gráfica.", file=sys.stderr)
        return 1
    Player(root, tk, ttk, filedialog)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
