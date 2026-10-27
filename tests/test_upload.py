"""YouTube upload with a mocked googleapiclient service (no network, no OAuth)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from historychannel.models import Scene, VideoScript, Visual
from historychannel.upload import youtube as yt
from historychannel.upload.youtube import build_description, build_video_body, upload_video


def make_script(**kw) -> VideoScript:
    base = dict(
        slug="berlin-1848",
        title="The Night Berlin Built Barricades",
        description="In March 1848 Berlin rose against its king.",
        tags=["1848", "Berlin", "March Revolution", "berlin"],
        language="de",
        scenes=[
            Scene(
                id="s01",
                narration="Cobblestones.",
                visuals=[Visual(kind="archival", query="Barrikade 1848")],
                sources=["https://example.org/a", "https://example.org/b"],
            )
        ],
        sources=["https://example.org/a"],
    )
    base.update(kw)
    return VideoScript(**base)


# --------------------------------------------------------------------------- build_description


def test_build_description_sections_and_dedupe():
    d = build_description(make_script(), attribution=["Painter, 1848, Public domain, via Wikimedia Commons"] * 2)
    assert d.startswith("In March 1848 Berlin rose against its king.")
    assert "Sources:\n1. https://example.org/a\n2. https://example.org/b" in d
    assert d.count("https://example.org/a") == 1
    assert d.count("Painter, 1848") == 1 and "Image credits:" in d
    assert d.rstrip().endswith(yt.DEFAULT_AI_DISCLOSURE)
    assert d.index("Sources:") < d.index("Image credits:") < d.index("How this video was made")


def test_build_description_strips_angle_brackets_and_fits_limit():
    many = [f"https://example.org/source/{i:04d}/" + "x" * 80 for i in range(100)]
    script = make_script(description="Kings <and> crowds", sources=many)
    credits = [f"Artist {i}, CC BY-SA 4.0, via Wikimedia Commons" for i in range(20)]
    d = build_description(script, attribution=credits)
    assert "<" not in d and ">" not in d
    assert len(d.encode("utf-8")) <= yt.DESCRIPTION_MAX_BYTES
    assert "more" in d  # sources were trimmed with a note
    assert all(c in d for c in credits)  # credits are kept before sources are
    assert yt.DEFAULT_AI_DISCLOSURE in d


def test_build_description_without_optional_sections():
    d = build_description(make_script(sources=[], scenes=[Scene(id="s", narration="n", visuals=[])]), ai_disclosure="")
    assert d == "In March 1848 Berlin rose against its king."


def test_title_and_tags_limits():
    body = build_video_body(make_script(title="A" * 60 + " " + "B" * 60, tags=["x" * 300, "y" * 300, "ok"]))
    assert len(body["snippet"]["title"]) <= 100 and body["snippet"]["title"].endswith("…")
    tags = body["snippet"]["tags"]
    assert tags == ["x" * 300]  # second tag would exceed the 500-char budget
    assert build_video_body(make_script())["snippet"]["tags"] == ["1848", "Berlin", "March Revolution"]


# --------------------------------------------------------------------------- upload_video


def _service(response=None, side_effects=None):
    svc = MagicMock()
    req = MagicMock()
    if side_effects is not None:
        req.next_chunk.side_effect = side_effects
    else:
        req.next_chunk.return_value = (None, response or {"id": "vid123", "status": {"privacyStatus": "private"}})
    svc.videos.return_value.insert.return_value = req
    cap_req = MagicMock()
    cap_req.next_chunk.return_value = (None, {"id": "cap1"})
    svc.captions.return_value.insert.return_value = cap_req
    return svc, req


@pytest.fixture
def files(tmp_path: Path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"\x00" * 1024)
    thumb = tmp_path / "thumb.png"
    thumb.write_bytes(b"\x89PNG")
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHallo\n")
    return SimpleNamespace(video=video, thumb=thumb, srt=srt, tmp=tmp_path)


def test_upload_video_request_body(files):
    svc, _ = _service()
    vid = upload_video(
        files.video,
        make_script(),
        attribution=["Painter, 1848, Public domain"],
        client_secret=files.tmp / "client_secret.json",
        token_path=files.tmp / "token.json",
        service=svc,
    )
    assert vid == "vid123"
    kwargs = svc.videos.return_value.insert.call_args.kwargs
    assert kwargs["part"] == "snippet,status"
    assert kwargs["notifySubscribers"] is False
    body = kwargs["body"]
    assert body["snippet"]["title"] == "The Night Berlin Built Barricades"
    assert body["snippet"]["categoryId"] == "27"
    assert body["snippet"]["defaultLanguage"] == "de"
    assert body["snippet"]["defaultAudioLanguage"] == "de"
    assert "Painter, 1848, Public domain" in body["snippet"]["description"]
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["status"]["containsSyntheticMedia"] is False  # archival only
    media = kwargs["media_body"]
    assert media.resumable() and media.mimetype() == "video/mp4"
    svc.thumbnails.assert_not_called()
    svc.captions.assert_not_called()


def test_upload_with_thumbnail_captions_and_generated_visuals(files):
    svc, _ = _service()
    script = make_script(
        scenes=[Scene(id="s1", narration="n", visuals=[Visual(kind="generated", prompt="a 1848 barricade")])]
    )
    upload_video(
        files.video, script, thumbnail=files.thumb, captions=files.srt, privacy="unlisted",
        client_secret=Path("x"), token_path=Path("y"), service=svc,
    )
    body = svc.videos.return_value.insert.call_args.kwargs["body"]
    assert body["status"]["containsSyntheticMedia"] is True
    assert body["status"]["privacyStatus"] == "unlisted"
    t_kwargs = svc.thumbnails.return_value.set.call_args.kwargs
    assert t_kwargs["videoId"] == "vid123" and t_kwargs["media_body"].mimetype() == "image/png"
    c_kwargs = svc.captions.return_value.insert.call_args.kwargs
    assert c_kwargs["body"]["snippet"] == {"videoId": "vid123", "language": "de", "name": "", "isDraft": False}


def test_upload_retries_on_5xx(files, monkeypatch):
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    err = HttpError(SimpleNamespace(status=503, reason="busy"), b"busy")
    progress = SimpleNamespace(progress=lambda: 0.5)
    svc, req = _service(side_effects=[err, (progress, None), (None, {"id": "v2"})])
    assert upload_video(files.video, make_script(), client_secret=Path("x"), token_path=Path("y"), service=svc) == "v2"
    assert req.next_chunk.call_count == 3


def test_upload_does_not_retry_4xx(files, monkeypatch):
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    err = HttpError(SimpleNamespace(status=403, reason="quotaExceeded"), b"quota")
    svc, req = _service(side_effects=[err])
    with pytest.raises(HttpError):
        upload_video(files.video, make_script(), client_secret=Path("x"), token_path=Path("y"), service=svc)
    assert req.next_chunk.call_count == 1


def test_thumbnail_failure_does_not_fail_upload(files):
    svc, _ = _service()
    svc.thumbnails.return_value.set.return_value.execute.side_effect = HttpError(
        SimpleNamespace(status=403, reason="forbidden"), b"no"
    )
    assert upload_video(files.video, make_script(), thumbnail=files.thumb,
                        client_secret=Path("x"), token_path=Path("y"), service=svc) == "vid123"


def test_invalid_privacy_and_missing_video(files):
    with pytest.raises(ValueError):
        build_video_body(make_script(), privacy="secret")
    with pytest.raises(yt.UploadError):
        upload_video(files.tmp / "nope.mp4", make_script(), client_secret=Path("x"), token_path=Path("y"),
                     service=MagicMock())


def test_missing_client_secret_message(tmp_path):
    with pytest.raises(yt.UploadError, match="client secret"):
        yt.get_credentials(tmp_path / "client_secret.json", tmp_path / "token.json", [yt.SCOPE_UPLOAD])


def test_captions_request_force_ssl_scope(files, monkeypatch):
    seen = {}

    def fake_creds(cs, tp, scopes):
        seen["scopes"] = scopes
        return object()

    svc, _ = _service()
    monkeypatch.setattr(yt, "get_credentials", fake_creds)
    monkeypatch.setattr(yt, "build_service", lambda c: svc)
    upload_video(files.video, make_script(), captions=files.srt, client_secret=Path("x"), token_path=Path("y"))
    assert seen["scopes"] == [yt.SCOPE_UPLOAD, yt.SCOPE_FORCE_SSL]
    upload_video(files.video, make_script(), client_secret=Path("x"), token_path=Path("y"))
    assert seen["scopes"] == [yt.SCOPE_UPLOAD]
