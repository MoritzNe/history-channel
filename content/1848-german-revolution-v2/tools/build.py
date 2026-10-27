"""Builds content/1848-german-revolution-v2/script.json.

Each scene is a list of (segment_text, visual) pairs. A segment is the narration spoken while
that visual is on screen; its share of the scene's characters becomes the visual's `weight`.
Any `at` given as "@phrase" (optionally "@phrase|+0.05") is resolved to the fraction of the
shot at which that phrase starts being spoken (minus a small lead), clamped to [0.05, 0.95].
"""

import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
LEAD = 0.03

# ----------------------------------------------------------------- geography

C = {
    "Paris": (2.35, 48.86), "Vienna": (16.37, 48.21), "Berlin": (13.40, 52.52),
    "Frankfurt": (8.68, 50.11), "Munich": (11.58, 48.14), "Budapest": (19.04, 47.50),
    "Milan": (9.19, 45.46), "Prague": (14.42, 50.08), "Dresden": (13.74, 51.05),
    "Rastatt": (8.20, 48.86), "Karlsruhe": (8.40, 49.01), "Mannheim": (8.47, 49.49),
    "Konstanz": (9.17, 47.66), "Kandern": (7.66, 47.71), "Kaiserslautern": (7.77, 49.44),
    "Germersheim": (8.37, 49.22), "Waghäusel": (8.51, 49.25), "Elberfeld": (7.15, 51.26),
    "Iserlohn": (7.70, 51.38), "Malmö": (13.00, 55.60), "Flensburg": (9.44, 54.78),
    "Rendsburg": (9.67, 54.30), "Copenhagen": (12.57, 55.68), "Kiel": (10.13, 54.32),
    "Hamburg": (9.99, 53.55), "Stuttgart": (9.18, 48.78), "Zurich": (8.54, 47.37),
    "London": (-0.13, 51.51), "Manchester": (-2.24, 53.48), "Carlsbad": (12.87, 50.23),
    "Hambach": (8.13, 49.33), "Peterswaldau": (16.58, 50.71), "Königsberg": (20.51, 54.71),
    "Cologne": (6.96, 50.94), "Mainz": (8.27, 50.00), "Heidelberg": (8.69, 49.41),
    "Durlach": (8.47, 49.00), "Kreuznach": (7.87, 49.84), "Freiburg": (7.85, 47.99),
    "Bremerhaven": (8.58, 53.54), "Le Havre": (0.11, 49.49), "New York": (-74.01, 40.71),
    "Cincinnati": (-84.51, 39.10), "St. Louis": (-90.20, 38.63), "Milwaukee": (-87.91, 43.04),
    "Belleville": (-89.98, 38.52), "Watertown": (-88.73, 43.19), "Weimar": (11.33, 50.98),
    "Bonn": (7.10, 50.73), "Trieste": (13.77, 45.65), "Strasbourg": (7.75, 48.58),
    "Basel": (7.59, 47.56), "Gernsbach": (8.34, 48.76),
}

# German Confederation, approximate outer border (lon, lat), 1815-1848.
CONFED = [
    (7.2, 53.25), (7.0, 53.7), (8.0, 53.75), (8.6, 53.9), (8.9, 54.05), (9.3, 54.3), (9.9, 54.38),
    (10.2, 54.45), (11.1, 54.45), (11.0, 54.0), (12.0, 54.2), (12.5, 54.45), (13.4, 54.65),
    (14.3, 54.1), (15.6, 54.2), (16.6, 54.55), (17.6, 54.75), (17.3, 54.0), (16.9, 53.25),
    (15.9, 52.55), (16.2, 52.0), (16.6, 51.6), (17.5, 51.3), (18.2, 51.1), (18.7, 50.8),
    (19.1, 50.3), (18.85, 49.6), (18.2, 49.3), (17.6, 48.85), (17.0, 48.1), (16.8, 47.7),
    (16.3, 46.9), (16.4, 46.4), (15.7, 45.85), (15.2, 45.45), (14.4, 45.3), (13.6, 45.5),
    (13.7, 46.0), (13.4, 46.5), (12.4, 46.6), (11.7, 45.9), (11.0, 45.75), (10.5, 46.0),
    (10.4, 46.6), (9.6, 47.05), (9.55, 47.5), (8.7, 47.7), (7.6, 47.6), (7.6, 48.2),
    (8.2, 48.97), (7.0, 49.15), (6.4, 49.45), (5.8, 49.5), (5.75, 50.1), (6.1, 50.7),
    (5.95, 50.9), (5.7, 51.25), (6.1, 51.6), (6.0, 51.9), (6.8, 51.95), (7.05, 52.25),
    (6.75, 52.5), (7.05, 52.8), (7.2, 53.25),
]

# Basemap 1815 polity NAMEs (checked against world_1815.geojson by check.py)
LESSER = [
    "Prussia", "Bavaria", "Palatinate", "Saxony", "Hanover", "Württemberg", "Baden",
    "Grand Duchy of Hesse", "Electoral Hesse", "Nassau", "Thuringia", "Mecklenburg-Schwerin",
    "Mecklenburg-Strelitz", "Oldenburg", "Brunswick", "Anhalt", "Lippe-Detmold",
    "Schaumburg-Lippe", "Waldeck", "Holstein", "Hamburg", "Bremen", "Lübeck", "Hohenzollern",
    "Wetzlar", "Cuxhaven", "Luxembourg",
]
HABSBURG = ["Austrian Empire", "Lombardy", "Venetia"]
REJECTED = ["Prussia", "Austrian Empire", "Bavaria", "Saxony", "Hanover"]
ACCEPTED = [
    "Baden", "Württemberg", "Grand Duchy of Hesse", "Electoral Hesse", "Nassau", "Oldenburg",
    "Brunswick", "Thuringia", "Holstein", "Hamburg", "Bremen", "Lübeck", "Lippe-Detmold",
]

EUROPE = (-6.0, 26.0, 40.5, 58.0)
CONFED_EXT = (3.5, 21.0, 44.6, 56.0)
BADEN_EXT = (6.6, 10.4, 47.25, 49.9)

RED, BLUE, GOLD, GREEN, GREY = "#9b2d20", "#2f4b7c", "#c9a227", "#4f7a4a", "#6b6b6b"

TIMELINE = [
    ("1815", "German Confederation founded"),
    ("Mar 1848", "March Revolutions"),
    ("May 1848", "Parliament opens in Frankfurt"),
    ("Nov 1848", "Counter-revolution"),
    ("Apr 1849", "Crown refused"),
    ("Jul 1849", "Rastatt falls"),
]


def timeline(highlight, weight=None):
    return {
        "kind": "timeline", "motion": "static",
        "events": [{"date": d, "label": lab, "highlight": d == highlight} for d, lab in TIMELINE],
    }


