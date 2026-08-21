"""
Corta el texto de un RAC en apartados consultables.

Esta es la parte delicada de todo el buscador. Si el corte queda mal, el
resultado no es "se ve feo": es que el buscador cita un numeral y muestra el
texto de otro. En materia normativa eso es peor que no responder, asi que aqui
las reglas son conservadoras y todo lo que no se reconoce con seguridad se
queda pegado al apartado anterior en vez de inventar uno nuevo.

Estructura real de un RAC (verificada contra el RAC 91):

    CAPITULO A  GENERALIDADES
    91.001  Definiciones, abreviaturas y simbolos
        (a) ...
        (b) ...
    91.005  Aplicacion
    APENDICE 1  ...

El numeral SIEMPRE empieza por el numero del reglamento ("91.001" en el RAC
91). Esa restriccion es la que evita el error clasico: "1.5 metros", "RVR 550"
o una fila de tabla que empieza por un decimal no pueden confundirse con un
numeral, porque no empiezan por el numero del reglamento.
"""

import re
import unicodedata
from collections import Counter

# Encabezados que no son numerales.
#
# Sin IGNORECASE, y esto no es un detalle: en el RAC 215 la linea
# "capitulo E)" -el final de una frase que continuaba de la linea anterior-
# creaba un apartado falso "CAPITULO E" que se llevaba 4.709 caracteres del
# numeral que venia arriba. En los RAC los encabezados reales van siempre en
# mayuscula, asi que exigirla elimina toda esa familia de falsos positivos.
CAPITULO = re.compile(
    r"^\s*(CAP[IÍ]TULO|SECCI[OÓ]N|SUBPARTE|PARTE)\s+"
    r"([A-Z]|[IVXLC]+|\d{1,2})\b\s*[.\-–—:]?\s*(.*)$")
APENDICE = re.compile(
    r"^\s*(AP[EÉ]NDICE|ANEXO|ADJUNTO)\s+([A-Z]|[IVXLC]+|\d{1,2})\b\s*[.\-–—:]?\s*(.*)$")

# Un encabezado de verdad es corto y su titulo, si lo trae, empieza en
# mayuscula o en un digito. "CAPÍTULO 11, 11.2.1.2, salvo que el ATS
# prescriba lo contrario." cumple la expresion de arriba pero es el segundo
# renglon de una frase: continua en minuscula y en coma.
def _encabezado_creible(m, linea: str) -> bool:
    if len(linea) > 90:
        return False
    titulo = (m.group(3) or "").strip()
    if not titulo:
        return True
    return bool(re.match(r"[A-ZÁÉÍÓÚÑ0-9(]", titulo))


def encabezado_de_seccion(linea: str):
    """CAPITULO / APENDICE / ANEXO real, o None."""
    for patron in (CAPITULO, APENDICE):
        m = patron.match(linea)
        if m and _encabezado_creible(m, linea):
            return m
    return None

# Linea de tabla de contenido: termina en puntos suspensivos y numero de
# pagina, o en varios espacios y un numero suelto. Hay que descartarlas antes
# de segmentar, porque son encabezados identicos a los reales y crearian un
# apartado vacio que luego "gana" en la busqueda por tener el titulo exacto.
# Los puntos guia por si solos delatan un renglon de indice, este el numero de
# pagina al final o no. En el RAC 14 la version anterior exigia que la linea
# TERMINARA en el numero, y 28 renglones del indice -en los que el numero
# quedaba en otra posicion- se colaron como apartados con cuerpo, es decir,
# como resultados de busqueda con titulo de norma y contenido de indice.
LINEA_INDICE = re.compile(r"\.{3,}|(\s{3,}|\t)\s*\d{1,4}\s*$")
# Un encabezado que termina en un numero suelto es un renglon del indice
# ("CAPITULO A GENERALIDADES 3"), no el encabezado real del articulado.
TERMINA_EN_PAGINA = re.compile(r"(\.{2,}\s*|\s)\d{1,4}\s*$")
SOLO_NUMERO = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")
# Numeracion de pagina del tipo "RAC 91 - 12" o "91-4".
PIE_RAC = re.compile(r"^\s*(RAC\s*)?\d{1,3}\s*[-–—]\s*\d{1,4}\s*$", re.IGNORECASE)


