#!/usr/bin/env python3
"""Harvest Radishchev Museum wallpapers and enrich them from artkatalog.radmuseumart.ru.

Reads the monthly wallpaper pages (artist/title/technique), matches each work in
the museum's online catalogue, and emits a JSON manifest for the Lumen Gallery
fork: src/config/art.ts is rebuilt from tools/radmuseum.json + the original tools.

Run on cybervolga next to the wallpaper folder:
    python3 tools/harvest_radmuseum.py [--target-dir /mnt/homestorage/media/Картины] [--out tools/out/radmuseum.json]

Be polite: one second between HTTP requests, both sites are museum servers.
"""
import argparse
import html
import json
import os
import re
import struct
import sys
import time
import urllib.parse
import urllib.request

MUSEUM = "https://www.radmuseumart.ru"
KATALOG = "https://artkatalog.radmuseumart.ru"
LIST = MUSEUM + "/projects/wallpapersarts/"
UA = {"User-Agent": "Mozilla/5.0 (home wallpaper research)"}
EN_MONTHS = {m: i + 1 for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}
RU_MONTHS = {m: i + 1 for i, m in enumerate(
    "январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь".split())}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    time.sleep(1)
    return data.decode("utf-8", "replace")


def norm(s):
    return re.sub(r"[^а-яёa-z0-9]+", " ", (s or "").lower()).strip()


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def surname_of(artist):
    m = re.search(r"[А-ЯЁA-Z][а-яёa-z-]+", artist or "")
    return m.group(0).lower() if m else ""


def initial_of(artist):
    m = re.search(r"\b([А-ЯЁA-Z])\.?", artist or "")
    return m.group(1).lower() if m else ""


# --- phase 1: wallpaper pages ---------------------------------------------

def wallpaper_pages():
    links, page = [], 1
    while True:
        s = get(LIST + "?PAGEN_1=%d" % page)
        new = [u for u in re.findall(r'href="(/projects/wallpapersarts/\d+/)"', s) if u not in links]
        if not new:
            return links
        links += new
        page += 1


def parse_wallpaper(s):
    head = re.search(r"<h1>([^<]*)</h1>(.*?)(?:reviews-reply-form|$)", s, re.S)
    if not head:
        return None
    title, body = head.groups()
    imgs = []
    for path, folder, w, h in re.findall(
            r'href="(/images/desktop-wallpaper/([^"]+?)/(?:\d\d_)?(\d+)[xх](\d+)\.jpg)"', body):
        if "MOBILE" not in folder.upper():
            imgs.append((path, folder, int(w), int(h)))
    if not imgs:
        return None
    exact = [i for i in imgs if i[2:] == (1920, 1080)]
    path, folder, _, _ = exact[0] if exact else max(imgs, key=lambda i: i[2] * i[3])
    year = re.search(r"(20\d\d)", folder)
    month = None
    for name, n in EN_MONTHS.items():
        if name in folder.lower():
            month = n
    if not month:
        words = re.findall(r"[а-яё]+", title.lower())
        month = RU_MONTHS.get(words[-1]) if words else None
    if not year or not month:
        return None
    intro = body.split('href="/images/desktop-wallpaper', 1)[0]
    artist = work = technique = ""
    m = re.search(r"<b>(.*?)</b>", intro, re.S)
    if m:
        parts = [text(x) for x in re.split(r"<br\s*/?>", m.group(1))]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            artist, work = parts[0], parts[1]
            if len(parts) >= 3:
                technique = parts[2]
        elif parts:
            work = parts[0]
    return {
        "year": int(year.group(1)), "month": month, "artist": artist, "title": work,
        "technique": technique, "page": None,  # filled by caller
        "image": MUSEUM + urllib.parse.quote(path),
        "wide": any(i[2] / i[3] >= 1.25 for i in imgs if i[2] and i[3] and i[2:]),
    }


# --- phase 2: catalogue matching ------------------------------------------

def katalog_search(query):
    s = get(KATALOG + "/ru/fullsearch?sp1=" + urllib.parse.quote(query))
    items = []
    for m in re.finditer(
            r'<a href="/ru/fullsearch/(\d+)".*?<span class="an">([^<]*)</span><br>(.*?)<br>([^<]*)',
            s, re.S):
        items.append({
            "id": m.group(1), "author": html.unescape(m.group(2)).strip(),
            "title": text(m.group(3)), "date": m.group(4).strip(),
        })
    return items


