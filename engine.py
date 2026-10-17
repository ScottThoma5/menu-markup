"""Menu Markup core: OCR, menu-line parsing, dish matching and ingredient costing."""
from __future__ import annotations
import io, os, re, sqlite3
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps
import pytesseract
from rapidfuzz import fuzz, process

ROOT = Path(__file__).parent
DB_PATH = ROOT / "menu_markup.db"

# ---------------------------------------------------------------- database
def db():
    if not DB_PATH.exists():
        import seed
        seed.build(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

@lru_cache(maxsize=1)
def load_catalog():
    con = db()
    ings = {r["name"]: dict(r) for r in con.execute(
        "SELECT i.*, s.note AS source_note FROM ingredients i LEFT JOIN sources s ON s.code=i.source_code")}
    dishes = {}
    for r in con.execute("SELECT * FROM dishes"):
        dishes[r["id"]] = {"id": r["id"], "name": r["name"], "cuisine": r["cuisine"], "aliases": [], "recipe": []}
    for r in con.execute("SELECT * FROM dish_aliases"):
        dishes[r["dish_id"]]["aliases"].append(r["alias"])
    for r in con.execute("SELECT ri.dish_id, ri.quantity, i.name FROM recipe_items ri JOIN ingredients i ON i.id=ri.ingredient_id"):
        dishes[r["dish_id"]]["recipe"].append((r["name"], r["quantity"]))
    con.close()
    choices = {}  # normalized text -> dish id
    for d in dishes.values():
        for t in [d["name"], *d["aliases"]]:
            choices.setdefault(norm(t), d["id"])
    return ings, dishes, choices

def reload_catalog():
    load_catalog.cache_clear()

# ---------------------------------------------------------------- OCR
# Tuned for tiny hosts (e.g. Render free: 0.1 CPU / 512 MB). Override via env vars.
OCR_MAX_WIDTH = int(os.environ.get("OCR_MAX_WIDTH", "1600"))    # downscale wider images to this
OCR_MIN_WIDTH = int(os.environ.get("OCR_MIN_WIDTH", "1400"))    # upscale narrower images to OCR_MAX_WIDTH
OCR_MAX_PIXELS = int(os.environ.get("OCR_MAX_PIXELS", "4000000"))  # cap total pixels (very tall menus)
OCR_PASSES = max(1, min(4, int(os.environ.get("OCR_PASSES", "1"))))  # 1 = single best pass; up to 4 variants
OCR_TIMEOUT = float(os.environ.get("OCR_TIMEOUT", "50"))        # seconds per tesseract call
OCR_GOOD_ENOUGH = 0.9   # stop extra passes early when >=90% of parsed lines match known dishes

def preprocess(img: Image.Image) -> Image.Image:
    if img.format == "JPEG":   # fast DCT-domain downscale while decoding big phone photos
        img.draft("L", (OCR_MAX_WIDTH, OCR_MAX_WIDTH))  # keeps both sides >= max width (EXIF-rotation safe)
    img = ImageOps.exif_transpose(img).convert("L")
    w, h = img.size
    s = 1.0
    if w > OCR_MAX_WIDTH or w < OCR_MIN_WIDTH:
        s = OCR_MAX_WIDTH / w
    if w * h * s * s > OCR_MAX_PIXELS:
        s = (OCR_MAX_PIXELS / (w * h)) ** 0.5
    if abs(s - 1.0) > 0.01:
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img.filter(ImageFilter.SHARPEN)

def _otsu(img: Image.Image) -> int:
    hist = img.histogram()[:256]
    total = sum(hist); sum_all = sum(i * h for i, h in enumerate(hist))
    wb = sb = 0; best_t, best_v = 128, -1.0
    for t in range(256):
        wb += hist[t]
        if wb == 0 or wb == total:
            continue
        sb += t * hist[t]
        mb, mf = sb / wb, (sum_all - sb) / (total - wb)
        v = wb * (total - wb) * (mb - mf) ** 2
        if v > best_v:
            best_t, best_v = t, v
    return best_t

def _quality(items) -> float:
    """Score an OCR pass: items whose names match the dish DB count more than garbage lines."""
    q = 0.0
    for it in items:
        _, score = match_dish(it["name"])
        q += 1.0 if score >= MED else 0.4 if score >= 55 else 0.1
    return q

def ocr_image(data: bytes, passes: int | None = None) -> str:
    """OCR a menu photo. Runs the single best variant (Otsu-binarized, psm 4) by default;
    OCR_PASSES=2..4 also tries other variants and keeps the one whose lines best match known dishes."""
    passes = OCR_PASSES if passes is None else max(1, min(4, passes))
    base = preprocess(Image.open(io.BytesIO(data)))
    den = base.filter(ImageFilter.MedianFilter(3))
    t = _otsu(den)
    binary = den.point(lambda p: 255 if p > t else 0)
    # psm 4 = single column of variable-size text, psm 6 = uniform block
    variants = ((lambda: binary, 4), (lambda: base, 4), (lambda: den, 4), (lambda: base, 6))[:passes]
    best, best_q = "", -1.0
    for get_img, psm in variants:
        try:
            txt = pytesseract.image_to_string(get_img(), config=f"--oem 1 --psm {psm}", timeout=OCR_TIMEOUT)
        except RuntimeError as e:   # pytesseract raises RuntimeError on timeout
            if best:
                break
            raise TimeoutError("reading the menu took too long; try a smaller or clearer photo") from e
        if passes == 1:
            return txt
        items = parse_menu_text(txt)
        q = _quality(items)
        if q > best_q:
            best, best_q = txt, q
        if items and q >= OCR_GOOD_ENOUGH * len(items):
            break
    return best

# ---------------------------------------------------------------- parsing
PRICE_RE = re.compile(
    r"""(?:^|[\s.·•…_\-–—|:])        # separator before price
        (?P<dollar>[$S5]\s?)?         # $ (OCR often reads $ as S or 5 -> handled below)
        (?P<num>\d{1,3}(?:[.,]\s?\d{2})?|\d{1,3}[.,]\d)
        \s*(?:USD)?\s*$""", re.X)
LEADERS = re.compile(r"[.·•…_]{2,}|\s[-–—|]+\s*$|[|]")

def _fix_ocr_digits(s: str) -> str:
    # common OCR confusions inside a trailing price token
    m = re.search(r"[\$S]?[\dOoIl|]{1,3}[.,][\dOoIl]{2}\s*$", s)
    if m:
        tok = m.group(0)
        fixed = tok[0] + tok[1:].translate(str.maketrans("OoIl|", "00111")) if tok[0] in "$S" else tok.translate(str.maketrans("OoIl|", "00111"))
        s = s[: m.start()] + fixed
    return s

def split_price(line: str):
    line = _fix_ocr_digits(line.rstrip())
    m = PRICE_RE.search(line)
    if not m:
        return None, line
    num = m.group("num").replace(" ", "").replace(",", ".")
    dollar = m.group("dollar")
    name = line[: m.start()].strip()
    # A leading '5' captured as the dollar sign: "Burger 512.99" is ambiguous; only treat
    # S/5 as $ when the remaining number has cents.
    if dollar and dollar.strip() == "5" and "." not in num:
        num = "5" + num
    try:
        price = float(num)
    except ValueError:
        return None, line
    if "." not in num and not dollar:
        # bare integer: accept only if it looks like a price column (name has letters, 1..299)
        if not (1 <= price <= 299):
            return None, line
    if not (0.5 <= price <= 999):
        return None, line
    return price, name

JUNK_TOKEN = re.compile(r"^[cCeEuUoOsSaArRvVgGnNmwW.,:;'`]{6,}$")

def clean_name(s: str) -> str:
    # Dot leaders ("Burger ........ 12.99") are often OCR'd as ",....cccceuceu"; cut at the first leader run.
    s = re.split(r"\s?[.,·…:;_]{2,}|\s[.,·…]\s", s, maxsplit=1)[0]
    s = " ".join(t for t in s.split() if not (JUNK_TOKEN.match(t) and sum(t.lower().count(c) for c in "ce") >= 3))
    toks = s.split()
    # trailing leader debris: "oo", "00", "1.1" (dots misread as zeros/ones); keeps real tokens like "Combo 1"
    while toks and re.fullmatch(r"[oOcCeE.,'`\-~=]{1,3}|[0oO]{2,3}|[01][.,][01]", toks[-1]):
        toks.pop()
    s = LEADERS.sub(" ", " ".join(toks))
    s = re.sub(r"^[\W_]+|[\W_]+$", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()

def is_header(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and line.upper() == line and len(line.split()) <= 4

def parse_menu_text(text: str) -> list[dict]:
    """Turn OCR/pasted text into [{name, price, description}]. Supports 'Name | 12.99 | desc'."""
    items, pending_name = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 2:
            continue
        if line.count("|") >= 1 and re.search(r"\|\s*\$?\d", line):  # manual pipe format
            parts = [p.strip() for p in line.split("|")]
            try:
                price = float(parts[1].lstrip("$S").replace(",", "."))
                items.append({"name": clean_name(parts[0]), "price": price,
                              "description": " ".join(parts[2:]).strip()})
                pending_name = None
                continue
            except (ValueError, IndexError):
                pass
        price, rest = split_price(line)
        name = clean_name(rest) if price is not None else None
        if price is not None and name and re.search(r"[A-Za-z]{2,}", name):
            items.append({"name": name, "price": price, "description": ""})
            pending_name = None
        elif price is not None and not name and pending_name:  # price on its own line
            items.append({"name": pending_name, "price": price, "description": ""})
            pending_name = None
        elif price is None:
            if is_header(line):
                pending_name = clean_name(line) if len(line) > 3 else None
                continue
            txt = clean_name(line)
            if not re.search(r"[A-Za-z]{3,}", txt):
                continue
            if items and pending_name is None:
                d = items[-1]["description"]
                items[-1]["description"] = (d + " " + txt).strip()[:300]
            pending_name = txt
    return items

# ---------------------------------------------------------------- matching
STOP = {"the", "our", "house", "famous", "classic", "signature", "fresh", "homemade", "with", "w", "and",
        "a", "of", "served", "choice", "your", "style", "chef", "chefs", "special", "new"}

def norm(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("'", "")
    s = re.sub(r"\b\d+\s*(oz|ounce|pc|pcs|piece|inch|in)\b", " ", s)
    s = re.sub(r"[^a-z\s]", " ", s)
    return " ".join(w for w in s.split() if w not in STOP) or s.strip()

def _cover(a: list[str], b: list[str]) -> float:
    """Fraction of words in a that have a close (typo-tolerant) match in b."""
    if not a:
        return 0.0
    return sum(1 for w in a if any(fuzz.ratio(w, x) >= 80 for x in b)) / len(a)

def match_dish(name: str):
    """Return (dish_id, score 0-100). Score blends whole-string similarity with word coverage in
    BOTH directions so 'Chicken' doesn't match 'Chicken Noodle Soup' and 'Lobster Ravioli' doesn't
    match 'Lobster Roll'."""
    _, _, choices = load_catalog()
    q = norm(name)
    if not q:
        return None, 0.0
    keys = list(choices.keys())
    cands = {t for t, _, _ in process.extract(q, keys, scorer=fuzz.token_set_ratio, limit=10)}
    cands |= {t for t, _, _ in process.extract(q, keys, scorer=fuzz.WRatio, limit=10)}
    qw = q.split()
    top, top_score = None, 0.0
    for text in cands:
        tw = text.split()
        cov = (_cover(qw, tw) + _cover(tw, qw)) / 2
        s = 0.4 * fuzz.token_sort_ratio(q, text) + 60 * cov
        if s > top_score:
            top, top_score = text, s
    return (choices[top] if top else None), round(top_score, 1)

# keyword fallback: (pattern, {ingredient: qty}, label). Mains: first protein hit + first base hit + extras.
PROTEINS = [
    (r"lobster", {"lobster meat": 0.3}, "lobster"), (r"crab", {"crab meat": 0.25}, "crab"),
    (r"scallop", {"scallops": 0.35}, "scallops"), (r"shrimp|prawn", {"shrimp": 0.35}, "shrimp"),
    (r"salmon", {"salmon fillet": 0.45}, "salmon"), (r"tuna|ahi", {"sushi grade tuna": 0.3}, "tuna"),
    (r"cod|haddock|halibut|fish|tilapia|snapper|bass|mahi", {"cod": 0.45}, "fish"),
    (r"mussel|clam", {"mussels": 1.0}, "shellfish"), (r"calamari|squid|octopus", {"calamari": 0.4}, "squid"),
    (r"filet|tenderloin", {"beef tenderloin": 0.5}, "tenderloin"), (r"ribeye|rib eye|prime rib", {"ribeye steak": 0.8}, "ribeye"),
    (r"strip|t-?bone|porterhouse", {"ny strip steak": 0.8}, "strip steak"), (r"short rib|brisket|pot roast", {"beef short ribs": 0.6}, "braised beef"),
    (r"steak|sirloin|beef|asada", {"sirloin steak": 0.45}, "steak"), (r"burger|patty", {"ground beef": 0.375}, "beef patty"),
    (r"lamb|gyro", {"lamb": 0.4}, "lamb"), (r"duck", {"duck breast": 0.5}, "duck"),
    (r"bacon", {"bacon": 0.15}, "bacon"), (r"sausage|chorizo|pepperoni", {"italian sausage": 0.3}, "sausage"),
    (r"pork|carnitas|ham", {"pork shoulder": 0.4}, "pork"), (r"turkey", {"turkey deli": 0.3}, "turkey"),
    (r"boneless", {"chicken breast": 0.5}, "boneless chicken"), (r"wing", {"chicken wings": 1.0}, "wings"), (r"chicken|pollo", {"chicken breast": 0.4}, "chicken"),
    (r"tofu", {"tofu": 0.4}, "tofu"), (r"paneer", {"paneer": 0.25}, "paneer"), (r"egg|omelet", {"eggs": 3}, "eggs"),
]
BASES = [
    (r"pizza|flatbread", {"pizza dough": 0.6, "mozzarella": 0.3, "marinara sauce": 0.25}, "pizza base"),
    (r"pasta|spaghetti|penne|linguine|fettuccine|rigatoni|ravioli|gnocchi|noodle|mac", {"dried pasta": 0.3, "marinara sauce": 0.3, "parmesan": 0.02}, "pasta"),
    (r"ramen|pho|udon", {"ramen noodles": 0.3, "chicken stock": 16}, "noodle soup"),
    (r"burger|sandwich|sub|hoagie|melt|panini|slider", {"hamburger bun": 1, "iceberg lettuce": 0.04, "tomato": 0.08, "mayonnaise": 0.5}, "bread/bun"),
    (r"taco", {"corn tortilla": 3, "salsa": 0.1, "cabbage": 0.05}, "tacos"),
    (r"burrito|wrap|quesadilla", {"flour tortilla": 1.6, "white rice": 0.12, "black beans": 0.15, "monterey jack": 0.08}, "tortilla"),
    (r"salad|bowl|greens", {"mixed greens": 0.25, "tomato": 0.08, "cucumber": 0.08, "vinaigrette": 1.5}, "salad"),
    (r"soup|chowder|bisque|stew", {"chicken stock": 10, "yellow onion": 0.08, "carrot": 0.08}, "soup"),
    (r"curry|masala|korma", {"basmati rice": 0.2, "crushed tomatoes": 0.25, "coconut milk": 3, "spices and seasoning": 0.25}, "curry"),
    (r"fried rice|rice|risotto|teriyaki|stir", {"white rice": 0.25, "broccoli": 0.1, "soy sauce": 1}, "rice"),
    (r"roll|sushi|maki|nigiri", {"sushi rice": 0.18, "nori sheet": 1, "rice vinegar": 0.4}, "sushi rice"),
    (r"fries|chips", {"russet potato": 0.5, "frying oil (absorbed/amortized)": 1.5}, "fries"),
]
EXTRAS = [
    (r"cheese|cheddar|queso", {"cheddar cheese": 0.08}, "cheese"), (r"mozzarella|parm", {"mozzarella": 0.1}, "mozzarella"),
    (r"avocado|guac", {"avocado": 0.5}, "avocado"), (r"mushroom", {"mushrooms": 0.12}, "mushrooms"),
    (r"fried|crispy|tempura|battered|breaded", {"all-purpose flour": 0.08, "frying oil (absorbed/amortized)": 2}, "fried"),
    (r"potato|mash", {"russet potato": 0.4}, "potato side"), (r"cream|alfredo", {"heavy cream": 3}, "cream"),
]
DRINKS = [(r"\bipa\b|\bale\b|lager|stout|pilsner|beer|draft", "Craft Draft Beer"), (r"wine|cabernet|merlot|pinot|chardonnay|sauvignon|riesling|malbec|ros[eé]", "House Wine (Glass)"),
          (r"margarita", "Margarita"), (r"martini|cocktail|mule|spritz|negroni|sour|paloma|mojito|daiquiri", "Cosmopolitan"),
          (r"whiskey|whisky|bourbon|scotch|rye", "Old Fashioned"), (r"latte|cappuccino|mocha|americano", "Cappuccino"),
          (r"coffee|espresso", "Drip Coffee"), (r"tea\b", "Hot Tea"), (r"soda|cola|pop\b|sprite|lemonade", "Fountain Soda"),
          (r"juice", "Orange Juice"), (r"shake|smoothie", "Milkshake")]

def keyword_estimate(name: str, desc: str):
    ings, dishes, _ = load_catalog()
    text = f"{name} {desc}".lower()
    by_name = {d["name"]: d for d in dishes.values()}
    for pat, dish_name in DRINKS:
        if re.search(pat, name.lower()):
            return by_name[dish_name]["recipe"], [f"drink keyword → {dish_name}"]
    recipe, hits = {}, []
    for group in (PROTEINS, BASES):
        for pat, rec, label in group:
            if re.search(pat, text):
                for k, v in rec.items():
                    recipe[k] = recipe.get(k, 0) + v
                hits.append(label)
                break
    for pat, rec, label in EXTRAS:
        if re.search(pat, text):
            for k, v in rec.items():
                recipe[k] = recipe.get(k, 0) + v
            hits.append(label)
    if not hits:
        # generic entrée plate: protein-free guess
        recipe = {"chicken breast": 0.3, "white rice": 0.2, "mixed greens": 0.1, "vegetable oil": 1}
        hits = ["no keywords — generic plate guess"]
    recipe["spices and seasoning"] = recipe.get("spices and seasoning", 0) + 0.1
    return list(recipe.items()), hits

def adjust_protein(name: str, recipe):
    """If the menu name names a different protein than the DB recipe (e.g. 'Shrimp Fried Rice' matched
    'Chicken Fried Rice'), swap the recipe's main protein for the named one, same weight."""
    ings, _, _ = load_catalog()
    low = name.lower()
    hit = next(((pat, rec) for pat, rec, _ in PROTEINS if re.search(pat, low)), None)
    if not hit:
        return recipe, []
    pat, rec = hit
    new_ing, new_qty = next(iter(rec.items()))
    prots = [(n, q) for n, q in recipe if ings[n]["category"] in ("protein", "seafood")]
    if not prots:
        if ings[new_ing]["unit"] == "lb":
            return list(recipe) + [(new_ing, round(new_qty * 0.5, 3))], [f"added {new_ing} (named in menu item, half portion)"]
        return recipe, []
    main, qty = max(prots, key=lambda x: x[1] * ings[x[0]]["price_per_unit"])
    if main == new_ing or re.search(pat, main) or ings[main]["unit"] != ings[new_ing]["unit"]:
        return recipe, []
    return [(new_ing if n == main else n, q) for n, q in recipe], [f"swapped {main} → {new_ing} (named in menu item)"]

def scale_oz(name: str, recipe, notes):
    """'12 oz Ribeye' -> set the main (lb-priced) protein to 12/16 lb."""
    m = re.search(r"\b(\d{1,2})\s*-?\s*(oz|0z|ounce)", name.lower())
    if not m:
        return recipe, notes
    oz = int(m.group(1))
    ings, _, _ = load_catalog()
    prots = [(n, q) for n, q in recipe if ings[n]["category"] in ("protein", "seafood") and ings[n]["unit"] == "lb"]
    if not prots or not (3 <= oz <= 48):
        return recipe, notes
    main = max(prots, key=lambda x: x[1] * ings[x[0]]["price_per_unit"])[0]
    return [(n, oz / 16 if n == main else q) for n, q in recipe], notes + [f"{main} set to {oz} oz from menu"]

# ---------------------------------------------------------------- costing
def cost_recipe(recipe):
    ings, _, _ = load_catalog()
    lines, total = [], 0.0
    for name, qty in recipe:
        ing = ings[name]
        c = qty * ing["price_per_unit"]
        total += c
        lines.append({"ingredient": name, "quantity": round(qty, 3), "unit": ing["unit"],
                      "unit_price": ing["price_per_unit"], "cost": round(c, 2), "source": ing["source_code"]})
    return round(total, 2), sorted(lines, key=lambda l: -l["cost"])

HIGH, MED = 85.0, 76.0

def analyze_items(items: list[dict]) -> dict:
    _, dishes, _ = load_catalog()
    by_name = {d["name"].lower(): d for d in dishes.values()}
    results = []
    for it in items:
        name = (it.get("name") or "").strip()
        try:
            price = float(it.get("price"))
        except (TypeError, ValueError):
            continue
        if not name or price <= 0:
            continue
        desc = it.get("description") or ""
        override = (it.get("dish") or "").strip().lower()
        dish_id, score = match_dish(name)
        if override and override in by_name:
            d = by_name[override]
            recipe, method, conf, matched, notes = d["recipe"], "manual", "high", d["name"], ["dish chosen manually"]
            score = 100.0
        elif dish_id and score >= MED:
            d = dishes[dish_id]
            recipe, notes = adjust_protein(name, d["recipe"])
            recipe, notes = scale_oz(name, recipe, notes)
            method, matched = "database", d["name"]
            conf = "high" if score >= HIGH else "medium"
        else:
            recipe, notes = keyword_estimate(name, desc)
            method, conf, matched = "keyword", "low", None
        cost, breakdown = cost_recipe(recipe)
        cost = max(cost, 0.05)
        mult = price / cost
        results.append({
            "name": name, "description": desc, "price": round(price, 2),
            "matched_dish": matched, "match_score": score, "method": method, "confidence": conf,
            "notes": notes, "est_cost": cost, "markup_multiple": round(mult, 2),
            "markup_pct": round((price - cost) / cost * 100), "food_cost_pct": round(cost / price * 100, 1),
            "breakdown": breakdown,
        })
    results.sort(key=lambda r: r["markup_multiple"])
    for i, r in enumerate(results, 1):
        r["rank"] = i
        m = r["markup_multiple"]
        r["band"] = "green" if m <= 3 else "lime" if m <= 4.5 else "orange" if m <= 7 else "red"
    return {
        "disclaimer": "All ingredient costs are ESTIMATES of raw food cost per typical restaurant portion at "
                      "U.S. grocery/wholesale prices. They exclude labor, rent, prep waste, and overhead.",
        "count": len(results), "results": results,
    }
