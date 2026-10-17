"""Menu Markup — FastAPI app. Run: ./run.sh  (or: .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000)"""
import asyncio, os, time

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import engine

VERSION = "2026.10.16-fastocr"
app = FastAPI(title="Menu Markup")
# One OCR job at a time: a tiny host (0.1 CPU) gets slower, not faster, with parallel tesseract runs.
OCR_SLOTS = asyncio.Semaphore(int(os.environ.get("OCR_CONCURRENCY", "1")))

class Item(BaseModel):
    name: str
    price: float
    description: str = ""
    dish: str = ""          # optional manual override: exact dish name from /api/dishes

class ItemsIn(BaseModel):
    items: list[Item]

class TextIn(BaseModel):
    text: str

@app.get("/")
def index():
    return FileResponse(engine.ROOT / "static/index.html")

@app.get("/api/health")
def health():
    ings, dishes, _ = engine.load_catalog()
    return {"ok": True, "version": VERSION, "ocr_passes": engine.OCR_PASSES, "ingredients": len(ings), "dishes": len(dishes)}

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "image too large (25 MB max)")
    t0 = time.monotonic()
    try:
        async with OCR_SLOTS:   # OCR runs in a worker thread so /api/health stays responsive
            text = await run_in_threadpool(engine.ocr_image, data)
    except TimeoutError as e:
        raise HTTPException(504, str(e))
    except Exception as e:
        raise HTTPException(400, f"could not read image: {e}")
    items = engine.parse_menu_text(text)
    return {"ocr_text": text, "items": items, "ocr_seconds": round(time.monotonic() - t0, 2),
            **engine.analyze_items(items)}

@app.post("/api/parse")
def parse(body: TextIn):
    items = engine.parse_menu_text(body.text)
    return {"items": items, **engine.analyze_items(items)}

@app.post("/api/analyze")
def analyze(body: ItemsIn):
    return engine.analyze_items([i.model_dump() for i in body.items])

@app.get("/api/dishes")
def dishes():
    _, d, _ = engine.load_catalog()
    return sorted(({"name": x["name"], "cuisine": x["cuisine"], "aliases": x["aliases"]} for x in d.values()),
                  key=lambda x: x["name"])

@app.get("/api/ingredients")
def ingredients():
    ings, _, _ = engine.load_catalog()
    return sorted(ings.values(), key=lambda x: x["name"])

@app.post("/api/reload")
def reload():
    import seed
    seed.build()
    engine.reload_catalog()
    return health()

app.mount("/static", StaticFiles(directory=engine.ROOT / "static"), name="static")
