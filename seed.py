"""Build menu_markup.db (SQLite) from data/ingredients.csv and data/dishes.json.

Run:  python seed.py      (safe to re-run; the DB is rebuilt from the seed files)
"""
import csv, json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "menu_markup.db"

SOURCES = {
    "BLS": "Estimate informed by U.S. BLS CPI 'Average Price Data' (retail, U.S. city average) ranges for 2024-2025; rounded, not re-verified per item.",
    "RET": "Estimate of typical U.S. supermarket / warehouse-club (e.g. Costco, Walmart) shelf price 2025-2026; rounded.",
    "WHL": "Estimate of typical U.S. foodservice/wholesale (restaurant-supply) price 2025-2026; rounded.",
    "BEV": "Estimate from typical U.S. wholesale keg / 750 ml bottle costs (e.g. 1/2-bbl domestic keg ~$150 = 1,984 fl oz; house wine ~$10/bottle; well spirits $14-25/750 ml).",
}

SCHEMA = """
DROP TABLE IF EXISTS recipe_items; DROP TABLE IF EXISTS dish_aliases;
DROP TABLE IF EXISTS dishes; DROP TABLE IF EXISTS ingredients; DROP TABLE IF EXISTS sources;
CREATE TABLE sources (code TEXT PRIMARY KEY, note TEXT NOT NULL);
CREATE TABLE ingredients (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, unit TEXT NOT NULL,
  price_per_unit REAL NOT NULL, category TEXT, source_code TEXT REFERENCES sources(code));
CREATE TABLE dishes (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, cuisine TEXT);
CREATE TABLE dish_aliases (dish_id INTEGER REFERENCES dishes(id), alias TEXT NOT NULL);
CREATE TABLE recipe_items (
  dish_id INTEGER REFERENCES dishes(id), ingredient_id INTEGER REFERENCES ingredients(id),
  quantity REAL NOT NULL);
"""

def build(db_path=DB_PATH):
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO sources VALUES (?,?)", SOURCES.items())
    ing_ids = {}
    with open(ROOT / "data/ingredients.csv", newline="") as f:
        for row in csv.DictReader(f):
            cur = con.execute(
                "INSERT INTO ingredients(name,unit,price_per_unit,category,source_code) VALUES (?,?,?,?,?)",
                (row["name"].strip(), row["unit"].strip(), float(row["price_usd"]), row["category"], row["source"]))
            ing_ids[row["name"].strip()] = cur.lastrowid
    dishes = json.loads((ROOT / "data/dishes.json").read_text())
    errors = []
    for d in dishes:
        cur = con.execute("INSERT INTO dishes(name,cuisine) VALUES (?,?)", (d["name"], d.get("cuisine")))
        did = cur.lastrowid
        con.executemany("INSERT INTO dish_aliases VALUES (?,?)", [(did, a) for a in d.get("aliases", [])])
        for ing, qty in d["recipe"].items():
            if ing not in ing_ids:
                errors.append(f"{d['name']}: unknown ingredient '{ing}'")
                continue
            con.execute("INSERT INTO recipe_items VALUES (?,?,?)", (did, ing_ids[ing], qty))
    if errors:
        con.close()
        sys.exit("Seed errors:\n  " + "\n  ".join(errors))
    con.commit()
    con.close()
    print(f"Seeded {db_path.name}: {len(ing_ids)} ingredients, {len(dishes)} dishes")

if __name__ == "__main__":
    build()
