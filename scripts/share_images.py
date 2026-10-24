"""Share images (1200x630 PNG), one per page, written to docs/share/<key>.png.

A card is redrawn only when its content hash changes (data/share_hashes.json): the hash covers
the card text, the team color, the headshot bytes and TEMPLATE. No NBA or team logos, no
jersey images. Player cards use the committed face2 headshot crop (data/share/headshots/, 400px
from nba-headshots face2); the pages themselves keep the face-tight images.
"""
import hashlib
import json
import os

from PIL import Image, ImageDraw, ImageFont

TEMPLATE = "v4"  # bump whenever the layout code changes, so every card is redrawn
W, H = 1200, 630
HERE = os.path.dirname(os.path.abspath(__file__))
BOLD = os.path.join(HERE, "fonts", "Inter-Bold.otf")
MED = os.path.join(HERE, "fonts", "Inter-Medium.otf")
INK, MUTED, BG = (29, 29, 31), (110, 110, 115), (245, 245, 247)
MAX_BYTES = 300 * 1024


def _rgb(hexs):
    hexs = (hexs or "#3b82f6").lstrip("#")
    return tuple(int(hexs[i:i + 2], 16) for i in (0, 2, 4))


def _font(path, size):
    return ImageFont.truetype(path, size)


def _fit(draw, text, path, size, max_w, min_size=22):
    """Largest font (down to min_size) that fits max_w; text is cut with a period-free ellipsis only as a last resort."""
    while size > min_size:
        f = _font(path, size)
        if draw.textlength(text, font=f) <= max_w:
            return f, text
        size -= 2
    f = _font(path, min_size)
    while draw.textlength(text, font=f) > max_w and len(text) > 4:
        text = text[:-2].rstrip() + "…"
    return f, text


def _mix(c, w, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c, w))


def _card(card, shot):
    color = _rgb(card["color"])
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 18], fill=color)
    d.rounded_rectangle([40, 50, W - 40, H - 40], radius=28, fill=(255, 255, 255))
    left, right = 90, W - 90
    text_right = right
    if card["kind"] == "player":
        size = 210
        cx, cy = W - 100 - size // 2, 80 + size // 2
        d.ellipse([cx - size // 2 - 10, cy - size // 2 - 10, cx + size // 2 + 10, cy + size // 2 + 10], fill=color)
        d.ellipse([cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 2], fill=_mix(color, (255, 255, 255), .82))
        if shot:
            face = shot.convert("RGBA").resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
            img.paste(face, (cx - size // 2, cy - size // 2), Image.composite(face.split()[3], Image.new("L", (size, size), 0), mask))
        else:
            initials = "".join(w[0] for w in card["name"].replace("'", "").split()[:2]).upper()
            fi = _font(BOLD, 84)
            d.text((cx - d.textlength(initials, font=fi) / 2, cy - 52), initials, font=fi, fill=color)
        text_right = cx - size // 2 - 40
    f, name = _fit(d, card["name"], BOLD, 72, text_right - left)
    d.text((left, 110), name, font=f, fill=INK)
    f2, line = _fit(d, card["line"], MED, 32, text_right - left)
    d.text((left, 210), line, font=f2, fill=MUTED)
    d.rectangle([left, 270, left + 90, 276], fill=color)
    stats = card["stats"][:3]
    gap = 24
    n = max(1, len(stats))
    bw = (right - left - gap * (n - 1)) // n
    # One value size for the whole row: the largest that fits every box.
    vsize = min(_fit(d, val, BOLD, 54, bw - 40, 24)[0].size for _, val in stats) if stats else 54
    for i, (lab, val) in enumerate(stats):
        x0 = left + i * (bw + gap)
        d.rounded_rectangle([x0, 330, x0 + bw, 500], radius=18, fill=_mix(color, (255, 255, 255), .9))
        fl, lab = _fit(d, lab.upper(), MED, 22, bw - 40, 16)
        d.text((x0 + 22, 352), lab, font=fl, fill=MUTED)
        fv, val = _fit(d, val, BOLD, vsize, bw - 40, 24)
        d.text((x0 + 22, 400 + (54 - fv.size) // 2), val, font=fv, fill=INK)
    d.text((left, 528), "hoopsmatic.com/jerseys", font=_font(BOLD, 28), fill=INK)
    fs = _font(MED, 22)
    tag = "HoopsMatic ranking of official NBA jersey sales lists"
    d.text((right - d.textlength(tag, font=fs), 534), tag, font=fs, fill=MUTED)
    return img


def _save(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG", optimize=True)
    if os.path.getsize(path) > MAX_BYTES:
        img.convert("P", palette=Image.ADAPTIVE, colors=256).save(path, "PNG", optimize=True)


def render_all(cards, out_dir, hashes_path, shots_dir):
    try:
        with open(hashes_path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    new, rendered = {}, 0
    for c in cards:
        shot_path = os.path.join(shots_dir, c["slug"] + ".webp") if c.get("slug") else None
        shot_bytes = open(shot_path, "rb").read() if shot_path and os.path.exists(shot_path) else b""
        h = hashlib.sha256((TEMPLATE + json.dumps(c, sort_keys=True, ensure_ascii=False)).encode("utf-8") + shot_bytes).hexdigest()
        path = os.path.join(out_dir, c["key"] + ".png")
        new[c["key"]] = h
        if old.get(c["key"]) == h and os.path.exists(path):
            continue
        shot = Image.open(shot_path) if shot_bytes else None
        _save(_card(c, shot), path)
        rendered += 1
    # Drop images of pages that no longer exist.
    keep = {os.path.normpath(os.path.join(out_dir, k + ".png")) for k in new}
    for dp, _, fs in os.walk(out_dir):
        for fn in fs:
            p = os.path.normpath(os.path.join(dp, fn))
            if p.endswith(".png") and p not in keep:
                os.remove(p)
    with open(hashes_path, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=1, sort_keys=True)
        f.write("\n")
    return {"total": len(new), "rendered": rendered}
