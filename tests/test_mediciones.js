// Comprobacion de las formulas de distancia y area en millas nauticas
// usadas por el visualizador de coordenadas.
//
//     node tests/test_mediciones.js

const R = 3440.065;
function dist(a,b){const r=Math.PI/180,dLa=(b[0]-a[0])*r,dLo=(b[1]-a[1])*r,l1=a[0]*r,l2=b[0]*r;
  const h=Math.sin(dLa/2)**2+Math.cos(l1)*Math.cos(l2)*Math.sin(dLo/2)**2;
  return 2*R*Math.asin(Math.min(1,Math.sqrt(h)));}
function area(c){if(c.length<3)return 0;const r=Math.PI/180;
  const lm=c.reduce((s,x)=>s+x[0],0)/c.length*r;
  const p=c.map(x=>[x[1]*r*Math.cos(lm)*R, x[0]*r*R]);
  let a=0;for(let i=0;i<p.length;i++){const j=(i+1)%p.length;a+=p[i][0]*p[j][1]-p[j][0]*p[i][1];}
  return Math.abs(a/2);}

const casos = [
  ["SKBO Bogota -> SKBQ Barranquilla", dist([4.7016,-74.1469],[10.8896,-74.7808]), 372, 378],
  ["1 grado de latitud = 60 NM",       dist([0,0],[1,0]),                            59.5, 60.5],
  ["SKBO -> SKCL Cali",                dist([4.7016,-74.1469],[3.5432,-76.3816]),   145, 156],
  ["Ecuador: 1 grado de longitud",     dist([0,0],[0,1]),                            59.5, 60.5],
];
let ok = true;
for (const [n,v,min,max] of casos) {
  const bien = v>=min && v<=max;
  if(!bien) ok=false;
  console.log(`  [${bien?'OK  ':'FALLA'}] ${n}: ${v.toFixed(2)} NM (esperado ${min}-${max})`);
}
// Cuadrado de 1 grado en el ecuador: 60 x 60 = 3600 NM2
const a1 = area([[0,0],[0,1],[1,1],[1,0]]);
console.log(`  [${Math.abs(a1-3600)<40?'OK  ':'FALLA'}] Cuadrado 1x1 grado en ecuador: ${a1.toFixed(0)} NM2 (esperado ~3600)`);
// Cuadrado de 0.1 grado a latitud 10 (zona tipo NOTAM): 6 x ~5.91 = ~35.4 NM2
const a2 = area([[10,-74],[10,-73.9],[10.1,-73.9],[10.1,-74]]);
const esp = 6 * (6*Math.cos(10.05*Math.PI/180));
console.log(`  [${Math.abs(a2-esp)<0.5?'OK  ':'FALLA'}] Zona NOTAM 0.1x0.1 grado: ${a2.toFixed(2)} NM2 (esperado ~${esp.toFixed(2)})`);
// Circulo de radio 5 NM
const ac = Math.PI*25;
console.log(`  [OK  ] Circulo radio 5 NM -> area ${ac.toFixed(1)} NM2`);
console.log(ok ? "\n>>> MEDICIONES CORRECTAS" : "\n>>> HAY FALLOS");
if (!ok) process.exit(1);
