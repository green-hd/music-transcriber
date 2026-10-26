"""Transcrição de letras no terminal, com cache e reprodução opcional."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma"}
CACHE_VERSION = 3


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Use um inteiro maior que zero.")
    return number


def volume_value(value):
    number = float(value)
    if not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("O volume deve ficar entre 0 e 1.")
    return number


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("arquivo", nargs="?", type=Path, help="Caminho da música; sem ele, abre um menu.")
    cli.add_argument("--pasta", type=Path, default=ROOT / "musicas", help="Pasta das músicas.")
    cli.add_argument("--saida", type=Path, default=ROOT / "transcricoes", help="Pasta dos resultados e cache.")
    cli.add_argument("--modelo", choices=("tiny", "base", "small", "small.en", "medium", "medium.en", "large-v3"), default=None)
    modes = cli.add_mutually_exclusive_group()
    modes.add_argument("--preciso", action="store_true", help="Usa medium; consome mais recursos e tempo.")
    modes.add_argument("--leve", action="store_true", help="Usa base com busca simples; pode errar mais.")
    cli.add_argument("--contexto", default="", help="Nomes e termos da música para orientar o reconhecimento.")
    cli.add_argument("--idioma", default="auto", help="auto, pt, en, es etc.; informar ajuda em músicas curtas.")
    cli.add_argument("--threads", type=positive_int, default=min(4, os.cpu_count() or 1))
    cli.add_argument("--sem-reproducao", "--no-play", action="store_true", help="Só transcreve e salva, sem tocar áudio.")
    cli.add_argument("--refazer", action="store_true", help="Ignora a transcrição em cache.")
    cli.add_argument("--offline", action="store_true", help="Usa apenas modelos já baixados.")
    cli.add_argument("--volume", type=volume_value, default=0.5)
    return cli


def select_audio(args):
    if args.arquivo is not None:
        audio = args.arquivo.expanduser().resolve()
        if not audio.is_file():
            raise ValueError(f"Arquivo não encontrado: {audio}")
        if audio.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError(f"Formato não suportado: {audio.suffix}")
        return audio
    folders = {args.pasta.expanduser().resolve(), ROOT}
    files = sorted({p.resolve() for folder in folders if folder.is_dir()
                    for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS},
                   key=lambda p: (p.name.casefold(), str(p)))
    if not files:
        raise ValueError(f"Nenhuma música encontrada. Coloque os arquivos em {args.pasta} ou informe o caminho.")
    print("\nMúsicas disponíveis:")
    for index, audio in enumerate(files, 1):
        print(f"  {index}. {audio.name} ({audio.parent.name})")
    if not sys.stdin.isatty():
        raise ValueError('Informe o arquivo no comando, por exemplo: python transcriber.py "musicas/faixa.mp3"')
    while True:
        try:
            answer = input("\nEscolha o número (q para sair): ").strip()
        except EOFError:
            raise KeyboardInterrupt from None
        if answer.lower() in {"q", "sair"}:
            raise KeyboardInterrupt
        if answer.isdecimal() and 1 <= int(answer) <= len(files):
            return files[int(answer) - 1]
        print("Digite um número da lista.")


def cache_key(audio, settings):
    digest = hashlib.sha256()
    with audio.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(json.dumps(settings, sort_keys=True).encode())
    return digest.hexdigest()


def valid_timing(item):
    return (isinstance(item, dict) and isinstance(item.get("text"), str)
            and bool(item["text"].strip())
            and all(type(item.get(k)) in (int, float) and math.isfinite(item[k]) for k in ("start", "end"))
            and 0 <= item["start"] <= item["end"])


def load_cache(path, key):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("key") == key and data.get("version") == CACHE_VERSION
                and isinstance(data.get("language"), str)
                and isinstance(data.get("segments"), list) and data["segments"]
                and all(valid_timing(s) and isinstance(s.get("words"), list)
                        and all(valid_timing(w) for w in s["words"]) for s in data["segments"])):
            return data
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def transcribe(audio, settings, threads, offline):
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError("Instale as dependências: python -m pip install -r requirements.txt") from error
    model_path = settings["model"]
    if not offline:
        from huggingface_hub import snapshot_download

        print(f"Verificando/baixando modelo {model_path}; arquivos existentes serão reutilizados.", flush=True)
        if model_path == "medium":
            print("O modelo medium tem aproximadamente 1,53 GB para baixar. O token HF é opcional.", flush=True)
        # O downloader interno do faster-whisper oculta o progresso.
        # Baixa no mesmo cache, com as barras padrão do Hugging Face.
        model_path = snapshot_download(
            repo_id=f"Systran/faster-whisper-{model_path}",
            allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                            "tokenizer.json", "vocabulary.*"],
        )
    print(f"Carregando modelo {settings['model']} na CPU (int8, {threads} threads)...", flush=True)
    model = WhisperModel(model_path, device="cpu", compute_type="int8",
                         cpu_threads=threads, num_workers=1, local_files_only=offline)
    try:
        iterator, info = model.transcribe(
            str(audio), language=settings["language"], beam_size=settings["beam_size"],
            best_of=1, temperature=0.0, word_timestamps=True,
            vad_filter=False, condition_on_previous_text=False,
            hallucination_silence_threshold=2.0,
            hotwords=settings["context"],
        )
        print(f"Idioma: {info.language}. Transcrevendo...", flush=True)
        segments = []
        for segment in iterator:
            item = {"start": float(segment.start), "end": float(segment.end),
                    "text": segment.text.strip(), "words": []}
            if not valid_timing(item):
                continue
            for word in segment.words or []:
                if word.start is None or word.end is None:
                    continue
                entry = {"start": float(word.start), "end": float(word.end), "text": word.word.strip()}
                if valid_timing(entry):
                    item["words"].append(entry)
            segments.append(item)
            print(f"  [{timestamp(item['start'])}] {item['text']}", flush=True)
        if not segments:
            raise ValueError("Nenhuma voz reconhecida. Tente --idioma pt (ou en) e --preciso.")
        return {"version": CACHE_VERSION, "language": info.language, "settings": settings, "segments": segments}
    finally:
        # Libera o modelo antes de iniciar a reprodução.
        del model
        gc.collect()


def timestamp(seconds, srt=False):
    milliseconds = max(0, round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    if srt:
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
    return f"{hours * 60 + minutes:02}:{secs:02}.{millis // 10:02}"


def atomic_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_outputs(base, data):
    segments = data["segments"]
    outputs = {
        ".txt": "\n".join(s["text"] for s in segments) + "\n",
        ".srt": "\n\n".join(f"{i}\n{timestamp(s['start'], True)} --> {timestamp(s['end'], True)}\n{s['text']}"
                               for i, s in enumerate(segments, 1)) + "\n",
        ".lrc": "\n".join(f"[{timestamp(s['start'])}]{s['text']}" for s in segments) + "\n",
        ".json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    }
    for extension, content in outputs.items():
        atomic_write(Path(str(base) + extension), content)


def show_playback_header(audio):
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")
    print(r"""
          /\_____/\
         /  o   o  \
        ( ==  ^  == )
         )         (
        (           )
       ( (  )   (  ) )
      (__(__)___(__)__)

             by green
""")
    title = f"[ {audio.stem} ]"
    border = "=" * max(37, len(title))
    print(f"{border}\n{title}\n{border}")
    print("Ctrl+C para parar\n", flush=True)


def type_timed_text(text, start, end, wait_until):
    """Distribui os caracteres no tempo da palavra, sem atraso acumulado."""
    interval = min(max(0.0, end - start) / max(1, len(text)), 0.06)
    for index, character in enumerate(text):
        if not wait_until(start + index * interval):
            return False
        print(character, end="", flush=True)
    return True


def play(audio, data, volume):
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    try:
        import pygame
    except ImportError:
        print("Reprodução indisponível. Instale: python -m pip install -r requirements-playback.txt", file=sys.stderr)
        return False
    try:
        pygame.mixer.init()
        pygame.mixer.music.load(str(audio))
        pygame.mixer.music.set_volume(volume)
        show_playback_header(audio)
        pygame.mixer.music.play()

        def wait_until(target):
            while pygame.mixer.music.get_busy():
                position = pygame.mixer.music.get_pos() / 1000
                if position >= target:
                    return True
                time.sleep(min(max(target - position, 0.001), 0.05))
            return False

        for segment in data["segments"]:
            for index, word in enumerate(segment["words"] or [segment]):
                if not wait_until(word["start"]):
                    return True
                if index:
                    print(" ", end="", flush=True)
                if not type_timed_text(word["text"], word["start"], word["end"], wait_until):
                    print(flush=True)
                    return True
            print(flush=True)
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        return True
    except pygame.error as error:
        print(f"Não foi possível reproduzir o áudio: {error}. A transcrição já foi salva.", file=sys.stderr)
        return False
    finally:
        pygame.mixer.quit()


def recognition_settings(audio, args):
    if args.modelo and args.modelo.endswith(".en") and args.idioma.lower() not in {"auto", "en"}:
        raise ValueError("Modelos .en reconhecem apenas inglês; use --idioma en ou um modelo multilíngue.")
    return {"model": args.modelo or ("medium" if args.preciso else "base" if args.leve else "small"),
            "language": "en" if args.modelo and args.modelo.endswith(".en") else
                        None if args.idioma.lower() == "auto" else args.idioma.lower(),
            "beam_size": 1 if args.leve else 5,
            "context": f"{audio.stem}. {args.contexto}".strip(), "version": CACHE_VERSION}


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        audio = select_audio(args)
        settings = recognition_settings(audio, args)
        output = args.saida.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        key = cache_key(audio, settings)
        # Mantém nomes legíveis e distingue arquivos/configurações diferentes.
        base = output / f"{audio.stem[:80]}.{key[:16]}"
        cache = Path(str(base) + ".json")
        data = None if args.refazer else load_cache(cache, key)
        if data is None:
            started = time.monotonic()
            data = transcribe(audio, settings, args.threads, args.offline)
            data.update(key=key, source=audio.name)
            print(f"\nTranscrição concluída em {time.monotonic() - started:.1f}s.")
        else:
            print("Transcrição carregada do cache; modelo não foi carregado.")
        save_outputs(base, data)
        print(f"Resultados (TXT, SRT, LRC e JSON): {base}.*", flush=True)
        if not args.sem_reproducao:
            play(audio, data, args.volume)
        return 0
    except KeyboardInterrupt:
        print("\nInterrompido.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Falha na transcrição: {error}\nVerifique o áudio e o acesso ao modelo. "
              "Se estiver offline, baixe o modelo antes de usar --offline.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
