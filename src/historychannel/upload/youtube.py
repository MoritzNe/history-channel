"""Upload a rendered video to YouTube via the YouTube Data API v3.

Setup (Google Cloud project, OAuth client, consent screen, quota, audit caveat) is in
docs/tools-upload.md. Videos are uploaded **private** by default; flip them public in
YouTube Studio after checking them, or pass `privacy="public"`.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any, Iterable

from historychannel.models import VideoScript

log = logging.getLogger(__name__)

SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
# captions.insert requires youtube.force-ssl (youtube.upload is not enough).
SCOPE_FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"

CATEGORY_EDUCATION = "27"
PRIVACY_VALUES = ("private", "unlisted", "public")

TITLE_MAX = 100  # characters
DESCRIPTION_MAX_BYTES = 5000  # YouTube limit is 5000 bytes (UTF-8)
TAGS_MAX_CHARS = 500  # total; a tag containing a space counts as if wrapped in quotes

CHUNK_SIZE = 8 * 1024 * 1024
MAX_RETRIES = 10
RETRIABLE_STATUS = (500, 502, 503, 504)

DEFAULT_AI_DISCLOSURE = (
    "How this video was made: the script was researched and drafted with the help of an AI "
    "model (Anthropic Claude) and fact-checked against the sources listed above; the "
    "narration uses a synthetic (text-to-speech) voice. Historical images are archival and "
    "credited above unless marked otherwise."
)


class UploadError(RuntimeError):
    pass


# --------------------------------------------------------------------------- metadata


def _clean(text: str) -> str:
    # YouTube rejects '<' and '>' in titles, descriptions and tags.
    return text.replace("<", "‹").replace(">", "›")


def build_title(script: VideoScript) -> str:
    title = " ".join(_clean(script.title).split())
    if len(title) <= TITLE_MAX:
        return title
    cut = title[: TITLE_MAX - 1]
    if " " in cut[TITLE_MAX // 2 :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-") + "…"


def build_tags(tags: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for t in tags:
        t = " ".join(_clean(t).replace(",", " ").split())
        if not t or t.lower() in seen:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if total + cost > TAGS_MAX_CHARS:
            break
        out.append(t)
        seen.add(t.lower())
        total += cost
    return out


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        i = i.strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _nbytes(s: str) -> int:
    return len(s.encode("utf-8"))


def build_description(
    script: VideoScript,
    *,
    attribution: list[str] | None = None,
    ai_disclosure: str = DEFAULT_AI_DISCLOSURE,
    max_bytes: int = DESCRIPTION_MAX_BYTES,
) -> str:
    """Compose the YouTube description. Pure function (no I/O).

    Layout: script.description, "Sources" (script + scene sources, de-duplicated), "Image
    credits" (one line per attribution), then the AI-disclosure line. If the result exceeds
    YouTube's 5000-byte limit, sources (then, only if still too long, credits) are trimmed
    from the end with a "...and N more" note; the description and the disclosure are kept.
    """
    sources = _dedupe([*script.sources, *(u for s in script.scenes for u in s.sources)])
    credits = _dedupe(attribution or [])
    head = _clean(script.description.strip())
    tail = _clean(ai_disclosure.strip())

    def compose(n_src: int, n_cred: int) -> str:
        parts = [head] if head else []
        if sources:
            lines = [f"{i}. {_clean(u)}" for i, u in enumerate(sources[:n_src], 1)]
            if n_src < len(sources):
                lines.append(f"…and {len(sources) - n_src} more")
            parts.append("Sources:\n" + "\n".join(lines))
        if credits:
            lines = [f"• {_clean(c)}" for c in credits[:n_cred]]
            if n_cred < len(credits):
                lines.append(f"…and {len(credits) - n_cred} more")
            parts.append("Image credits:\n" + "\n".join(lines))
        if tail:
            parts.append(tail)
        return "\n\n".join(parts)

    n_src, n_cred = len(sources), len(credits)
    text = compose(n_src, n_cred)
    while _nbytes(text) > max_bytes and (n_src or n_cred):
        # Trim sources first: image credits are often a licence requirement (CC BY/BY-SA).
        if n_src:
            n_src -= 1
        else:
            n_cred -= 1
        text = compose(n_src, n_cred)
    if _nbytes(text) > max_bytes:  # head alone is too long: truncate it, keep the disclosure
        budget = max_bytes - _nbytes("\n\n" + tail) - 3
        head = head.encode("utf-8")[: max(budget, 0)].decode("utf-8", "ignore").rstrip() + "…"
        text = compose(0, 0)
    return text


def contains_synthetic_media_default(script: VideoScript) -> bool:
    """True if the visual plan includes AI-generated images (realistic synthetic scenes).

    YouTube asks creators to disclose *realistic* altered or synthetic content (e.g. a
    generated scene that looks like real footage/photos of an event). A synthetic narrator
    voice over archival images is generally not in scope, but generated imagery of
    historical scenes is, so we flag it whenever any `generated` visual is planned.
    """
    return any(v.kind == "generated" for s in script.scenes for v in s.visuals)


def build_video_body(
    script: VideoScript,
    *,
    privacy: str = "private",
    attribution: list[str] | None = None,
    contains_synthetic_media: bool | None = None,
    ai_disclosure: str = DEFAULT_AI_DISCLOSURE,
) -> dict[str, Any]:
    """The `videos.insert` request body (parts: snippet, status)."""
    if privacy not in PRIVACY_VALUES:
        raise ValueError(f"privacy must be one of {PRIVACY_VALUES}, got {privacy!r}")
    if contains_synthetic_media is None:
        contains_synthetic_media = contains_synthetic_media_default(script)
    return {
        "snippet": {
            "title": build_title(script),
            "description": build_description(script, attribution=attribution, ai_disclosure=ai_disclosure),
            "tags": build_tags(script.tags),
            "categoryId": CATEGORY_EDUCATION,
            "defaultLanguage": script.language,
            "defaultAudioLanguage": script.language,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            # Added to the Data API on 2024-10-30: discloses realistic altered/synthetic
            # (A/S) content. Settable on videos.insert and videos.update.
            "containsSyntheticMedia": bool(contains_synthetic_media),
            "embeddable": True,
            "license": "youtube",
        },
    }


# --------------------------------------------------------------------------- auth


def get_credentials(client_secret: Path, token_path: Path, scopes: list[str]) -> Any:
    """Installed-app OAuth flow with the token cached at `token_path`.

    The first run opens a browser for consent; later runs reuse/refresh the cached token.
    If the cached token lacks a needed scope (e.g. captions were enabled later), the
    consent flow runs again.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = Path(token_path)
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        if creds and not set(scopes).issubset(set(creds.scopes or [])):
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:  # revoked / expired refresh token (7 days for "Testing" apps)
            log.warning("Token refresh failed (%s); re-running OAuth consent", e)
            creds = None
    if not creds or not creds.valid:
        client_secret = Path(client_secret)
        if not client_secret.exists():
            raise UploadError(
                f"OAuth client secret not found at {client_secret}. See docs/tools-upload.md "
                "(Google Cloud -> Credentials -> OAuth client ID -> Desktop app -> Download JSON)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes)
        creds = flow.run_local_server(port=0, prompt="consent")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    return creds


def build_service(credentials: Any) -> Any:
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


# --------------------------------------------------------------------------- upload


def _is_retriable(exc: BaseException) -> bool:
    from googleapiclient.errors import HttpError

    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None) in RETRIABLE_STATUS
    return isinstance(exc, OSError)  # ConnectionError, TimeoutError, socket errors