def mk(name, label=None, style="city", at=None, pulse=False, lonlat=None):
    lon, lat = lonlat or C[name]
    m = {"name": name, "lon": lon, "lat": lat, "style": style}
    if label:
        m["label"] = label
    if at is not None:
        m["at"] = at
    if pulse:
        m["pulse"] = True
    return m


def arrow(a, b, label=None, style="advance", at=None, draw=0.25, via=(), color=None):
    fa = C[a] if isinstance(a, str) else a
    fb = C[b] if isinstance(b, str) else b
    d = {"from_lonlat": list(fa), "to_lonlat": list(fb), "style": style, "draw": draw}
    if label:
        d["label"] = label
    if at is not None:
        d["at"] = at
    if via:
        d["via"] = [list(p) for p in via]
    if color:
        d["color"] = color
    return d


def reg(label=None, historical=None, polygon=None, countries=None, at=None, hatch=False, color=None):
    r = {}
    if label:
        r["label"] = label
    if historical:
        r["historical"] = list(historical)
    if polygon:
        r["polygon"] = [list(p) for p in polygon]
    if countries:
        r["countries"] = list(countries)
    if at is not None:
        r["at"] = at
    if hatch:
        r["hatch"] = True
    if color:
        r["color"] = color
    return r


def txt(text, lon, lat, size="medium", at=None):
    d = {"text": text, "lon": lon, "lat": lat, "size": size}
    if at is not None:
        d["at"] = at
    return d


def stamp(text, at):
    return {"text": text, "at": at}


def MAP(title, extent, *, camera_to=None, basemap="1815", markers=(), arrows=(), regions=(), texts=(), stamps=()):
    m = {"title": title, "extent": list(extent), "basemap": basemap}
    if camera_to:
        m["camera_to"] = list(camera_to)
    for k, v in (("markers", markers), ("arrows", arrows), ("regions", regions), ("texts", texts), ("stamps", stamps)):
        if v:
            m[k] = list(v)
    return {"kind": "map", "motion": "static", "map": m}


def stat(value, text, subtext=None):
    d = {"kind": "stat", "value": value, "text": text, "motion": "static"}
    if subtext:
        d["subtext"] = subtext
    return d


def person(file, name, role):
    return {"kind": "person", "file": file, "text": name, "subtext": role, "motion": "static"}


def archival(file, caption, motion="zoom_in", query=None):
    d = {"kind": "archival", "file": file, "caption": caption, "motion": motion}
    if query:
        d["query"] = query
    return d


def compare(a_title, a_points, b_title, b_points, a_color=None, b_color=None):
    ca = {"title": a_title, "points": a_points}
    cb = {"title": b_title, "points": b_points}
    if a_color:
        ca["color"] = a_color
    if b_color:
        cb["color"] = b_color
    return {"kind": "compare", "columns": [ca, cb], "motion": "static"}


# ----------------------------------------------------------------- scenes

S = []


def scene(id_, mood, segs, sources, pause=0.6):
    S.append({"id": id_, "mood": mood, "segs": segs, "sources": sources, "pause_after": pause})


W = "https://en.wikipedia.org/wiki/"
D = "https://de.wikipedia.org/wiki/"

scene("s01_hook", "neutral", [
    ("In the spring of 1848, revolution swept Europe. Paris rose in February; "
     "within a month, Vienna, Budapest, Milan and Berlin followed. Across the German states, rulers gave way. "
     "By May, an elected German parliament sat in Frankfurt.",
     MAP("Europe, spring 1848", EUROPE, markers=[
         mk("Paris", "Paris, 24 Feb", "uprising", "@Paris rose"),
         mk("Vienna", "Vienna, 13 Mar", "uprising", "@Vienna"),
         mk("Budapest", "Pest, 15 Mar", "uprising", "@Budapest"),
         mk("Milan", "Milan, 18 Mar", "uprising", "@Milan"),
         mk("Berlin", "Berlin, 18 Mar", "uprising", "@Berlin"),
         mk("Frankfurt", "Frankfurt parliament", "capital", "@Frankfurt", pulse=True),
     ], regions=[reg("German Confederation", polygon=CONFED, at="@German states", color=GOLD)],
        stamps=[stamp("February 1848", "@Paris rose"), stamp("March 1848", "@within a month"), stamp("May 1848", "@By May")])),
    ("Little more than a year later, all of it was gone. "
     "So why did a revolution that won almost everywhere in March 1848 lose everything by July 1849?",
     {"kind": "title_card", "text": "Won in March, Lost by July", "subtext": "The German Revolutions of 1848–49", "motion": "static"}),
], [W + "Revolutions_of_1848_in_the_German_states", W + "Frankfurt_Parliament"], pause=1.0)

scene("s02_confederation", "neutral", [
    ("The answer has three parts: the map Germany started from, the revolutionaries' own divisions, "
     "and two great powers waiting to recover. We start in 1815.",
     timeline("1815")),
    ("That year the Congress of Vienna created not a Germany but the German Confederation: thirty-nine states.",
     stat("39", "states in the German Confederation", "at its founding by the Congress of Vienna, 1815")),
    ("Its only common body was a Federal Diet of princes' envoys in Frankfurt. "
     "There was no German government, no German army, no German citizenship.",
     MAP("The German Confederation, 1815–1866", EUROPE, camera_to=CONFED_EXT,
         regions=[reg("German Confederation (approx.)", polygon=CONFED, at=0.05, color=GOLD)],
         markers=[mk("Frankfurt", "Frankfurt: Federal Diet", "capital", "@Federal Diet", pulse=True)],
         texts=[txt("no government", 11.0, 53.0, "small", "@no German government"),
                txt("no army", 11.0, 51.9, "small", "@no German army"),
                txt("no citizenship", 11.0, 50.8, "small", "@no German citizenship")])),
    ("Two members were giants, Prussia and Austria, and both ruled large lands outside the Confederation. "
     "Austria's empire included Hungarians, Czechs, Poles, Croats and Italians. That will decide the ending.",
     MAP("Two great powers", (3.0, 27.0, 43.5, 56.5),
         regions=[reg("German Confederation", polygon=CONFED, color=GOLD),
                  reg("Prussia", historical=["Prussia"], at="@Prussia", color=BLUE),
                  reg("Austrian Empire", historical=HABSBURG, at="@and Austria", color=RED)],
         texts=[txt("EAST PRUSSIA", 21.0, 54.3, "small", "@outside the Confederation"),
                txt("POSEN", 17.2, 52.3, "small", "@outside the Confederation|+0.04"),
                txt("HUNGARY", 20.5, 47.0, "medium", "@Hungarians"),
                txt("GALICIA", 22.5, 49.8, "small", "@Poles"),
                txt("LOMBARDY–VENETIA", 10.8, 45.3, "small", "@Italians")])),
], [W + "German_Confederation", W + "Revolutions_of_1848_in_the_German_states"])

