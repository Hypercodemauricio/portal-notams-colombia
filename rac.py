"""
Busqueda sobre los Reglamentos Aeronauticos de Colombia.

La base `sistema_rac.db` la construye `rac_indexar.py`. Aqui solo se consulta,
siempre en modo solo-lectura, igual que con la base de NOTAMs.

Decision de fondo: el buscador devuelve TEXTO LITERAL. La IA se usa despues,
sobre los apartados que este modulo encontro, y con la obligacion de citar el
numeral. Nunca al reves. Si la IA se equivoca, el usuario tiene el texto
original debajo para desmentirla.
"""

import os
import re
import sqlite3
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RAC_DB = BASE_DIR / os.getenv("RAC_DB", "sistema_rac.db")

# Palabras que no aportan a la busqueda y que, con prefijo comodin, traerian
# medio reglamento. "no" se conserva a proposito: en normativa la negacion
# cambia el sentido ("no se requiere").
VACIAS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
    "a", "ante", "bajo", "con", "contra", "desde", "en", "entre", "hacia",
    "hasta", "para", "por", "segun", "sin", "sobre", "tras", "y", "o", "u",
    "e", "que", "cual", "cuales", "como", "cuando", "donde", "quien", "es",
    "son", "ser", "esta", "estan", "se", "su", "sus", "lo", "mi", "me", "yo",
    "cuanto", "cuanta", "cuantos", "cuantas", "debo", "puedo", "tengo",
    "necesito", "hay", "cuál", "qué",
    # Muletillas de pregunta. "significa" parece contenido, pero en la
    # consulta "que significa AIS" es ruido: al ir en Y con el termino
    # buscado, exige que el reglamento contenga la palabra "significa" y
    # devuelve "cambios significativos" en vez de la definicion de AIS.
    "significa", "significado", "sirve", "trata", "puede", "pueden", "existe",
    "quiero", "saber", "dime", "explicame", "ayuda",
}

# Vocabulario aeronautico: el usuario pregunta en un idioma y el reglamento
# esta escrito en otro. Quien pregunta por "FPL" no encuentra nada, porque el
# RAC dice "plan de vuelo"; quien pregunta por "gasolina" tampoco, porque el
# RAC dice "combustible". Cada entrada agrega terminos, nunca los reemplaza.
SINONIMOS = {
    "fpl": ["plan", "vuelo"],
    "plan": ["plan"],
    "atc": ["transito", "control"],
    "ats": ["transito", "servicio"],
    "afis": ["informacion", "vuelo"],
    "ifr": ["instrumentos"],
    "vfr": ["visual"],
    "notam": ["notam"],
    "metar": ["meteorologico", "informe"],
    "taf": ["pronostico", "aerodromo"],
    "ais": ["informacion", "aeronautica"],
    "aip": ["publicacion", "informacion", "aeronautica"],
    "gasolina": ["combustible"],
    "fuel": ["combustible"],
    "alterno": ["alterno", "alternativo"],
    "alternate": ["alterno"],
    "despegue": ["despegue"],
    "aterrizaje": ["aterrizaje"],
    "demora": ["demora", "retraso"],
    "retraso": ["retraso", "demora"],
    "cierre": ["cierre", "clausura"],
    "piloto": ["piloto", "tripulacion"],
    "dron": ["uas", "tripulada"],
    "drone": ["uas", "tripulada"],
    "uas": ["uas", "tripulada"],
    "rvsm": ["rvsm"],
    "slot": ["turno", "asignacion"],
    "minimos": ["minimos"],
    "altura": ["altura", "altitud"],
    "altitud": ["altitud", "altura"],
    "peso": ["peso", "masa"],
    "masa": ["masa", "peso"],
    "licencia": ["licencia"],
    "carga": ["carga"],
}

NUMERAL_SUELTO = re.compile(r"^\s*(?:rac\s*)?(\d{1,3})\.(\d{1,4}(?:\.\d{1,3})*)\s*$",
                            re.IGNORECASE)