def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


def _normalizar_espacios(t: str) -> str:
    return re.sub(r"[ \t ]+", " ", t).strip()


def _forma(t: str) -> str:
    """Texto con los digitos sustituidos por '#': 'rac 91 91-2' -> 'rac ## ##-#'."""
    return re.sub(r"\d", "#", t)


def patron_numeral(rac: str) -> re.Pattern:
    """
    Numeral de un RAC concreto: 91.001, 91.1000, 215.500, 91.001.1
    Admite que el titulo venga en la misma linea o en la siguiente.

    En un RAC de un solo digito -el 1- el segundo nivel se limita a dos
    cifras. En espanol el punto tambien separa los miles, y "1.944" es un ano:
    la linea "1.944 Sobre Aviacion Civil Internacional y sus anexos tecnicos."
    -el final de una frase sobre el Convenio de Chicago de 1944- se convertia
    en un apartado que se llevaba 178 definiciones consigo. Con dos cifras,
    "1.2.1" sigue entrando y "1.944" ya no. En los RAC de dos o tres digitos
    la ambiguedad no existe y se admiten hasta cuatro (91.1000 es real).
    """
    nivel = r"\d{1,2}" if len(str(rac)) == 1 else r"\d{1,4}"
    return re.compile(
        r"^\s*\(?\s*(" + re.escape(str(rac)) + r"\." + nivel + r"(?:\.\d{1,3})*)\s*\)?"
        r"\s*[.\-–—:]?(?:\s+(.*))?$")


def quitar_repetidos(paginas: list) -> list:
    """
    Elimina encabezado y pie que se repiten en todas las paginas.

    Se hace por frecuencia y no por posicion: en los RAC el encabezado a veces
    ocupa una linea y a veces dos, y en las paginas de tabla desaparece. Una
    linea corta que aparece en mas de un tercio de las paginas es plantilla,
    no contenido normativo.
    """
    if not paginas:
        return []

    conteo = Counter()
    conteo_forma = Counter()
    for pagina in paginas:
        vistas, formas = set(), set()
        for linea in pagina.splitlines():
            clave = _sin_tildes(_normalizar_espacios(linea)).lower()
            if clave and len(clave) <= 120:
                vistas.add(clave)
                if len(clave) <= 60 and any(c.isdigit() for c in clave):
                    formas.add(_forma(clave))
        conteo.update(vistas)
        conteo_forma.update(formas)

    umbral = max(2, int(len(paginas) * 0.34))
    plantilla = {k for k, n in conteo.items() if n >= umbral}
    # El pie de pagina cambia en cada hoja ("RAC 91 91-2", "RAC 91 91-3"), asi
    # que contarlo por texto exacto no lo detecta nunca. Se cuenta tambien la
    # FORMA -los digitos sustituidos por #-, que si se repite. Solo aplica a
    # lineas cortas con digitos, para no barrer texto normativo.
    plantilla_forma = {k for k, n in conteo_forma.items() if n >= umbral}

    limpias = []
    for pagina in paginas:
        salida = []
        for linea in pagina.splitlines():
            texto = _normalizar_espacios(linea)
            if not texto:
                continue
            clave = _sin_tildes(texto).lower()
            if clave in plantilla:
                continue
            if (len(clave) <= 60 and any(c.isdigit() for c in clave)
                    and _forma(clave) in plantilla_forma):
                continue
            if SOLO_NUMERO.match(texto) or PIE_RAC.match(texto):
                continue
            salida.append(texto)
        limpias.append("\n".join(salida))
    return limpias


