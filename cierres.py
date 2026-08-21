# -*- coding: utf-8 -*-
"""
Clasificador de cierres y restricciones sobre NOTAMs colombianos.

Escrito mirando los 453 NOTAMs reales del servidor, no de memoria. Dos cosas
que solo se ven con los datos delante:

  * El separador de clausulas es la coma, no el punto: las frecuencias llevan
    punto ("VOR CJN 113.40 MHZ U/S"). Excluir el punto del salto entre el
    equipo y su estado dejaba fuera casi todas las radioayudas.
  * "LISTA DE VERIFICACION" es un NOTAM administrativo que enumera otros
    NOTAMs. Menciona de todo y no restringe nada.
"""
import re

FIRS = {"SKED": "FIR Bogotá", "SKEC": "FIR Barranquilla"}

RUIDO = re.compile(r"LISTA\s+DE\s+VERIFICACION|CHECKLIST|TRIGGER\s+NOTAM", re.I)

# Salto entre el elemento y su estado. Permite puntos (frecuencias) pero no
# comas ni punto y coma, que en un NOTAM separan asuntos distintos.
S = r"[^,;]{0,70}?"
FUERA = r"(?:CLSD|U/S|INOP|NO\s+AVBL|NOT\s+AVBL)"
MERMA = r"(?:LTD|RESTR\w*|REDUCID\w*)"

# El orden manda: gana la primera regla que coincida, de mas grave a menos.
REGLAS = [
    ("ad_cerrado", "Aeródromo cerrado", "cierre", 1,
     re.compile(rf"\bAD\s+{FUERA}|\bAERODROM\w*\s+(?:CLSD|CERRAD\w*)", re.I)),

    ("ad_limitado", "Aeródromo limitado", "limitacion", 2,
     re.compile(rf"\bAD\s+{MERMA}", re.I)),

    ("pista", "Pista cerrada", "cierre", 1,
     re.compile(rf"\bRWY\b{S}\bCLSD\b", re.I)),
    ("pista", "Pista limitada", "limitacion", 2,
     re.compile(rf"\bRWY\b{S}\b(?:{FUERA}|{MERMA})\b", re.I)),

    ("helipuerto", "Helipuerto", "cierre", 2,
     re.compile(rf"\bHLP\b{S}\b(?:{FUERA}|{MERMA})\b", re.I)),

    ("rodaje", "Calles y plataforma", "cierre", 2,
     re.compile(rf"\b(?:TWY|APN|PSN\s+PRKG|SPOT|STAND|CALLE\s+DE\s+RODAJE)\b{S}\bCLSD\b", re.I)),
    ("rodaje", "Calles y plataforma", "limitacion", 3,
     re.compile(rf"\b(?:TWY|APN|PSN\s+PRKG|SPOT|STAND)\b{S}\b(?:{FUERA}|{MERMA})\b", re.I)),

    ("iluminacion", "Iluminación", "limitacion", 3,
     re.compile(rf"\b(?:ABN|RTHL|RTIL|RCLL|REDL|REIL|PAPI|VASIS|ALS|LGT|LUCES|BCN|"
                rf"ILUMINACION)\b{S}\b(?:{FUERA}|{MERMA})\b", re.I)),

    ("navegacion", "Ayudas a la navegación", "limitacion", 3,
     re.compile(rf"\b(?:VOR|DVOR|DME|NDB|ILS|LOC|GP|GNSS|GPS|RADAR|TACAN|"
                rf"SSR|ADS-?B|MLAT)\b{S}"
                rf"\b(?:{FUERA}|{MERMA}|PERDIDA)\b", re.I)),

    # Un procedimiento no disponible obliga a replanificar la aproximacion o
    # la salida: es una restriccion operativa, no un cambio de carta.
    ("procedimiento", "Procedimientos", "limitacion", 3,
     re.compile(rf"\b(?:SID|STAR|IAC|RNP|RNAV|APCH|APP\s+\w+)\b{S}"
                rf"\b(?:{FUERA}|{MERMA})\b", re.I)),

    ("comunicaciones", "Comunicaciones", "limitacion", 3,
     re.compile(rf"\b(?:ATIS|FREQ|FRECUENCIA|RADIO|COM|VHF|HF)\b{S}\b(?:{FUERA}|{MERMA})\b", re.I)),

    ("meteorologia", "Meteorología", "limitacion", 4,
     re.compile(rf"\b(?:WXR|AWOS|AWS|SISTEMA\s+MET|MET)\b{S}\b(?:{FUERA}|{MERMA})\b", re.I)),

    ("servicio", "Servicios del aeródromo", "limitacion", 3,
     re.compile(rf"\b(?:TWR|AFIS|ATS|APP|ACC|SSEI|RFF|SEI|COMBUSTIBLE|FUEL|JET\s*A1)\b{S}"
                rf"\b(?:{FUERA}|{MERMA})\b", re.I)),

    # "UA SE REALIZARA" y "MOA" salieron de revisar a mano lo que el
    # clasificador dejaba fuera: son actividad aerea en los FIR, justo lo que
    # hay que mostrar. "UA" a secas seria demasiado corto, pero en los 453
    # NOTAMs solo aparece en esa construccion, las dos veces en SKED.
    ("espacio", "Espacio aéreo", "limitacion", 3,
     re.compile(rf"\b(?:TMA|CTR|ATZ|AWY|RTE)\b{S}\b(?:{FUERA}|{MERMA})\b|"
                r"\b(?:UAS|RPAS|DRONE|GLOBO|BALLOON|ASCENSO\s+DE|MOA|"
                r"UA\s+SE\s+REALIZARA|"
                r"AREA\s+(?:PELIGROSA|RESTRINGIDA|PROHIBIDA)|RESTR\w*|PROH\w*|SUSP\w*)\b", re.I)),

    # Un bache en pista o calle limita la operacion aunque el NOTAM no diga
    # CLSD ni LTD en ninguna parte.
    ("pavimento", "Estado del pavimento", "limitacion", 3,
     re.compile(r"\bBACHE\w*\b|\bHUECO\w*\b|\bFISURA\w*\b|\bPAVIMENTO\b" + S + r"\bDANAD", re.I)),

    ("obstaculo", "Obstáculos", "limitacion", 3,
     re.compile(r"\bOBST\b|\bOBSTACULO\w*\b|\bGRUA\b", re.I)),

    ("obras", "Obras en curso", "limitacion", 4,
     re.compile(r"\bWIP\b|\bTRABAJOS\b|\bOBRAS\b", re.I)),
]

