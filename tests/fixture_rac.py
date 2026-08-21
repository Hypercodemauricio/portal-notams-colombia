"""
Genera un PDF con la misma forma que un RAC real, para probar el segmentador
sin depender de la red ni de tener los PDF oficiales a mano.

Incluye a proposito las cuatro trampas que trae un RAC de verdad:
  1. encabezado y pie repetidos en todas las paginas,
  2. tabla de contenido con los mismos titulos que el articulado,
  3. cifras decimales dentro del texto ("1.500 m", "0.5 NM") que se parecen a
     un numeral pero no lo son,
  4. titulos de numeral que se van a la linea siguiente.
"""

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ENCABEZADO = ("Unidad Administrativa Especial de Aeronautica Civil - "
              "Oficina de Transporte Aereo")

CUERPO = [
    ("cap", "CAPITULO A GENERALIDADES"),
    ("num", "91.001", "Definiciones, abreviaturas y simbolos", [
        "(a) Para efectos de este reglamento aplican las definiciones del RAC 1.",
        "(b) Cuando se indique una distancia de 1.500 m o de 0.5 NM se entendera",
        "medida desde el umbral.",
    ]),
    ("num", "91.005", "Aplicacion", [
        "Este reglamento aplica a toda aeronave civil de matricula colombiana.",
    ]),
    ("num", "91.310", "", [  # titulo en la linea siguiente
        "Plan de vuelo: presentacion, contenido y cierre",
        "(a) Antes de iniciar un vuelo se presentara un plan de vuelo ante la",
        "dependencia ATS correspondiente, con una antelacion minima de sesenta",
        "(60) minutos.",
        "(b) El plan de vuelo se cerrara tan pronto como sea posible despues del",
        "aterrizaje.",
    ]),
    ("num", "91.315", "Aerodromo de alternativa", [
        "Se designara un aerodromo de alternativa cuando las condiciones",
        "meteorologicas previstas esten por debajo de los minimos.",
    ]),
    ("cap", "CAPITULO B OPERACIONES"),
    ("num", "91.1000", "Combustible y aceite", [
        "La aeronave llevara combustible suficiente para completar el vuelo",
        "previsto mas las reservas exigidas.",
    ]),
    ("ape", "APENDICE 1 FORMULARIO DE PLAN DE VUELO"),
    ("num", "91.2001", "Casilla 18 del formulario", [
        "En la casilla 18 se anotara la informacion suplementaria.",
    ]),
]


def _titulos_para_indice():
    for bloque in CUERPO:
        if bloque[0] == "num":
            titulo = bloque[2] or bloque[3][0]
            yield f"{bloque[1]} {titulo}"
        else:
            yield bloque[1]


def crear(destino: Path, rac: str = "91") -> Path:
    destino = Path(destino)
    c = canvas.Canvas(str(destino), pagesize=letter)
    ancho, alto = letter
    estado = {"pagina": 1}

    def plantilla():
        c.setFont("Helvetica", 7)
        c.drawString(60, alto - 40, ENCABEZADO)
        c.drawString(60, 40, f"RAC {rac}")
        c.drawRightString(ancho - 60, 40, f"{rac}-{estado['pagina']}")

    def nueva_pagina():
        c.showPage()
        estado["pagina"] += 1
        plantilla()
        return alto - 80

    plantilla()
    y = alto - 80

    # --- Tabla de contenido -------------------------------------------------
    c.setFont("Helvetica-Bold", 11)
    c.drawString(60, y, "TABLA DE CONTENIDO")
    y -= 20
    c.setFont("Helvetica", 9)
    for i, linea in enumerate(_titulos_para_indice(), start=3):
        c.drawString(60, y, linea)
        c.drawRightString(ancho - 60, y, str(i))
        y -= 14
        if y < 80:
            y = nueva_pagina()

    y = nueva_pagina()

    # --- Articulado ---------------------------------------------------------
    for bloque in CUERPO:
        if y < 120:
            y = nueva_pagina()
        if bloque[0] in ("cap", "ape"):
            c.setFont("Helvetica-Bold", 11)
            c.drawString(60, y, bloque[1])
            y -= 22
            continue

        _, numeral, titulo, lineas = bloque
        c.setFont("Helvetica-Bold", 10)
        c.drawString(60, y, f"{numeral} {titulo}".strip())
        y -= 16
        c.setFont("Helvetica", 9)
        for linea in lineas:
            c.drawString(60, y, linea)
            y -= 13
            if y < 80:
                y = nueva_pagina()
                c.setFont("Helvetica", 9)
        y -= 8

    c.showPage()
    c.save()
    return destino


if __name__ == "__main__":
    print(crear(Path("/tmp/RAC_91_falso.pdf")))
