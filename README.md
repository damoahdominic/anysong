# 🎵 anysong

Download any song as a properly named MP3. One command.

```bash
anysong "Lil Wayne Lollipop"
# → ~/music/lollipop_by_lil_wayne.mp3
```

## How It Works

1. **Search Deezer** (free API) for clean metadata — title, artist, album
2. **Download from YouTube** using yt-dlp (falls back to SoundCloud if YouTube blocks)
3. **Clean filename** — `title_by_artist.mp3`, no garbage characters

## Install

```bash
git clone https://github.com/damoahdominic/anysong.git
cd anysong
pip install -r requirements.txt

# Optional: install deno for best yt-dlp YouTube support
curl -fsSL https://deno.land/install.sh | sh
```

## Usage

```bash
# Download a song
./anysong download "Lil Wayne Lollipop"

# Download to specific directory
./anysong download "Wild Thoughts Rihanna" --dir ~/Music

# Browse results before downloading
./anysong download "Bohemian Rhapsody" --pick

# Search without downloading
./anysong search "Drake" --limit 10

# Batch download from a text file
./anysong batch playlist.txt --dir ~/Music
```

### Batch File Format
```
# playlist.txt — one song per line
Lil Wayne Lollipop
Rihanna Wild Thoughts
Drake Hotline Bling
Queen Bohemian Rhapsody
```

## YouTube Cookies (Optional)

If YouTube blocks downloads, export your browser cookies once:

```bash
mkdir -p ~/.anysong
yt-dlp --cookies-from-browser chrome --cookies ~/.anysong/cookies.txt "https://youtube.com"
```

anysong will pick them up automatically. Without cookies, it falls back to SoundCloud.

## Output

Songs are saved to `~/music/` by default with clean filenames:

```
~/music/
├── lollipop_by_lil_wayne.mp3
├── wild_thoughts_by_rihanna.mp3
├── hotline_bling_by_drake.mp3
└── bohemian_rhapsody_by_queen.mp3
```

## Tech

- **Deezer API** — Free, no auth. Provides accurate metadata (title, artist, album, duration)
- **yt-dlp** — Downloads audio from YouTube and SoundCloud
- **Rich** — Pretty CLI output
- **Typer** — CLI framework

## License

MIT