def katalog_record(rec_id):
    s = get(KATALOG + "/ru/" + rec_id)
    m = re.search(r"Главная\s*•\s*.*?(.*?)(?:Главная\s*$)", text(s), re.S)
    block = m.group(1) if m else ""
    tags = []
    tm = re.search(r"Предметный указатель:(.*?)(?:[А-ЯЁ][а-яё]+ [А-ЯЁ]\.|[А-ЯЁ][а-яё]+ [а-яё]+,)", block)
    if tm:
        tags = [t.strip() for t in re.split(r",\s*", tm.group(1)) if t.strip()]
    return {"url": KATALOG + "/ru/" + rec_id, "tags": tags}


def match_katalog(entry):
    if not entry["title"]:
        return None
    queries = [entry["title"]]
    tail = norm(entry["title"]).split()
    if len(tail) > 1:
        queries.append(tail[-1])  # "Прилив в бурю. Трепор" -> "Трепор"
    sn, ini = surname_of(entry["artist"]), initial_of(entry["artist"])
    for q in queries:
        for item in katalog_search(q):
            if sn and surname_of(item["author"]) != sn:
                continue
            if ini and initial_of(item["author"]) and initial_of(item["author"]) != ini:
                continue
            rec = katalog_record(item["id"])
            rec.update(item)
            return rec
    return None


# --- phase 3: local files ---------------------------------------------------

def jpeg_size(path):
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        size = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + size
    return None


def local_match(target_dir, year, month):
    prefix = "%04d-%02d " % (year, month)
    for name in sorted(os.listdir(target_dir)):
        if name.startswith(prefix) and name.lower().endswith(".jpg"):
            return name
    return None


# --- phase 4: classification -------------------------------------------------

def classify(entry, kat):
    title = norm(entry["title"])
    tags = norm(" ".join(kat["tags"])) if kat else ""
    blob = title + " " + tags
    if re.search(r"япон", blob):
        return "Japanese Prints"
    if re.search(r"кита|инд(и|ий|ия)|персидск", blob):
        return "Asian Art"
    if re.search(r"ислам|миниатюр", blob):
        return "Islamic Art"
    if re.search(r"марина|морск|море|шторм|прибой|прилив|отлив|волн|берег|причал|гаван|корабл|судно|барк|фрегат|парус", blob):
        return "Seascapes"
    if re.search(r"натюрморт|цвет|букет|фрукт|ягод|лимон", blob):
        return "Flowers & Still Life"
    if re.search(r"пейзаж| вид на|вид гор|лес|горы|роща|поле|луг|зима|весна|лето|осень|дорог|улиц|переулок", blob):
        return "Landscapes"
    return "Paintings"


def flags(entry, kat):
    blob = norm(entry["title"]) + " " + norm(" ".join(kat["tags"])) if kat else norm(entry["title"])
    people = 1 if re.search(r"портрет|библейск|мифолог|бытовой жанр|аллегор|историческ", blob) else 0
    animals = 1 if re.search(r"анималистик|охота", blob) else 0
    return people, animals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-dir", default="/mnt/homestorage/media/Картины")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "out", "radmuseum.json"))
    args = ap.parse_args()

    entries = []
    pages = wallpaper_pages()
    print("wallpaper pages: %d" % len(pages), file=sys.stderr)
    for i, link in enumerate(pages, 1):
        entry = parse_wallpaper(get(MUSEUM + link))
        if not entry:
            print("unparsed page: %s" % link, file=sys.stderr)
            continue
        entry["page"] = MUSEUM + link
        entry["key"] = "%04d-%02d" % (entry["year"], entry["month"])
        entry["file"] = local_match(args.target_dir, entry["year"], entry["month"])
        size = jpeg_size(os.path.join(args.target_dir, entry["file"])) if entry["file"] else None
        entry["w"], entry["h"] = (size or (0, 0))
        kat = match_katalog(entry) if entry["title"] else None
        entry["catalog"] = kat
        entry["collection"] = classify(entry, kat)
        entry["p"], entry["n"] = flags(entry, kat)
        entries.append(entry)
        print("[%d/%d] %s | %s — %s | %s | %s" % (
            i, len(pages), entry["key"], entry["artist"] or "?", entry["title"] or "?",
            "catalog:%s" % kat["id"] if kat else "catalog:-", entry["collection"]), file=sys.stderr)

    out = {"source": "radmuseum", "entries": entries}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    matched = sum(1 for e in entries if e["catalog"])
    print("done: %d entries, %d matched in catalogue, %d with local file" % (
        len(entries), matched, sum(1 for e in entries if e["file"])), file=sys.stderr)


if __name__ == "__main__":
    main()
