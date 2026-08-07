"""Tests de interfaz del saldo de hilo, sobre el HTML REAL del portal.

    python -m playwright install chromium     # una sola vez
    python tests/test_saldo_ui.py

El saldo se declara POR OS, no por suborden: Achorado 3.0 lo suma al total de
cada OS. Por eso no vive en la tabla (que es por suborden) sino en una tarjeta
con ventana propia, y una OS solo se puede declarar cuando TODAS sus subordenes
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
        """Las OS elegibles, sin el marcador 'Elige una OS...' del principio."""
        sel = pg.locator("#saldo-os")
        if not sel.count():
            return []
        return [o for o in sel.locator("option").all_text_contents() if not o.startswith("Elige")]

    print("\n--- la tabla ya NO tiene columna de saldo ---")
    check("14 columnas en el encabezado",
          pg.locator("#table-body").page.locator("thead tr").first.locator("th").count() == 14,
          str(pg.locator("thead tr").first.locator("th").count()))
    check("no hay ningun campo de saldo en las filas",
          pg.locator("#table-body .saldo-input").count() == 0)

    print("\n--- la tarjeta ---")
    check("existe y esta a la derecha", pg.locator("#saldo-card").is_visible())
    check("NO muestra el total de kg", pg.locator("#saldo-card .num").count() == 0)
    check("informa cuantas OS hay declaradas",
          "1 OS declarada" in pg.locator("#saldo-hint").inner_text(),
          pg.locator("#saldo-hint").inner_text())

    print("\n--- la ventana ---")
    check("arranca cerrada", pg.locator("#saldo-modal").is_hidden())
    pg.click("#saldo-card")
    check("se abre al tocar la tarjeta", pg.locator("#saldo-modal").is_visible())
    caja = pg.locator("#saldo-modal .modal").bounding_box()
    vp = pg.viewport_size
    centro_x = caja["x"] + caja["width"] / 2
    centro_y = caja["y"] + caja["height"] / 2
    check("esta centrada en pantalla",
          abs(centro_x - vp["width"] / 2) < 12 and abs(centro_y - vp["height"] / 2) < 12,
          f"centro=({centro_x:.0f},{centro_y:.0f}) pantalla=({vp['width']},{vp['height']})")
    check("tiene boton de guardar", pg.locator("#saldo-guardar").count() == 1)
    check("arranca deshabilitado (nada que guardar)", pg.locator("#saldo-guardar").is_disabled())

    print("\n--- solo aparecen OS con TODAS sus subordenes terminadas ---")
    ops = opciones()
    check("solo TRI1801 (la unica completa)", len(ops) == 1 and ops[0].startswith("TRI1801"), str(ops))
    check("arranca SIN OS elegida", pg.locator("#saldo-os").input_value() == "",
          repr(pg.locator("#saldo-os").input_value()))
    check("y con el campo de kg vacio", pg.locator("#saldo-kg").input_value() == "",
          repr(pg.locator("#saldo-kg").input_value()))
    check("el campo esta deshabilitado hasta elegir OS", pg.locator("#saldo-kg").is_disabled())
    pg.select_option("#saldo-os", "TRI1801")
    check("al elegir OS se habilita", pg.locator("#saldo-kg").is_enabled())
    check("y trae los 40.5 ya declarados (para corregirlos)",
          pg.locator("#saldo-kg").input_value() == "40.5", pg.locator("#saldo-kg").input_value())

    print("\n--- TRI1802 tiene DOS subordenes: con una no basta ---")
    # La ventana es modal y bloquea la tabla, asi que hay que cerrarla para
    # llegar a las casillas: es el mismo flujo que sigue una persona.
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

    print("\n--- NO se guarda solo: hay que pulsar el boton ---")
    SALDOS_POST.clear()
    pg.select_option("#saldo-os", "TRI1802")
    pg.fill("#saldo-kg", "150")
    pg.wait_for_timeout(1500)      # de sobra para el viejo autoguardado de 900ms
    check("escribir NO manda nada", len(SALDOS_POST) == 0, str(len(SALDOS_POST)))
    check("el boton se habilita", pg.locator("#saldo-guardar").is_enabled())
    check("aparece marcado como sin guardar",
          "sin guardar" in pg.locator(".saldo-lista").inner_text(),
          pg.locator(".saldo-lista").inner_text().replace("\n", " | "))

    pg.click("#saldo-guardar")
    pg.wait_for_timeout(600)
    check("al pulsar Guardar llega 1 peticion", len(SALDOS_POST) == 1, str(len(SALDOS_POST)))
    if SALDOS_POST:
        check("con la OS y los kg correctos",
              SALDOS_POST[0]["filas"] == [{"orden": "TRI1802", "kg": 150}],
              json.dumps(SALDOS_POST[0]["filas"]))
    check("la ventana se cierra al guardar", pg.locator("#saldo-modal").is_hidden())
    check("la tarjeta pasa a 2 OS declaradas",
          "2 OS declaradas" in pg.locator("#saldo-hint").inner_text(),
          pg.locator("#saldo-hint").inner_text())

    print("\n--- varias OS en un solo guardado ---")
    SALDOS_POST.clear()
    pg.click("#saldo-card")
    pg.select_option("#saldo-os", "TRI1801")
    pg.fill("#saldo-kg", "10")
    pg.select_option("#saldo-os", "TRI1802")
    pg.fill("#saldo-kg", "20")
    check("el boton cuenta las dos", "2" in pg.locator("#saldo-guardar").inner_text(),
          pg.locator("#saldo-guardar").inner_text())
    pg.click("#saldo-guardar")
    pg.wait_for_timeout(600)
    check("una sola peticion con las 2 OS",
          len(SALDOS_POST) == 1 and len(SALDOS_POST[0]["filas"]) == 2,
          json.dumps(SALDOS_POST[0]["filas"]) if SALDOS_POST else "nada")

    print("\n--- al volver a abrir, limpia otra vez ---")
    pg.click("#saldo-card")
    check("sigue abriendo sin OS elegida", pg.locator("#saldo-os").input_value() == "",
          repr(pg.locator("#saldo-os").input_value()))
    check("y con los kg vacios", pg.locator("#saldo-kg").input_value() == "",
          repr(pg.locator("#saldo-kg").input_value()))
    pg.select_option("#saldo-os", "TRI1801")
    check("TRI1801 mantiene sus 10", pg.locator("#saldo-kg").input_value() == "10",
          pg.locator("#saldo-kg").input_value())

    print("\n--- negativo: se rechaza y no se puede guardar ---")
    SALDOS_POST.clear()
    pg.fill("#saldo-kg", "-5")
    pg.wait_for_timeout(200)
    check("avisa", "negativo" in pg.locator("#saldo-aviso").inner_text().lower(),
          pg.locator("#saldo-aviso").inner_text())
    check("el boton queda deshabilitado", pg.locator("#saldo-guardar").is_disabled())

    print("\n--- cifra desproporcionada: avisa pero deja guardar ---")
    pg.fill("#saldo-kg", "900")     # TRI1801 es 1 suborden x 1000; 20% = 200
    pg.wait_for_timeout(200)
    check("sale el aviso '¿Seguro?'", "Seguro" in pg.locator("#saldo-aviso").inner_text(),
          pg.locator("#saldo-aviso").inner_text())
    check("y se puede guardar igual", pg.locator("#saldo-guardar").is_enabled())

    print("\n--- 0 es una declaracion valida, vacio no ---")
    SALDOS_POST.clear()
    pg.fill("#saldo-kg", "0")
    pg.wait_for_timeout(200)
    check("0 habilita el guardado", pg.locator("#saldo-guardar").is_enabled())
    pg.click("#saldo-guardar")
    pg.wait_for_timeout(600)
    check("y se manda como 0", SALDOS_POST and SALDOS_POST[0]["filas"][0]["kg"] == 0,
          json.dumps(SALDOS_POST[0]["filas"]) if SALDOS_POST else "nada")

    SALDOS_POST.clear()
    pg.click("#saldo-card")
    pg.select_option("#saldo-os", "TRI1801")
    pg.fill("#saldo-kg", "")
    pg.wait_for_timeout(200)
    check("vacio no deja guardar", pg.locator("#saldo-guardar").is_disabled())

    print("\n--- cerrar la ventana ---")
    pg.keyboard.press("Escape")
    check("Escape la cierra", pg.locator("#saldo-modal").is_hidden())
    pg.click("#saldo-card")
    # Clic en el fondo oscuro, fuera de la caja blanca (esquina superior izquierda).
    pg.mouse.click(5, 5)
    check("un clic en el fondo tambien", pg.locator("#saldo-modal").is_hidden())

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

    print("\n--- OS ya recogida por MECSA: se ve pero no se toca ---")
    # Mecsa peso el hilo de TRI1801 y la merma de esa OS ya quedo calculada
    # sobre esa cifra: cambiarla ahora dejaria dos numeros contradictorios.
    STOCK["saldosOs"] = [{"orden": "TRI1801", "kg": 40.5, "recibido": True}]
    pg.reload()
    pg.wait_for_selector("#table-body tr[data-subos]")
    pg.click("#saldo-card")
    check("aparece marcada como recogida en la lista",
          "recogido" in pg.locator(".saldo-lista").inner_text(),
          pg.locator(".saldo-lista").inner_text().replace("\n", " | "))
    pg.select_option("#saldo-os", "TRI1801")
    check("el campo queda en solo lectura", pg.locator("#saldo-kg").is_disabled())
    check("pero se sigue viendo el valor", pg.locator("#saldo-kg").input_value() == "40.5",
          pg.locator("#saldo-kg").input_value())
    check("y explica por que", "MECSA ya recogió" in pg.locator("#saldo-aviso").inner_text(),
          pg.locator("#saldo-aviso").inner_text())
    check("no se puede guardar", pg.locator("#saldo-guardar").is_disabled())
    pg.keyboard.press("Escape")

    print("\n--- taller sin nada declarado: la tarjeta muestra solo su titulo ---")
    STOCK["saldosOs"] = []
    pg.reload()
    pg.wait_for_selector("#table-body tr[data-subos]")
    check("no muestra ninguna cifra", pg.locator("#saldo-card .num").count() == 0)
    check("invita a declarar", "Toca para declarar" in pg.locator("#saldo-hint").inner_text(),
          pg.locator("#saldo-hint").inner_text())
    # El CSS lo pone en mayusculas (text-transform), igual que los otros
    # indicadores, asi que la comparacion va sin distinguir mayusculas.
    check("si muestra el titulo",
          "saldo de hilo (kg)" in pg.locator("#saldo-card .lbl").inner_text().lower(),
          pg.locator("#saldo-card .lbl").inner_text())
    SALDOS_POST.clear()
    pg.click("#saldo-card")
    pg.select_option("#saldo-os", "TRI1801")
    pg.fill("#saldo-kg", "25")
    pg.click("#saldo-guardar")
    pg.wait_for_timeout(600)
    check("tras guardar la tarjeta lo refleja",
          "1 OS declarada" in pg.locator("#saldo-hint").inner_text(),
          pg.locator("#saldo-hint").inner_text())
    check("y sigue sin mostrar kg", pg.locator("#saldo-card .num").count() == 0)

    print("\n--- errores de JavaScript ---")
    check("ninguno", not errores, "; ".join(errores[:3]))

    br.close()

srv.shutdown()
print("\n" + ("TODO OK" if not fallos else f"FALLARON {len(fallos)}: {fallos}"))
sys.exit(1 if fallos else 0)
