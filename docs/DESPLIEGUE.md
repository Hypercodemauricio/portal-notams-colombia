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
| **Oracle Cloud Always Free** | permanente | 12 GB de RAM y 10 TB de salida. Solo verificación de tarjeta. **Lo que usamos.** |
| Google Cloud `e2-micro` | permanente | 1 GB de RAM y 1 GB de salida. En Colombia exige un prepago de 100.000 COP. |
| AWS `t3.micro` | 12 meses | Después se paga. |

### Oracle y la inactividad: qué esperar

Oracle marca como **inactiva** una instancia Always Free si durante 7 días
seguidos se cumplen las tres cosas a la vez: percentil 95 de CPU por debajo del
20 %, red por debajo del 20 % y —solo en las ARM— memoria por debajo del 20 %.

Este portal encaja en ese perfil. La de memoria es la que no hay forma de
esquivar: en una ARM de 12 GB el umbral son 2,4 GB, y la API gasta unos 400 MB.
Ni pidiendo la más pequeña, de 6 GB, se llega.

**Lo que pasa entonces no es tan grave como suena.** Oracle no borra nada:
avisa por correo y **apaga** la instancia una semana después del aviso. El disco
y los datos quedan intactos y se vuelve a encender desde la consola. Son unos 14
días desde que empieza a estar ociosa, con notificación de por medio.

Al encenderla, todo vuelve solo. El portal servirá datos viejos unos minutos,
pero eso se ve: `/health` estará en `degradado` y los NOTAM saldrán con la
etiqueta `VENCIDO` hasta la siguiente extracción, que llega en menos de 15
minutos.

El único escenario incómodo es que la documentación condiciona el reencendido a
que **el shape siga disponible en la región**, y la capacidad ARM de Oracle se
agota a menudo. Es poco probable, pero es la razón real para no confiarse.

### Cómo quitarse el problema del todo

La política está acotada a las cuentas Always Free: *«Idle **Always Free**
compute instances may be reclaimed by Oracle»*. **Convertir la cuenta a Pay As
You Go la desactiva**, y los recursos Always Free se siguen usando sin coste. Es
lo que recomienda el propio soporte de Oracle.

El precio honesto: con Pay As You Go hay una tarjeta activa que **sí puede
cobrar** si algún día se crea algo fuera de los límites gratuitos. Con Always
Free eso es imposible por construcción. Se cambia «me la pueden apagar» por «me
pueden cobrar si me equivoco».

Si se hace, dos precauciones que no cuestan nada: crear solo recursos que la
consola marque como *Always Free eligible*, y poner una alerta de presupuesto en
1 USD para enterarse el día que algo empiece a facturar.

**Recomendación:** empezar en Always Free. Si llega el aviso de inactividad, a
esas alturas ya se sabrá si la herramienta se ganó su sitio; si se lo ganó, se
convierte a Pay As You Go ese día.

---

## Crear la máquina en Oracle Cloud

Estos pasos son los únicos que hay que hacer a mano, en la consola web.

**1. Cuenta.** [cloud.oracle.com/free](https://www.oracle.com/cloud/free/).
Pide una tarjeta para verificar identidad: hace una retención temporal de
alrededor de un dólar que se libera sola. No hay prepago. Elige como *home
region* una cercana —São Paulo o Santiago— porque **no se puede cambiar
después**.

**2. La instancia.** *Compute → Instances → Create instance*.

| Campo | Valor | Por qué |
|---|---|---|
| Shape | `VM.Standard.A1.Flex`, 1 OCPU y 6 GB | Sobra para el portal y deja margen para Chrome. Tiene que decir *Always Free eligible*. |
| Imagen | Ubuntu 24.04 (aarch64) | Es ARM; el instalador lo detecta solo. |
| Disco | el de por defecto (~47 GB) | El tope gratuito son 200 GB entre todos. |
| Clave SSH | descarga la privada | Sin ella no vuelves a entrar. |

> Si sale **«Out of capacity»**, no es un error tuyo: la capacidad ARM se agota
> a menudo. Prueba en otro dominio de disponibilidad, en otra región o más
> tarde. A veces hay que insistir varias veces.

Tres cosas que se pasan por alto y cuestan rehacer la máquina:

- **Elige el shape antes que la imagen.** Al seleccionar Ampere, la lista de
  imágenes se filtra a las compatibles con ARM. Al revés se acaba con una
  imagen x86 que no arranca en ese shape.
- **El shape tiene que decir «Always Free eligible».** Una cuenta nueva empieza
  en Free Trial con 300 USD de crédito, y durante 30 días la consola deja crear
  lo que sea: todo lo que no sea Always Free **se apaga al terminar la prueba**.
- **Descarga la clave privada antes de pulsar Create.** Oracle la ofrece una
  sola vez. Sin ella no se entra, y la única salida es borrar la instancia.

Y en la sección de red, comprueba que *Assign a public IPv4 address* esté en
**Yes**. Sin IP pública no se llega al portal desde fuera.

**3. Abrir los puertos.** Dos sitios, y hay que hacer los dos:

- En la consola: *Networking → Virtual Cloud Networks → tu VCN → Security Lists*
  → añade reglas de entrada para los puertos **80** y **443** desde `0.0.0.0/0`.
- En la máquina, porque la imagen de Ubuntu trae iptables cerrado. Los comandos
  están más abajo, en «Abrir el puerto».

**4. Entrar.** `ssh -i tu-clave.key ubuntu@LA_IP`

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

No basta con el firewall de Ubuntu: hay que abrir el puerto también en la
*Security List* de la consola, como se indica arriba. Y además, porque la imagen
de Ubuntu de Oracle trae iptables cerrado por defecto:

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
