# Visuals tooling notes

Researched 2026-10. Prices change, so check them before budgeting.

## Wikimedia Commons API (implemented: `visuals/wikimedia.py`)

- Endpoint: `https://commons.wikimedia.org/w/api.php`, `format=json&formatversion=2`.
- **Search plus metadata in one call:** `action=query&generator=search&gsrsearch=<q> filetype:bitmap`
  `&gsrnamespace=6&gsrlimit=12&prop=imageinfo&iiprop=url|size|mime|extmetadata&iiurlwidth=2560`.
  Each page carries `index`, the search rank. `list=search&srnamespace=6` also works, but it returns
  titles only and needs a second call.
- **Exact file:** the same `prop=imageinfo` call with `titles=File:...&redirects=1`.
- `iiurlwidth=2560` makes Commons return `thumburl` (a scaled copy), so we never pull
  multi-hundred-MB TIFF originals. If the original is narrower than 2560, `thumburl` is the
  original. TIFFs and PDFs get JPEG thumbnails.
- `iiextmetadatafilter=LicenseShortName|License|Artist|Credit|UsageTerms|ObjectName|DateTimeOriginal|NonFree`
  keeps responses small. `Artist` and `Credit` are HTML, so strip the tags before printing.
  `License` is a short code (`pd`, `cc-by-sa-4.0`, ...).
- **License policy:** we keep PD, CC0, CC BY and CC BY-SA, and drop NC, ND, fair use, `NonFree=true`
  and unknown licenses. ND is excluded because we grade and crop images, which makes them
  derivatives. CC BY and BY-SA need credit, so every Asset carries an attribution line and
  `attribution_lines()` collects them for the YouTube description.
- **User-Agent:** this is mandatory. Generic UAs such as `python-requests` get a 403 or a block. We send
  `HistoryChannelBot/0.1 (https://github.com/MoritzNe/history-channel) python-requests`.
  The policy asks for a client name and version, contact info, and "bot" in the name for automated agents.
- **Etiquette:** make requests one at a time, never in parallel (we space them about 250 ms apart). Send
  `maxlag=5` on API calls. On 429 or 5xx, back off exponentially and honour `Retry-After`. Cache
  everything: we keep `.cache/wikimedia/files/*.json|jpg` and `search/*.json`, so reruns stay offline.

## Other free archives worth adding later

| Archive | API | Key? | Notes |
|---|---|---|---|
| Library of Congress | `https://www.loc.gov/{collection}/?q=..&fo=json` | no | Prints and Photographs has many "no known restrictions" items. Check `rights` for each item. |
| Smithsonian Open Access | `https://api.si.edu/openaccess/api/v1.0/search` | free api.data.gov key | About 5M CC0 items. Filter on `online_media_type:Images` and CC0. |
| Europeana | Search API `https://api.europeana.eu/record/v2/search.json` | free key | Aggregates EU museums. Filter on `reusability=open`. Strong for German and Austrian 1848 prints. |
| Rijksmuseum | Data services / Linked Art search (the old `/api/` with key is being retired) | none for the new API, key for the legacy one | Public-domain high-res images, strong on Dutch material. |
| Others | Met Museum Open Access (`collectionapi.metmuseum.org`, no key, CC0), Art Institute of Chicago (no key, CC0 `is_public_domain`), Deutsche Digitale Bibliothek (free key) | | |

## AI image generation (implemented: `visuals/imagegen.py`, inactive without keys)

These are ballpark prices, so verify them on each provider's pricing page. One 1920x1080 image is about 2 MP.

| Provider | Model / endpoint | Approx. price | Notes |
|---|---|---|---|
| fal.ai (`FAL_KEY`) | `POST https://fal.run/fal-ai/flux/dev` (`Authorization: Key ...`) | schnell about $0.003/MP, dev about $0.025/MP, FLUX1.1 pro about $0.04/MP, billed per MP and rounded up | `image_size` takes `{width,height}` or presets such as `landscape_16_9`. The response has `images[0].url`. |
| OpenAI (`OPENAI_API_KEY`) | `POST /v1/images/generations`, `model=gpt-image-1` | roughly $0.02 (low) to $0.19+ (high) per image, token-billed | Sizes are 1024x1024, 1536x1024 and 1024x1536. Returns `b64_json`. Strong prompt adherence. |
| Google | Imagen 4 / Gemini image via Gemini API or Vertex | roughly $0.02 to $0.06 per image depending on tier | Adds a SynthID watermark. Needs a Google Cloud project. Not implemented yet. |
| Replicate | `black-forest-labs/flux-*` predictions | similar to fal, per image | Simple REST API with polling. Not implemented yet. |

**Prompt style for history.** A house style is prepended automatically; override it with `HC_IMAGE_STYLE`:
- Ask for period media such as "19th-century oil painting", "lithograph", "wood engraving" or
  "watercolour sketch". Use a muted earthy palette and visible brushwork. Specify "no text"
  (models garble lettering) and "no modern objects".
- **Avoid fake photorealism.** No photographs of 1848 street fighting exist, and a photoreal fake
  misleads viewers. A painterly image reads as illustration.
- Do not generate real, identifiable people (Bismarck, Metternich). Use archival portraits instead.
  Use generation for atmosphere: crowds, streets, barricades at night, and so on.
- **YouTube disclosure:** creators must set the "altered or synthetic content" flag for *realistic*
  synthetic media. Stylised or painterly illustration is generally exempt, but we label generated
  assets "AI-generated illustration (...)" in the credits anyway, and the upload stage should set
  the flag whenever any `generated` asset is used.

## Maps

- Natural Earth 1:50m data (public domain) comes from `https://naciscdn.org/naturalearth/50m/...`.
  We use `cultural/ne_50m_admin_0_countries.zip` (about 800 KB), plus `physical/ne_50m_rivers_lake_centerlines.zip`
  and `physical/ne_50m_lakes.zip`. Each is downloaded once into `.cache/naturalearth/`, and geopandas reads
  it as `zip://...`.
- Modern borders are only an approximation for 1848, so they are drawn faint and dashed. The story
  goes in `MapRegion.polygon`. Historical boundary datasets to consider later are
  HGIS Germany / IEG Maps (Mainz) and the `historical-basemaps` GeoJSON on GitHub (coarse, CC BY 4.0).