# Un apartado no deberia pasar de unos pocos miles de caracteres. Cuando lo
# hace no suele ser un fallo del corte: hay secciones que de verdad son
# larguisimas -las definiciones del RAC 1, el APENDICE 7 del RAC 215 con el
# formato del plan de vuelo- y no tienen numeracion interna donde partirlas.
# Dejarlas de una pieza hace inutil la busqueda: el resultado es un bloque de
# 70.000 caracteres del que solo se ve un extracto suelto. Se trocean por
# renglon, sin cortar frases, y cada trozo dice de que parte viene.
TOPE_APARTADO = 6000


def trocear(apartados: list, tope: int = TOPE_APARTADO) -> list:
    salida = []
    for a in apartados:
        texto = a["texto"]
        if len(texto) <= tope:
            salida.append(a)
            continue

        trozos, actual, tamano = [], [], 0
        for linea in texto.splitlines():
            if tamano and tamano + len(linea) > tope:
                trozos.append("\n".join(actual))
                actual, tamano = [], 0
            actual.append(linea)
            tamano += len(linea) + 1
        if actual:
            trozos.append("\n".join(actual))

        for i, trozo in enumerate(trozos, start=1):
            copia = dict(a)
            copia["texto"] = trozo
            copia["parte"] = i
            copia["partes"] = len(trozos)
            copia["titulo"] = f"{a['titulo']} (parte {i} de {len(trozos)})"
            salida.append(copia)
    return salida


# --------------------------------------------------------------------------
# Glosarios
# --------------------------------------------------------------------------
# El RAC 1 no es un articulado: son 130 paginas de "Termino: definicion". Eso
# rompe la regla general de dos maneras.
#
# Primero, el numero del reglamento es "1", asi que CUALQUIER enumeracion
# dentro del texto se parece a un numeral. Una linea real del RAC 1 es
# "1.3. Vso. (al peso maximo certificado de aterrizaje) son aquellos valores
# establecidos para las" -un item de una lista dentro de la definicion de
# "Velocidad"- y se convertia en un apartado que se tragaba 182.000
# caracteres, con ese trozo de frase como titulo.
#
# Segundo, aunque el corte saliera bien, un unico apartado "1.2.1
# Definiciones" con todo el diccionario dentro no sirve para buscar: se
# encuentra siempre y nunca dice cual definicion.
#
# Dentro de una seccion de glosario, entonces, se ignora la numeracion y se
# corta por termino.

INICIO_GLOSARIO = re.compile(r"definicion|abreviatur|expresiones de uso",
                             re.IGNORECASE)

# "Aerodromo: Area definida..."  y  "Cargar. Accion de colocar..."
TERMINO_DOS_PUNTOS = re.compile(
    r"^([A-ZÁÉÍÓÚÜÑ][^:]{1,70}?)\s*:\s+(.{10,})$")
TERMINO_PUNTO = re.compile(
    r"^([A-ZÁÉÍÓÚÜÑ][^.]{1,70}?)\.\s+([A-ZÁÉÍÓÚÜÑ].{10,})$")

# Palabras que abren una aclaracion, no un termino nuevo. Sin esto, cada
# "Nota: Definicion adicionada conforme a la Resolucion..." partiria en dos la
# definicion que acaba de terminar.
NO_SON_TERMINOS = {
    "nota", "notas", "nota 1", "nota 2", "nota 3", "ejemplo", "ejemplos",
    "fuente", "ver", "vease", "observacion", "observaciones", "excepcion",
    "referencia", "aclaracion", "definicion", "definiciones", "abreviatura",
    "abreviaturas", "articulo", "paragrafo", "resolucion", "capitulo",
}
TERMINOS_MINIMOS = 50


def _es_termino(linea: str):
    for patron in (TERMINO_DOS_PUNTOS, TERMINO_PUNTO):
        m = patron.match(linea)
        if not m:
            continue
        termino = _normalizar_espacios(m.group(1))
        clave = _sin_tildes(termino).lower().strip()
        if clave in NO_SON_TERMINOS or len(termino) < 3:
            continue
        # Un termino no es una frase entera: si trae verbo conjugado y comas
        # a mansalva, es prosa. Se aproxima por el numero de palabras.
        if len(termino.split()) > 9:
            continue
        return termino, m.group(2)
    return None


