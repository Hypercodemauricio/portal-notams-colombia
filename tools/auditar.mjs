import pw from '/home/claude/.npm-global/lib/node_modules/playwright/index.js';
const { chromium } = pw; import fs from 'fs';
/*  Auditoria de contraste de TODA la pagina, no de una lista escogida a
    mano. Se recorren los elementos que contienen texto directamente -no
    contenedores- para que cada trozo se mida con SU propio color: medir
    un boton entero usando el color del boton daba 2.68:1 cuando el peor
    pixel era en realidad la flecha, que tiene color propio y se lee bien. */
const [,, URL_PAGINA, SALIDA, PREPARAR] = process.argv;
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  args: ['--autoplay-policy=no-user-gesture-required'] });
const ctx = await b.newContext({ viewport: { width: 1440, height: 950 } });
const pg = await ctx.newPage();
for (const p of ['**://cdn.tailwindcss.com/**','**://cdnjs.cloudflare.com/**',
                 '**://fonts.googleapis.com/**','**://fonts.gstatic.com/**','**://api.avwx.rest/**'])
  await pg.route(p, r => r.abort());
await pg.goto(URL_PAGINA, { waitUntil: 'domcontentloaded' });
await pg.waitForTimeout(3000);
if (PREPARAR) { await pg.evaluate(PREPARAR); await pg.waitForTimeout(2500); }

const nodos = await pg.evaluate(() => {
  const salida = [];
  const visto = new Set();
  for (const e of document.querySelectorAll('body *')) {
    // solo elementos con texto propio, no contenedores
    let propio = '';
    for (const n of e.childNodes) if (n.nodeType === 3) propio += n.textContent;
    propio = propio.trim();
    if (propio.length < 2) continue;
    const c = getComputedStyle(e);
    if (c.visibility === 'hidden' || c.display === 'none' || parseFloat(c.opacity) < 0.15) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.top > innerHeight) continue;
    const ruta = e.className && typeof e.className === 'string'
      ? '.' + e.className.trim().split(/\s+/).slice(0,2).join('.')
      : e.tagName.toLowerCase();
    const clave = ruta + '|' + Math.round(r.y);
    if (visto.has(clave)) continue;
    visto.add(clave);
    salida.push({ sel: ruta, txt: propio.replace(/\s+/g,' ').slice(0,26),
      color: c.color, size: parseFloat(c.fontSize), w: c.fontWeight,
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  }
  return salida;
});

const CSS = `*, *::before, *::after { color: transparent !important;
  -webkit-text-fill-color: transparent !important; text-shadow: none !important; }`;
fs.mkdirSync(SALIDA, { recursive: true });
const ts = [0, 2, 4, 6, 8];
for (const t of ts) {
  await pg.evaluate(tt => { const v=document.getElementById('bgVideo'); if(v){v.pause(); v.currentTime=tt;} }, t);
  await pg.waitForTimeout(500);
  await pg.screenshot({ path: `${SALIDA}/con-${t}.png` });
  const h = await pg.addStyleTag({ content: CSS });
  await pg.waitForTimeout(250);
  await pg.screenshot({ path: `${SALIDA}/sin-${t}.png` });
  await pg.evaluate(el => el.remove(), h);
  await pg.waitForTimeout(150);
}
fs.writeFileSync(`${SALIDA}/meta.json`, JSON.stringify({ nodos, ts }));
await b.close();
console.log(`${nodos.length} trozos de texto localizados`);
