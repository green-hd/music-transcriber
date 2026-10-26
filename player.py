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

BG = "#101714"
PANEL = "#18231d"
GREEN = "#a3edb2"
TEXT = "#eff5ef"
MUTED = "#94a69a"
CAT = r""" /\_____/\
/  o   o  \
( == ^ == )
 )       (
(_________)"""


def display_word(word, stretch=False):
    """Alongamento visual aproximado; nunca altera a letra salva."""
    text = word["text"]
    duration = max(0, word["end"] - word["start"])
    if not stretch or duration < max(1.0, len(text) * 0.11):
        return text
    last = next((i for i in range(len(text) - 1, -1, -1) if text[i].isalpha()), None)
    if last is None:
        return text
    count = min(12, max(1, int((duration - len(text) * 0.07) / 0.16)))
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
            text = display_word(word, stretch)
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
            text = display_word(word, stretch)
            interval = max(0, word["end"] - word["start"]) / max(1, len(text))
            if text == word["text"]:
                interval = min(interval, 0.06)
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

    def label(self, parent, text, size=12, color=TEXT, **kwargs):
        return self.tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                             font=("Segoe UI", size), **kwargs)

    def button(self, parent, text, command, primary=False):
        return self.tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                              bg=GREEN if primary else "#283a2e", fg=BG if primary else TEXT,
                              activebackground="#baf5c6", activeforeground=BG,
                              font=("Segoe UI", 11, "bold"), padx=17, pady=10, cursor="hand2")

    def build(self):
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Green.Horizontal.TProgressbar", troughcolor=PANEL, background=GREEN,
                        borderwidth=0, lightcolor=GREEN, darkcolor=GREEN)
        sidebar = tk.Frame(self.root, bg=PANEL, width=270, padx=20, pady=22)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text=CAT, font=("Courier New", 18, "bold"), fg=GREEN, bg=PANEL).pack(pady=(0, 5))
        self.label(sidebar, "by green", 12, GREEN).pack()
        self.label(sidebar, "SUA BIBLIOTECA", 10, MUTED, anchor="w").pack(fill="x", pady=(32, 12))
        self.library = tk.Listbox(sidebar, bg=PANEL, fg=TEXT, selectbackground="#35543d",
                                  selectforeground=GREEN, highlightthickness=0, bd=0,
                                  font=("Segoe UI", 11), activestyle="none", exportselection=False)
        self.library.pack(fill="both", expand=True)
        self.library.bind("<<ListboxSelect>>", self.select_library)
        self.library.bind("<Double-Button-1>", lambda event: self.prepare())
        self.open_button = self.button(sidebar, "+ Abrir música", self.open_file)
        self.open_button.pack(fill="x", pady=(14, 8))
        self.label(sidebar, "Seu áudio. Sua letra. Seu ritmo.", 9, MUTED).pack(pady=(8, 0))

        main = tk.Frame(self.root, bg=BG, padx=30, pady=24)
        main.pack(side="left", fill="both", expand=True)
        self.label(main, "GREEN / MUSIC & LYRICS", 10, GREEN, anchor="w").pack(fill="x")
        self.title = self.label(main, "Escolha uma música", 24, anchor="w", wraplength=630, justify="left")
        self.title.pack(fill="x", pady=(12, 4))
        self.status = self.label(main, "Abra uma faixa para começar.", 10, MUTED, anchor="w",
                                 wraplength=620, justify="left")
        self.status.pack(fill="x", pady=(0, 18))

        options = tk.Frame(main, bg=BG)
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
            ttk.Combobox(group, textvariable=variable, values=values, state="readonly", width=width).pack()
        self.refazer = tk.BooleanVar(value=False)
        self.offline = tk.BooleanVar(value=False)
        flags = tk.Frame(options, bg=BG)
        flags.pack(side="left")
        for title, variable in (("Refazer", self.refazer), ("Offline", self.offline)):
            tk.Checkbutton(flags, text=title, variable=variable, bg=BG, fg=MUTED,
                           activebackground=BG, activeforeground=GREEN, selectcolor=PANEL).pack(anchor="w")
        self.label(main, "CONTEXTO  ·  nomes ou expressões da música (opcional)", 9, MUTED,
                   anchor="w").pack(fill="x", pady=(14, 6))
        self.context = tk.Entry(main, bg=PANEL, fg=TEXT, insertbackground=GREEN, relief="flat",
                                font=("Segoe UI", 11))
        self.context.pack(fill="x", ipady=7)

        lyric_frame = tk.Frame(main, bg=PANEL, padx=20, pady=18)
        lyric_frame.pack(fill="both", expand=True, pady=18)
        lyric_header = tk.Frame(lyric_frame, bg=PANEL)
        lyric_header.pack(fill="x", pady=(0, 12))
        self.label(lyric_header, "LETRA", 9, GREEN).pack(side="left")
        self.emphasis = tk.BooleanVar(value=True)
        tk.Checkbutton(lyric_header, text="Ênfase", variable=self.emphasis, command=self.update_lyric_font,
                       bg=PANEL, fg=MUTED, selectcolor=BG, activebackground=PANEL,
                       activeforeground=GREEN).pack(side="right", padx=(8, 0))
        self.font_choice = tk.StringVar(value="Serifada")
        font_picker = ttk.Combobox(lyric_header, textvariable=self.font_choice, state="readonly",
                                  values=["Serifada", "Limpa", "Monoespaçada"], width=14)
        font_picker.pack(side="right")
        font_picker.bind("<<ComboboxSelected>>", self.update_lyric_font)
        effects = tk.Frame(lyric_frame, bg=PANEL)
        effects.pack(fill="x", pady=(0, 6))
        self.stretch = tk.BooleanVar(value=False)
        self.show_dot = tk.BooleanVar(value=True)
        for title, variable, callback in (("Alongar palavras", self.stretch, self.rebuild_animation),
                                          ("Bolinha karaokê", self.show_dot, self.update_dot)):
            tk.Checkbutton(effects, text=title, variable=variable, command=callback,
                           bg=PANEL, fg=MUTED, selectcolor=BG, activebackground=PANEL,
                           activeforeground=GREEN).pack(side="left", padx=(0, 12))
        self.lyrics = tk.Text(lyric_frame, bg=PANEL, fg=TEXT, wrap="word",
                              relief="flat", highlightthickness=0, spacing1=18, spacing3=8, spacing2=18,
                              state="disabled", cursor="arrow", padx=0, height=6)
        self.lyrics.pack(fill="both", expand=True)
        self.dot = tk.Canvas(self.lyrics, width=14, height=14, bg=PANEL, highlightthickness=0)
        self.dot.create_oval(1, 1, 13, 13, fill="#35543d", outline="")
        self.dot.create_oval(4, 4, 10, 10, fill=GREEN, outline="")
        self.update_lyric_font()
        self.set_lyrics("A música ganha voz aqui.\n\nEscolha uma faixa e clique em Preparar & tocar.")
        self.progress = ttk.Progressbar(main, style="Green.Horizontal.TProgressbar", maximum=1)
        self.progress.pack(fill="x")
        self.time_label = self.label(main, "00:00 / 00:00", 10, MUTED, anchor="e")
        self.time_label.pack(fill="x", pady=(5, 10))
        controls = tk.Frame(main, bg=BG)
        controls.pack(fill="x")
        self.prepare_button = self.button(controls, "Preparar & tocar", self.prepare, True)
        self.prepare_button.pack(side="left")
        self.prepare_button.configure(state="disabled")
        self.pause_button = self.button(controls, "Pausar", self.toggle_pause)
        self.pause_button.pack(side="left", padx=8)
        self.pause_button.configure(state="disabled")
        self.label(controls, "VOL", 9, MUTED).pack(side="left", padx=(8, 3))
        self.volume = tk.Scale(controls, from_=0, to=100, orient="horizontal", length=105,
                               bg=BG, fg=MUTED, troughcolor=PANEL, highlightthickness=0,
                               activebackground=GREEN, command=self.change_volume)
        self.volume.set(10)
        self.volume.pack(side="left")
        # Reserva espaço para os controles antes de expandir a área da letra.
        controls.pack_configure(side="bottom", before=lyric_frame)
        self.time_label.pack_configure(side="bottom", before=lyric_frame)
        self.progress.pack_configure(side="bottom", before=lyric_frame)

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