scene("s03_metternich", "neutral", [
    ("Austria's chancellor, Prince Metternich, guarded this order. He feared two ideas: liberalism, meaning constitutions and a free press, "
     "and nationalism, the idea of one German state.",
     person("File:Metternich by Lawrence.jpeg", "Klemens von Metternich", "Austrian chancellor, architect of the 1815 order")),
    ("Both threatened a multinational empire like his. From 1819, the Carlsbad Decrees put universities under watch and newspapers under censorship.",
     MAP("The Metternich system", (4.0, 20.0, 45.5, 55.5),
         regions=[reg("Censorship across the Confederation", polygon=CONFED, at="@censorship", hatch=True, color=GREY)],
         markers=[mk("Vienna", "Vienna", "capital", 0.05, pulse=True),
                  mk("Carlsbad", "Carlsbad Decrees, 1819", "dot", "@Carlsbad")],
         stamps=[stamp("1819", "@From 1819")])),
    ("When tens of thousands rallied at Hambach in 1832 under black, red and gold, the Diet answered with more repression.",
     archival("File:Zug auf das Schloss Hambach 1832 Deutschland.png",
              "The Hambach Festival, May 1832: black, red and gold", "pan_right")),
], [W + "Klemens_von_Metternich", W + "Carlsbad_Decrees", W + "Hambach_Festival"])

scene("s04_pressure", "neutral", [
    ("The pressure was economic too. Machine-made cloth, much of it British, undercut home weavers. "
     "In 1844, Silesian weavers rose, and Prussian troops shot eleven of them.",
     MAP("Industry and the weavers, 1844", (-4.0, 20.0, 46.5, 57.0), camera_to=(8.0, 19.0, 49.0, 54.5),
         arrows=[arrow("Manchester", "Hamburg", "British machine-made cloth", "flow", "@Machine-made", 0.3,
                       via=[(1.5, 53.6), (6.0, 54.0)], color=GREY)],
         markers=[mk("Peterswaldau", "Silesian weavers, June 1844", "uprising", "@In 1844", pulse=True)],
         regions=[reg("German Confederation", polygon=CONFED, color=GOLD)],
         stamps=[stamp("1844", "@In 1844")])),
    ("Then came hunger. Potato blight struck in 1845, and the harvest of 1846 failed. "
     "In April 1847, hungry Berliners stormed market stalls: the Potato Revolution.",
     MAP("The hungry years, 1845–47", CONFED_EXT,
         regions=[reg("Harvest failure", polygon=CONFED, at="@the harvest", hatch=True, color=GREY)],
         texts=[txt("POTATO BLIGHT 1845", 9.5, 52.6, "large", "@Potato blight"),
                txt("HARVEST FAILURE 1846", 11.5, 48.6, "large", "@the harvest")],
         markers=[mk("Berlin", "Berlin 'Potato Revolution', April 1847", "uprising", "@In April 1847", pulse=True)],
         stamps=[stamp("1845", "@Potato blight"), stamp("1846", "@the harvest"), stamp("April 1847", "@In April 1847")])),
    ("Liberals wanted rights and a nation; craftsmen, labourers and peasants wanted bread, work "
     "and an end to feudal dues. In March they fought side by side.",
     compare("Middle-class liberals", ["A constitution", "Free press, free assembly", "An elected parliament", "One German nation"],
             "Craftsmen, labourers, peasants", ["Affordable bread", "Work and fair pay", "An end to feudal dues"],
             BLUE, RED)),
], [W + "Silesian_weavers%27_uprising", W + "European_potato_failure", D + "Kartoffelrevolution"])

scene("s05_spark", "tense", [
    ("The spark came from Paris. In late February 1848, after three days of street fighting, the king abdicated and France became a republic.",
     MAP("Paris, February 1848", (-3.0, 14.0, 44.5, 53.5), camera_to=(1.0, 11.0, 46.5, 51.5),
         markers=[mk("Paris", "Paris: republic declared", "uprising", 0.08, pulse=True),
                  mk("Mannheim", "Mannheim", "city", 0.75)],
         arrows=[arrow("Paris", "Mannheim", "News of the republic", "flow", 0.72, 0.22)],
         stamps=[stamp("22–24 February 1848", 0.08)])),
    ("Three days later, a meeting in Mannheim drew up four demands: arming of the people, "
     "unconditional freedom of the press, trial by jury, and the immediate creation of a German parliament. "
     "These became the March demands.",
     MAP("The Mannheim demands, 27 February 1848", (6.4, 10.6, 48.2, 50.6),
         regions=[reg("Baden", historical=["Baden"], at=0.05, color=GOLD)],
         markers=[mk("Mannheim", "Mannheim", "capital", 0.06, pulse=True)],
         texts=[txt("1. Arm the people", 9.25, 50.25, "small", "@arming"),
                txt("2. Free press", 9.25, 49.95, "small", "@unconditional"),
                txt("3. Jury trials", 9.25, 49.65, "small", "@trial by jury"),
                txt("4. A German parliament", 9.25, 49.35, "small", "@the immediate creation")],
         stamps=[stamp("27 February 1848", 0.05)])),
], [D + "Mannheimer_Volksversammlung", W + "Revolutions_of_1848_in_the_German_states"])

scene("s06_march", "tense", [
    ("The princes gave in fast. Baden, Württemberg, Hesse, Saxony and Hanover got liberal March ministries. "
     "In Bavaria, the king abdicated.",
     MAP("March 1848: the princes give way", CONFED_EXT,
         regions=[reg("German Confederation", polygon=CONFED, color=GOLD),
                  reg("Baden", historical=["Baden"], at="@Baden", color=BLUE),
                  reg("Württemberg", historical=["Württemberg"], at="@Württemberg", color=BLUE),
                  reg("Hesse", historical=["Grand Duchy of Hesse"], at="@Hesse", color=BLUE),
                  reg("Saxony", historical=["Saxony"], at="@Saxony", color=BLUE),
                  reg("Hanover", historical=["Hanover"], at="@Hanover", color=BLUE),
                  reg("Bavaria", historical=["Bavaria"], at="@In Bavaria", color=BLUE)],
         texts=[txt("Ludwig I abdicates", 11.6, 47.9, "small", "@abdicated")],
         stamps=[stamp("March 1848", 0.05)])),
    ("Then came the two capitals that mattered. In Vienna, on the thirteenth of March, troops fired on demonstrators. "
     "That evening Metternich resigned. He fled into exile in England.",
     MAP("Vienna, 13 March 1848", (11.0, 21.0, 45.5, 51.0), camera_to=(-4.0, 21.0, 45.0, 55.0),
         markers=[mk("Vienna", "Vienna, 13 March", "uprising", "@In Vienna", pulse=True),
                  mk("London", "London", "city", "@exile")],
         arrows=[arrow("Vienna", "London", "Metternich into exile", "retreat", "@He fled", 0.3)],
         stamps=[stamp("13 March 1848", "@In Vienna"), stamp("Metternich resigns", "@That evening")])),
], [W + "Revolutions_of_1848_in_the_German_states", W + "Klemens_von_Metternich", W + "Ludwig_I_of_Bavaria"])

