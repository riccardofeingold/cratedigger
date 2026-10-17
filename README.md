# Crate Digger

One search bar for YouTube, Jamendo and the Internet Archive's netlabel collection. Preview tracks, then download them into a DJ-ready folder, tagged with title, artist, cover art, BPM and key (with Camelot codes for harmonic mixing).

It's a Python app with a browser interface: start it and it opens at `http://127.0.0.1:8765`.

## Set up (once)

You need Python 3.10 or newer.

```bash
cd crate-digger
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

For YouTube, also install **Deno** from [deno.com](https://deno.com) (macOS: `brew install deno`). yt-dlp needs a JavaScript runtime to get audio from YouTube, and Deno is the one it uses by default.

Optional, for Jamendo: create a free app at [devportal.jamendo.com](https://devportal.jamendo.com), copy its client ID and paste it into Settings in the app.

## Run

```bash
python -m cratedigger            # opens your browser
python -m cratedigger --port 9000 --no-browser
```

Press Ctrl+C in the terminal to quit. `pip install -e .` also gives you a `cratedigger` command.

## Using it

**Dig** searches every source at once, or just one. The play button previews a track; YouTube previews play in YouTube's own embedded player. **Download** saves the track into your crate. YouTube audio is converted to 320 kbps MP3; Jamendo and Internet Archive tracks come as MP3, or FLAC if you turn on *Prefer FLAC* in Settings.

**Crate** lists everything you've collected, with BPM and key detected after each download.

- Sort by any column. Filter by name, by BPM (type `124` to see 122–126) or by Camelot key (`8A`).
- In the **⋯** menu on a track:
  - **Find tracks that mix with this** shows tracks in the same or neighbouring Camelot key, within ±6% BPM. Half and double time count too.
  - **Double/Halve the BPM** fixes half-time readings.
  - You can also re-detect BPM and key, show the file in your folder, or delete it.
- **Copy credits** copies "Artist – Title (License) link" for the tracks shown, ready to paste under a mix you publish.
- **Rescan folder** picks up files you added to the folder yourself. BPM and key tags already set by your DJ software are kept.

Keyboard: `/` jumps to search, Space plays or pauses, ←/→ seek 10 s (hold Shift for 30 s).

## With your DJ software

Point rekordbox, Serato, Traktor or Mixxx at the library folder (default `~/Music/Crate Digger`, changeable in Settings). Tags are written the way DJ software reads them:

- MP3/WAV get ID3v2.3 tags, including `TBPM` and `TKEY`.
- FLAC/Ogg get `BPM` and `INITIALKEY`.
- Cover art is embedded.

Your DJ software will still build its own beat grid; Crate Digger's BPM and key are for browsing and planning sets before you import.

## Sources

| Source | What you get | Needs |
| --- | --- | --- |
| YouTube | Anything on YouTube, via yt-dlp. *Creative Commons videos only* limits results to videos their uploaders released for reuse. | yt-dlp, Deno, ffmpeg |
| Jamendo | Independent artists, all Creative Commons licensed | free client ID |
| Internet Archive | The netlabels collection: Creative Commons electronic releases, often with FLAC | nothing |

The License column shows what each track allows. YouTube's terms only permit downloading where YouTube or the uploader allows it, and copyright still applies to whatever you download.

## How accurate is BPM and key detection?

- **BPM:** usually within 0.1 BPM on steady electronic music. The typical mistake is half or double time (drum & bass read as 87 instead of 174); use *Double the BPM*.
- **Key:** a standard pitch-profile estimate. When it's wrong, it's usually the relative major or minor, which sits next to the right key on the Camelot wheel. Treat it as a guide.

## Troubleshooting

- **"YouTube wants a sign-in check"** or an age-restricted video: in Settings, choose the browser you're signed in to YouTube with.
- **YouTube downloads start failing:** YouTube changes often. Run `pip install -U "yt-dlp[default]"` and make sure Deno is installed. Settings → Helpers shows what's missing.
- **"This page belongs to an earlier session":** reload the tab. Each run uses a new security token.
- **Where's my data?** Settings and the crate database live in `~/Library/Application Support/CrateDigger` (macOS), `%APPDATA%\CrateDigger` (Windows) or `~/.config/cratedigger` (Linux). Set `CRATEDIGGER_HOME` to use another folder.

The app only listens on 127.0.0.1. Every request must carry a per-run token, and the Host header is checked, so other websites open in your browser can't control it.

## Tests

```bash
python -m unittest discover -s tests -t .
```

The tests use synthetic audio and a local test server, with no internet needed. They need ffmpeg; imageio-ffmpeg provides it.

The Jost typeface is included under the SIL Open Font License (`cratedigger/static/fonts/OFL.txt`).
