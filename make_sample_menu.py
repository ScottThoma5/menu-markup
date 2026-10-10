"""Generate samples/sample_menu.png — a realistic-looking 12-item menu for end-to-end testing."""
from PIL import Image, ImageDraw, ImageFont
F = "/usr/share/fonts/truetype/dejavu/"
title, head = ImageFont.truetype(F + "DejaVuSerif-Bold.ttf", 54), ImageFont.truetype(F + "DejaVuSans-Bold.ttf", 34)
item, desc = ImageFont.truetype(F + "DejaVuSans-Bold.ttf", 30), ImageFont.truetype(F + "DejaVuSans.ttf", 22)
MENU = [
    ("STARTERS", [("Buffalo Chicken Wings", "12.99", "ten wings, celery, blue cheese"),
                  ("Mozzarella Sticks", "9.49", "house marinara"),
                  ("Chips and Guacamole", "8.99", "")]),
    ("MAINS", [("Classic Cheeseburger", "15.99", "lettuce, tomato, pickles, fries"),
               ("Chicken Parmesan", "21.00", "spaghetti, marinara, mozzarella"),
               ("Grilled Atlantic Salmon", "27.50", "rice, asparagus, lemon butter"),
               ("12 oz Ribeye Steak", "38.00", "mashed potatoes, broccoli"),
               ("Margherita Pizza", "16.00", "fresh mozzarella, basil"),
               ("Caesar Salad", "11.00", "romaine, parmesan, croutons")]),
    ("DRINKS", [("Fountain Soda", "3.49", ""), ("Draft Beer", "7.00", ""), ("House Wine", "10.00", "")]),
]
W, H = 1240, 1754
img = Image.new("RGB", (W, H), (252, 248, 238))
d = ImageDraw.Draw(img)
d.text((W // 2, 80), "THE CORNER BISTRO", font=title, fill=(40, 30, 20), anchor="mm")
y = 170
for section, items in MENU:
    d.text((90, y), section, font=head, fill=(140, 40, 30)); y += 60
    for name, price, sub in items:
        d.text((90, y), name, font=item, fill=(20, 20, 20))
        pw = d.textlength(price, font=item); nw = d.textlength(name, font=item)
        d.text((W - 90 - pw, y), price, font=item, fill=(20, 20, 20))
        x = 90 + nw + 14
        while x < W - 90 - pw - 14:
            d.text((x, y + 4), ".", font=item, fill=(120, 120, 120)); x += 14
        y += 42
        if sub:
            d.text((110, y), sub, font=desc, fill=(90, 90, 90)); y += 36
        y += 14
    y += 20
img.save("samples/sample_menu.png")
print("saved samples/sample_menu.png", img.size)