scene("s07_berlin", "tense", [
    ("Five days later, Berlin. On the eighteenth of March, two shots went off outside the royal palace, most probably by accident.",
     MAP("Berlin, 18 March 1848", CONFED_EXT, camera_to=(10.5, 16.5, 50.8, 54.2),
         regions=[reg("Prussia", historical=["Prussia"], at=0.05, color=BLUE)],
         markers=[mk("Berlin", "Berlin", "capital", "@Berlin", pulse=True)],
         stamps=[stamp("18 March 1848", "@eighteenth")])),
    ("Within hours, Berliners built more than nine hundred barricades, by one count. "
     "More than two hundred civilians died, and fewer than fifty soldiers.",
     archival("File:Die Barrikade an der Kronen- und Friedrichstraße in Berlin am 18. März 1848.jpg",
              "Barricade at Kronenstraße and Friedrichstraße, Berlin, 18 March 1848", "zoom_in")),
    ("The next day, King Friedrich Wilhelm the Fourth withdrew his troops. Days later he rode through Berlin wearing black, red and gold.",
     person("File:1846 Krueger Friedrich Wilhelm IV anagoria.JPG", "Friedrich Wilhelm IV", "King of Prussia, 1840–1861")),
], [D + "M%C3%A4rzrevolution_1848_in_Berlin"])

scene("s08_parliament", "hopeful", [
    ("By the end of March, even the old Federal Diet had adopted black, red and gold. Next came the fourth demand: a national parliament.",
     timeline("May 1848")),
    ("Elections followed across the Confederation, under rules that varied by state. "
     "Deputies came to Frankfurt from Königsberg to Trieste.",
     MAP("Electing a German parliament, spring 1848", CONFED_EXT, camera_to=(2.5, 22.0, 44.5, 56.5),
         regions=[reg("German Confederation (approx.)", polygon=CONFED, at=0.05, color=GOLD)],
         markers=[mk("Frankfurt", "Frankfurt", "capital", 0.05, pulse=True)],
         arrows=[arrow("Königsberg", "Frankfurt", None, "flow", "@Deputies", 0.35, color=GOLD),
                 arrow("Trieste", "Frankfurt", None, "flow", "@Deputies|+0.04", 0.35, color=GOLD),
                 arrow("Berlin", "Frankfurt", None, "flow", "@Deputies|+0.08", 0.3, color=GOLD),
                 arrow("Vienna", "Frankfurt", None, "flow", "@Deputies|+0.1", 0.3, color=GOLD),
                 arrow("Hamburg", "Frankfurt", None, "flow", "@Deputies|+0.12", 0.3, color=GOLD),
                 arrow("Munich", "Frankfurt", None, "flow", "@Deputies|+0.14", 0.3, color=GOLD),
                 arrow("Cologne", "Frankfurt", None, "flow", "@Deputies|+0.16", 0.25, color=GOLD)],
         texts=[txt("Königsberg", 20.5, 55.15, "small", "@Königsberg"), txt("Trieste", 13.8, 45.25, "small", "@Trieste")])),
    ("On the eighteenth of May 1848, they walked in procession into St Paul's Church.",
     archival("File:Paulskirche einzugparlamentarierer1848.jpg",
              "Deputies enter St Paul's Church, Frankfurt, 18 May 1848", "pan_up")),
], [W + "Flag_of_Germany", W + "Frankfurt_Parliament", D + "Frankfurter_Nationalversammlung"])

scene("s09_who", "neutral", [
    ("The Assembly had about five hundred and eighty-five seats. Counting substitutes, around eight hundred men served.",
     stat("585", "seats in the National Assembly", "about 809 men served in them, counting substitutes (figures vary)")),
    ("Most were judges, civil servants, lawyers and professors; one historian counted not a single worker. "
     "People called it the professors' parliament.",
     archival("File:Zeitgenössige Lithografie der Nationalversammlung in der Paulskirche.jpg",
              "The National Assembly in session, St Paul's Church, 1848", "pan_right")),
    ("Its president was Heinrich von Gagern, a Hessian liberal who wanted a constitutional monarchy.",
     person("File:Eduard von Heuss - Heinrich von Gagern, 1848.jpg", "Heinrich von Gagern", "President of the National Assembly, 1848")),
], [D + "Frankfurter_Nationalversammlung", D + "Heinrich_von_Gagern"])

scene("s10_divided", "neutral", [
    ("It was divided from the start. Liberals like Gagern wanted a constitutional monarchy, built with the princes. "
     "Democrats wanted popular sovereignty, an equal vote for all men, often a republic.",
     compare("Liberals", ["Constitutional monarchy", "Reform with the princes", "Vote often tied to property", "e.g. Heinrich von Gagern"],
             "Democrats", ["Sovereignty of the people", "Often a republic", "Equal vote for all men", "e.g. Robert Blum, Friedrich Hecker"],
             BLUE, RED)),
    ("Some were ready to fight. One of them, the Mannheim lawyer Friedrich Hecker, had not waited for Frankfurt.",
     person("File:Illustrirte Chronik Friedrich Hecker 1848.jpg", "Friedrich Hecker", "Democrat, lawyer from Mannheim")),
], [W + "Frankfurt_Parliament", W + "Friedrich_Hecker"])