# Preguntas que si buscan una definicion. Con ellas, el glosario manda.
PREGUNTA_DE_DEFINICION = re.compile(
    r"\b(que\s+es|que\s+son|que\s+significa|definicion|definiciones|significa|"
    r"significado|glosario|termino|concepto|abreviatura|sigla|"
    r"a\s+que\s+se\s+refiere)\b", re.IGNORECASE)

# Cuantos apartados del mismo numeral pueden ocupar la lista de resultados.
# Sin tope, una consulta sobre el plan de vuelo devolvia tres definiciones
# seguidas del RAC 1 -"1.2.1" tres veces- y empujaba fuera de la vista el
# numeral del RAC 215 que de verdad responde.
TOPE_POR_NUMERAL = 2

# Las definiciones son cortas y llevan el termino en el titulo, asi que BM25
# las pone arriba de todo aunque la pregunta sea operativa. Este factor las
# corre hacia abajo sin sacarlas: acerca su puntaje a cero, y el orden va de
# menor a mayor.
CASTIGO_GLOSARIO = 0.55


def _es_glosario(rac_num: str) -> bool:
    try:
        import rac_catalogo
        reglamento = rac_catalogo.buscar(rac_num)
        return bool(reglamento and reglamento.glosario)
    except Exception:  # noqa: BLE001
        return False


def reordenar(filas, consulta: str, limite: int, exactos=None) -> list:
    """
    Aplica el castigo al glosario y el tope por numeral.

    `exactos` son los ids que salieron de la busqueda estricta: van primero,
    por delante de los que solo cumplen parte de la consulta.
    """
    # Sin quitar las tildes esto no detecta nada: el usuario escribe "qué es"
    # con tilde, y entonces una pregunta por definicion se trataba como
    # operativa y el glosario quedaba castigado justo cuando era la respuesta.
    definicional = bool(PREGUNTA_DE_DEFINICION.search(_sin_tildes(consulta or "")))
    exactos = exactos or set()

    puntuadas = []
    for f in filas:
        puntaje = f["puntaje"]
        if not definicional and _es_glosario(f["rac"]):
            puntaje *= CASTIGO_GLOSARIO
        puntuadas.append((0 if f["apartado_id"] in exactos else 1, puntaje, f))
    puntuadas.sort(key=lambda x: (x[0], x[1]))

    salida, por_numeral = [], {}
    for _, _, f in puntuadas:
        clave = (f["rac"], f["numeral"])
        if por_numeral.get(clave, 0) >= TOPE_POR_NUMERAL:
            continue
        por_numeral[clave] = por_numeral.get(clave, 0) + 1
        salida.append(f)
        if len(salida) >= limite:
            break
    return salida


def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


def hay_base() -> bool:
    return RAC_DB.exists()