def _resumable(request: Any, *, what: str, sleep=time.sleep) -> dict[str, Any]:
    """Drive a resumable upload request to completion with exponential backoff."""
    response = None
    retries = 0
    last_pct = -1
    while response is None:
        try:
            status, response = request.next_chunk()
            if status is not None:
                pct = int(status.progress() * 100)
                if pct // 10 != last_pct // 10:
                    log.info("%s: %d%%", what, pct)
                    last_pct = pct
            retries = 0
        except Exception as e:
            if not _is_retriable(e):
                raise
            retries += 1
            if retries > MAX_RETRIES:
                raise UploadError(f"{what}: giving up after {MAX_RETRIES} retries: {e}") from e
            delay = min(2**retries, 64) * random.random() + 1
            log.warning("%s: retriable error (%s), retry %d in %.1fs", what, e, retries, delay)
            sleep(delay)
    return response


def upload_video(
    video: Path,
    script: VideoScript,
    *,
    thumbnail: Path | None = None,
    privacy: str = "private",
    attribution: list[str] = [],  # noqa: B006 - never mutated
    client_secret: Path,
    token_path: Path,
    captions: Path | None = None,
    caption_language: str | None = None,
    contains_synthetic_media: bool | None = None,
    ai_disclosure: str = DEFAULT_AI_DISCLOSURE,
    service: Any = None,
) -> str:
    """Upload `video` with metadata from `script`; returns the new YouTube video id.

    * `privacy` defaults to "private".
    * `thumbnail`: set via thumbnails.set (needs a phone-verified channel; failure is logged,
      not raised, since the video itself is already up).
    * `captions`: an .srt uploaded via captions.insert; requests the extra
      `youtube.force-ssl` scope. Failure is logged, not raised.
    * `contains_synthetic_media`: None = auto (True if the script uses generated images).
    * `service`: inject a prebuilt googleapiclient Resource (tests); skips OAuth.
    """
    from googleapiclient.http import MediaFileUpload

    video = Path(video)
    if not video.exists():
        raise UploadError(f"Video file not found: {video}")
    body = build_video_body(
        script,
        privacy=privacy,
        attribution=list(attribution),
        contains_synthetic_media=contains_synthetic_media,
        ai_disclosure=ai_disclosure,
    )

    if service is None:
        scopes = [SCOPE_UPLOAD] + ([SCOPE_FORCE_SSL] if captions else [])
        service = build_service(get_credentials(Path(client_secret), Path(token_path), scopes))

    mime = "video/mp4" if video.suffix.lower() == ".mp4" else "application/octet-stream"
    media = MediaFileUpload(str(video), mimetype=mime, chunksize=CHUNK_SIZE, resumable=True)
    request = service.videos().insert(
        part="snippet,status", body=body, media_body=media, notifySubscribers=privacy == "public"
    )
    response = _resumable(request, what=f"upload {video.name}")
    video_id = response.get("id")
    if not video_id:
        raise UploadError(f"Upload finished without a video id: {response}")
    log.info("uploaded https://youtu.be/%s", video_id)

    got = (response.get("status") or {}).get("privacyStatus")
    if got and got != privacy:
        log.warning(
            "Requested privacy %r but YouTube set %r. Uploads from unaudited API projects can be "
            "locked to private; see docs/tools-upload.md.", privacy, got,
        )

    if thumbnail:
        _set_thumbnail(service, video_id, Path(thumbnail))
    if captions:
        _insert_captions(service, video_id, Path(captions), caption_language or script.language)
    return video_id


def _set_thumbnail(service: Any, video_id: str, thumbnail: Path) -> None:
    from googleapiclient.http import MediaFileUpload

    if not thumbnail.exists():
        log.warning("thumbnail %s not found; skipping", thumbnail)
        return
    mime = "image/png" if thumbnail.suffix.lower() == ".png" else "image/jpeg"
    try:
        req = service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail), mimetype=mime))
        req.execute()
        log.info("thumbnail set")
    except Exception as e:
        log.warning("thumbnails.set failed (custom thumbnails need a verified channel): %s", e)


def _insert_captions(service: Any, video_id: str, srt: Path, language: str) -> None:
    from googleapiclient.http import MediaFileUpload

    if not srt.exists():
        log.warning("captions %s not found; skipping", srt)
        return
    body = {"snippet": {"videoId": video_id, "language": language, "name": "", "isDraft": False}}
    try:
        req = service.captions().insert(
            part="snippet",
            body=body,
            media_body=MediaFileUpload(str(srt), mimetype="application/octet-stream", resumable=True),
        )
        _resumable(req, what="captions")
        log.info("captions uploaded (%s)", language)
    except Exception as e:
        log.warning("captions.insert failed: %s", e)