scene("s11_hecker", "tense", [
    ("In April 1848 he set out from Konstanz to march on Karlsruhe and declare a republic. He expected tens of thousands. "
     "His column wound west through the Black Forest.",
     MAP("Hecker's march, April 1848", BADEN_EXT, camera_to=(7.2, 10.0, 47.3, 49.2),
         regions=[reg("Baden", historical=["Baden"], at=0.05, color=GOLD)],
         markers=[mk("Konstanz", "Konstanz, 13 April", "uprising", "@Konstanz", pulse=True),
                  mk("Karlsruhe", "Karlsruhe: the goal", "capital", "@Karlsruhe")],
         arrows=[arrow("Konstanz", "Kandern", "Hecker's column (approx.)", "route", "@wound", 0.3,
                       via=[(8.97, 47.74), (9.01, 47.85), (8.77, 47.86), (8.45, 47.74), (8.34, 47.82), (8.0, 47.8)], color=RED)],
         texts=[txt("BLACK FOREST", 8.15, 48.3, "medium", "@Black Forest")],
         stamps=[stamp("13 April 1848", "@In April")])),
    ("Near Kandern, government troops scattered his roughly one thousand men. The general killed that day was Friedrich von Gagern, "
     "older brother of the future parliament president. Hecker fled to Switzerland.",
     MAP("Kandern, 20 April 1848", (7.2, 10.0, 47.3, 49.2), camera_to=(7.0, 8.8, 47.3, 48.3),
         markers=[mk("Kandern", "Kandern, 20 April", "battle", "@Near Kandern", pulse=True),
                  mk("Basel", "Basel", "city", "@Switzerland")],
         arrows=[arrow("Kandern", (7.75, 47.48), "Hecker flees", "retreat", "@Hecker fled", 0.2)],
         texts=[txt("SWITZERLAND", 8.2, 47.42, "medium", "@Switzerland")],
         stamps=[stamp("20 April 1848", "@Near Kandern")])),
], [D + "Heckerzug", W + "Friedrich_Hecker", W + "Friedrich_Balduin_von_Gagern"])

scene("s12_schleswig", "neutral", [
    ("The first test came in the north. Schleswig and Holstein were ruled by the Danish king, "
     "but only Holstein was in the Confederation. When Danish nationalists moved to bind Schleswig to Denmark, "
     "Germans there revolted, and Frankfurt backed them.",
     MAP("Schleswig and Holstein, 1848", (5.5, 15.5, 52.6, 57.6), camera_to=(7.0, 13.8, 53.2, 56.4),
         regions=[reg("Denmark", historical=["Denmark"], at="@Danish king", color=RED),
                  reg("Holstein: in the Confederation", historical=["Holstein"], at="@only Holstein", color=GOLD),
                  reg("Schleswig: contested", historical=["Schleswig"], at="@Danish nationalists", hatch=True, color=RED)],
         markers=[mk("Rendsburg", "Rendsburg: revolt, March 1848", "uprising", "@revolted", pulse=True)],
         texts=[txt("NORTH SEA", 6.8, 55.2, "large"), txt("BALTIC SEA", 12.4, 54.75, "medium")])),
    ("But Frankfurt had no army, so Prussian troops marched into Schleswig for it. "
     "Then, under great-power pressure, Prussia signed an armistice at Malmö without asking Frankfurt.",
     MAP("War and armistice, 1848", (7.0, 13.8, 53.2, 56.4), camera_to=(7.5, 14.5, 53.3, 56.6),
         regions=[reg(None, historical=["Holstein"], color=GOLD),
                  reg(None, historical=["Schleswig"], hatch=True, color=RED)],
         arrows=[arrow("Hamburg", "Flensburg", "Prussian and federal troops", "advance", "@marched into Schleswig", 0.3,
                       via=[(9.67, 54.3)], color=BLUE)],
         markers=[mk("Flensburg", None, "battle", "@marched into Schleswig|+0.15"),
                  mk("Malmö", "Malmö armistice", "dot", "@Malmö", pulse=True)],
         stamps=[stamp("April 1848", "@marched into Schleswig"), stamp("26 August 1848", "@Malmö")])),
    ("The Assembly first rejected it, then gave in. In September crowds rose in Frankfurt, "
     "two conservative deputies were killed, and Prussian and Austrian troops from Mainz restored order.",
     MAP("Frankfurt, September 1848", (7.3, 10.0, 49.4, 50.8), camera_to=(7.9, 9.3, 49.75, 50.4),
         markers=[mk("Frankfurt", "Frankfurt", "capital", 0.05, pulse=True),
                  mk("Mainz", "Federal fortress of Mainz", "city", "@from Mainz")],
         arrows=[arrow("Mainz", "Frankfurt", "Prussian and Austrian troops", "advance", "@Prussian and Austrian", 0.25, color=BLUE)],
         texts=[txt("2 deputies killed", 8.95, 50.25, "small", "@two conservative deputies")],
         stamps=[stamp("5 Sept: armistice rejected", 0.05), stamp("16 Sept: accepted", "@then gave in"),
                 stamp("18 Sept: uprising", "@In September")])),
], [W + "First_Schleswig_War", D + "Waffenstillstand_von_Malm%C3%B6", W + "Hans_Adolf_Erdmann_von_Auerswald",
    "https://www.bundesarchiv.de/themen-entdecken/online-entdecken/storys/schlaglichter-einer-revolution/september-1848/"])

scene("s13_counter", "somber", [
    ("By autumn, the old powers had their armies back, and they used them.",
     timeline("Nov 1848")),
    ("In October, Vienna rose again. Prince Windischgrätz, who had crushed Prague in June, "
     "stormed the city with Croatian troops under Jelačić.",
     MAP("Counter-revolution in Austria, 1848", (11.5, 21.5, 45.0, 51.5), camera_to=(13.0, 19.5, 46.5, 50.6),
         markers=[mk("Prague", "Prague, June 1848", "battle", "@Prague"),
                  mk("Vienna", "Vienna, October 1848", "uprising", "@Vienna rose", pulse=True)],
         arrows=[arrow("Prague", "Vienna", "Windischgrätz", "advance", "@stormed", 0.25, color=GREY),
                 arrow((18.2, 46.6), "Vienna", "Jelačić", "advance", "@Croatian", 0.25, color=GREY)],
         stamps=[stamp("June 1848", "@Prague"), stamp("6 October 1848", "@In October"), stamp("31 October 1848", "@stormed")])),
    ("Among the defenders was Robert Blum, a leader of the Frankfurt left. He claimed immunity as a deputy. "
     "On the ninth of November he was shot.",
     person("File:Portrait of Robert Blum by August Hunger.jpg", "Robert Blum", "Leader of the Frankfurt left, 1807–1848")),
    ("The next day, General Wrangel marched Prussian troops back into Berlin. "
     "In December the king imposed his own constitution.",
     MAP("Berlin, November 1848", (10.5, 16.5, 50.8, 54.2), camera_to=(12.2, 14.6, 51.9, 53.1),
         regions=[reg("Prussia", historical=["Prussia"], color=BLUE)],
         markers=[mk("Berlin", "Berlin", "capital", 0.05, pulse=True)],
         arrows=[arrow((12.6, 52.3), "Berlin", "Wrangel's troops", "advance", "@marched Prussian troops", 0.25, color=GREY)],
         stamps=[stamp("10 November 1848", "@The next day"), stamp("12 November: state of siege", "@back into Berlin|+0.06"),
                 stamp("5 December: imposed constitution", "@In December")])),
], [W + "Prague_uprising_of_1848", W + "Vienna_Uprising", D + "Robert_Blum",
    "https://parlament-berlin.de/Das-Haus/Berliner-Ehrenbuerger/friedrich-heinrich-ernst-graf-von-wrangel",
    W + "Prussian_National_Assembly"], pause=0.9)

