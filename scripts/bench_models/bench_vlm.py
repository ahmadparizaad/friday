"""Benchmark local VLMs for (a) UI element grounding and (b) goal-done verification.

Usage: python bench_vlm.py <mlx-vlm-repo-id>
Screens are SYNTHETIC (PIL-drawn Mail/Finder-like windows, 1280x800) with known
element boxes, because real screen capture needs Screen Recording permission.
Grounding is scored as click-point-inside-box. Qwen3-VL emits coords on a 0-1000 grid.
"""
import json, random, re, sys, time
from PIL import Image, ImageDraw, ImageFont
import mlx.core as mx
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config

W, H = 1280, 800
FONT = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
LABELS = ["Send", "Attach", "Reply", "Delete", "Archive", "Downloads", "Search", "Compose", "Cancel", "Save"]
FILES = ["invoice_march.pdf", "photo.png", "notes.txt", "setup.dmg", "report_q3.xlsx", "receipt.pdf"]

def make_screen(seed, sent=False):
    r = random.Random(seed)
    im = Image.new("RGB", (W, H), (236, 236, 240)); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 36], fill=(200, 200, 205)); d.text((W // 2 - 30, 8), r.choice(["Mail", "Finder"]), font=FONT, fill=(20, 20, 20))
    boxes = {}
    labels = r.sample(LABELS, 6)
    x = 220
    for lab in labels:  # toolbar buttons at shuffled positions
        w = 24 + len(lab) * 11
        y = r.choice([56, 56, 60])
        d.rounded_rectangle([x, y, x + w, y + 36], 8, fill=(255, 255, 255), outline=(150, 150, 155))
        d.text((x + 12, y + 8), lab, font=FONT, fill=(0, 0, 0)); boxes[lab] = (x, y, x + w, y + 36); x += w + 24
    d.rectangle([0, 36, 200, H], fill=(220, 222, 230))
    for i, s in enumerate(["Recents", "Downloads", "Desktop", "Documents"]):
        d.text((20, 70 + i * 40), s, font=FONT, fill=(30, 30, 30)); boxes.setdefault("side_" + s, (10, 62 + i * 40, 190, 92 + i * 40))
    files = r.sample(FILES, 5)
    for i, f in enumerate(files):
        y = 130 + i * 44; d.text((240, y), f, font=FONT, fill=(0, 0, 0)); boxes["file_" + f] = (230, y - 6, 700, y + 30)
    if sent:
        d.rounded_rectangle([W - 380, 60, W - 20, 120], 12, fill=(40, 40, 44)); d.text((W - 350, 80), "Message sent", font=FONT, fill=(255, 255, 255))
    return im, boxes, labels, files

def parse_xy(txt):
    nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", txt)]
    if len(nums) >= 4: return ((nums[0] + nums[2]) / 2, (nums[1] + nums[3]) / 2)
    if len(nums) >= 2: return (nums[0], nums[1])
    return None

def main(repo):
    t0 = time.time(); model, proc = load(repo); cfg = load_config(repo); load_s = time.time() - t0
    mx.reset_peak_memory()
    def ask(img_path, q, max_tokens=60):
        p = apply_chat_template(proc, cfg, q, num_images=1)
        t = time.time(); res = generate(model, proc, p, [img_path], max_tokens=max_tokens, verbose=False, temperature=0.0)
        text = res.text if hasattr(res, "text") else res[0]
        return text, time.time() - t, res
    S = sys.argv[2] if len(sys.argv) > 2 else "."
    rows = []; lat = []
    ask_path = f"{S}/warm.png"; make_screen(0)[0].save(ask_path); ask(ask_path, "Describe this screen briefly.", 20)
    for seed in range(1, 11):
        im, boxes, labels, files = make_screen(seed); path = f"{S}/s{seed}.png"; im.save(path)
        targets = [labels[0], labels[3], "file_" + files[1]]
        for tg in targets:
            name = tg.replace("file_", "")
            q = f'Find the UI element "{name}". Reply with its bounding box as [x1, y1, x2, y2] on a 0-1000 grid.'
            txt, dt, res = ask(path, q); xy = parse_xy(txt); lat.append(dt)
            hit = False
            if xy:
                px, py = xy[0] / 1000 * W, xy[1] / 1000 * H; b = boxes[tg]
                hit = b[0] <= px <= b[2] and b[1] <= py <= b[3]
            rows.append(dict(kind="ground", seed=seed, target=name, hit=hit, sec=round(dt, 2), out=txt.strip()[:60]))
    ver = []
    for seed in range(20, 26):
        for sent in (False, True):
            im, *_ = make_screen(seed, sent); path = f"{S}/v.png"; im.save(path)
            txt, dt, res = ask(path, "Goal: send an email. Does this screen show a 'Message sent' confirmation? Answer only YES or NO.", 5)
            ans = "yes" in txt.lower(); ver.append(ans == sent)
    g = [r for r in rows if r["kind"] == "ground"]
    summ = dict(model=repo, load_s=round(load_s, 1), peak_gb=round(mx.get_peak_memory() / 1e9, 2),
                grounding=f"{sum(r['hit'] for r in g)}/{len(g)}", verify=f"{sum(ver)}/{len(ver)}",
                median_sec_per_query=round(sorted(lat)[len(lat) // 2], 2))
    print(json.dumps(dict(summary=summ, rows=rows), indent=1))

main(sys.argv[1])
