"""
Portal de Tejedores - backend
=============================
Reemplaza el AppScript de reporte de stock por tejedor.

Fuentes (Supabase, las mismas que consume OC_Hilo):
  - guia_os        : detalle de subordenes (estado PENDIENTE = por reportar)
  - logs_ingresos  : log append-only de reportes, sellado por `vez`
  - usuarios       : identidad (reusa es_tejedor/tejedor/clave_hash/token)

Regla de oro: el `taller` SIEMPRE sale del token, nunca del cliente.
"""
import hashlib
import hmac
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

load_dotenv()

DB_URL = os.getenv("SUPABASE_DB_URL")
if not DB_URL:
    raise RuntimeError("Falta SUPABASE_DB_URL en .env")

app = FastAPI(title="Portal Tejedores")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restringir al origen del portal al desplegar
    allow_methods=["*"],
    allow_headers=["*"],
)

# Abrir una conexion a Supabase cuesta ~1s (TLS a us-west-2); la query solo ~0.16s.
# El pool mantiene las conexiones vivas: ese es el grueso de la mejora de velocidad.
# La DB esta en us-west-2 y el servidor en Peru: ~170ms por round-trip.
# Por eso: (a) pool, para no pagar el handshake TLS (~1s), y (b) autocommit,
# para que devolver la conexion al pool no gaste un rollback extra de ida y vuelta.
# La regla aqui es contar viajes, no optimizar SQL (la query corre en 23ms).
POOL = ConnectionPool(
    DB_URL,
    min_size=2,
    max_size=8,
    timeout=20,
    kwargs={"row_factory": dict_row, "autocommit": True},
    open=False,
)


@app.on_event("startup")
def _abrir_pool():
    POOL.open(wait=True, timeout=30)


@app.on_event("shutdown")
def _cerrar_pool():
    POOL.close()


@contextmanager
def db():
    with POOL.connection() as conn:
        yield conn


# ---------------------------------------------------------------- auth

# Identico a _hash_clave/_verif_clave del backend de OC_Hilo (10.0.1.13:8010).
# pbkdf2-sha256, 100k iteraciones, formato 'salt_hex$hash_hex' (97 chars).
# Debe quedarse igual: una cuenta creada alla tiene que validar aca y viceversa.
PBKDF2_ITER = 100_000


