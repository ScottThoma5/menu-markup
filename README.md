# Menu Markup (prototype)

Photograph a restaurant menu → the app OCRs each dish + price, estimates the **raw ingredient cost** of a
typical restaurant portion (U.S. grocery/wholesale prices), and ranks dishes by **lowest markup** (best value).

> ⚠️ **Every cost here is an estimate.** "Est. cost" = food ingredients only. It excludes labor, rent,
> prep waste/trim, utilities and overhead, so it is *not* what the restaurant actually spends per plate.
> Typical restaurants aim for a 25–35 % food-cost ratio (≈3–4× markup on ingredients).

## Run (one command)

```bash
sudo apt-get install -y tesseract-ocr      # once (already installed on this box)
cd /workspace/menu-markup
./run.sh            # foreground, http://0.0.0.0:8000   (PORT=9000 ./run.sh to change)
./run.sh --bg       # background; logs in server.log, pid in server.pid
./stop.sh           # stop the background server
```
First run creates `.venv`, installs `requirements.txt`, and builds `menu_markup.db` from the seed files.

Open http://localhost:8000 → tap **Take photo / choose menu image** (on a phone this opens the camera) →
**Read menu**. The parsed items appear in an editable box (`Name | price | optional description`); fix OCR
mistakes or paste/type a menu and press **Recalculate**. Each result card has an ingredient breakdown and a
"Wrong dish?" dropdown to force a specific DB dish.

## How it works

| Step | File | Notes |
|---|---|---|
| OCR | `engine.py: ocr_image` | Pillow preprocessing (EXIF rotate, grayscale, resize to ~2000 px, autocontrast, denoise, Otsu binarize) → Tesseract 5 in several modes; the pass whose lines best match the dish DB wins. |
| Parsing | `parse_menu_text` | Finds a trailing price on each line (`$12.99`, `12.99`, `12`, OCR `S12.99`/`1O.99`), strips dot leaders (incl. OCR garbage like `.....cccceu`), skips ALL-CAPS section headers, attaches following lines as the description, handles a price on its own line, and accepts `Name \| price \| desc`. |
| Matching | `match_dish` | rapidfuzz over dish names + aliases; score = 0.4·token-sort similarity + 60·two-way word coverage (typo tolerant). ≥85 high, 76–85 medium. |
| Adjusting | `adjust_protein`, `scale_oz` | "Shrimp Fried Rice" matched to "Chicken Fried Rice" swaps chicken→shrimp; "12 oz Ribeye" sets the steak to 12 oz. |
| Fallback | `keyword_estimate` | No good match → build a recipe from keywords in name + description (protein + base such as pasta/bun/tortilla/salad/soup + extras such as cheese/fried/avocado). Always **low confidence**. |
| Costing | `cost_recipe` / `analyze_items` | Σ qty × unit price. Markup multiple = price ÷ cost; markup % = (price − cost) ÷ cost; food-cost % = cost ÷ price. Colors: green ≤3×, lime ≤4.5×, orange ≤7×, red >7×. |

### API
- `POST /api/upload` (multipart `file`) → `{ocr_text, items, results, disclaimer}`
- `POST /api/parse` `{"text": "..."}` → parse + analyze pasted text
- `POST /api/analyze` `{"items":[{"name","price","description","dish"?}]}` (`dish` = manual override, exact DB name)
- `GET /api/dishes`, `GET /api/ingredients`, `GET /api/health`, `POST /api/reload` (re-seed DB from files)

## Data

SQLite `menu_markup.db` is **generated** from two editable seed files (re-run `python seed.py` or
`POST /api/reload`; the seeder refuses to build if a recipe references an unknown ingredient):

- `data/ingredients.csv` — `name,unit,price_usd,category,source` (240 ingredients). Units: `lb`, `each`,
  `fl oz`, `oz`, `dash`. Categories `protein`/`seafood` are used for protein swaps.
- `data/dishes.json` — 164 dishes across American, burgers/sandwiches, salads, Italian, Mexican, Chinese,
  Japanese, Indian, Thai/Vietnamese/Korean/Mediterranean, breakfast, desserts, and drinks (soda, coffee, beer,
  wine by the glass, cocktails). Each: `name`, `cuisine`, `aliases`, `recipe` = `{ingredient: quantity in that
  ingredient's unit}` for one restaurant portion.

Tables: `ingredients(name, unit, price_per_unit, category, source_code)`, `sources(code, note)`,
`dishes(name, cuisine)`, `dish_aliases(dish_id, alias)`, `recipe_items(dish_id, ingredient_id, quantity)`.

**Price sources** (codes in the CSV; full notes in the `sources` table). Prices are rounded estimates from
general knowledge of 2024-26 U.S. prices — they were **not individually re-verified** against live data:
- `BLS` – informed by U.S. BLS CPI *Average Price Data* (retail, U.S. city average) ranges.
- `RET` – typical supermarket / warehouse-club shelf price.
- `WHL` – typical foodservice/wholesale price.
- `BEV` – typical wholesale keg/bottle cost (½-bbl domestic keg ≈ $150 / 1,984 fl oz; house wine ≈ $10/750 ml; well spirits $14–25/750 ml).

### Adding an ingredient
Append a row to `data/ingredients.csv`, e.g. `mascarpone,lb,7.99,dairy,RET`, then `.venv/bin/python seed.py`.

### Adding a dish
Add a line to `data/dishes.json`:
```json
{"name":"Shakshuka","cuisine":"breakfast","aliases":["shakshouka"],"recipe":{"eggs":2,"crushed tomatoes":0.4,"bell pepper":0.1,"yellow onion":0.08,"feta":0.04,"pita":1}}
```
then `.venv/bin/python seed.py` (or `curl -X POST localhost:8000/api/reload` while running). Aliases are the
cheapest way to improve matching — add the names restaurants actually print.

## Testing
```bash
.venv/bin/python test_e2e.py     # server must be running; regenerates samples/sample_menu.png + samples/sample_results.json
```
Also in `samples/`: `sample_menu_photo_like.jpg` (rotated/blurred/noisy/JPEG version) and its results, to show
how OCR degrades on real-world photos (10 of 12 read correctly, with 1 spurious line).

## Known limitations
- **Estimates only**: portion sizes are "typical" guesses; real portions, ingredient quality (e.g. wagyu,
  organic), regional prices and inflation vary a lot. Cocktail and wine costs depend heavily on the brand poured.
- **Tesseract OCR** struggles with stylized fonts, multi-column layouts, glare, curved pages and low light.
  Items may be missed, merged or mis-priced — hence the editable list. Multi-column menus are read column
  by column only when Tesseract's layout analysis gets it right.
- Multiple prices per line (small/large, glass/bottle) → only the last price is used.
- Quantities in names ("10 wings", "6 pc") aren't used except "N oz" for proteins.
- Keyword fallback is crude (low confidence); unusual/regional dishes need DB entries.
- No auth, no persistence of scans, single-process dev server.

## Next steps
- Expose it publicly for phone use (e.g. a Cloudflare Tunnel / ngrok, or deploy to Fly.io/Render/Railway;
  camera capture on mobile needs HTTPS for some browsers).
- Better OCR/parsing via a vision LLM (GPT-4o/Claude/Gemini) returning structured `{name, price, description}`,
  keeping Tesseract as the free fallback; or PaddleOCR/docTR for layout-aware local OCR.
- Pull live prices (BLS API series, USDA AMS, Kroger/Walmart product APIs) with dated source per ingredient.
- Let users adjust portion size per dish; save/share scans; per-restaurant history.