scene("s14_where", "neutral", [
    ("Now the hardest question: where was Germany? A Greater Germany would include Austria. "
     "But only Austria's German lands and Bohemia were in the Confederation. Hungary, Galicia and northern Italy were not. "
     "Including Austria meant splitting its empire.",
     MAP("Option 1: Greater Germany", (3.0, 27.0, 43.5, 56.5),
         regions=[reg("German Confederation", polygon=CONFED, at=0.05, color=GOLD),
                  reg("Habsburg lands outside", historical=HABSBURG, at="@Hungary", hatch=True, color=RED)],
         markers=[mk("Vienna", "Vienna", "capital", "@Austria's German")],
         texts=[txt("BOHEMIA", 14.8, 49.9, "small", "@Bohemia"),
                txt("HUNGARY", 20.5, 47.0, "medium", "@Hungary"),
                txt("GALICIA", 22.5, 49.8, "small", "@Galicia"),
                txt("NORTHERN ITALY", 10.8, 45.3, "small", "@northern Italy")])),
    ("Vienna refused. In March 1849 it bound the whole empire, Hungary included, into one state. "
     "That left a Lesser Germany, without Austria, led by the King of Prussia.",
     MAP("Option 2: Lesser Germany", (3.0, 27.0, 43.5, 56.5),
         regions=[reg("Austrian Empire: one state", historical=HABSBURG, at=0.05, hatch=True, color=GREY),
                  reg("Lesser Germany (approx.)", historical=LESSER, at="@Lesser Germany", color=BLUE)],
         markers=[mk("Vienna", "Vienna", "capital", 0.05),
                  mk("Berlin", "Berlin: King of Prussia", "capital", "@King of Prussia", pulse=True)],
         stamps=[stamp("March 1849", "@In March 1849")])),
], [D + "Kleindeutsche_L%C3%B6sung", W + "March_Constitution_of_Austria"])

scene("s15_constitution", "hopeful", [
    ("In December 1848 the Assembly passed the Basic Rights: equality before the law, "
     "freedom of the press, of religion and of assembly, and the abolition of the death penalty, with exceptions.",
     MAP("Basic Rights of the German People, 27 December 1848", CONFED_EXT,
         regions=[reg(None, historical=LESSER, at=0.05, color=BLUE)],
         texts=[txt("Equality before the law", 10.2, 53.2, "small", "@equality"),
                txt("Free press", 10.2, 52.3, "small", "@freedom of the press"),
                txt("Freedom of religion", 10.2, 51.4, "small", "@of religion"),
                txt("Freedom of assembly", 10.2, 50.5, "small", "@of assembly"),
                txt("No death penalty (with exceptions)", 10.2, 49.6, "small", "@abolition")],
         stamps=[stamp("27 December 1848", "@In December")])),
    ("In March 1849 came the constitution: a federal empire, an elected parliament, a hereditary emperor.",
     MAP("The Frankfurt Constitution, 28 March 1849", CONFED_EXT,
         regions=[reg("German Empire under the constitution (approx.)", historical=LESSER, at=0.05, color=BLUE)],
         markers=[mk("Frankfurt", "Frankfurt: elected parliament", "capital", "@an elected parliament", pulse=True),
                  mk("Berlin", "Berlin: hereditary emperor", "capital", "@a hereditary emperor")],
         stamps=[stamp("28 March 1849", 0.05)])),
    ("The Assembly then elected Friedrich Wilhelm the Fourth emperor: two hundred and ninety votes for, two hundred and forty-eight abstentions.",
     stat("290", "votes for Friedrich Wilhelm IV as emperor", "248 deputies abstained · 28 March 1849")),
], [D + "Grundrechte_des_deutschen_Volkes", D + "Paulskirchenverfassung", D + "Kaiserdeputation"])

scene("s16_refusal", "somber", [
    ("On the third of April 1849, deputies led by Eduard Simson offered him the crown in Berlin. "
     "He stalled, and within weeks refused outright.",
     archival("File:Blos 527 Kaiserdeputation.jpg", "The Frankfurt delegation before Friedrich Wilhelm IV, Berlin, 3 April 1849", "zoom_in")),
    ("Privately he had called it an imaginary hoop baked out of mud and clay. "
     "A king by the grace of God would not take a crown from a parliament.",
     {"kind": "quote", "text": "Such an imaginary hoop, baked out of mud and clay …",
      "subtext": "Friedrich Wilhelm IV, private letter to Christian von Bunsen, 13 December 1848 (translated)", "motion": "static"}),
    ("Austria and the larger kingdoms rejected the constitution. Twenty-eight smaller governments accepted it. "
     "Frankfurt had a constitution, and no power to enforce it.",
     MAP("Who accepted the constitution? April 1849", (3.0, 24.0, 44.0, 56.0),
         regions=[reg("Rejected", historical=REJECTED, at=0.05, hatch=True, color=GREY),
                  reg("Accepted", historical=ACCEPTED, at="@Twenty-eight", color=BLUE)],
         markers=[mk("Frankfurt", "Frankfurt", "capital", "@Frankfurt had", pulse=True)],
         stamps=[stamp("14 April 1849: Note of the 28", "@Twenty-eight")])),
], [D + "Kaiserdeputation", "https://www.dhm.de/assets/DHM/Download/Publikationen/Begleitheft_RNT/1848_Aufgaben_G04.pdf",
    D + "Paulskirchenverfassung"], pause=1.0)

scene("s17_campaign", "tense", [
    ("In May 1849, democrats rose to force the princes to accept it: the Reich Constitution Campaign. "
     "Dresden fought for a week, with Richard Wagner among the rebels. "
     "Elberfeld and Iserlohn rose in Prussia's west.",
     MAP("The Reich Constitution Campaign, May 1849", CONFED_EXT, camera_to=(4.5, 16.5, 47.0, 54.0),
         markers=[mk("Dresden", "Dresden, 3–9 May", "uprising", "@Dresden", pulse=True),
                  mk("Elberfeld", "Elberfeld", "uprising", "@Elberfeld"),
                  mk("Iserlohn", "Iserlohn", "uprising", "@Iserlohn")],
         stamps=[stamp("May 1849", 0.05)])),
    ("In the Palatinate, revolutionaries took over. In Baden the army changed sides: Rastatt's garrison mutinied and the Grand Duke fled. "
     "The parliament's rump moved to Stuttgart, where troops dispersed it in June.",
     MAP("The south-west, May–June 1849", (5.5, 11.5, 47.3, 50.8),
         regions=[reg("Palatinate", historical=["Palatinate"], at="@Palatinate", color=RED),
                  reg("Baden", historical=["Baden"], at="@In Baden", color=RED)],
         markers=[mk("Kaiserslautern", "Kaiserslautern", "uprising", "@revolutionaries took over"),
                  mk("Rastatt", "Rastatt mutiny, May", "uprising", "@Rastatt", pulse=True),
                  mk("Stuttgart", "Stuttgart, 18 June", "city", "@Stuttgart")],
         arrows=[arrow("Frankfurt", "Stuttgart", "Rump parliament", "route", "@moved to Stuttgart", 0.25, color=GREY)],
         stamps=[stamp("May 1849", 0.05), stamp("18 June 1849", "@in June")])),
], [W + "May_Uprising_in_Dresden", D + "Reichsverfassungskampagne", D + "Badische_Revolution", W + "Frankfurt_Parliament"])