# Red de seguridad. Si un NOTAM dice que algo esta cerrado o fuera de
# servicio y ninguna regla lo reconocio, entra igual: en una herramienta
# aeronautica es peor perder un cierre que mostrar uno de mas.
RESIDUAL = re.compile(rf"\b(?:{FUERA}|{MERMA})\b", re.I)


def clasificar(icao, texto):
    """(categoria, etiqueta, severidad, prioridad, ambito) o None si no aplica."""
    t = texto or ""
    if RUIDO.search(t):
        return None
    ambito = FIRS.get(icao, "Aeródromo")
    for cat, etiqueta, sev, prio, patron in REGLAS:
        if patron.search(t):
            return (cat, etiqueta, sev, prio, ambito)
    if RESIDUAL.search(t):
        return ("otros", "Otras restricciones", "limitacion", 5, ambito)
    return None


# ---------------------------------------------------------------------------
# Lectura del NOTAM crudo
# ---------------------------------------------------------------------------
# Un NOTAM del Charlie1 viene asi:
#
#   D 0566 / 26 MEDELLIN/PABLO TOBON URIBE (SKAN) 2608031500 / 2608242100 , HLP CLSD
#   |_________| |__________________________|      |___________________|     |______|
#    cabecera            aerodromo                      vigencia            lo que
#                                                                           importa
#
# En la tabla actual el operador tiene que leer toda la cabecera para llegar
# al final, que es lo unico que dice que esta pasando. Estas dos funciones
# separan las piezas para poder mostrarlas en su sitio.

# El nombre no cruza comas ni parentesis: en un NOTAM la coma separa la
# vigencia del cuerpo, y sin ese limite la captura se comia el texto entero
# cuando el NOTAM menciona el FIR antes del aerodromo.
_NOMBRE = re.compile(r"/\s*\d{2}\s+([^,()]{3,70}?)\s*\(([A-Z]{4})\)")
_NOMBRE_FIR = re.compile(r"\b(FIR(?:/UIR)?\s+[A-ZÁÉÍÓÚÑ]+)", re.I)
# El segundo termino puede ser PERM (permanente) en vez de una fecha. Pedir
# diez digitos en los dos lados hacia que la busqueda saltara el par de la
# cabecera y cogiera otro de mas adelante en el texto: una vigencia
# equivocada es peor que ninguna.
_VIGENCIA = re.compile(r"\b(\d{10})\s*/\s*(\d{10}|PERM)\b", re.I)


def nombre_aerodromo(texto):
    """'MEDELLIN/PABLO TOBON URIBE' a partir del NOTAM crudo, o None.

    Los NOTAM de FIR no llevan '(ICAO)' sino 'FIR/UIR BOGOTA', asi que se
    reconocen aparte.
    """
    m = _NOMBRE.search(texto or "")
    if not m:
        f = _NOMBRE_FIR.search(texto or "")
        if f:
            return " ".join(f.group(1).upper().split())
        return None
    nombre = " ".join(m.group(1).split())
    # Muchos NOTAM traen la cabecera repetida: "D0446/26 D 0689 / 26 LETICIA".
    # La expresion arranca en la primera "/26" y se lleva la segunda cabecera
    # dentro del nombre. Se recorta todo lo que sea numeracion de NOTAM,
    # con o sin espacios alrededor de la barra.
    # Los prefijos se apilan en cualquier orden y pueden repetirse:
    #   "C3308/24 260818 1837 3 C 1297 / 25 ARMENIA/EL EDEN"
    #    |______| |___________| |_________|
    #     numero      sello       cabecera
    # Por eso no basta una pasada de cada uno: se quitan en bucle hasta que
    # no quede prefijo que reconocer.
    basura = (
        r"^[A-Z]?\s*\d{3,4}\s*/\s*\d{2}\s+",   # C 1297 / 25
        r"^\d{6}\s+\d{4}\s+\d+\s+",            # 260818 1837 3
    )
    previo = None
    while previo != nombre:
        previo = nombre
        for patron in basura:
            nombre = re.sub(patron, "", nombre)
    return nombre[:70].strip(" -") or None


def resumen_operativo(texto):
    """
    La parte del NOTAM que dice que esta pasando: lo que va tras la ultima
    coma que separa la vigencia del cuerpo. Si no hay separador reconocible
    se devuelve el texto entero, que es preferible a devolver nada.
    """
    t = (texto or "").strip()
    if " , " in t:
        cuerpo = t.split(" , ")[-1].strip()
        if len(cuerpo) >= 8:
            return cuerpo
    return t


def vigencia(texto):
    """(inicio, fin) en el formato AAMMDDHHMM del NOTAM, o (None, None)."""
    m = _VIGENCIA.search(texto or "")
    return (m.group(1), m.group(2)) if m else (None, None)
