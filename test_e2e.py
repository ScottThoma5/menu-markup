"""End-to-end test: generate the sample menu, upload it to the running server, check the output.
Usage: .venv/bin/python test_e2e.py [base_url]   (default http://localhost:8000)"""
import json, subprocess, sys, urllib.request, uuid
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
ROOT = Path(__file__).parent
subprocess.run([sys.executable, str(ROOT / "make_sample_menu.py")], check=True, cwd=ROOT)
img = (ROOT / "samples/sample_menu.png").read_bytes()
b = uuid.uuid4().hex
body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"menu.png\"\r\n"
        "Content-Type: image/png\r\n\r\n").encode() + img + f"\r\n--{b}--\r\n".encode()
req = urllib.request.Request(f"{BASE}/api/upload", body, {"Content-Type": f"multipart/form-data; boundary={b}"})
res = json.load(urllib.request.urlopen(req, timeout=120))
(ROOT / "samples/sample_results.json").write_text(json.dumps(res, indent=2))

expected = {"Guacamole", "Buffalo Wings", "Grilled Ribeye Steak", "Mozzarella Sticks", "Grilled Salmon", "Chicken Parmesan",
            "House Wine (Glass)", "Margherita Pizza", "Cheeseburger", "Caesar Salad", "Domestic Draft Beer", "Fountain Soda"}
r = res["results"]
got = {x["matched_dish"] for x in r}
mults = [x["markup_multiple"] for x in r]
checks = {
    "12 items parsed": res["count"] == 12,
    "all expected dishes matched": expected <= got,
    "prices read correctly": sorted(x["price"] for x in r) == sorted([12.99, 9.49, 8.99, 15.99, 21.0, 27.5, 38.0, 16.0, 11.0, 3.49, 7.0, 10.0]),
    "costs positive and below price": all(0 < x["est_cost"] < x["price"] for x in r),
    "sorted by lowest markup": mults == sorted(mults),
}
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
print(f"{'Dish':28} {'Price':>7} {'Est.cost':>8} {'Markup':>7}")
for x in r:
    print(f"{x['name'][:28]:28} {x['price']:7.2f} {x['est_cost']:8.2f} {x['markup_multiple']:6.1f}x")
sys.exit(0 if all(checks.values()) else 1)