def partir_glosario(apartado: dict) -> list:
    """
    Convierte un apartado de glosario en un apartado por termino.

    Si no reconoce suficientes terminos devuelve [] y quien llama se queda con
    el apartado tal cual: mas vale un bloque grande que un diccionario partido
    por donde no era.
    """
    terminos = []
    actual = None
    for linea in apartado["texto"].splitlines():
        hallazgo = _es_termino(linea)
        if hallazgo:
            if actual:
                terminos.append(actual)
            termino, resto = hallazgo
            actual = {"termino": termino, "texto": f"{termino}: {resto}"}
        elif actual:
            actual["texto"] += "\n" + linea
        # Lo que aparece antes del primer termino (el parrafo introductorio de
        # la seccion) se descarta a proposito: no es una definicion.
    if actual:
        terminos.append(actual)

    if len(terminos) < TERMINOS_MINIMOS:
        return []

    salida = []
    for i, t in enumerate(terminos, start=1):
        copia = dict(apartado)
        copia["titulo"] = t["termino"]
        copia["texto"] = t["texto"].strip()
        copia["parte"] = i
        copia["partes"] = len(terminos)
        salida.append(copia)
    return salida


def inicio_del_articulado(lineas: list, es_encabezado) -> int:
    """
    Devuelve el indice donde termina la portada con la tabla de contenido.

    No se descarta linea por linea, sino la region completa, y por su forma:
    una tabla de contenido es una sucesion densa de encabezados sin texto
    entre ellos, mientras que el articulado es el primer encabezado seguido de
    prosa. Se busca el primer encabezado que tenga al menos dos lineas
    corridas de texto normal detras.

    Hacerlo por region y no por linea importa: los titulos del indice son
    identicos a los del articulado, y si sobrevive uno se cuela un apartado
    con el titulo correcto y el cuerpo vacio. Ese apartado gana en la busqueda
    -el titulo coincide palabra por palabra- y le muestra al usuario un
    numeral sin contenido, que es justo el resultado mas confuso posible.
    """
    limite = max(40, int(len(lineas) * 0.30))
    for i, (texto, _) in enumerate(lineas[:limite]):
        if not es_encabezado(texto):
            continue
        corridas = 0
        for texto_siguiente, _ in lineas[i + 1:i + 6]:
            if es_encabezado(texto_siguiente):
                break
            corridas += 1
            if corridas >= 2:
                # El articulado empieza en el primer numeral con prosa, pero
                # el CAPITULO que lo encabeza esta una o dos lineas antes y
                # tambien es contenido. Se retrocede mientras las lineas
                # previas sean encabezados sin numero de pagina al final: eso
                # distingue "CAPITULO A GENERALIDADES" (articulado) de
                # "CAPITULO A GENERALIDADES 3" (indice).
                j = i
                while j > 0:
                    anterior = lineas[j - 1][0]
                    if not es_encabezado(anterior) or TERMINA_EN_PAGINA.search(anterior):
                        break
                    j -= 1
                return j
    return 0  # sin tabla de contenido reconocible: se conserva todo


