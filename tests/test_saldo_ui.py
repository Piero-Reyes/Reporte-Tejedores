"""Tests de interfaz del saldo de hilo (§8 del encargo), sobre el HTML REAL.

    python -m playwright install chromium     # una sola vez
    python tests/test_saldo_ui.py

El backend va simulado A PROPOSITO. Los tests §8.2/§8.5/§8.6/§8.7/§8.8 son
comportamientos del CLIENTE, y ejercitarlos contra la base real significaria
crear un `vez` en logs_ingresos (que es append-only) y dispararle un correo al
equipo de Mecsa en cada corrida. El simulador sirve el mismo
reporte-tejedores.html y captura el payload que el cliente manda, que es
exactamente lo que hay que verificar. Lo del lado del servidor (el upsert, que
kg_recibido sobreviva, el bloqueo de las cerradas) se verifica contra la base,
no aca.
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
PROG = 1509.75              # 20% => 301.95, el umbral del aviso de la §3.4.4

ENVIADOS = []               # payloads que el cliente mando a POST /api/stock


def fila(subos, finalizado=0, saldo=None):
    return {
        "subos": subos, "os": subos[:7], "tejido": "JERSEY", "ancho": "90",
        "fibra": None, "nombre": None, "proveedor": "PROV",
        "programado": PROG, "despachado": 100.0, "queda": PROG - 100,
        "fecha_inicio": "2026-07-01", "fecha_inicio_taller": None,
        "estado_actual": "En proceso", "rollos": None, "peso": None,
        "finalizado": finalizado, "fecha_liquidacion": None,
        "cerrada": False, "saldo_hilo": saldo, "guias": [],
    }


SUBOS = ["FRA1601AAA00001", "FRA1602AAA00002", "FRA1603AAA00003",
         "FRA1604AAA00004", "FRA1605AAA00005", "FRA1606AAA00006"]

STOCK = {
    "rol": "tejedor", "taller": "FRA", "usuario": "test",
    "nombreTaller": "Textiles DeFranco", "entregasMes": [],
    "ultimaVez": 10, "proximaVez": 11, "saldoActivo": True,
    # La primera ya viene terminada y CON saldo declarado: sirve para comprobar
    # que se pre-carga y que, si no se toca, no se reenvia.
    "data": [fila(SUBOS[0], finalizado=1, saldo=40.5)] + [fila(s) for s in SUBOS[1:]],
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
        if self.path.startswith("/api/stock"):
            ENVIADOS.append(body)
            ns = sum(1 for f in body["filas"] if f.get("saldo_hilo") is not None)
            return self._json({"ok": True, "vez": 11, "filas": len(body["filas"]),
                               "saldos": ns, "correo": {"enviado": True}})
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

    def tr(i):
        return pg.locator(f'#table-body tr[data-subos="{SUBOS[i]}"]')

    def saldo(i):
        return tr(i).locator(".saldo-input")

    def chk(i):
        return tr(i).locator(".estado-check")

    def toast():
        return pg.locator("#toast").inner_text()

    print("\n--- §3.3  el campo es accesible ---")
    check("tiene nombre accesible (aria-label)",
          "Saldo de hilo" in (saldo(1).get_attribute("aria-label") or ""),
          saldo(1).get_attribute("aria-label"))
    check("step=0.01 y min=0",
          saldo(1).get_attribute("step") == "0.01" and saldo(1).get_attribute("min") == "0")

    print("\n--- §3.2 / §8.5  aparece, se oculta y NO pierde el valor ---")
    check("sin marcar: oculto", saldo(1).is_hidden())
    chk(1).check()
    check("al marcar: visible", saldo(1).is_visible())
    saldo(1).fill("12.5")
    chk(1).uncheck()
    check("al desmarcar: oculto", saldo(1).is_hidden())
    chk(1).check()
    check("al volver a marcar: reaparece con 12.5", saldo(1).input_value() == "12.5",
          saldo(1).input_value())

    print("\n--- pre-carga de lo ya declarado ---")
    check("la que ya tenia 40.5 la muestra", saldo(0).input_value() == "40.5",
          saldo(0).input_value())
    check("y esta visible porque viene terminada", saldo(0).is_visible())

    print("\n--- §8.6  negativo: se rechaza ---")
    chk(2).check()
    saldo(2).fill("-5")
    saldo(2).blur()
    pg.wait_for_timeout(200)
    check("avisa que no puede ser negativo", "negativo" in toast().lower(), toast())
    check("y no se queda el valor negativo", saldo(2).input_value() != "-5",
          repr(saldo(2).input_value()))

    print("\n--- §8.7  cifra desproporcionada: avisa pero deja seguir ---")
    chk(3).check()
    saldo(3).fill("400")            # > 20% de 1509.75 (=301.95)
    saldo(3).blur()
    pg.wait_for_timeout(200)
    t = toast()
    check("sale el aviso '¿Seguro?'", "Seguro" in t, t)
    check("pero el valor SIGUE ahi (no bloquea)", saldo(3).input_value() == "400")

    print("\n--- por debajo del umbral no molesta ---")
    # showToast solo quita la clase "show": el TEXTO del toast se queda pegado.
    # Asi que aca hay que mirar si esta visible, no lo que dice.
    def toast_visible():
        return "show" in (pg.locator("#toast").get_attribute("class") or "")

    pg.wait_for_timeout(3200)       # deja morir el toast anterior
    check("el toast anterior ya se fue", not toast_visible())
    chk(4).check()
    saldo(4).fill("100")            # < 301.95
    saldo(4).blur()
    pg.wait_for_timeout(400)
    check("no avisa por una cifra normal", not toast_visible(), toast())

    print("\n--- §8.8  cinco en blanco y todo en la sexta: sin quejas ---")
    saldo(1).fill("")
    saldo(2).fill("")
    saldo(3).fill("")
    saldo(4).fill("")
    chk(5).check()
    saldo(5).fill("500")            # el total del taller, cargado en una sola
    saldo(5).blur()
    pg.wait_for_timeout(200)
    check("avisa (es >20%) pero no bloquea", saldo(5).input_value() == "500")

    print("\n--- §8.2  enviar: lo que viaja al servidor ---")
    ENVIADOS.clear()
    pg.click("#enviar-btn")
    pg.wait_for_function("() => !document.getElementById('enviar-btn').disabled")
    pg.wait_for_timeout(500)

    check("se envio exactamente 1 reporte", len(ENVIADOS) == 1, str(len(ENVIADOS)))
    filas = {f["subos"]: f for f in ENVIADOS[0]["filas"]}
    con_saldo = {k: v["saldo_hilo"] for k, v in filas.items() if v["saldo_hilo"] is not None}

    check("solo viaja el saldo de la sexta", con_saldo == {SUBOS[5]: 500}, str(con_saldo))
    check("§8.2 terminada sin saldo -> saldo_hilo null",
          filas[SUBOS[4]]["finalizado"] is True and filas[SUBOS[4]]["saldo_hilo"] is None,
          f"finalizado={filas[SUBOS[4]]['finalizado']} saldo={filas[SUBOS[4]]['saldo_hilo']}")
    check("la ya declarada (40.5) sin tocar NO se reenvia",
          filas[SUBOS[0]]["saldo_hilo"] is None, str(filas[SUBOS[0]]["saldo_hilo"]))
    check("vaciar el campo no retira la declaracion (no manda 0 ni null forzado)",
          filas[SUBOS[1]]["saldo_hilo"] is None)

    print("\n--- §8.4  cambiar un valor ya declarado SI viaja ---")
    ENVIADOS.clear()
    saldo(0).fill("45.75")
    saldo(0).blur()
    pg.wait_for_timeout(200)
    pg.click("#enviar-btn")
    pg.wait_for_function("() => !document.getElementById('enviar-btn').disabled")
    pg.wait_for_timeout(500)
    filas2 = {f["subos"]: f for f in ENVIADOS[0]["filas"]}
    check("el valor corregido viaja", filas2[SUBOS[0]]["saldo_hilo"] == 45.75,
          str(filas2[SUBOS[0]]["saldo_hilo"]))

    print("\n--- errores de JavaScript ---")
    check("ninguno", not errores, "; ".join(errores[:3]))

    br.close()

srv.shutdown()
print("\n" + ("TODO OK" if not fallos else f"FALLARON {len(fallos)}: {fallos}"))
sys.exit(1 if fallos else 0)