def crear_clave_hash(clave: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", str(clave or "").encode("utf-8"), salt, PBKDF2_ITER)
    return salt.hex() + "$" + h.hex()


def verificar_clave(clave: str, clave_hash: str) -> bool:
    try:
        salt_hex, h_hex = str(clave_hash or "").split("$", 1)
        h = hashlib.pbkdf2_hmac(
            "sha256", str(clave or "").encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITER
        )
        return hmac.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


def _token_nuevo() -> str:
    """Mismo formato que OC_Hilo: 24 bytes en hex = 48 chars."""
    return os.urandom(24).hex()


def usuario_desde_token(token: str | None) -> dict:
    if not token:
        raise HTTPException(401, "Falta el token de sesion.")
    with db() as conn:
        u = conn.execute(
            """select usuario, tejedor, es_tejedor, es_admin, activo
                 from usuarios where token = %s""",
            (token,),
        ).fetchone()
    if not u or not u["activo"]:
        raise HTTPException(401, "Sesion invalida o usuario inactivo.")
    return u


def tejedor_desde_token(token: str | None) -> dict:
    """Traduce token -> usuario tejedor. Unica fuente del `taller`."""
    u = usuario_desde_token(token)
    if not u["es_tejedor"] or not u["tejedor"]:
        raise HTTPException(403, "Este usuario no es un tejedor.")
    return u


def admin_desde_token(token: str | None) -> dict:
    u = usuario_desde_token(token)
    if not u["es_admin"]:
        raise HTTPException(403, "Se requiere un usuario administrador.")
    return u


# ---------------------------------------------------------------- modelos

class LoginReq(BaseModel):
    usuario: str
    clave: str


class FilaReporte(BaseModel):
    subos: str
    rollos: float | None = None
    peso: float | None = None
    finalizado: bool = False


class ReporteReq(BaseModel):
    filas: list[FilaReporte]


class TejedorReq(BaseModel):
    usuario: str
    taller: str
    clave: str | None = None      # opcional al editar: vacio = no cambiar
    activo: bool = True


# ---------------------------------------------------------------- endpoints

PORTAL_HTML = os.path.join(os.path.dirname(__file__), "..", "reporte-tejedores.html")


@app.get("/", response_class=HTMLResponse)
def portal():
    """Sirve el portal desde el mismo origen que la API (evita CORS)."""
    with open(PORTAL_HTML, encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/login")
def login(req: LoginReq):
    with db() as conn:
        u = conn.execute(
            """select id, usuario, clave_hash, es_tejedor, es_admin, tejedor, activo
                 from usuarios where usuario = %s""",
            (req.usuario.strip(),),
        ).fetchone()

        if not u or not u["activo"] or not verificar_clave(req.clave, u["clave_hash"]):
            raise HTTPException(401, "Usuario o contrasena incorrectos.")

        es_tejedor = bool(u["es_tejedor"] and u["tejedor"])
        if not es_tejedor and not u["es_admin"]:
            raise HTTPException(403, "Este usuario no tiene acceso al portal de tejedores.")

        token = _token_nuevo()
        conn.execute("update usuarios set token = %s where id = %s", (token, u["id"]))

    return {
        "token": token,
        "usuario": u["usuario"],
        "tejedor": u["tejedor"],
        "rol": "tejedor" if es_tejedor else "admin",
    }


# Las subordenes de un taller viven en DOS sitios, y hay que unir ambos:
#
#   1. guia_os        -> espejo de la hoja "Achorado". OS ya sincronizadas.
#   2. preorden_lineas -> OS creadas en la propia pagina como EPTe. Solo llegan a
#                         guia_os cuando alguien sincroniza el sheet; mientras tanto
#                         existen unicamente aqui (p.ej. FAM0081 / EPTe-0010).
#
# Ojo con los datos sucios:
#   - `preordenes.os` viene con mayusculas inconsistentes ('fam0081' vs 'FRA1601') -> upper().
#   - `guia_os.estado` trae 'cERRADO' ademas de 'CERRADO'                          -> upper(trim()).
#   - `ancho` puede venir '90' o '90.00' y `subos` se arma concatenando            -> _NORM_ANCHO.
#
# Para las lineas de EPTe no hay columna `consumo`; se deriva de mov_segregado.
# Verificado contra guia_os: consumo == sum(peso_mecsa) (el peso que Mecsa
# re-pesa), NO peso_guia (el declarado por el proveedor, siempre algo mayor).
_NORM_ANCHO = r"(case when {c} like '%%.%%' then regexp_replace({c}, '\.?0+$', '') else {c} end)"
_ANCHO_L = _NORM_ANCHO.format(c="l.ancho")

SQL_SUBORDENES = f"""
externas as (
    select g.suborden                as subos,
           upper(g.orden)            as os,
           g.tejido, g.ancho, g.fibra, g.nombre,
           g.proveedor_hilado        as proveedor,
           g.kilogramos              as programado,
           g.consumo                 as despachado,
           g.restante                as queda,
           g.fecha                   as fecha_inicio
      from guia_os g, yo
     where left(upper(g.orden), 3) = yo.taller
       and upper(trim(g.estado)) = 'PENDIENTE'
),
movs as (
    select suborden, sum(peso_mecsa) as mecsa from mov_segregado group by suborden
),
eptes as (
    select upper(p.os) || l.tejido || {_ANCHO_L}   as subos,
           upper(p.os)               as os,
           l.tejido, l.ancho, l.fibra,
           null::text                as nombre,
           l.proveedor               as proveedor,
           l.kg                      as programado,
           coalesce(m.mecsa, 0)      as despachado,
           l.kg - coalesce(m.mecsa, 0) as queda,
           -- Fecha de Inicio = cuando el tejedor confirmo que recibio el hilo.
           -- NO es fecha_registro (cuando se creo la EPTe) ni fecha_entrega (estimada).
           -- Verificado contra las OS que estan en ambas tablas: guia_os.fecha ==
           -- fecha_confirmacion en 5/6; registro y entrega calzan en 0/6.
           p.fecha_confirmacion      as fecha_inicio
      from preordenes p
      join preorden_lineas l on l.preorden_id = p.id
      cross join yo
      left join movs m on m.suborden = upper(p.os) || l.tejido || {_ANCHO_L}
     where left(upper(coalesce(p.os, '')), 3) = yo.taller
       and p.confirmada = 1
       and coalesce(l.tejido_cerrado, 0) = 0
       and coalesce(l.liquidado, 0) = 0
       and not exists (select 1 from guia_os g where upper(g.orden) = upper(p.os))
),
subordenes as (
    select * from externas
    union all
    select * from eptes
)
"""

# Todo en un solo round-trip: resuelve el token, saca el `vez` y trae las filas.
# El LEFT JOIN desde `yo` garantiza >=1 fila si el token es valido aunque no haya
# subordenes, para poder distinguir "sin trabajo" de "token invalido".
SQL_STOCK = f"""
with yo as (
    select usuario, tejedor as taller
      from usuarios
     where token = %(token)s and activo = 1 and es_tejedor = 1 and tejedor <> ''
),
-- Nombre comercial del taller para el saludo, tal como lo muestra el AppScript
-- ("Hola, FAMICOTTON", "Hola, TEXTIL DEFRANCO E.I.R.L."): sale de
-- guia_os.proveedor_tejeduria. Se toma el MAS FRECUENTE, no max(): TRI tiene dos
-- variantes ('TRICOT FINE S.A' x325 y 'T&F TEXTILES S.A.' x3) y por orden
-- alfabetico podria ganar la equivocada.
nombre_taller as (
    select taller, nombre from (
        select left(upper(g.orden), 3) as taller,
               g.proveedor_tejeduria   as nombre,
               row_number() over (partition by left(upper(g.orden), 3)
                                  order by count(*) desc) as rn
          from guia_os g
         where coalesce(g.orden, '') <> ''
           and coalesce(g.proveedor_tejeduria, '') <> ''
         group by 1, 2
    ) x where rn = 1
),
ult as (
    select max(vez) as vez from logs_ingresos
     where taller = (select taller from yo)
),
-- Ultimo estado conocido POR SUBORDEN, no "las filas del ultimo vez".
-- Un reporte no siempre incluye todas las subordenes: p.ej. FAM0072JLL13590 no
-- entro en el vez 84 (trae 15 de 16), y su ultimo dato es del vez 83. Filtrar por
-- vez = max(vez) le borraria el estado. El AppScript arrastra el ultimo conocido.
ultimo as (
    select distinct on (l.subos)
           l.subos, l.rollos, l.peso, l.finalizado, l.vez
      from logs_ingresos l
     where l.taller = (select taller from yo)
     order by l.subos, l.vez desc
),
{SQL_SUBORDENES}
select yo.usuario,
       yo.taller,
       coalesce(nt.nombre, yo.usuario) as nombre_taller,
       (select vez from ult)    as ultima_vez,
       s.subos, s.os, s.tejido, s.ancho, s.fibra, s.nombre, s.proveedor,
       s.programado, s.despachado, s.queda, s.fecha_inicio,
       r.rollos                 as rollos,
       r.peso                   as peso,
       coalesce(r.finalizado, 0) as finalizado,
       -- Desglose del `despachado`: las guias de remision que lo componen.
       -- Va como agregado JSON para no gastar un segundo round-trip (~170ms).
       --
       -- Solo guias REALES (numero de guia numerico). mov_segregado tambien guarda
       -- 5 filas 'Historico (reporte tejedor)' con 0 rollos y sin guia: son backfill
       -- de Mecsa a partir del propio reporte del tejedor, no entregas. Mostrarlas
       -- como guias contradiria el Despachado (p.ej. FAM0079JLL: despachado 0.00 y
       -- una 'guia' de 504.8). Filtrando, las 34 subordenes con guias cuadran exacto.
       coalesce((
           select json_agg(json_build_object(
                      'guia',      m.guia,
                      'parte',     m.parte_entrada,
                      'fecha',     m.fecha,
                      'rollos',    m.rollos,
                      'peso_guia', m.peso_guia,
                      'peso_mecsa', m.peso_mecsa)
                  order by m.fecha, m.id)
             from mov_segregado m
            where upper(m.suborden) = upper(s.subos)
              and m.guia ~ '^[0-9]+$'
       ), '[]'::json)           as guias
  from yo
  left join nombre_taller nt on nt.taller = yo.taller
  left join subordenes s on true
  left join ultimo r on r.subos = s.subos
 order by s.os, s.tejido
"""

CAMPOS_FILA = ("subos", "os", "tejido", "ancho", "fibra", "nombre", "proveedor",
               "programado", "despachado", "queda", "fecha_inicio",
               "rollos", "peso", "finalizado", "guias")


def _fecha_iso(v: str | None) -> str | None:
    """'17/12/2025', '4/2/2026', '1/10/2021' -> '2025-12-17' (criterio del AppScript).

    En la data conviven 4 variantes de D/M/YYYY (con y sin ceros a la izquierda);
    todas son dia/mes/anio. Si algo no parsea se devuelve tal cual: mejor mostrar
    el dato crudo que inventar una fecha.
    """
    if not v:
        return None
    s = str(v).strip().split(" ")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s



@app.get("/api/stock")
def get_stock(x_token: str | None = Header(default=None)):
    if not x_token:
        raise HTTPException(401, "Falta el token de sesion.")

    with db() as conn:
        filas = conn.execute(SQL_STOCK, {"token": x_token}).fetchall()

    if not filas:
        raise HTTPException(401, "Sesion invalida o usuario sin acceso de tejedor.")

    cab = filas[0]

    data = []
    for f in filas:
        if f["subos"] is None:
            continue
        fila = {k: f[k] for k in CAMPOS_FILA if k in f}
        # Las fechas vienen como texto en 4 variantes de D/M/YYYY; el AppScript
        # las muestra en ISO. Se normaliza aca para que la tabla sea homogenea.
        fila["fecha_inicio"] = _fecha_iso(f["fecha_inicio"])
        for g in fila["guias"]:
            g["fecha"] = _fecha_iso(g.get("fecha"))
        data.append(fila)

    return {
        "taller": cab["taller"],
        "usuario": cab["usuario"],
        # Solo para el saludo: el nombre comercial, no el usuario de login.
        "nombreTaller": cab["nombre_taller"],
        "ultimaVez": cab["ultima_vez"],
        "proximaVez": (cab["ultima_vez"] or 0) + 1,
        "data": data,
    }


# ---------------------------------------------------------------- admin

@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    with open(os.path.join(os.path.dirname(__file__), "..", "admin.html"), encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/api/admin/tejedores")
def listar_tejedores(x_token: str | None = Header(default=None)):
    admin_desde_token(x_token)
    with db() as conn:
        usuarios = conn.execute(
            """select usuario, tejedor as taller, activo, (token is not null) as con_sesion
                 from usuarios where es_tejedor = 1 order by tejedor, usuario"""
        ).fetchall()
        # Talleres reales: los que tienen subordenes PENDIENTE en guia_os.
        talleres = conn.execute(
            """select left(orden,3) as codigo,
                      count(*) filter (where upper(trim(estado)) = 'PENDIENTE') as pendientes,
                      max(proveedor_tejeduria) as nombre
                 from guia_os
                where orden is not null and orden <> ''
                group by 1
               having count(*) filter (where upper(trim(estado)) = 'PENDIENTE') > 0
                order by 1"""
        ).fetchall()
    return {"usuarios": [dict(u) for u in usuarios], "talleres": [dict(t) for t in talleres]}


@app.post("/api/admin/tejedores")
def guardar_tejedor(req: TejedorReq, x_token: str | None = Header(default=None)):
    admin_desde_token(x_token)

    usuario = req.usuario.strip()
    taller = req.taller.strip().upper()
    if not usuario:
        raise HTTPException(400, "Falta el nombre de usuario.")
    if not taller:
        raise HTTPException(400, "Falta el taller.")

    with db() as conn:
        # El taller debe existir en guia_os: evita crear cuentas que nunca verian nada.
        hay = conn.execute(
            "select 1 from guia_os where left(orden,3) = %s limit 1", (taller,)
        ).fetchone()
        if not hay:
            raise HTTPException(400, f"El taller '{taller}' no existe en guia_os.")

        existe = conn.execute(
            "select id, es_admin, es_tejedor from usuarios where usuario = %s", (usuario,)
        ).fetchone()

        if existe:
            if existe["es_admin"]:
                raise HTTPException(400, "No se puede convertir un admin en tejedor.")
            if req.clave:
                conn.execute(
                    """update usuarios set clave_hash = %s, es_tejedor = 1,
                         tejedor = %s, activo = %s, token = null where id = %s""",
                    (crear_clave_hash(req.clave), taller, 1 if req.activo else 0, existe["id"]),
                )
            else:
                conn.execute(
                    """update usuarios set es_tejedor = 1, tejedor = %s, activo = %s
                        where id = %s""",
                    (taller, 1 if req.activo else 0, existe["id"]),
                )
            return {"ok": True, "accion": "actualizado", "usuario": usuario, "taller": taller}

        if not req.clave:
            raise HTTPException(400, "Un usuario nuevo necesita contrasena.")
        conn.execute(
            """insert into usuarios (usuario, clave_hash, es_admin, secciones, activo,
                                     token, es_tejedor, tejedor)
               values (%s, %s, 0, '', %s, null, 1, %s)""",
            (usuario, crear_clave_hash(req.clave), 1 if req.activo else 0, taller),
        )
    return {"ok": True, "accion": "creado", "usuario": usuario, "taller": taller}


@app.post("/api/admin/tejedores/eliminar")
def eliminar_tejedor(req: dict, x_token: str | None = Header(default=None)):
    admin_desde_token(x_token)
    usuario = str(req.get("usuario", "")).strip()
    if not usuario:
        raise HTTPException(400, "Falta el usuario.")
    with db() as conn:
        n = conn.execute(
            "delete from usuarios where usuario = %s and es_tejedor = 1", (usuario,)
        ).rowcount
    if not n:
        raise HTTPException(404, "No se encontro ese tejedor.")
    return {"ok": True, "usuario": usuario}


@app.post("/api/stock")
def post_stock(req: ReporteReq, x_token: str | None = Header(default=None)):
    u = tejedor_desde_token(x_token)
    taller = u["tejedor"]

    if not req.filas:
        raise HTTPException(400, "El reporte no trae filas.")

    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with db() as conn:
        with conn.transaction():
            # Bloquea el taller para que dos reportes simultaneos no colisionen el `vez`.
            # (no hay unique en (subos, vez): el lock es lo que garantiza el contador)
            conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"tejedor:{taller}",))

            ult = conn.execute(
                "select coalesce(max(vez), 0) as vez from logs_ingresos where taller = %s",
                (taller,),
            ).fetchone()
            vez = ult["vez"] + 1

            # Solo se aceptan subordenes que realmente son de este taller.
            # Misma union que el GET: si no, un reporte de una OS creada como EPTe
            # (sin fila en guia_os todavia) se rechazaria.
            validas = {
                r["subos"]: r
                for r in conn.execute(
                    f"""with yo as (select %(taller)s::text as taller),
                        {SQL_SUBORDENES}
                        select subos, despachado from subordenes""",
                    {"taller": taller},
                ).fetchall()
            }

            desconocidas = [f.subos for f in req.filas if f.subos not in validas]
            if desconocidas:
                raise HTTPException(400, f"Subordenes no asignadas a {taller}: {desconocidas}")

            for f in req.filas:
                consumo = validas[f.subos]["despachado"] or 0.0
                # Misma aritmetica que el AppScript: acumula sobre lo ya despachado.
                peso_con_mas_rep = consumo + (f.peso or 0.0)
                conn.execute(
                    """insert into logs_ingresos
                         (subos, fecha_ingreso, rollos, peso, peso_con_mas_rep,
                          peso_pendiente, finalizado, vez, taller)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (f.subos, fecha, f.rollos, f.peso, peso_con_mas_rep,
                     None, 1 if f.finalizado else 0, vez, taller),
                )

    return {"ok": True, "vez": vez, "filas": len(req.filas)}
