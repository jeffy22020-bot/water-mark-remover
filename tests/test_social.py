import yt_dlp

from app.core import social


def _select(formats):
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        chosen = ydl.build_format_selector(social.FORMAT)({"formats": formats, "has_merged_format": True, "incomplete_formats": False})
    return [f["format_id"] for f in chosen]


def test_tiktok_picks_unwatermarked_h264():
    formats = [
        {"format_id": "download", "format_note": "watermarked", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "url": "a", "height": 1280, "protocol": "https"},
        {"format_id": "play_h264", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "url": "b", "height": 1024, "protocol": "https"},
        {"format_id": "play_hevc", "ext": "mp4", "vcodec": "h265", "acodec": "aac", "url": "c", "height": 1920, "protocol": "https"},
    ]
    assert _select(formats) == ["play_h264"]


def test_falls_back_to_watermarked_when_nothing_else():
    formats = [{"format_id": "download", "format_note": "watermarked", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "url": "a", "protocol": "https"}]
    assert _select(formats) == ["download"]


def test_extract_url_from_share_text():
    text = "Check this out! https://vm.tiktok.com/ZMabc123/ #fyp"
    assert social.extract_url(text) == "https://vm.tiktok.com/ZMabc123/"
    assert social.extract_url("no link here") is None


def test_friendly_errors():
    assert "logged in" in social.friendly_error(Exception("ERROR: This video is private. Use --cookies"))
    assert "supported" in social.friendly_error(Exception("ERROR: Unsupported URL: https://x"))
