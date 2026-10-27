# Historical basemap polity names (Central Europe)

Source: [aourednik/historical-basemaps](https://github.com/aourednik/historical-basemaps), licensed **GPL-3.0**.
Files: `world_1815.geojson` (`MapSpec.basemap="1815"`, the default) and `world_1880.geojson` (`basemap="1880"`).

Use these strings in `MapRegion.historical` to highlight historical polities. Matching is
case-insensitive against the `NAME` attribute; if no `NAME` matches, the `ABBREVN` and then the
`SUBJECTO` (overlord) attribute are tried. All polygons with a matching name are merged, so
`"Prussia"` picks up all five Prussian exclaves. `SUBJECTO` matching means e.g.
`historical: ["Austrian Empire"]` gives the core empire only (NAME match wins); to add Lombardy-Venetia
list them explicitly: `["Austrian Empire", "Lombardy", "Venetia"]`.

Listed: every polity whose polygon intersects lon 0–30 E, lat 42–58 N. Columns: NAME | SUBJECTO (overlord) | polygon count.

Notes on 1815 (which is also what the map shows for 1848, the closest snapshot):
- The German Confederation itself is **not** a polygon; highlight its members (Prussia, Austrian Empire,
  Bavaria, Saxony, Hanover, Württemberg, Baden, ...) or use `MapRegion.polygon`.
- The United Kingdom of the Netherlands (incl. Belgium) has an empty NAME upstream; the loader names it
  `"United Kingdom of the Netherlands"`, and `"Netherlands"` matches too (ABBREVN).
- Upstream error fixed by the loader: the 1815 file puts Belgium inside the `"Austrian Empire"`
  multipolygon; it is moved to the Netherlands (see `_apply_errata` in `animate/_mapbase.py`).
- Austria is `"Austrian Empire"` (includes Bohemia, Hungary, Galicia); Lombardy, Venetia are separate.
- 1880 has unified `"Germany"`, `"Austria Hungary"` (no hyphen), `"Italy"`.

## 1815

| NAME | SUBJECTO | polygons |
|---|---|---|
| United Kingdom of the Netherlands *(also matches `Netherlands`)* | United Kingdom of Netherlands | 1 |
| Anhalt | Anhalt | 2 |
| Austrian Empire | Austrian Empire | 1 |
| Baden | Baden | 1 |
| Bavaria | Bavaria | 1 |
| Bremen | Bremen | 1 |
| Brunswick | Brunswick | 2 |
| Cuxhaven | Cuxhaven | 1 |
| Denmark | Denmark | 1 |
| Electoral Hesse | Electoral Hesse | 1 |
| Fivizzano | Fivizzano | 1 |
| France | France | 1 |
| Grand Duchy of Hesse | Grand Duchy of Hesse | 2 |
| Hamburg | Hamburg | 1 |
| Hanover | Hanover | 2 |
| Hohenzollern | Hohenzollern | 1 |
| Holstein | Holstein | 1 |
| Kingdom of Sardinia | Kingdom of Sardinia | 1 |
| Kingdom of the Two Sicilies | Kingdom of the Two Sicilies | 1 |
| Lippe-Detmold | Lippe-Detmold | 1 |
| Lombardy | Austrian Empire | 1 |
| Lucca | Lucca | 1 |
| Luxembourg | Luxembourg | 1 |
| Lübeck | Lübeck | 1 |
| Massa | Massa | 1 |
| Mecklenburg-Schwerin | Mecklenburg-Schwerin | 1 |
| Mecklenburg-Strelitz | Mecklenburg-Strelitz | 1 |
| Modena | Austrian Empire | 1 |
| Nassau | Nassau | 1 |
| Oldenburg | Oldenburg | 2 |
| Ottoman Empire | Ottoman Empire | 1 |
| Palatinate | Palatinate | 1 |
| Papal States | Papal States | 1 |
| Parma | Austrian Empire | 1 |
| Pontremoli | Pontremoli | 1 |
| Prussia | Prussia | 5 |
| Republic of Kraków | Republic of Kraków | 1 |
| Russian Empire | Russian Empire | 1 |
| San Marino | San Marino | 1 |
| Saxony | Saxony | 1 |
| Schaumburg-Lippe | Schaumburg-Lippe | 1 |
| Schleswig | Schleswig | 1 |
| Spain | Spain | 1 |
| Sweden–Norway |  | 1 |
| Switzerland | Switzerland | 1 |
| Thuringia | Thuringia | 1 |
| Tuscany | Tuscany | 1 |
| United Kingdom of Great Britain and Ireland | United Kingdom of Great Britain and Ireland | 1 |
| Venetia | Austrian Empire | 1 |
| Waldeck | Waldeck | 1 |
| Wetzlar | Wetzlar | 1 |
| Württemberg | Württemberg | 1 |
## 1880

| NAME | SUBJECTO | polygons |
|---|---|---|
| Austria Hungary | Austria Hungary | 1 |
| Belgium | Belgium | 1 |
| Bosnia-Herzegovina | Bosnia-Herzegovina | 1 |
| Bulgaria | Bulgaria | 1 |
| Denmark | Denmark | 1 |
| France | France | 1 |
| Germany | Germany | 2 |
| Greece | Greece | 1 |
| Italy | Italy | 1 |
| Luxembourg | Luxembourg | 1 |
| Montenegro | Montenegro | 1 |
| Netherlands | Netherlands | 1 |
| Romania | Romania | 1 |
| Russian Empire | Russian Empire | 1 |
| Serbia | Serbia | 1 |
| Spain | Spain | 1 |
| Sweden–Norway | Sweden–Norway | 1 |
| Switzerland | Switzerland | 1 |
| United Kingdom of Great Britain and Ireland | United Kingdom of Great Britain and Ireland | 1 |
