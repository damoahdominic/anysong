#!/usr/bin/env python3
"""anysong — Download any song as a properly named MP3.

Usage:
    anysong "Lil Wayne Lollipop"
    anysong "Wild Thoughts Rihanna"
    anysong "Drake Hotline Bling" --dir ~/Music
    anysong search "Bohemian Rhapsody" --limit 10
    anysong batch playlist.txt
"""
import os
import sys
import re
import json
import time
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

app = typer.Typer(name="anysong", help="🎵 Download any song as a properly named MP3", add_completion=False)
console = Console()

DEFAULT_DIR = Path.home() / "music"
DEEZER_API = "https://api.deezer.com"
YTC_URL = "https://ytc.mba.sh"
COOKIES_DIR = Path.home() / ".anysong"
COOKIES_FILE = COOKIES_DIR / "cookies.txt"

# Download sources in priority order
SOURCES = [
    ("youtube", "ytsearch1:{query}"),
    ("soundcloud", "scsearch1:{query}"),
]


def _get_env() -> dict:
    env = os.environ.copy()
    deno_path = os.path.expanduser("~/.deno/bin")
    if os.path.isdir(deno_path):
        env["PATH"] = f"{deno_path}:{env.get('PATH', '')}"
    return env


def _find_ytdlp() -> str:
    venv_ytdlp = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
    if os.path.isfile(venv_ytdlp):
        return venv_ytdlp
    return "yt-dlp"


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    name = name.lower()
    name = name.strip('_.')
    return name


