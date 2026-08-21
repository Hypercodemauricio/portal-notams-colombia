"""
Catalogo de Reglamentos Aeronauticos de Colombia (RAC) que indexa el portal.

Alcance elegido: los reglamentos de operacion (los que uno consulta al planear
y ejecutar un vuelo) mas los de apoyo (definiciones, meteorologia, informacion
aeronautica, aerodromos, licencias). No estan los puramente administrativos ni
los de aeronavegabilidad de fabricacion.

Cada entrada guarda la FECHA DE LA VERSION publicada por la Aerocivil. Esa
fecha viaja con cada resultado de busqueda: el usuario tiene que poder ver de
que version salio el texto que esta leyendo, porque los RAC se enmiendan con
frecuencia y un numeral puede cambiar de contenido sin cambiar de numero.

`id_archivo` es el identificador del gestor documental de la Aerocivil. Es lo
unico que hay que actualizar cuando publican una enmienda: el numero cambia y
la URL de descarga apunta al PDF nuevo.
"""

from dataclasses import dataclass

# El portal de la Aerocivil sirve los PDF por un unico script, cambiando el
# idFile. El dominio principal y el de los aeropuertos regionales corren el
# mismo gestor, asi que el segundo sirve de respaldo cuando el primero falla
# (cosa que pasa a menudo, igual que con el boletin Charlie1).
PLANTILLA_URL = (
    "https://{host}/autoridad_aeronautica/loader.php"
    "?lServicio=Tools2&lTipo=descargas&lFuncion=descargar&idFile={id_archivo}"
)
HOSTS = ("www.aerocivil.gov.co", "aeropuertocali.aerocivil.gov.co")


@dataclass(frozen=True)
class Reglamento:
    rac: str
    titulo: str
    id_archivo: int
    fecha_version: str  # dd/mm/aaaa, tal como la publica la Aerocivil
    grupo: str  # "operacion" | "apoyo"
    # El RAC 1 no es un articulado sino un diccionario de terminos, y se corta
    # de otra manera: por termino y no por numeral. Ver rac_segmentar.py.
    glosario: bool = False

    @property
    def archivo(self) -> str:
        return f"RAC_{self.rac}.pdf"

    def urls(self) -> list:
        return [
            PLANTILLA_URL.format(host=h, id_archivo=self.id_archivo) for h in HOSTS
        ]


CATALOGO = [
    # --- Operacion: lo que se consulta para planear y volar -------------------
    Reglamento("91", "Reglas Generales de Vuelo y de Operacion",
               32239, "30/07/2026", "operacion"),
    Reglamento("121", "Requisitos de Operacion: operaciones domesticas e "
                      "internacionales, regulares y no regulares",
               16908, "05/08/2025", "operacion"),
    Reglamento("135", "Requisitos de Operacion: operaciones nacionales e "
                      "internacionales, regulares y no regulares",
               16967, "17/02/2026", "operacion"),
    Reglamento("100", "Operacion de Sistemas de Aeronaves No Tripuladas (UAS)",
               16966, "20/05/2025", "operacion"),
    Reglamento("211", "Gestion del Transito Aereo",
               16974, "05/11/2025", "operacion"),
    Reglamento("212", "Servicio de Busqueda y Salvamento",
               16975, "03/03/2026", "operacion"),

    # --- Apoyo: definiciones, informacion, infraestructura, personal ----------
    Reglamento("1", "Definiciones",
               16878, "17/02/2026", "apoyo", glosario=True),
    Reglamento("215", "Servicios de Informacion Aeronautica",
               16976, "20/02/2026", "apoyo"),
    Reglamento("203", "Servicio Meteorologico para la Navegacion Aerea",
               16969, "09/03/2026", "apoyo"),
    Reglamento("204", "Cartas Aeronauticas",
               16970, "19/07/2025", "apoyo"),
    Reglamento("205", "Unidades de medida para las operaciones aereas y "
                      "terrestres de las aeronaves",
               16971, "04/12/2020", "apoyo"),
    Reglamento("210", "Telecomunicaciones Aeronauticas",
               16973, "04/02/2025", "apoyo"),
    Reglamento("14", "Aerodromos, Aeropuertos y Helipuertos",
               16891, "06/08/2024", "apoyo"),
    Reglamento("61", "Licencias para Pilotos y sus Habilitaciones",
               16900, "02/09/2022", "apoyo"),
    Reglamento("65", "Licencias para el personal aeronautico, diferente de la "
                     "tripulacion de vuelo",
               16902, "02/05/2025", "apoyo"),
    Reglamento("219", "Gestion de Seguridad Operacional",
               16978, "25/04/2024", "apoyo"),
]

POR_NUMERO = {r.rac: r for r in CATALOGO}


def buscar(rac: str):
    return POR_NUMERO.get(str(rac).strip().upper())
