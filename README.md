# Watermark Remover

A Windows desktop app that removes watermarks from **images, videos, PDFs, PowerPoint and Word files**. It can also **download social-media videos (TikTok, Instagram, YouTube, …) without the watermark**.

Everything runs on your own PC. Nothing is uploaded, there are no accounts and no limits. The AI works offline.

| Tab | What it does |
| --- | --- |
| **Image watermark remover** | Paint over a watermark, or press **Auto-detect**, then the AI (LaMa) rebuilds the picture underneath. Works on JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF and AVIF. Batch mode cleans many photos at once. |
| **Video watermark remover** | Finds logos and text that stay in place while the video moves, including TikTok-style marks that jump between corners. Removes them from every frame and keeps the sound. Works on MP4, MOV, WEBM, MKV, AVI, WMV, FLV, MPG, 3GP and GIF. |
| **PDF watermark remover** | Finds watermark text, stamps, logos and watermark layers, and deletes just those objects, so the real text stays sharp and selectable. **Paint mode** handles scanned PDFs. |
| **PowerPoint & Word** | Removes "Confidential", "Draft", "Evaluation Warning" and logo shapes from slide masters, slides and page headers. It can also clean watermarks that are part of a picture inside the file. Works on .pptx and .docx; .ppt and .doc work when LibreOffice is installed. |
| **Social media downloader** | Paste a TikTok, Instagram, YouTube, Facebook, X, Reddit, Pinterest, Vimeo or Douyin link to save the clean video. If it still has a logo burned in, **Remove watermark** sends it to the video tool. |

> Only remove watermarks from content you own or have permission to edit.

## Install (Windows 10/11, 64-bit)

1. Open the repository's **Actions** tab on GitHub, then the latest **Build Windows installer** run.
2. Download the **WatermarkRemover-Setup** artifact and unzip it.
   - If someone has published a release (`git tag v1.0.0 && git push --tags`), the installer is also attached to the **Releases** page.
3. Run `WatermarkRemover-Setup-1.0.0.exe`.
   - The installer isn't code-signed, so Windows SmartScreen may say "Windows protected your PC". Click **More info → Run anyway**.
   - It installs for your user account without needing admin rights, and adds Start-menu and (optionally) desktop shortcuts.
   - If Microsoft Edge WebView2 is missing (rare on Windows 10/11), the installer adds it.

The installer is about 300 MB because it includes the AI models and ffmpeg.

### Graphics card (GPU) acceleration

The AI runs on any DirectX 12 graphics card (NVIDIA, AMD or Intel) through DirectML, and falls back to the processor otherwise. The top bar shows where it runs ("AI: GPU (DirectML)" or "AI: CPU").

- **Images:** a few seconds on any PC.
- **Videos on a GPU:** several frames per second.
- **Videos on a CPU:** about 1–3 seconds per frame in AI mode. Use **Fast** mode for quick results on small logos.

## Tips

- **Auto-detect** finds text watermarks, including faint tiled "© stock" patterns. For logos, paint over them with the brush or draw a box.
- Something left behind? In the result view, click **Touch up**, paint over the leftover and run again.
- **TikTok-style moving watermarks:** in the video tool, Auto-detect creates one "area" per position, each with its own time range. You can also add areas yourself with **Add area from here** and set **Start here / End here** at the current playback position.
- **Logged-in content:** for private or login-only videos, open **Settings → Use my browser login** and choose the browser you're logged in with.
- **Downloads stopped working?** Sites change often. Click **Settings → Update downloader**, then restart the app.
- **Keyboard shortcuts:** Ctrl+O open · B brush · R rectangle · E eraser · H move · [ and ] brush size · Ctrl+Z / Ctrl+Y undo/redo · hold Space to pan · mouse wheel to zoom · Ctrl+V pastes an image or link.

## Limits

- No tool removes every watermark perfectly. Large semi-transparent overlays on very detailed backgrounds, and watermarks that move every frame, may need a manual touch-up.
- Auto-detect can mark real text in a photo (for example a sign). Check the red area and erase what should stay.
- The PowerPoint/Word tool can't show slide previews. It lists the watermark shapes it found, with their text or picture.
- YouTube sometimes limits downloads to lower qualities unless a JavaScript runtime is installed (a yt-dlp requirement). TikTok and Instagram are not affected.

## Run from source / develop

The app needs Python 3.12. `run.bat` creates a virtual environment, installs everything, downloads the models, and starts the app. To do the same by hand:

```bash
pip install -r requirements-dev.txt
python scripts/fetch_models.py      # AI models (~95 MB, SHA-256 verified)
python scripts/make_samples.py      # sample files for "Try one of these" and the tests
python run_app.py                   # desktop window (Windows); add --browser to use a web browser
python -m pytest -q                 # tests
python run_app.py --self-test       # end-to-end check (also run on the built .exe in CI)
```

To build the installer yourself on Windows:

1. Run `pyinstaller --noconfirm packaging/WatermarkRemover.spec`.
2. Download the [WebView2 bootstrapper](https://go.microsoft.com/fwlink/p/?LinkId=2124703) to `packaging/MicrosoftEdgeWebview2Setup.exe`.
3. Run `iscc packaging/installer.iss` (needs Inno Setup 6).

The GitHub workflow `.github/workflows/build-windows.yml` does all of this on every push.

### How it works

```
app/
  main.py          starts a local server on 127.0.0.1 (random port + secret token) and opens it in a WebView2 window
  server.py        HTTP API used by the UI
  core/inpaint.py  LaMa ONNX inpainting (DirectML/CPU) in 512px tiles + OpenCV "Fast" mode
  core/detect.py   text-watermark detection (PP-OCRv3 DB) and static-overlay detection for videos/batches
  core/video.py    ffmpeg decode → inpaint per frame (with a temporal cache against flicker) → x264 encode
  core/pdf.py      splits PDF content streams into objects and deletes the watermark ones (pikepdf, pypdfium2)
  core/office.py   finds and deletes watermark shapes in .pptx/.docx; swaps cleaned pictures back in
  core/social.py   yt-dlp with a format selector that skips watermarked TikTok/Douyin files
  web/             the user interface (HTML/CSS/JavaScript, no build step)
```

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the licenses of bundled components.