def segmentar(paginas: list, rac: str, glosario: bool = False) -> list:
    """
    paginas: texto de cada pagina, en orden.
    glosario: el documento es un diccionario de terminos (RAC 1).
    Devuelve [{numeral, titulo, texto, pagina, capitulo, orden}, ...]
    """
    numeral_re = patron_numeral(rac)
    paginas = quitar_repetidos(paginas)

    def es_encabezado(t: str) -> bool:
        return bool(numeral_re.match(t) or encabezado_de_seccion(t))

    # (texto, numero_de_pagina) por linea, ya sin plantilla ni indice.
    lineas = []
    for n_pagina, pagina in enumerate(paginas, start=1):
        for linea in pagina.splitlines():
            # Renglon de indice con puntos guia: se descarta aunque la region
            # completa no se haya reconocido.
            if LINEA_INDICE.search(linea) and es_encabezado(linea):
                continue
            lineas.append((linea, n_pagina))

    lineas = lineas[inicio_del_articulado(lineas, es_encabezado):]

    apartados = []
    actual = None
    capitulo = ""
    esperando_titulo = False
    en_glosario = False

    def cerrar():
        if actual is not None and (actual["texto"].strip() or actual["titulo"]):
            apartados.append(actual)

    for texto, n_pagina in lineas:
        m_cap = encabezado_de_seccion(texto)
        m_num = numeral_re.match(texto)

        # Dentro de una seccion de glosario, un "1.3." suele ser un item de una
        # lista dentro de una definicion, no un numeral. Ahi solo se acepta
        # como encabezado si la linea es corta: "1.2.2. Abreviaturas" mide 19
        # caracteres, mientras que el falso "1.3. Vso. (al peso maximo
        # certificado de aterrizaje) son aquellos valores establecidos para
        # las" mide 95. Fuera del glosario esta restriccion no se aplica,
        # porque ahi los titulos largos son legitimos.
        if m_num and glosario and en_glosario and len(texto) > 80:
            m_num = None

        if m_num:
            cerrar()
            numeral = m_num.group(1)
            resto = (m_num.group(2) or "").strip()
            actual = {"numeral": numeral, "titulo": resto, "texto": "",
                      "pagina": n_pagina, "capitulo": capitulo,
                      "aparicion": len(apartados), "_de_numeral": True}
            en_glosario = bool(glosario and INICIO_GLOSARIO.search(resto))
            # Titulo en la linea siguiente (pasa cuando el titulo es largo).
            esperando_titulo = not resto
            continue

        if m_cap:
            cerrar()
            etiqueta = _normalizar_espacios(
                f"{m_cap.group(1).upper()} {m_cap.group(2).upper()}")
            titulo = m_cap.group(3).strip()
            capitulo = _normalizar_espacios(f"{etiqueta} {titulo}").strip()
            # Un capitulo tambien es consultable: suele traer el alcance.
            actual = {"numeral": etiqueta, "titulo": titulo, "texto": "",
                      "pagina": n_pagina, "capitulo": capitulo,
                      "aparicion": len(apartados), "_de_numeral": False}
            en_glosario = False
            esperando_titulo = not titulo
            continue

        if actual is None:
            continue  # portada, indice, cualquier cosa antes del primer numeral

        if esperando_titulo:
            actual["titulo"] = texto.strip()
            esperando_titulo = False
            if (glosario and actual.get("_de_numeral")
                    and INICIO_GLOSARIO.search(actual["titulo"])):
                en_glosario = True
            continue

        actual["texto"] += texto + "\n"

    cerrar()

    # Red de seguridad: si a pesar de todo sobrevive un renglon de indice, su
    # titulo conserva los puntos guia. No se deja pasar.
    apartados = [a for a in apartados if "..." not in a["titulo"]]

    # Un numeral repetido casi siempre es el eco de la tabla de contenido o de
    # una referencia cruzada. Se conserva la aparicion con mas cuerpo, que es
    # la del articulado real.
    mejor = {}
    for a in apartados:
        clave = a["numeral"]
        if clave not in mejor or len(a["texto"]) > len(mejor[clave]["texto"]):
            mejor[clave] = a

    # El orden es el del documento, no el alfabetico del numeral: ordenar por
    # texto pondria 91.1000 antes que 91.315, que no es como esta escrito el
    # reglamento ni como espera leerlo quien lo consulta.
    ordenados = sorted(mejor.values(), key=lambda a: a["aparicion"])
    for a in ordenados:
        a.pop("aparicion", None)
        a["texto"] = a["texto"].strip()
        a["titulo"] = _normalizar_espacios(a["titulo"])
        a.pop("_de_numeral", None)
        a.setdefault("parte", 1)
        a.setdefault("partes", 1)

    if glosario:
        expandidos = []
        for a in ordenados:
            porciones = partir_glosario(a) if len(a["texto"]) > 4000 else []
            expandidos.extend(porciones or [a])
        ordenados = expandidos

    ordenados = trocear(ordenados)
    for i, a in enumerate(ordenados):
        a["orden"] = i
    return ordenados
