"""Checks a script: pydantic load, at in [0,1] (first >= 0.05), historical names exist, shot mix, durations."""

import json
import sys
from collections import Counter

from historychannel.models import VideoScript

path, basemaps = sys.argv[1], sys.argv[2]
s = VideoScript.load(path)
names = {}
for y in ("1815", "1880"):
    d = json.load(open(f"{basemaps}/world_{y}.geojson"))
    ns = set()
    for f in d["features"]:
        p = f["properties"]
        for k in ("NAME", "ABBREVN", "SUBJECTO"):
            if p.get(k):
                ns.add(p[k].lower())
    names[y] = ns

errors = []
WPS = 150 / 60
kinds = Counter()
durs = []
for sc in s.scenes:
    words = len(sc.narration.split())
    secs = words / WPS + sc.pause_after
    tw = sum(v.weight for v in sc.visuals)
    for i, v in enumerate(sc.visuals):
        kinds[v.kind] += 1
        d = secs * v.weight / tw
        durs.append((d, sc.id, i, v.kind))
        if v.caption and v.kind != "archival":
            errors.append(f"{sc.id}[{i}] caption on non-archival")
        if v.map:
            m = v.map
            ats = []
            for coll in (m.markers, m.arrows, m.regions, m.texts):
                ats += [x.at for x in coll if x.at is not None]
            ats += [x.at for x in m.stamps]
            for a in ats:
                if not 0 <= a <= 1:
                    errors.append(f"{sc.id}[{i}] at={a} out of range")
            if ats and min(ats) < 0.05:
                errors.append(f"{sc.id}[{i}] first at {min(ats)} < 0.05")
            for r in m.regions:
                for h in r.historical:
                    if m.basemap == "modern" or h.lower() not in names[m.basemap]:
                        errors.append(f"{sc.id}[{i}] historical {h!r} not in basemap {m.basemap}")
            for mk in m.markers:
                lo0, lo1, la0, la1 = m.extent
                ext = [m.extent] + ([m.camera_to] if m.camera_to else [])
                if not any(e[0] <= mk.lon <= e[1] and e[2] <= mk.lat <= e[3] for e in ext):
                    errors.append(f"{sc.id}[{i}] marker {mk.name} outside extent")

n = sum(kinds.values())
wc = sum(len(x.narration.split()) for x in s.scenes)
print(f"scenes={len(s.scenes)} words={wc} est_min={wc / 150:.1f} shots={n}")
print(dict(kinds))
anim = sum(kinds[k] for k in ("stat", "timeline", "compare", "person", "title_card", "quote"))
print(f"map {kinds['map'] / n:.0%}  motion-graphics {anim / n:.0%}  archival {kinds['archival'] / n:.0%}")
durs.sort()
print("shortest:", [(round(d, 1), a, b, c) for d, a, b, c in durs[:5]])
print("longest:", [(round(d, 1), a, b, c) for d, a, b, c in durs[-6:]])
for sc in s.scenes:
    print(f"  {sc.id:20s} {len(sc.narration.split()):4d}w  " + " ".join(v.kind for v in sc.visuals))
print("ERRORS:" if errors else "OK", *errors, sep="\n  ")