def _ensure_cookies() -> Optional[str]:
    """Ensure we have YouTube cookies.
    
    Priority:
    1. Fresh local cookies (~/.anysong/cookies.txt, < 24h old)
    2. Fetch from ytc.mba.sh central cookie service
    3. Stale local cookies (better than nothing)
    4. None
    """
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)

    # Check if local cookies are fresh (< 24h)
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 100:
        age_hours = (time.time() - COOKIES_FILE.stat().st_mtime) / 3600
        if age_hours < 24:
            return str(COOKIES_FILE)

    # Try fetching from central service
    try:
        req = urllib.request.Request(f"{YTC_URL}/health", headers={"User-Agent": "anysong/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read().decode())
            if health.get("cookies_available"):
                req2 = urllib.request.Request(f"{YTC_URL}/cookies.txt", headers={"User-Agent": "anysong/1.0"})
                with urllib.request.urlopen(req2, timeout=10) as resp2:
                    if resp2.status == 200:
                        content = resp2.read()
                        if len(content) > 100:
                            COOKIES_FILE.write_bytes(content)
                            console.print("  [dim]🍪 Cookies refreshed from ytc.mba.sh[/dim]")
                            return str(COOKIES_FILE)
    except Exception:
        pass

    # Fall back to stale local cookies
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 100:
        return str(COOKIES_FILE)

    return None


def _deezer_search(query: str, limit: int = 5) -> list:
    encoded = urllib.parse.quote(query)
    url = f"{DEEZER_API}/search?q={encoded}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "anysong/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("data", [])
    except Exception as e:
        console.print(f"[dim]Deezer search failed: {e}[/dim]")
        return []


def _format_duration(seconds: int) -> str:
    if not seconds:
        return "?"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _build_filename(artist: str, title: str) -> str:
    clean_title = _sanitize_filename(title)
    clean_artist = _sanitize_filename(artist)
    return f"{clean_title}_by_{clean_artist}.mp3"


def _try_download(search_query: str, output_path: str, source_template: str, source_name: str) -> bool:
    ytdlp = _find_ytdlp()
    env = _get_env()

    cmd = [
        ytdlp,
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--max-downloads", "1",
        "--no-playlist",
        "--output", output_path.replace(".mp3", ".%(ext)s"),
        "--print", "after_move:filepath",
        "--remote-components", "ejs:github",
    ]

    if "search" in source_template:
        cmd.extend(["--match-filter", "duration<=600"])

    cmd.append(source_template.format(query=search_query))

    # Auto-fetch cookies for YouTube
    if source_name == "youtube":
        cookies_path = _ensure_cookies()
        if cookies_path:
            cmd.extend(["--cookies", cookies_path])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        if result.returncode not in (0, 101):  # 101 = max downloads reached
            return False

        if os.path.isfile(output_path):
            return True

        actual_path = result.stdout.strip().split("\n")[-1]
        if actual_path and os.path.isfile(actual_path):
            os.rename(actual_path, output_path)
            return True

        parent = Path(output_path).parent
        mp3s = sorted(parent.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        if mp3s:
            mp3s[0].rename(output_path)
            return True

        return False
    except (subprocess.TimeoutExpired, Exception):
        return False


def _try_deezer_preview(preview_url: str, output_path: str) -> bool:
    if not preview_url:
        return False
    try:
        req = urllib.request.Request(preview_url, headers={"User-Agent": "anysong/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if len(data) < 10000:
                return False
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(data)
            return True
    except Exception:
        return False


def _download_song(yt_query: str, output_path: str, simple_query: str = "", preview_url: str = "") -> tuple:
    if not simple_query:
        simple_query = yt_query

    for source_name, template in SOURCES:
        console.print(f"  [dim]Trying {source_name}...[/dim]")
        q = yt_query if source_name == "youtube" else simple_query
        success = _try_download(q, output_path, template, source_name)
        if success:
            size = os.path.getsize(output_path)
            if size < 100_000:
                console.print(f"  [dim]{source_name}: got a short clip, trying next...[/dim]")
                os.remove(output_path)
                continue
            return (True, source_name, "full")
        console.print(f"  [dim]{source_name}: failed, trying next...[/dim]")

    if preview_url:
        console.print(f"  [dim]Trying deezer preview (30s)...[/dim]")
        if _try_deezer_preview(preview_url, output_path):
            return (True, "deezer", "30s preview")

    return (False, "", "")


@app.command()
def download(
    query: str = typer.Argument(..., help="Song to download, e.g. 'Lil Wayne Lollipop'"),
    dir: str = typer.Option(None, "--dir", "-d", help="Output directory"),
    pick: bool = typer.Option(False, "--pick", "-p", help="Show results and let you pick"),
    preview_ok: bool = typer.Option(False, "--preview-ok", help="Accept 30s Deezer preview as fallback"),
):
    """Download any song as a properly named MP3."""
    output_dir = Path(dir) if dir else DEFAULT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel(f"🎵 [bold]anysong[/bold] — {query}"))

    with console.status("[blue]Looking up metadata..."):
        results = _deezer_search(query)

    if not results:
        console.print("[yellow]No metadata found. Downloading with raw query...[/yellow]")
        filename = _sanitize_filename(query) + ".mp3"
        output_path = str(output_dir / filename)

        with console.status("[magenta]Downloading..."):
            success, source, quality = _download_song(query, output_path)

        if success:
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            console.print(f"\n[bold green]✓[/bold green] {output_path} ({size_mb:.1f} MB) [dim]via {source}[/dim]")
        else:
            console.print(f"\n[bold red]✗ Could not download: {query}[/bold red]")
        return

    if pick and len(results) > 1:
        table = Table(title="Pick a track")
        table.add_column("#", style="dim", width=3)
        table.add_column("Title", style="cyan")
        table.add_column("Artist", style="green")
        table.add_column("Album", style="yellow")
        table.add_column("Duration", style="magenta")

        for i, t in enumerate(results[:10], 1):
            table.add_row(
                str(i),
                t.get("title", "?"),
                t.get("artist", {}).get("name", "?"),
                t.get("album", {}).get("title", "?"),
                _format_duration(t.get("duration", 0)),
            )
        console.print(table)

        choice = typer.prompt("Pick #", default="1")
        try:
            track = results[int(choice) - 1]
        except (ValueError, IndexError):
            track = results[0]
    else:
        track = results[0]

    title = track.get("title", "Unknown")
    artist = track.get("artist", {}).get("name", "Unknown")
    album = track.get("album", {}).get("title", "")
    duration = track.get("duration", 0)
    preview_url = track.get("preview", "") if preview_ok else ""

    console.print(f"  [cyan]{title}[/cyan] by [green]{artist}[/green]", end="")
    if album:
        console.print(f" — [yellow]{album}[/yellow]", end="")
    console.print(f" [dim]({_format_duration(duration)})[/dim]")

    filename = _build_filename(artist, title)
    output_path = str(output_dir / filename)

    if os.path.isfile(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        console.print(f"[yellow]Already have it:[/yellow] {output_path} ({size_mb:.1f} MB)")
        return

    yt_query = f"{artist} {title} official audio"
    simple_query = f"{artist} {title}"

    with console.status(f"[magenta]Downloading..."):
        success, source, quality = _download_song(yt_query, output_path, simple_query=simple_query, preview_url=preview_url)

    if success:
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        quality_note = f" [yellow]({quality})[/yellow]" if quality != "full" else ""
        console.print(f"\n[bold green]✓[/bold green] {output_path} ({size_mb:.1f} MB){quality_note} [dim]via {source}[/dim]")
    else:
        console.print(f"\n[bold red]✗ Could not download: {title} by {artist}[/bold red]")
        console.print("[dim]No cookies available. Upload cookies to ytc.mba.sh or provide your own:[/dim]")
        console.print("[dim]  anysong setup-cookies[/dim]")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n"),
):
    """Search for songs (Deezer metadata)."""
    with console.status(f"[blue]Searching '{query}'..."):
        results = _deezer_search(query, limit=limit)

    if not results:
        console.print("[red]No results.[/red]")
        return

    table = Table(title=f"🔍 '{query}'")
    table.add_column("#", style="dim", width=3)
    table.add_column("Title", style="cyan")
    table.add_column("Artist", style="green")
    table.add_column("Album", style="yellow")
    table.add_column("Duration", style="magenta")

    for i, t in enumerate(results, 1):
        table.add_row(
            str(i),
            t.get("title", "?"),
            t.get("artist", {}).get("name", "?"),
            t.get("album", {}).get("title", "?"),
            _format_duration(t.get("duration", 0)),
        )
    console.print(table)


@app.command()
def batch(
    file: str = typer.Argument(..., help="Text file with one song per line"),
    dir: str = typer.Option(None, "--dir", "-d", help="Output directory"),
    preview_ok: bool = typer.Option(False, "--preview-ok", help="Accept 30s Deezer preview as fallback"),
):
    """Download multiple songs from a text file."""
    output_dir = Path(dir) if dir else DEFAULT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = Path(file).read_text().strip().splitlines()
    lines = [l.strip() for l in lines if l.strip() and not l.startswith("#")]

    console.print(f"[bold]Downloading {len(lines)} songs → {output_dir}[/bold]\n")

    ok, previews, fail = 0, 0, []
    for i, line in enumerate(lines, 1):
        console.print(f"[dim]({i}/{len(lines)})[/dim] {line}")
        try:
            results = _deezer_search(line, limit=1)
            if results:
                track = results[0]
                title = track.get("title", "Unknown")
                artist = track.get("artist", {}).get("name", "Unknown")
                filename = _build_filename(artist, title)
                yt_query = f"{artist} {title} official audio"
                simple_q = f"{artist} {title}"
                preview_url = track.get("preview", "") if preview_ok else ""
            else:
                filename = _sanitize_filename(line) + ".mp3"
                yt_query = line
                simple_q = line
                preview_url = ""

            output_path = str(output_dir / filename)

            if os.path.isfile(output_path):
                console.print(f"  [yellow]exists[/yellow] {filename}")
                ok += 1
                continue

            success, source, quality = _download_song(yt_query, output_path, simple_query=simple_q, preview_url=preview_url)
            if success:
                if quality == "30s preview":
                    console.print(f"  [yellow]⚠ preview only[/yellow] {filename}")
                    previews += 1
                else:
                    console.print(f"  [green]✓[/green] {filename} [dim]via {source}[/dim]")
                ok += 1
            else:
                console.print(f"  [red]✗[/red] failed")
                fail.append(line)
        except Exception as e:
            console.print(f"  [red]✗ {e}[/red]")
            fail.append(line)

    console.print(f"\n[bold]{ok}/{len(lines)} downloaded[/bold]", end="")
    if previews:
        console.print(f" [yellow]({previews} previews only)[/yellow]", end="")
    console.print()
    if fail:
        console.print(f"[red]Failed:[/red]")
        for f in fail:
            console.print(f"  - {f}")


@app.command()
def setup_cookies():
    """Set up YouTube cookies (local or upload to ytc.mba.sh)."""
    console.print(Panel("[bold]🍪 YouTube Cookie Setup[/bold]"))

    console.print("\nanysong automatically fetches cookies from [cyan]ytc.mba.sh[/cyan].")
    console.print("If that fails, it uses local cookies from ~/.anysong/cookies.txt\n")

    # Check central service
    try:
        req = urllib.request.Request(f"{YTC_URL}/health", headers={"User-Agent": "anysong/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read().decode())
            if health.get("cookies_available"):
                age = health.get("cookies_age_hours", 0)
                console.print(f"[green]✓ Central cookies available[/green] (age: {age:.0f}h)")
            else:
                console.print("[yellow]⚠ No cookies on central server yet[/yellow]")
    except Exception:
        console.print("[red]✗ Could not reach ytc.mba.sh[/red]")

    # Check local
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 100:
        age = (time.time() - COOKIES_FILE.stat().st_mtime) / 3600
        console.print(f"[green]✓ Local cookies found[/green] ({COOKIES_FILE.stat().st_size} bytes, {age:.0f}h old)")
    else:
        console.print(f"[dim]No local cookies at {COOKIES_FILE}[/dim]")

    console.print("\n[bold]To add cookies:[/bold]")
    console.print("  1. On a machine with Chrome + YouTube logged in:")
    console.print("     [cyan]yt-dlp --cookies-from-browser chrome --cookies cookies.txt 'https://youtube.com'[/cyan]")
    console.print("\n  2. Upload to central server (so everyone benefits):")
    console.print(f"     [cyan]scp cookies.txt cbot@server:~/ytc/cookies.txt[/cyan]")
    console.print("\n  3. Or keep local only:")
    console.print(f"     [cyan]cp cookies.txt ~/.anysong/cookies.txt[/cyan]")


if __name__ == "__main__":
    app()
