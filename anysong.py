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
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

app = typer.Typer(name="anysong", help="🎵 Download any song as a properly named MP3", add_completion=False)
console = Console()

DEFAULT_DIR = Path.home() / "music"
DEEZER_API = "https://api.deezer.com"

# Download sources in priority order
SOURCES = [
    ("youtube", "ytsearch1:{query}"),
    ("soundcloud", "scsearch1:{query}"),
]


def _get_env() -> dict:
    """Get environment with deno + venv paths."""
    env = os.environ.copy()
    deno_path = os.path.expanduser("~/.deno/bin")
    if os.path.isdir(deno_path):
        env["PATH"] = f"{deno_path}:{env.get('PATH', '')}"
    return env


def _find_ytdlp() -> str:
    """Find yt-dlp binary."""
    venv_ytdlp = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
    if os.path.isfile(venv_ytdlp):
        return venv_ytdlp
    return "yt-dlp"


def _sanitize_filename(name: str) -> str:
    """Make a string safe for filenames."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    name = name.lower()
    name = name.strip('_.')
    return name


def _deezer_search(query: str, limit: int = 5) -> list:
    """Search Deezer for tracks. Free API, no auth needed."""
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
    """Build a clean filename: title_by_artist.mp3"""
    clean_title = _sanitize_filename(title)
    clean_artist = _sanitize_filename(artist)
    return f"{clean_title}_by_{clean_artist}.mp3"


def _ytmusic_search(query: str, limit: int = 1) -> list:
    """Search YouTube Music API for video IDs (no auth needed)."""
    try:
        from ytmusicapi import YTMusic
        yt = YTMusic()
        results = yt.search(query, filter="songs", limit=limit)
        return [
            {
                "title": r.get("title", ""),
                "artist": r.get("artists", [{}])[0].get("name", ""),
                "videoId": r.get("videoId", ""),
                "duration": r.get("duration", ""),
            }
            for r in results if r.get("videoId")
        ]
    except Exception:
        return []


def _try_download(search_query: str, output_path: str, source_template: str, source_name: str) -> bool:
    """Try downloading from a specific source."""
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
    ]

    # Only add duration filter for search-based sources
    if "search" in source_template:
        cmd.extend(["--match-filter", "duration<=600"])

    cmd.append(source_template.format(query=search_query))

    # YouTube cookies support
    if source_name == "youtube":
        cookies_path = os.path.expanduser("~/.anysong/cookies.txt")
        if os.path.isfile(cookies_path):
            cmd.extend(["--cookies", cookies_path])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)

        if result.returncode != 0:
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
    """Download 30-second preview from Deezer as last resort."""
    if not preview_url:
        return False
    try:
        req = urllib.request.Request(preview_url, headers={"User-Agent": "anysong/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if len(data) < 10000:  # Too small to be valid
                return False
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(data)
            return True
    except Exception:
        return False


def _download_song(yt_query: str, output_path: str, simple_query: str = "", preview_url: str = "") -> tuple:
    """Try downloading from multiple sources with fallback.
    
    Returns (success: bool, source: str, quality: str)
    """
    if not simple_query:
        simple_query = yt_query

    for source_name, template in SOURCES:
        console.print(f"  [dim]Trying {source_name}...[/dim]")
        q = yt_query if source_name == "youtube" else simple_query
        success = _try_download(q, output_path, template, source_name)
        if success:
            # Check file size to detect short clips
            size = os.path.getsize(output_path)
            if size < 100_000:  # Less than 100KB is probably a clip
                console.print(f"  [dim]{source_name}: got a short clip, trying next...[/dim]")
                os.remove(output_path)
                continue
            return (True, source_name, "full")
        console.print(f"  [dim]{source_name}: failed, trying next...[/dim]")

    # Last resort: Deezer 30-second preview
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

    # Step 1: Search Deezer for clean metadata
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

    # Step 2: Pick track
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

    # Step 3: Build filename + download
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
        console.print("[dim]YouTube requires cookies from this server. Fix:[/dim]")
        console.print("[dim]  1. On a machine with Chrome: yt-dlp --cookies-from-browser chrome --cookies cookies.txt 'https://youtube.com'[/dim]")
        console.print("[dim]  2. Copy to server: scp cookies.txt cbot@server:~/.anysong/cookies.txt[/dim]")
        console.print("[dim]  3. Or use --preview-ok for 30s Deezer previews[/dim]")


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
    """Interactive guide to set up YouTube cookies."""
    console.print(Panel("[bold]YouTube Cookie Setup[/bold]"))
    console.print()
    console.print("YouTube blocks downloads from servers without cookies.")
    console.print("To fix this, export cookies from a browser where you're logged into YouTube.\n")
    console.print("[bold]Option 1: From this machine (if you have a browser)[/bold]")
    console.print("  yt-dlp --cookies-from-browser chrome --cookies ~/.anysong/cookies.txt 'https://youtube.com'\n")
    console.print("[bold]Option 2: From your laptop/desktop[/bold]")
    console.print("  1. Install yt-dlp: pip install yt-dlp")
    console.print("  2. Export: yt-dlp --cookies-from-browser chrome --cookies cookies.txt 'https://youtube.com'")
    console.print("  3. Copy: scp cookies.txt cbot@server:~/.anysong/cookies.txt\n")
    console.print("[bold]Option 3: Manual Firefox export[/bold]")
    console.print("  1. Install 'cookies.txt' browser extension")
    console.print("  2. Go to youtube.com, click the extension, export")
    console.print("  3. Save as ~/.anysong/cookies.txt\n")

    cookies_path = os.path.expanduser("~/.anysong/cookies.txt")
    if os.path.isfile(cookies_path):
        size = os.path.getsize(cookies_path)
        console.print(f"[green]✓ Cookies file found:[/green] {cookies_path} ({size} bytes)")
    else:
        console.print(f"[red]✗ No cookies file at:[/red] {cookies_path}")
        os.makedirs(os.path.expanduser("~/.anysong"), exist_ok=True)
        console.print(f"[dim]Created ~/.anysong/ directory[/dim]")


if __name__ == "__main__":
    app()
