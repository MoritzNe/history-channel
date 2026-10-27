# YouTube upload (`historychannel.upload.youtube`)

```python
from historychannel.upload.youtube import upload_video
video_id = upload_video(
    Path("build/berlin-1848/final.mp4"), script,
    thumbnail=Path("build/berlin-1848/thumb.png"),      # optional
    captions=Path("build/berlin-1848/final.srt"),        # optional, needs the force-ssl scope
    attribution=[a.attribution for a in assets if a.attribution],
    client_secret=Path("client_secret.json"), token_path=Path(".cache/youtube_token.json"),
)  # privacy defaults to "private"
```

What gets sent (`videos.insert`, parts `snippet,status`):

- `snippet.title`: `script.title`, at most 100 characters (cut at a word boundary), `<` and `>` replaced.
- `snippet.description`: from `build_description()`: `script.description`, a numbered **Sources** list (script and scene sources, de-duplicated), **Image credits** (one line per attribution), then an AI-disclosure line. Kept under YouTube's 5,000-byte limit by trimming sources first; image credits (often a CC BY/BY-SA licence requirement) are trimmed only as a last resort.
- `snippet.tags` (at most 500 characters in total), `categoryId` `27` (Education), `defaultLanguage` and `defaultAudioLanguage` set to `script.language`.
- `status.privacyStatus` (default **`private`**), `status.selfDeclaredMadeForKids: false`, `status.containsSyntheticMedia` (see below).
- Resumable upload in 8 MB chunks; HTTP 500/502/503/504 and connection errors are retried with exponential backoff (up to 10 times). Other errors (e.g. 403 `quotaExceeded`) raise immediately.
- Then, optionally, `thumbnails.set` and `captions.insert` (the `.srt`, track name empty, not a draft). Failures of these two are logged as warnings rather than raised, because the video is already uploaded.

## Altered or synthetic content flag

The field is **`status.containsSyntheticMedia`** (boolean). The YouTube Data API revision history entry of **30 October 2024** added it: "To indicate whether a video contains A/S content, set the `status.containsSyntheticMedia` property." It can be set on `videos.insert` and `videos.update`. The `videos` resource docs describe it as letting "the channel owner to disclose that a video contains realistic Altered or Synthetic (A/S) content."

YouTube's disclosure policy is about **realistic** content a viewer could mistake for real: a real person made to say or do something they didn't, altered footage of real events, or realistic generated scenes. A synthetic narrator voice over archival images generally isn't in scope; generated "photo-like" images of historical scenes are. So `upload_video(contains_synthetic_media=None)` (the default) sets the flag to `True` whenever the script plans any `generated` visual, and `False` otherwise. Pass `True` or `False` to override. The description always carries a plain-language AI-disclosure line regardless (`ai_disclosure=` to change it).

Related, from the 15 October 2026 revision note: `selfDeclaredMadeForKids` is only updated when it's in the request, so we always send it explicitly.

## One-time setup

1. **Google Cloud project**: https://console.cloud.google.com/ → create a project (e.g. "history-channel").
2. **Enable the API**: APIs & Services → Library → "YouTube Data API v3" → Enable.
3. **OAuth consent screen** (Google Auth Platform → Branding / Audience): user type **External**, app name, support email. Under **Audience**, leave the publishing status at **Testing** and add the Google account that owns the channel as a **test user**. `youtube.upload` and `youtube.force-ssl` are sensitive scopes; in Testing mode only listed test users can grant them, and you'll see an "unverified app" warning you can click through.
4. **OAuth client**: Credentials (Clients) → Create OAuth client ID → Application type **Desktop app** → Download JSON. Save it as `client_secret.json` in the repo root (or point `YOUTUBE_CLIENT_SECRET` at it). It's a secret: keep it out of git.
5. **First run** opens a browser for consent (choose the channel's account / brand account). The token is cached at `token_path` (written with mode 600) and refreshed automatically. Enabling captions later triggers one more consent screen for the extra scope.
   - For apps in **Testing** status, Google expires refresh tokens after **7 days**, so expect to re-consent weekly. Moving the app to "In production" avoids that, but sensitive scopes then require Google's app verification.
6. **Custom thumbnails** need a channel with intermediate features enabled (phone verification at https://www.youtube.com/verify). Without it, `thumbnails.set` returns 403 and the upload continues without the thumbnail.

## Quota (checked October 2026)

The quota system changed recently, so older guides ("an upload costs 1,600 of your 10,000 units") are out of date:

- **4 December 2025**: the documented upload cost dropped from about 1,600 units to about 100 units.
- **1 June 2026**: the API moved to granular quota buckets; `videos.insert` and `search.list` are charged to their own buckets.
- Current docs: the `videos.insert` page says "100 calls per day. A call to this method has a quota cost of 1 unit in the Video Uploads quota bucket", and the Getting Started page gives a default allocation of "100 search.list calls, 100 videos.insert calls, and 10,000 units per day combined" for everything else.
- In the shared 10,000-unit bucket: `captions.insert` costs **400** units and `thumbnails.set` about **50** units. One video with thumbnail and captions therefore uses 1 upload call plus about 450 shared units.
- Quota resets at midnight Pacific time. More quota requires the API Compliance Audit (form linked from the quota page).

## Audit caveat (privacy lock)

Historically (revision history, **28 July 2020**): "All videos uploaded via the videos.insert endpoint from unverified API projects created after 28 July 2020 will be restricted to private viewing mode." To lift that, the project had to pass the YouTube API Services audit (compliance review).

The **current** `videos.insert` and `videos` resource docs (October 2026) say the opposite: "Videos uploaded from unverified API projects are not restricted to private viewing mode." We found no revision-history entry that announces this reversal, so treat it as likely but unconfirmed. In practice:

- We upload **private** by default anyway, which is unaffected either way. Review in YouTube Studio, then publish there (or set `privacy="public"`).
- `upload_video` compares the `privacyStatus` YouTube returns with the one requested and logs a warning if YouTube locked the video to private. Under the 2020 policy a locked video can't be made public from Studio either; the fix is the audit (https://support.google.com/youtube/contact/yt_api_form), or uploading that video manually through YouTube Studio instead.

<!-- TODO: confirm on first real upload whether a public/unlisted request from this
     unaudited project is honoured, and update this section. -->
