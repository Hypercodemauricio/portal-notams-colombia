import json, numpy as np, re, sys
from PIL import Image
DIR = sys.argv[1]
d = json.load(open(f"{DIR}/meta.json"))
def lin(c):
    c = c/255.0
    return np.where(c <= 0.03928, c/12.92, ((c+0.055)/1.055)**2.4)
def lum(a): return 0.2126*lin(a[...,0]) + 0.7152*lin(a[...,1]) + 0.0722*lin(a[...,2])
pares = {t: (np.asarray(Image.open(f"{DIR}/con-{t}.png").convert("RGB"), dtype=np.float64),
             np.asarray(Image.open(f"{DIR}/sin-{t}.png").convert("RGB"), dtype=np.float64))
         for t in d["ts"]}

filas = []
for o in d["nodos"]:
    x, y, w, h = o["box"]
    m = re.findall(r'[\d.]+', o["color"])[:3]
    if len(m) < 3: continue
    Lt = float(lum(np.array([float(v) for v in m]).reshape(1,1,3))[0,0])
    peor, cuando = 1e9, None
    for t, (con, sin) in pares.items():
        c = con[max(y,0):y+h, max(x,0):x+w]; s = sin[max(y,0):y+h, max(x,0):x+w]
        if c.size == 0: continue
        mask = np.abs(c - s).max(axis=2) > 40
        if mask.sum() < 12: continue
        Lb = lum(s)[mask]
        r = (np.maximum(Lt, Lb) + 0.05) / (np.minimum(Lt, Lb) + 0.05)
        v = float(np.percentile(r, 5))
        if v < peor: peor, cuando = v, t
    if cuando is None: continue
    grande = o["size"] >= 24 or (o["size"] >= 18.66 and float(o["w"]) >= 700)
    umbral = 3.0 if grande else 4.5
    filas.append((peor, umbral, o, cuando))

filas.sort(key=lambda f: f[0])
malos = [f for f in filas if f[0] < f[1]]
print(f"{len(filas)} trozos de texto medidos, {len(malos)} por debajo de WCAG AA\n")
if malos:
    print(f"  {'contraste':>10}  {'min':>4}  {'px':>4}  selector                texto")
    for peor, u, o, t in malos:
        print(f"  {peor:>8.2f}:1  {u:>4}  {o['size']:>4.0f}  {o['sel'][:22]:<22}  {o['txt']}")
else:
    print("  todo pasa AA")
print(f"\n  los 3 mas holgados: " +
      ", ".join(f"{o['sel'][:16]} {p:.1f}:1" for p,u,o,t in filas[-3:]))
sys.exit(1 if malos else 0)