def abrir():
    if not RAC_DB.exists():
        raise FileNotFoundError(f"No existe {RAC_DB}. Corre rac_indexar.py.")
    con = sqlite3.connect(f"file:{RAC_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def construir_consulta(texto: str) -> str:
    """
    Traduce lo que escribe una persona a una expresion MATCH de FTS5.

    Se escapa todo lo que FTS5 interpreta como sintaxis. Sin esto, un usuario
    que escriba `combustible AND "` no obtiene cero resultados: obtiene un
    error 500, porque FTS5 aborta la consulta con comillas sin cerrar.
    """
    texto = texto or ""
    frases = re.findall(r'"([^"]{2,80})"', texto)
    resto = re.sub(r'"[^"]*"', " ", texto)

    palabras = re.findall(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\.[0-9]+)*", resto)

    def con_comodin(t):
        # Comodin solo en palabras largas: con "ala*" entraria "alarma",
        # "alambre" y todo lo demas.
        return f"{t}*" if len(t) >= 5 else t

    grupos = []
    vistos = set()
    for palabra in palabras:
        limpia = _sin_tildes(palabra).lower()
        if not limpia or limpia in VACIAS or limpia in vistos:
            continue
        vistos.add(limpia)
        # El termino original SIEMPRE va, y los sinonimos se le suman dentro de
        # un grupo OR. La version anterior sustituia -"ais" se convertia en
        # "informacion aeronautica"- y entonces la busqueda de "AIS" ya no
        # encontraba la entrada "AIS" del glosario, que es justo la que se
        # estaba buscando. Ademas, sustituir con varios sinonimos los exigia
        # todos a la vez, porque los terminos van en Y.
        variantes = [limpia] + [x for x in SINONIMOS.get(limpia, [])
                                if x != limpia]
        partes = [con_comodin(v) for v in variantes]
        grupos.append(f"({' OR '.join(partes)})" if len(partes) > 1 else partes[0])

    consulta = ['"' + f.replace('"', " ").strip() + '"' for f in frases if f.strip()]
    consulta.extend(grupos)
    # El AND va explicito: FTS5 admite la Y implicita entre palabras sueltas,
    # pero en cuanto aparece un grupo entre parentesis exige el operador y, si
    # no esta, aborta la consulta entera con un error de sintaxis.
    return " AND ".join(consulta).strip()


# Marcas del resaltado. Se usan los caracteres de control 0x02/0x03 y no
# corchetes: un reglamento puede contener "[[" en una formula o una tabla, y
# entonces el frontend abriria un resaltado que nadie cierra. Estos dos
# caracteres no aparecen en un PDF de texto.
MARCA_INICIO, MARCA_FIN = "\x02", "\x03"


def _fila_a_dict(f, con_texto=True):
    d = {
        "rac": f["rac"],
        "numeral": f["numeral"],
        "titulo": f["titulo"],
        "capitulo": f["capitulo"],
        "pagina": f["pagina"],
        "documento": f["doc_titulo"],
        "fecha_version": f["fecha_version"],
    }
    if con_texto:
        d["texto"] = f["texto"]
    if "extracto" in f.keys():
        d["extracto"] = f["extracto"]
    return d


CAMPOS = """
    a.id AS apartado_id,
    a.rac, a.numeral, a.titulo, a.capitulo, a.pagina, a.texto,
    d.titulo AS doc_titulo, d.fecha_version
"""


def buscar(consulta: str, rac=None, limite: int = 20) -> dict:
    """
    Devuelve {'consulta_fts': ..., 'total': N, 'resultados': [...]}.

    Si la busqueda con todos los terminos no devuelve nada, se reintenta
    exigiendo solo uno. Es preferible dar resultados aproximados y ordenados
    que una pantalla vacia ante una pregunta escrita en lenguaje natural.
    """
    con = abrir()
    try:
        # Atajo: "91.001" o "RAC 91.001" es una consulta por numeral exacto.
        m = NUMERAL_SUELTO.match(consulta or "")
        if m:
            numeral = f"{m.group(1)}.{m.group(2)}"
            filas = con.execute(
                f"SELECT {CAMPOS} FROM apartados a "
                f"JOIN documentos d ON d.id = a.documento_id "
                f"WHERE a.numeral = ? LIMIT ?", (numeral, limite)).fetchall()
            if filas:
                return {"consulta_fts": numeral, "modo": "numeral",
                        "total": len(filas),
                        "resultados": [_fila_a_dict(f) for f in filas]}

        fts = construir_consulta(consulta)
        if not fts:
            return {"consulta_fts": "", "modo": "vacia", "total": 0,
                    "resultados": []}

        filtro_rac = ""
        parametros = [fts]
        if rac:
            filtro_rac = " AND a.rac = ? "
            parametros.append(str(rac))

        sql = f"""
            SELECT {CAMPOS},
                   snippet(apartados_fts, 2, char(2), char(3), ' … ', 28) AS extracto,
                   bm25(apartados_fts, 12.0, 8.0, 1.0) AS puntaje
            FROM apartados_fts
            JOIN apartados a ON a.id = apartados_fts.rowid
            JOIN documentos d ON d.id = a.documento_id
            WHERE apartados_fts MATCH ? {filtro_rac}
            ORDER BY puntaje
            LIMIT ?
        """
        # Se piden mas filas de las que se van a mostrar porque despues se
        # reordenan y se descartan las repeticiones del mismo numeral.
        holgura = limite * 4
        try:
            filas = con.execute(sql, parametros + [holgura]).fetchall()
        except sqlite3.OperationalError:
            return {"consulta_fts": fts, "modo": "error", "total": 0,
                    "resultados": []}

        modo = "todos_los_terminos"
        exactos = {f["apartado_id"] for f in filas}

        # Exigir TODOS los terminos es demasiado estricto para una pregunta
        # escrita en lenguaje natural. "con cuanta antelacion se presenta el
        # plan de vuelo" devolvia un unico apartado -un plan de instruccion de
        # pilotos que casualmente contenia las cuatro palabras- y dejaba fuera
        # el numeral que responde. Cuando la busqueda estricta trae poco, se
        # completa con la version flexible, pero los resultados exactos se
        # quedan arriba.
        if len(filas) < max(3, limite // 3) and " AND " in fts:
            alterna = " OR ".join(
                x for x in re.findall(r"\([^)]*\)|\S+", fts) if x != "AND")
            parametros[0] = alterna
            extra = con.execute(sql, parametros + [holgura]).fetchall()
            conocidos = set(exactos)
            for f in extra:
                if f["apartado_id"] not in conocidos:
                    conocidos.add(f["apartado_id"])
                    filas.append(f)
            modo = "completado_con_alguno" if exactos else "alguno_de_los_terminos"
            fts = f"{fts}  |  {alterna}" if exactos else alterna

        filas = reordenar(filas, consulta, limite, exactos=exactos)
        return {"consulta_fts": fts, "modo": modo, "total": len(filas),
                "resultados": [_fila_a_dict(f) for f in filas]}
    finally:
        con.close()


def apartado(rac: str, numeral: str):
    con = abrir()
    try:
        f = con.execute(
            f"SELECT {CAMPOS} FROM apartados a "
            f"JOIN documentos d ON d.id = a.documento_id "
            f"WHERE a.rac = ? AND a.numeral = ?", (str(rac), numeral)).fetchone()
        return _fila_a_dict(f) if f else None
    finally:
        con.close()


def documentos() -> list:
    con = abrir()
    try:
        return [dict(f) for f in con.execute(
            "SELECT rac, titulo, fecha_version, paginas, apartados, indexado_en "
            "FROM documentos ORDER BY CAST(rac AS INTEGER)")]
    finally:
        con.close()


def contexto_para_ia(resultados: list, tope_caracteres: int = 14000) -> str:
    """
    Arma el contexto que se le pasa al modelo. Va etiquetado apartado por
    apartado para que la cita sea verificable: si el modelo dice "RAC 91.310",
    ese rotulo tiene que estar aqui arriba.
    """
    piezas, usados = [], 0
    for r in resultados:
        cabecera = (f"[RAC {r['rac']} — {r['numeral']} — {r['titulo']} "
                    f"(version {r['fecha_version']}, pag. {r['pagina']})]")
        cuerpo = (r.get("texto") or "")[:3500]
        pieza = f"{cabecera}\n{cuerpo}"
        if usados + len(pieza) > tope_caracteres:
            break
        piezas.append(pieza)
        usados += len(pieza)
    return "\n\n---\n\n".join(piezas)


PROMPT = """Eres un asistente que responde dudas sobre los Reglamentos \
Aeronauticos de Colombia (RAC). Responde EXCLUSIVAMENTE con lo que digan los \
apartados que siguen.

Reglas:
1. Cita el numeral exacto entre parentesis cada vez que afirmes algo, asi: \
(RAC 91.310).
2. Si los apartados no contienen la respuesta, dilo con estas palabras: "Los \
apartados encontrados no responden esta pregunta." y sugiere que terminos \
buscar. No completes con conocimiento propio.
3. No cites numerales que no aparezcan abajo.
4. Espanol claro y directo, maximo 250 palabras. Sin encabezados ni saludos.
5. Cierra siempre con esta linea exacta: "Verifique la version vigente del \
reglamento antes de aplicar esta informacion."

PREGUNTA: {pregunta}

APARTADOS ENCONTRADOS:
{contexto}"""
