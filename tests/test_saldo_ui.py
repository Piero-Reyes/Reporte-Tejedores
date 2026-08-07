"""Tests de interfaz del saldo de hilo, sobre el HTML REAL del portal.

    python -m playwright install chromium     # una sola vez
    python tests/test_saldo_ui.py

El saldo se declara POR OS, no por suborden: Achorado 3.0 lo suma al total de
cada OS. Por eso no vive en la tabla (que es por suborden) sino en una tarjeta
con panel propio, y una OS solo se puede declarar cuando TODAS sus subordenes
estan marcadas como terminadas.

El backend va simulado A PROPOSITO. Estos son comportamientos del CLIENTE, y
ejercitarlos contra la base real significaria crear un `vez` en logs_ingresos
(que es append-only) y dispararle un correo al equipo de Mecsa en cada corrida.
El simulador sirve el mismo reporte-tejedores.html y captura lo que el cliente
manda, que es exactamente lo que hay que verificar. El lado del servidor (el
upsert, que kg_recibido sobreviva, el bloqueo de las cerradas) se verifica
contra la base, no aca.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.sync_api import sync_playwright

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "reporte-tejedores.html")
PUERTO = 8099
PROG = 1000.0            # por suborden

SALDOS_POST = []         # lo que llega a POST /api/saldos
REPORTES = []            # lo que llega a POST /api/stock


def fila(subos, os_, finalizado=0):
    return {
        "subos": subos, "os": os_, "tejido": "JERSEY", "ancho": "90",
        "fibra": None, "nombre": None, "proveedor": "PROV",
        "programado": PROG, "despachado": 100.0, "queda": PROG - 100,
        "fecha_inicio": "2026-07-01", "fecha_inicio_taller": None,
        "estado_actual": "En proceso", "rollos": None, "peso": None,
        "finalizado": finalizado, "fecha_liquidacion": None,
        "cerrada": False, "guias": [],
    }


# TRI1801: una sola suborden, ya terminada y ya declarada (prueba la pre-carga).
# TRI1802: DOS subordenes sin terminar -> sirve para la regla "todas o ninguna".
# TRI1803: una suborden sin terminar.
FILAS = [
    fila("TRI1801RLK240", "TRI1801", finalizado=1),
    fila("TRI1802RLK240", "TRI1802"),
    fila("TRI1802JLL135", "TRI1802"),
    fila("TRI1803RLK240", "TRI1803"),
]

STOCK = {
    "rol": "tejedor", "taller": "TRI", "usuario": "test",
    "nombreTaller": "T&F Textiles S.A.", "entregasMes": [],
    "ultimaVez": 10, "proximaVez": 11, "saldoActivo": True,
    "saldosOs": [{"orden": "TRI1801", "kg": 40.5}],
    "data": FILAS,
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
            return self._json(STOCK)
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
            return self._json({"token": "TOK", "rol": "tejedor"})
        if self.path.startswith("/api/saldos"):
            SALDOS_POST.append(body)
            return self._json({"ok": True, "filas": len(body["filas"])})
        if self.path.startswith("/api/stock"):
            REPORTES.append(body)
            return self._json({"ok": True, "vez": 11, "filas": len(body["filas"]),
                               "correo": {"enviado": True}})
        return self._json({"ok": True, "filas": 0})


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

    pg.goto(f"http://127.0.0.1:{PUERTO}/")
    pg.fill("#login-user", "test")
    pg.fill("#login-pass", "x")
    pg.click("#login-form button[type=submit]")
    pg.wait_for_selector("#table-body tr[data-subos]")

    def chk(subos):
        return pg.locator(f'#table-body tr[data-subos="{subos}"] .estado-check')

    def opciones():
        sel = pg.locator("#saldo-os")
        return sel.locator("option").all_text_contents() if sel.count() else []

    def guardar_y_esperar(n_previos):
        pg.wait_for_function(f"() => window.__n === undefined || true")
        pg.wait_for_timeout(1400)      # 900ms de retardo + margen de red
        return len(SALDOS_POST) > n_previos

    print("\n--- la tabla ya NO tiene columna de saldo ---")
    check("14 columnas en el encabezado",
          pg.locator("#table-body").page.locator("thead tr").first.locator("th").count() == 14,
          str(pg.locator("thead tr").first.locator("th").count()))
    check("no hay ningun campo de saldo en las filas",
          pg.locator("#table-body .saldo-input").count() == 0)

    print("\n--- la tarjeta ---")
    check("existe y esta a la derecha", pg.locator("#saldo-card").is_visible())
    check("muestra el total ya declarado", "40.50" in pg.locator("#saldo-card .num").inner_text(),
          pg.locator("#saldo-card .num").inner_text())

    print("\n--- el panel ---")
    check("arranca cerrado", pg.locator("#saldo-pop").is_hidden())
    pg.click("#saldo-card")
    check("se abre al tocar la tarjeta", pg.locator("#saldo-pop").is_visible())

    print("\n--- solo aparecen OS con TODAS sus subordenes terminadas ---")
    ops = opciones()
    check("solo TRI1801 (la unica completa)", len(ops) == 1 and ops[0].startswith("TRI1801"), str(ops))
    check("pre-carga los 40.5 ya declarados", pg.locator("#saldo-kg").input_value() == "40.5",
          pg.locator("#saldo-kg").input_value())

    print("\n--- TRI1802 tiene DOS subordenes: con una no basta ---")
    # El panel es flotante y tapa las primeras filas, asi que hay que cerrarlo
    # para llegar a las casillas: es el mismo flujo que sigue una persona.
    def marcar(subos, valor=True):
        pg.keyboard.press("Escape")
        chk(subos).set_checked(valor)
        pg.wait_for_timeout(150)
        pg.click("#saldo-card")
        pg.wait_for_timeout(150)

    marcar("TRI1802RLK240")
    check("con 1 de 2 marcadas NO aparece", not any(o.startswith("TRI1802") for o in opciones()),
          str(opciones()))
    marcar("TRI1802JLL135")
    check("con 2 de 2 marcadas SI aparece", any(o.startswith("TRI1802") for o in opciones()),
          str(opciones()))

    print("\n--- desmarcar la saca de nuevo ---")
    marcar("TRI1802JLL135", False)
    check("vuelve a desaparecer", not any(o.startswith("TRI1802") for o in opciones()),
          str(opciones()))
    marcar("TRI1802JLL135")

    print("\n--- autoguardado: se guarda solo, sin pulsar nada ---")
    SALDOS_POST.clear()
    pg.select_option("#saldo-os", "TRI1802")
    pg.fill("#saldo-kg", "150")
    pg.wait_for_timeout(1400)
    check("llego exactamente 1 guardado", len(SALDOS_POST) == 1, str(len(SALDOS_POST)))
    if SALDOS_POST:
        check("con la OS y los kg correctos",
              SALDOS_POST[0]["filas"] == [{"orden": "TRI1802", "kg": 150}],
              json.dumps(SALDOS_POST[0]["filas"]))
    check("la tarjeta avisa que guardo", "Guardado" in pg.locator("#saldo-hint").inner_text(),
          pg.locator("#saldo-hint").inner_text())
    check("y el total sube a 190.50", "190.50" in pg.locator("#saldo-card .num").inner_text(),
          pg.locator("#saldo-card .num").inner_text())

    print("\n--- teclear no manda una peticion por tecla ---")
    SALDOS_POST.clear()
    pg.locator("#saldo-kg").fill("")
    for c in "12345":
        pg.locator("#saldo-kg").type(c, delay=60)
    pg.wait_for_timeout(1400)
    check("5 pulsaciones -> 1 sola peticion", len(SALDOS_POST) == 1, str(len(SALDOS_POST)))

    print("\n--- negativo: se rechaza y NO se guarda ---")
    SALDOS_POST.clear()
    pg.fill("#saldo-kg", "-5")
    pg.wait_for_timeout(1400)
    check("avisa", "negativo" in pg.locator("#saldo-aviso").inner_text().lower(),
          pg.locator("#saldo-aviso").inner_text())
    check("no se mando nada", len(SALDOS_POST) == 0, str(len(SALDOS_POST)))

    print("\n--- cifra desproporcionada: avisa pero SI guarda ---")
    SALDOS_POST.clear()
    pg.fill("#saldo-kg", "900")     # TRI1802 son 2 subordenes x 1000 = 2000; 20% = 400
    pg.wait_for_timeout(1400)
    check("sale el aviso '¿Seguro?'", "Seguro" in pg.locator("#saldo-aviso").inner_text(),
          pg.locator("#saldo-aviso").inner_text())
    check("pero se guardo igual", len(SALDOS_POST) == 1, str(len(SALDOS_POST)))

    print("\n--- 0 es una declaracion valida, vacio no ---")
    SALDOS_POST.clear()
    pg.fill("#saldo-kg", "0")
    pg.wait_for_timeout(1400)
    check("0 se guarda", any(f["filas"][0]["kg"] == 0 for f in SALDOS_POST), str(SALDOS_POST))
    SALDOS_POST.clear()
    pg.fill("#saldo-kg", "")
    pg.wait_for_timeout(1400)
    check("vacio no manda nada", len(SALDOS_POST) == 0, str(len(SALDOS_POST)))

    print("\n--- cerrar el panel ---")
    pg.keyboard.press("Escape")
    check("Escape lo cierra", pg.locator("#saldo-pop").is_hidden())
    pg.click("#saldo-card")
    pg.locator("#stats-row").click()
    check("un clic fuera tambien", pg.locator("#saldo-pop").is_hidden())

    print("\n--- el reporte ya no carga saldos ---")
    REPORTES.clear()
    pg.click("#enviar-btn")
    pg.wait_for_function("() => !document.getElementById('enviar-btn').disabled")
    pg.wait_for_timeout(500)
    check("se envio", len(REPORTES) == 1, str(len(REPORTES)))
    if REPORTES:
        check("ninguna fila trae saldo_hilo",
              all("saldo_hilo" not in f for f in REPORTES[0]["filas"]),
              json.dumps(REPORTES[0]["filas"][0]))

    print("\n--- errores de JavaScript ---")
    check("ninguno", not errores, "; ".join(errores[:3]))

    br.close()

srv.shutdown()
print("\n" + ("TODO OK" if not fallos else f"FALLARON {len(fallos)}: {fallos}"))
sys.exit(1 if fallos else 0)