scene("s18_rastatt", "somber", [
    ("Each time, Prussia answered. Its troops helped retake Dresden. Then two Prussian corps under the king's brother, Prince Wilhelm, "
     "cleared the Palatinate, crossed the Rhine at Germersheim and beat the Baden army at Waghäusel.",
     MAP("Prussia strikes back, May–June 1849", CONFED_EXT, camera_to=(6.5, 10.5, 48.4, 50.2),
         markers=[mk("Dresden", "Dresden", "battle", "@retake Dresden"),
                  mk("Germersheim", "Germersheim, 20 June", "city", "@Germersheim"),
                  mk("Waghäusel", "Waghäusel, 21 June", "battle", "@Waghäusel", pulse=True)],
         arrows=[arrow("Berlin", "Dresden", "Prussian troops", "advance", "@retake Dresden", 0.2, color=BLUE),
                 arrow("Kreuznach", "Germersheim", "Prussian I Corps", "advance", "@cleared the Palatinate", 0.25,
                       via=[C["Kaiserslautern"]], color=BLUE),
                 arrow("Germersheim", "Waghäusel", None, "advance", "@crossed the Rhine", 0.15, color=BLUE)],
         stamps=[stamp("May 1849", 0.05), stamp("June 1849", "@cleared")])),
    ("The rebels fell back behind the Murg, then into Rastatt. Besieged from late June, "
     "the fortress surrendered on the twenty-third of July 1849.",
     MAP("Rastatt, July 1849", (7.4, 9.4, 48.4, 49.5), camera_to=(7.9, 8.7, 48.7, 49.05),
         markers=[mk("Rastatt", "Rastatt fortress", "battle", 0.05, pulse=True)],
         texts=[txt("MURG", 8.40, 48.78, "small", "@Murg")],
         stamps=[stamp("29–30 June: Murg line broken", "@Murg"), stamp("30 June: siege begins", "@Besieged"),
                 stamp("23 July 1849: surrender", "@twenty-third")])),
    ("Courts martial followed. Twenty-seven men were executed in Baden.",
     archival("File:Entwaffnung der Insurgentenbesatzung von Rastatt.jpg",
              "Rastatt's garrison is disarmed, 23 July 1849", "zoom_out")),
], [D + "Badische_Revolution", W + "May_Uprising_in_Dresden"], pause=1.2)

scene("s19_exiles", "neutral", [
    ("One of Rastatt's defenders was a twenty-year-old student, Carl Schurz.",
     person("File:Carl Schurz 1879.jpg", "Carl Schurz", "Student revolutionary, later US senator, 1829–1906")),
    ("He escaped through an unguarded sewer, crossed the Rhine to France and reached Switzerland.",
     MAP("Schurz's escape, July 1849 (approx.)", (6.8, 10.0, 47.1, 49.3), camera_to=(6.8, 9.6, 47.1, 49.1),
         markers=[mk("Rastatt", "Rastatt", "battle", 0.05),
                  mk("Zurich", "Zurich", "city", "@Switzerland")],
         arrows=[arrow("Rastatt", (8.02, 48.86), "through the sewer to the Rhine", "route", "@sewer", 0.15, color=RED),
                 arrow((8.02, 48.86), "Zurich", "via France to Switzerland", "route", "@crossed the Rhine", 0.4,
                       via=[(7.75, 48.58), (7.4, 47.9), (7.6, 47.56)], color=RED)],
         texts=[txt("FRANCE", 7.2, 48.4, "medium", "@France"), txt("SWITZERLAND", 8.4, 47.2, "medium", "@Switzerland")])),
    ("About eighty thousand people left Baden after the revolution, most for America, "
     "driven mostly by poverty, some by politics.",
     MAP("Leaving for America", (-80.0, 16.0, 30.0, 60.0), basemap="1880",
         markers=[mk("Bremerhaven", "Bremerhaven", "city", 0.05), mk("Le Havre", "Le Havre", "city", 0.05),
                  mk("New York", "New York", "city", "@for America", pulse=True)],
         arrows=[arrow("Bremerhaven", "New York", None, "flow", "@left Baden", 0.4,
                       via=[(4.0, 53.3), (1.5, 51.0), (-6.0, 49.5), (-40.0, 45.0)], color=GOLD),
                 arrow("Le Havre", "New York", None, "flow", "@left Baden|+0.08", 0.4,
                       via=[(-6.0, 49.0), (-40.0, 43.5)], color=GOLD)],
         texts=[txt("ATLANTIC OCEAN", -38.0, 40.0, "large")])),
    ("German emigration to America peaked in 1854: over two hundred and fifteen thousand in one year.",
     stat("215,000", "German immigrants to the US in 1854 alone", "the peak year of German emigration")),
    ("These exiles were the Forty-Eighters. Hecker led Union regiments in the Civil War; "
     "Schurz became a Union general and a senator for Missouri.",
     MAP("The Forty-Eighters in America", (-80.0, 16.0, 30.0, 60.0), camera_to=(-95.0, -70.0, 35.0, 46.0), basemap="1880",
         markers=[mk("New York", "New York", "city", 0.05),
                  mk("Cincinnati", "Cincinnati", "city", 0.25), mk("Milwaukee", "Milwaukee", "city", 0.28),
                  mk("Belleville", "Belleville, Illinois: Hecker's farm", "dot", "@Hecker", pulse=True),
                  mk("St. Louis", "St. Louis: Schurz, senator for Missouri", "dot", "@senator", pulse=True)],
         arrows=[arrow("New York", "Cincinnati", None, "flow", 0.15, 0.2, color=GOLD),
                 arrow("New York", "Milwaukee", None, "flow", 0.18, 0.2, color=GOLD),
                 arrow("New York", "St. Louis", None, "flow", 0.21, 0.2, color=GOLD)])),
], [W + "Carl_Schurz", "https://journals.ku.edu/ygas/article/download/18285/16434/44889", D + "Badische_Revolution",
    "https://www.immigrantentrepreneurship.org/entries/volume-2-introduction/", W + "Forty-Eighters", W + "Friedrich_Hecker"])

