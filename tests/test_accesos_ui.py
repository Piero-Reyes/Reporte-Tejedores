"""Tests de la pestana "Accesos", que antes era la pagina aparte /admin.

    python tests/test_accesos_ui.py

Comprueba lo que importa de la unificacion: que la pestana solo exista para el
admin, que el alta/edicion/borrado siga mandando lo mismo que mandaba
admin.html, y que un tejedor no la vea.

El backend va simulado: crear y borrar cuentas de verdad tocaria `usuarios`, la
tabla de identidad que el portal comparte con OC_Hilo.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.sync_api import sync_playwright

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "reporte-tejedores.html")
PUERTO = 8098

ROL = {"v": "admin"}       # lo que devuelve /api/login y /api/stock
GUARDADOS = []             # POST /api/admin/tejedores
BORRADOS = []              # POST /api/admin/tejedores/eliminar

TALLERES = [
    {"codigo": "FAM", "nombre": "Famicotton", "pendientes": 16},
    {"codigo": "TRI", "nombre": "T&F Textiles S.A.", "pendientes": 6},
]
USUARIOS = [
    {"usuario": "famicotton", "taller": "FAM", "activo": 1, "con_sesion": False},
    {"usuario": "tyf", "taller": "TRI", "activo": 0, "con_sesion": False},
]

STOCK_ADMIN = {"rol": "admin", "talleres": TALLERES, "data": []}
STOCK_TEJEDOR = {
    "rol": "tejedor", "taller": "TRI", "usuario": "tyf",
    "nombreTaller": "T&F Textiles S.A.", "entregasMes": [],
    "ultimaVez": 3, "proximaVez": 4, "saldoActivo": True, "saldosOs": [],
    "data": [{
        "subos": "TRI1801RLK240", "os": "TRI1801", "tejido": "JERSEY", "ancho": "90",
        "fibra": None, "nombre": None, "proveedor": "PROV",
        "programado": 1000.0, "despachado": 100.0, "queda": 900.0,
        "fecha_inicio": "2026-07-01", "fecha_inicio_taller": None,
        "estado_actual": "En proceso", "rollos": None, "peso": None,
        "finalizado": 0, "fecha_liquidacion": None, "cerrada": False, "guias": [],
    }],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/stock"):
            return self._json(STOCK_ADMIN if ROL["v"] == "admin" else STOCK_TEJEDOR)
        if self.path.startswith("/api/admin/tejedores"):
            return self._json({"usuarios": USUARIOS, "talleres": TALLERES})
        with open(HTML, encoding="utf-8") as fh:
            b = fh.read().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or "{}")
        if self.path.startswith("/api/login"):
            return self._json({"token": "TOK", "rol": ROL["v"]})
        if self.path.startswith("/api/admin/tejedores/eliminar"):
            BORRADOS.append(body)
            return self._json({"ok": True, "usuario": body.get("usuario")})
        if self.path.startswith("/api/admin/tejedores"):
            GUARDADOS.append(body)
            return self._json({"ok": True, "accion": "creado",
                               "usuario": body["usuario"], "taller": body["taller"]})
        return self._json({"ok": True})


srv = HTTPServer(("127.0.0.1", PUERTO), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

fallos = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA ") + nombre + (f"  [{detalle}]" if detalle else ""))
    if not cond:
        fallos.append(nombre)


with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_page()
    errores = []
    pg.on("pageerror", lambda e: errores.append(str(e)))
    pg.on("dialog", lambda d: d.accept())

    def entrar():
        pg.fill("#login-user", "u")
        pg.fill("#login-pass", "x")
        pg.click("#login-form button[type=submit]")

    print("\n--- un solo enlace: el admin entra por / ---")
    pg.goto(f"http://127.0.0.1:{PUERTO}/")
    entrar()
    pg.wait_for_selector("#admin-bar:not([hidden])")
    check("la pestana Accesos aparece", pg.locator("#tab-accesos").is_visible())
    check("y arranca en Reporte de Stock",
          pg.locator("#tab-stock").get_attribute("aria-selected") == "true")

    print("\n--- la pestana carga las cuentas ---")
    pg.click("#tab-accesos")
    pg.wait_for_selector("#acc-tbody tr")
    check("lista las 2 cuentas", pg.locator("#acc-tbody tr").count() == 2,
          str(pg.locator("#acc-tbody tr").count()))
    check("marca activo/inactivo",
          pg.locator("#acc-tbody .pill.on").count() == 1
          and pg.locator("#acc-tbody .pill.off").count() == 1)
    opciones = pg.locator("#acc-taller option").all_text_contents()
    check("el desplegable trae los talleres", len(opciones) == 3, str(opciones))

    print("\n--- alta ---")
    GUARDADOS.clear()
    pg.fill("#acc-usuario", "roca")
    pg.select_option("#acc-taller", "FAM")
    pg.fill("#acc-clave", "clave-de-prueba")
    pg.click("#acc-form button[type=submit]")
    pg.wait_for_timeout(500)
    check("manda usuario, taller y clave",
          GUARDADOS and GUARDADOS[0] == {"usuario": "roca", "taller": "FAM",
                                         "clave": "clave-de-prueba", "activo": True},
          json.dumps(GUARDADOS[0]) if GUARDADOS else "nada")
    check("avisa que se guardo", "creado" in pg.locator("#acc-alerta").inner_text(),
          pg.locator("#acc-alerta").inner_text())
    check("y limpia el formulario", pg.locator("#acc-usuario").input_value() == "")

    print("\n--- alta sin clave: se rechaza en el cliente ---")
    GUARDADOS.clear()
    pg.fill("#acc-usuario", "nuevo")
    pg.select_option("#acc-taller", "TRI")
    pg.click("#acc-form button[type=submit]")
    pg.wait_for_timeout(300)
    check("no manda nada", len(GUARDADOS) == 0, str(len(GUARDADOS)))
    check("y lo dice", "contraseña" in pg.locator("#acc-alerta").inner_text().lower(),
          pg.locator("#acc-alerta").inner_text())

    print("\n--- edicion ---")
    GUARDADOS.clear()
    pg.locator("#acc-tbody [data-edit='famicotton']").click()
    check("carga el usuario", pg.locator("#acc-usuario").input_value() == "famicotton")
    check("y lo bloquea (es la llave)", pg.locator("#acc-usuario").is_disabled())
    check("preselecciona su taller", pg.locator("#acc-taller").input_value() == "FAM")
    check("aparece Cancelar edicion", pg.locator("#acc-cancelar").is_visible())
    pg.click("#acc-form button[type=submit]")     # sin clave: no la cambia
    pg.wait_for_timeout(500)
    check("edita sin clave y manda clave null",
          GUARDADOS and GUARDADOS[0]["clave"] is None, json.dumps(GUARDADOS[0]) if GUARDADOS else "nada")

    print("\n--- cancelar la edicion ---")
    pg.locator("#acc-tbody [data-edit='tyf']").click()
    pg.click("#acc-cancelar")
    check("desbloquea el usuario", pg.locator("#acc-usuario").is_enabled())
    check("y vuelve a 'Nuevo tejedor'",
          "Nuevo" in pg.locator("#acc-form-titulo").inner_text(),
          pg.locator("#acc-form-titulo").inner_text())

    print("\n--- borrado ---")
    BORRADOS.clear()
    pg.locator("#acc-tbody [data-del='tyf']").click()
    pg.wait_for_timeout(500)
    check("manda el usuario a borrar", BORRADOS and BORRADOS[0] == {"usuario": "tyf"},
          json.dumps(BORRADOS[0]) if BORRADOS else "nada")

    print("\n--- un tejedor NO ve la pestana ---")
    ROL["v"] = "tejedor"
    pg.evaluate("sessionStorage.clear()")
    pg.reload()
    entrar()
    pg.wait_for_selector("#table-body tr[data-subos]")
    check("la pestana Accesos queda oculta", pg.locator("#tab-accesos").is_hidden())
    check("la vista tambien", pg.locator("#vista-accesos").is_hidden())

    print("\n--- errores de JavaScript ---")
    check("ninguno", not errores, "; ".join(errores[:3]))

    br.close()

srv.shutdown()
print("\n" + ("TODO OK" if not fallos else f"FALLARON {len(fallos)}: {fallos}"))
sys.exit(1 if fallos else 0)
