# VidToAudio

Paste a YouTube or Bilibili link, pick the part you want, compare cleaned-up
takes side by side, fine-tune the one you like, add the song details and a
cover, and save a song file ready for Spotify's Local Files.

It runs on your Mac as a small local web page. Nothing is uploaded anywhere.

## Start

Double-click **`VidToAudio.command`**. The first time it installs what it
needs (`setup.sh`: Homebrew ffmpeg, Python 3.12 and a private `.venv`, a few
minutes). After that it opens `http://127.0.0.1:8770` in your browser.
Double-clicking again while it's running just reopens the page. Close the
Terminal window to stop it.

## How it works

1. **Video:** paste the link and press *Get audio*. For a video that's
   already on your Mac, drop it into **`~/Movies/VidToAudio`** instead. It
   appears under the link box within a few seconds; click it. Only the sound
   is copied out, so big `.mov` files are fine. The folder sits outside
   `~/Music` on purpose, so Spotify never lists raw videos. Using a link or
   file you've used before reopens your earlier work instead of starting over.
   Drag the two handles on
   the waveform (or type times like `0:08` and `2:58.5`) to choose the part
   you want. *Hear the start* and *Hear the last 5 seconds* check the cut
   points. Fade in and out avoid clicks where you cut mid-song.
2. **Takes:** *Make test takes* separates your part into voice, echo,
   drums, bass and everything else (crowd, room, keys, guitars). That takes
   about a minute with *Standard*, or about 3 minutes with *Best quality*.
   Then press play and switch takes while it plays: click a take or press
   **A–I**. Every take is matched in loudness, so you hear the difference
   in processing, not volume. Hold **O** (or the *Hold for original* button)
   to hear the untouched audio. Drag the shaded band to audition another
   30 seconds.
3. **Fine-tune:** the sliders start from the take you picked and change the
   sound live. What you hear is exactly what gets saved. The browser and
   the export were checked against each other sample by sample, see
   `tests/parity.py`. *Save this 30-second clip* writes a test file if you
   want to listen elsewhere first.
4. **Song details:** song name, artist, album and cover. Artists and albums
   you've used before are suggested. Drop, paste or choose a picture, or use
   the video thumbnail. Drag to position the square, zoom to crop tighter, or
   *Fit, blur the edges* to keep a whole portrait photo.
5. **Save:** *Make the full song* renders your whole part with those
   settings and writes the file with its tags and cover.

Each song's download and stems stay in `work/`, so you can come back later
(the *Recent songs* menu), try a different take, and save again without
downloading or separating again. *Delete working files* on a saved song
frees the space (about 130 MB per song).

### The takes

The numbers follow what you picked in September ("other at 65", "deverb 55").

| | Take | What it does |
|---|---|---|
| A | Original | Untouched, only level-matched. The reference. |
| B | Light crowd cut | Crowd & backing 80% |
| C | Crowd cut | Crowd & backing 65%, your most-picked level |
| D | Less echo | Echo on the voice kept at 55% |
| E | Less echo + crowd cut | Echo 55%, crowd 65%. Your Whale Store / Please Be Mine pick. |
| F | Voice forward | Voice +3 dB, crowd 80%, clarity +1.5 dB |
| G | Smooth the highs | −5 dB at 6.5 kHz, air −3 dB. Your Please Be Mine fix. |
| H | Open up dull audio | Low end −4, clarity +3, air +5 dB. Your Hook fix. |
| I | Strong clean-up | Crowd 50%, echo 45%, width 85% |

The diagnosis above the takes measures the part you chose and marks up to
three takes as **Suggested**. It flags crowd share, echo, clipping, stinging or
muffled highs, low-quality uploads and fake-wide stereo. It's a starting point; your ears decide.

**Loudness:** *Natural* (−13 LUFS) keeps the live dynamics. *Match September*
(−11.5) matches the files we made in September. *Spotify level* (−14) is
Spotify's own normal level. Peaks are always kept under −1 dB, so songs that
clipped in the source stop clipping.

## Covers for Spotify Local Files

What the app writes, and why:

- **MP3 (320 kbps) is the default.** Spotify's desktop app shows embedded
  art for both MP3 and M4A, but its phone apps are widely reported to skip
  the art on M4A files while showing it on MP3s. Spotify's own help page
  no longer lists supported formats, so MP3 is the safe choice.
- **Square.** Spotify shows covers as squares and crops anything else
  itself. Cropping here lets you choose what stays in the picture.
- **640 × 640 baseline JPEG, sRGB, about 50–150 KB.** 640 px is the largest
  size Spotify serves for its own album art, so a bigger picture gains
  nothing. Very large or progressive JPEGs and PNGs are the usual suspects
  when local-file art doesn't show.
- **One picture, marked as the front cover, in ID3 v2.3 tags.** Some files in
  `~/Music/GMM Songs` carry five embedded pictures and ID3 v2.2 tags. Players
  disagree about which picture to show in files like that.
- **Album and album artist set** so Spotify groups the songs together.

To add them in Spotify on the Mac: Settings → Your Library → turn on
*Show Local Files* → *Add a source* → your save folder (default
`~/Music/VidToAudio`). To get them on your phone, add the songs to a
playlist and download that playlist on the phone while it's on the same
Wi-Fi as the Mac.

If Spotify keeps showing an old cover after you replace a file, remove and
re-add the folder source. Spotify caches local-file art.

## Under the hood

- `vta/fetch.py`: yt-dlp with the September fixes. It sends browser headers
  and backs off when Bilibili refuses with HTTP 412, takes only the video
  from YouTube radio (`&list=RD…`) links, and skips YouTube's
  dynamic-range-compressed audio copies.
- `vta/separate.py`: Demucs (htdemucs / htdemucs_ft) for voice, drums and
  bass, then UVR-DeEcho-DeReverb on the voice. Two stems are taken by
  subtraction (other = original − voice − drums − bass, echo = voice − dry
  voice), so all stems at 100% add up to the original exactly. It refuses
  silent results, the failure the MelBand Roformer models showed in September.
- `vta/mix.py`: stem gains → mid/side width → RBJ EQ (110 Hz, 300 Hz,
  2.8 kHz, 6.5 kHz, 10 kHz) → two-pass loudness → limiter → MP3 (LAME) or
  M4A (Apple AAC). `web/engine.js` runs the same chain in Web Audio.
- `vta/analyze.py`: the diagnosis. Its thresholds were calibrated on a live
  fancam against a studio master.

## Tests

```sh
.venv/bin/python -m unittest discover tests     # core, no network (15 checks)
VTA_WORK=work .venv/bin/python tests/parity.py   # browser vs export, needs Chrome + a prepared song
```

## Troubleshooting

- **A link stops working:** sites change. The launcher updates yt-dlp on each
  start; restart the app.
- **Bilibili refuses repeatedly:** the app already retries with pauses. Wait a
  few minutes; it's rate-limiting.
- **Separation stops with an error:** try again, or switch between Standard and
  Best quality.

This is for personal listening. Respect the uploaders and the sites' terms.