scene("s20_why", "neutral", [
    ("So why did March 1848 end at Rastatt, sixteen months later? Three reasons.",
     timeline("Jul 1849")),
    ("First, power. In March the princes lost their nerve, not their armies. "
     "Once Austria and Prussia had their soldiers back, Frankfurt had nothing to set against them.",
     MAP("1. Power", (3.0, 27.0, 43.5, 56.5),
         regions=[reg("Prussia", historical=["Prussia"], at="@Austria and Prussia", color=BLUE),
                  reg("Austrian Empire", historical=HABSBURG, at="@Austria and Prussia|+0.03", color=RED)],
         markers=[mk("Berlin", "Berlin", "capital", "@Austria and Prussia"), mk("Vienna", "Vienna", "capital", "@Austria and Prussia"),
                  mk("Frankfurt", "Frankfurt: no army", "dot", "@Frankfurt had nothing", pulse=True)])),
    ("Second, division. Liberals wanted order and a monarchy, democrats a republic, "
     "and many liberals came to fear the street more than the kings.",
     compare("Liberals", ["Constitution and order", "Monarchy, reformed", "Feared the street"],
             "Democrats", ["Republic, popular sovereignty", "Equal vote for all men", "Ready to fight"], BLUE, RED)),
    ("Third, geography. No one could agree where Germany was. "
     "Austria could not join without breaking its empire, nor simply be left out.",
     MAP("3. Geography", (3.0, 27.0, 43.5, 56.5),
         regions=[reg("German Confederation", polygon=CONFED, at=0.05, color=GOLD),
                  reg("Austrian Empire", historical=HABSBURG, at="@Austria could not", hatch=True, color=RED)],
         texts=[txt("Where is Germany?", 11.5, 55.3, "large", "@No one could agree")])),
], [W + "Revolutions_of_1848_in_the_German_states", W + "Frankfurt_Parliament"], pause=0.9)

scene("s21_legacy", "hopeful", [
    ("Germany was united in 1871 by Prussian arms, without Austria, under that same Prince Wilhelm, now emperor: "
     "the Lesser German map, without the Frankfurt freedoms.",
     MAP("1871: unification from above", (3.0, 27.0, 43.5, 56.5), basemap="1880",
         regions=[reg("German Empire, 1871", historical=["Germany"], at="@1871", color=BLUE),
                  reg("Austria-Hungary", historical=["Austria Hungary"], at="@without Austria", hatch=True, color=GREY)],
         markers=[mk("Berlin", "Berlin: Emperor Wilhelm I", "capital", "@Prince Wilhelm", pulse=True)],
         stamps=[stamp("1871", "@1871")])),
    ("Those freedoms returned. In 1919 the Weimar Republic adopted black, red and gold and drew on the Frankfurt basic rights; "
     "so did West Germany's Basic Law in 1949. Article twenty-two still names black, red and gold as the federal flag.",
     MAP("The constitution's afterlife", (4.0, 16.5, 47.0, 55.2), basemap="modern",
         markers=[mk("Frankfurt", "Frankfurt, 1849", "capital", 0.05),
                  mk("Weimar", "Weimar, 1919", "capital", "@In 1919", pulse=True),
                  mk("Bonn", "Bonn, 1949", "capital", "@Basic Law", pulse=True)],
         stamps=[stamp("1849", 0.05), stamp("1919", "@In 1919"), stamp("1949", "@Basic Law")])),
    ("The revolution lost in 1849. Its constitution became a model for German democracy.",
     archival("File:Paulskirche Frankfurt am Main 2021-08-17 01.jpg", "St Paul's Church, Frankfurt, today", "zoom_out")),
], [W + "William_I,_German_Emperor", W + "Flag_of_Germany", D + "Paulskirchenverfassung"], pause=1.5)


# ----------------------------------------------------------------- resolve

def resolve(at, seg, where):
    if not isinstance(at, str):
        return at
    assert at.startswith("@"), (where, at)
    phrase, _, off = at[1:].partition("|")
    i = seg.find(phrase)
    if i < 0:
        raise SystemExit(f"{where}: phrase {phrase!r} not in segment: {seg[:60]}...")
    f = i / len(seg) - LEAD + (float(off) if off else 0.0)
    return round(min(0.95, max(0.05, f)), 2)


def walk(obj, seg, where):
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k == "at":
                obj[k] = resolve(v, seg, where)
            else:
                walk(v, seg, where)
    elif isinstance(obj, list):
        for x in obj:
            walk(x, seg, where)


scenes = []
all_sources = []
for sc in S:
    segs = sc["segs"]
    total = sum(len(t) for t, _ in segs)
    visuals = []
    for k, (text, vis) in enumerate(segs):
        vis = json.loads(json.dumps(vis))
        walk(vis, text, f"{sc['id']}[{k}]")
        if len(segs) > 1:
            vis["weight"] = round(len(text) / total * len(segs), 2)
        visuals.append(vis)
    for s in sc["sources"]:
        if s not in all_sources:
            all_sources.append(s)
    scenes.append({
        "id": sc["id"], "mood": sc["mood"], "narration": " ".join(t for t, _ in segs),
        "visuals": visuals, "pause_after": sc["pause_after"], "sources": sc["sources"],
    })

DESCRIPTION = (
    "In March 1848 the German revolution won almost everywhere: princes gave way, censorship ended, and Germany's first freely "
    "elected parliament met in Frankfurt's St Paul's Church. Sixteen months later the last revolutionaries surrendered at the "
    "fortress of Rastatt. Why?\n\n"
    "This explainer walks through the map Germany started from in 1815, what liberals and democrats actually wanted, the "
    "Schleswig-Holstein war, the impossible choice between a Greater and a Lesser Germany, the crown Friedrich Wilhelm IV refused, "
    "and the Prussian campaigns of 1849, with animated maps for every step. It ends with the Forty-Eighters who emigrated to America "
    "and the Frankfurt constitution's afterlife in 1919 and 1949."
)

script = {
    "slug": "1848-german-revolution-v2",
    "title": "Why Germany's 1848 Revolution Won Everything, Then Lost It",
    "description": DESCRIPTION,
    "tags": ["1848", "German Revolution", "Revolutions of 1848", "March Revolution", "Frankfurt Parliament", "Paulskirche",
             "German Confederation", "Greater Germany", "Lesser Germany", "Schleswig-Holstein", "Metternich",
             "Friedrich Wilhelm IV", "Forty-Eighters", "German history", "history explained", "animated map"],
    "language": "en",
    "voice": None,
    "scenes": scenes,
    "sources": all_sources,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", OUT)
