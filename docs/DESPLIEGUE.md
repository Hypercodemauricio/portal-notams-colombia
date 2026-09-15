# Publicar el portal en un servidor

Guía para dejar el portal corriendo en internet con dominio y HTTPS, partiendo
de una máquina limpia.

---

## Antes de nada: el token de AVWX

El portal ya no manda el token al navegador. METAR y TAF se piden a
`/api/metar/{icao}` y `/api/taf/{icao}`, que son los que hablan con AVWX desde
el servidor, y la credencial vive solo en `.env`, que no se versiona.

Si vienes de una instalación anterior, **genera un token nuevo en
[avwx.rest](https://avwx.rest/) antes de desplegar y revoca el que tuvieras.**
Las versiones anteriores del portal lo llevaban dentro del HTML, así que
cualquiera que hubiera abierto la página pudo quedárselo. Ponlo en `.env`:

```
AVWX_TOKEN=...
```

Una credencial que estuvo en el frontend no se arregla moviéndola de sitio:
solo se arregla revocándola.

---

## Qué necesita este portal (y qué no sirve)

No es una web estática. Necesita tres cosas que la mayoría de los planes
gratuitos no dan:

- un proceso Python corriendo todo el tiempo,
- Chrome headless cada 15 minutos, para el extractor,
- un disco que sobreviva a los reinicios: `sistema_rac.db` son 15 MB de índice
  que cuesta un buen rato reconstruir.

Por eso no sirven Vercel, Netlify, Cloudflare Pages, ni el plan gratuito de
Render (disco efímero, sin cron y se apaga a los 15 minutos de inactividad).
Hace falta una máquina virtual de verdad.

| Opción | Gratis | Notas |
|---|---|---|
| **Google Cloud `e2-micro`** | permanente | 1 GB de RAM y 1 GB de salida al mes. Va justo, pero no desaparece. **Lo que usamos.** |
| Oracle Cloud Always Free | permanente | 12 GB de RAM y 10 TB de salida, pero ver abajo. |
| AWS `t3.micro` | 12 meses | Después se paga. |

### Por qué no Oracle, si da doce veces más memoria

Porque es probable que te apague el servidor. Oracle considera **inactiva** una
instancia Always Free si durante 7 días seguidos el percentil 95 de CPU está
por debajo del 20 %, la red por debajo del 20 % y —en las ARM— la memoria por
debajo del 20 %. Cumplidas las tres, la recupera.

Este portal encaja en ese perfil: en una máquina de 12 GB, el 20 % de memoria
son 2,4 GB y la API gasta unos pocos cientos de megas; la CPU solo se mueve los
cuarenta segundos que corre el extractor cada cuarto de hora; y una herramienta
interna no genera tráfico. Se puede esquivar dejando un generador de carga
artificial, pero eso es quemar CPU para engañar a un detector.

Se suma que en junio de 2026 Oracle recortó el nivel gratuito a la mitad sin
anunciarlo y apagó instancias, y que la capacidad ARM suele estar agotada.

Google Cloud va más justa de recursos —de ahí el swap y el límite de salida—
pero no se desvanece. Para una herramienta de trabajo eso pesa más.

---

## Crear la máquina en Google Cloud

Estos pasos son los únicos que hay que hacer a mano, en la consola web.

**1. Cuenta.** [console.cloud.google.com](https://console.cloud.google.com/).
Google pide una tarjeta para verificar identidad; el nivel gratuito no cobra,
pero la tarjeta es obligatoria. Crea un proyecto nuevo.

**2. La instancia.** *Compute Engine → VM instances → Create instance*.

| Campo | Valor | Por qué |
|---|---|---|
| Región | `us-central1`, `us-west1` o `us-east1` | **Solo estas tres son gratis.** En cualquier otra se factura. |
| Tipo | `e2-micro` | El único incluido en el nivel gratuito permanente. |
| Disco | 30 GB, *Standard persistent disk* | 30 GB es el tope gratuito. No elijas SSD: ese sí se cobra. |
| Imagen | Ubuntu 24.04 LTS | Es sobre la que está probado el instalador. |
| Firewall | marca *Allow HTTP* y *Allow HTTPS* | Abre los puertos 80 y 443. |

**3. Entrar.** El botón **SSH** de la consola abre una terminal en el navegador.
No hace falta instalar nada en tu equipo.

> **Sobre el gratis:** es una `e2-micro` al mes, 30 GB de disco y **1 GB de
> salida de datos mensual**. El portal manda unos 550 KB en la primera visita
> (el vídeo de fondo es casi todo) y mucho menos en las siguientes, porque se
> cachea 30 días. Da para unas 1.800 visitas nuevas al mes: de sobra para un
> equipo, pero tenlo presente si el enlace se difunde.

Con 1 GB de RAM, Chrome headless se queda corto y el kernel lo mata a mitad de
la extracción. El instalador lo detecta y crea 2 GB de swap solo; no tienes que
hacer nada, pero por eso la primera extracción tarda más de lo normal.

---

## Instalación

Probado en Ubuntu 22.04 y 24.04, en ARM y en x86.

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Hypercodemauricio/portal-notams-colombia.git
cd portal-notams-colombia
sudo bash deploy/install.sh
```

El script instala las dependencias, Chrome (detecta solo si la máquina es ARM
o x86), crea el entorno virtual, registra el servicio systemd, configura nginx
y la rotación de logs, programa el cron cada 15 minutos y lanza una primera
extracción.

### Completar el `.env`

`install.sh` crea `/home/ubuntu/proyecto_notams/.env` a partir del ejemplo.
Hay que rellenarlo:

```bash
sudo -u ubuntu nano /home/ubuntu/proyecto_notams/.env
```

| Variable | Qué poner |
|---|---|
| `AVWX_TOKEN` | El token **nuevo**. Sin esto, METAR y TAF salen vacíos. |
| `GEMINI_API_KEY` | Clave de [Google AI Studio](https://aistudio.google.com/). Sin esto, el resto del portal funciona pero los resúmenes con IA no. |
| `CORS_ORIGINS` | Tu dominio, cuando lo tengas. |

```bash
sudo systemctl restart notams
```

### El índice de reglamentos

No se construye solo. La primera vez, en el servidor:

```bash
cd /home/ubuntu/proyecto_notams
sudo -u ubuntu .venv/bin/python3 rac_indexar.py
```

Descarga los 16 RAC y arma `sistema_rac.db`. Tarda un rato y baja unos 50 MB.
Alternativa más rápida: copiar `sistema_rac.db` desde tu equipo con `scp`, que
es el mismo archivo.

> El host principal de la Aerocivil falla a menudo al servir los PDF. El
> catálogo tiene un segundo host de respaldo y lo usa solo; si ves reintentos
> en el log, es eso y no hay que hacer nada.

---

## Abrir el puerto

En Oracle y en Google Cloud no basta con el firewall de Ubuntu: hay que abrir
el puerto también en la consola web del proveedor (*Security List* /
*Firewall rules*), para los puertos **80** y **443**.

En Oracle, además, la imagen de Ubuntu trae iptables cerrado por defecto:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Comprueba desde tu equipo: `curl http://LA_IP/health`

---

## Dominio

Los dominios gratuitos tipo `.tk` y `.ml` ya no existen: Freenom cerró ese
negocio. Lo que queda gratis son subdominios.

**Opción gratis:** [DuckDNS](https://www.duckdns.org/). Entras con una cuenta
de GitHub o Google, eliges un nombre y lo apuntas a la IP del servidor.
Te queda `algo.duckdns.org`.

**Opción de pago (recomendada si esto lo va a usar el equipo):** un `.com`
cuesta unos 12 USD al año; un `.co` colombiano, unos 25. Un `duckdns.org` en
una herramienta de trabajo se lee como un experimento.

Cambiar de uno a otro después es editar `server_name` en nginx y volver a
correr certbot. Empieza por DuckDNS si quieres validar primero.

---

## HTTPS

Con el dominio ya apuntando a la IP:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d tu-dominio.com
```

Certbot edita el nginx, pide el certificado y deja la renovación automática.
Conviene fijar el dominio en la configuración antes de correrlo:

```bash
sudo sed -i 's/server_name _;/server_name tu-dominio.com;/' \
    /etc/nginx/sites-available/notams
sudo nginx -t && sudo systemctl reload nginx
```

---

## Comprobar que quedó bien

```bash
systemctl status notams
curl -s https://tu-dominio.com/health
journalctl -u notams -f
tail -f /home/ubuntu/proyecto_notams/logs/extractor.log
```

`/health` debe responder `"estado":"ok"` con un `total_notams` por encima de
400 y un `antiguedad_minutos` por debajo de 45. Si dice `"degradado"`, el
propio campo `detalle` explica por qué y qué hacer.

El umbral de antigüedad se ajusta con `NOTAMS_MAX_EDAD_MIN` en el `.env`. Son
45 minutos por defecto: tres corridas del cron, margen suficiente para que un
fallo suelto del portal de la Aerocivil no dispare una falsa alarma.

Esto sirve para conectar un monitor externo (UptimeRobot tiene plan gratuito y
sabe buscar una palabra en la respuesta): vigila `/health` y avísate cuando
aparezca `degradado`. Sin eso, un cron caído no se nota hasta que alguien mira
un NOTAM viejo creyendo que está vigente.

Y en el navegador, que es la prueba que de verdad cuenta:

- **METAR / TAF** con un aeródromo, p.ej. `SKBO`. Si sale "Estación sin
  reporte", falta el `AVWX_TOKEN` en el `.env`.
- **Cierres** y **Reglamentos**, que no dependen de ninguna clave.
- En las herramientas del navegador, pestaña Red: **no debe haber ni una sola
  petición a `avwx.rest`**. Si la hay, el `index.html` del servidor es el
  viejo.

---

## Antes de pasarle el enlace a tus compañeros

- **El aviso de que esto no sustituye la información oficial** está en el
  README, pero no en la página. Mientras fue una herramienta personal daba
  igual; si la va a usar el equipo para trabajar, tiene que verse en el portal.
  Es lo único de esta lista que sigue pendiente.

Lo que sí quedó resuelto, y conviene que sepas que está ahí:

- **Los NOTAM vencidos se marcan.** Cada uno se compara contra la hora Zulú y,
  si su vigencia ya terminó, lleva una etiqueta roja `VENCIDO` tanto en la
  tabla de NOTAMs como en la lista de Cierres. Se marcan, **no se ocultan**:
  que el portal esté mostrando información caducada es justo lo que quien
  opera necesita ver, y filtrarla en silencio daría una lista limpia y
  mentirosa. Los NOTAM permanentes (`PERM`) nunca se marcan.
- **`/health` vigila la antigüedad** y pasa a `degradado` con el número de
  minutos y qué revisar.
