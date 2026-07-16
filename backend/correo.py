"""
Aviso interno al equipo de Mecsa cuando un tejedor envia su reporte.

Es informativo: sirve para que el equipo vea cuanto llego de cada suborden y
pueda ir armando la programacion a tintoreria. No va a los tejedores.

Notas de formato (no son gusto, son restricciones de los clientes de correo):
  - Tablas y estilos inline: Outlook no soporta flexbox ni grid.
  - Fuente del sistema: Gmail bloquea las tipografias externas.
"""
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or "587")
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER or "no-reply@mecsa.com").strip()
MAIL_TO = [d.strip() for d in os.getenv("MAIL_TO", "").split(",") if d.strip()]
MAIL_ENABLED = os.getenv("MAIL_ENABLED", "true").strip().lower() in ("1", "true", "si", "yes")

MESES = ["ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic"]


def _kg(n) -> str:
    return f"{float(n or 0):,.2f}"


def _rollos(n) -> str:
    v = float(n or 0)
    return f"{int(v):,}" if v == int(v) else f"{v:,.1f}"


def _fecha_larga(dt) -> str:
    return f"{dt.day} {MESES[dt.month - 1]} {dt.year}, {dt:%H:%M}"


def asunto(nombre_taller: str, vez: int, tot_rollos, tot_kg) -> str:
    return f"{nombre_taller} · Reporte #{vez} · {_rollos(tot_rollos)} rollos, {_kg(tot_kg)} kg"


def construir_html(nombre_taller, vez, cuando, filas, total_asignadas):
    """filas: [{os, tejido, ancho, rollos, peso}] — solo las que el tejedor lleno."""
    tot_rollos = sum(float(f.get("rollos") or 0) for f in filas)
    tot_kg = sum(float(f.get("peso") or 0) for f in filas)

    C = {"borde": "#E2E8F0", "linea": "#F1F5F9", "tenue": "#94A3B8",
         "medio": "#64748B", "texto": "#0F172A", "gris": "#475569",
         "acero": "#3D5A80", "rojo": "#E11D2E"}
    FUENTE = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif")
    TH = (f"font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;"
          f"color:{C['tenue']};border-bottom:1px solid {C['borde']};padding-bottom:6px;")
    TD = f"font-size:13.5px;border-bottom:1px solid {C['linea']};padding:9px 8px;"
    NUM = TD + "font-variant-numeric:tabular-nums;"

    cuerpo = []
    for i, f in enumerate(filas):
        ultima = i == len(filas) - 1
        bb = f"border-bottom:1px solid {C['borde']};" if ultima else f"border-bottom:1px solid {C['linea']};"
        td = f"font-size:13.5px;padding:9px 8px;{bb}"
        num = td + "font-variant-numeric:tabular-nums;"
        cuerpo.append(f"""
        <tr>
          <td style="{td}padding-left:28px;font-weight:700;">{f['os']}</td>
          <td style="{td}color:{C['gris']};">{f['tejido']} / {f['ancho']}</td>
          <td align="right" style="{num}">{_rollos(f.get('rollos'))}</td>
          <td align="right" style="{num}padding-right:28px;">{_kg(f.get('peso'))}</td>
        </tr>""")

    pie = ""
    if total_asignadas:
        pie = (f'<tr><td colspan="4" style="padding:16px 28px 24px;">'
               f'<p style="margin:0;font-size:11.5px;color:{C["tenue"]};">'
               f'Reportó {len(filas)} de sus {total_asignadas} subórdenes.</p></td></tr>')

    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="520"
 style="width:520px;max-width:520px;margin:0 auto;background:#FFFFFF;border-collapse:collapse;
 font-family:{FUENTE};color:{C['texto']};">
  <tr><td colspan="4" style="height:3px;background:{C['rojo']};font-size:0;line-height:0;">&nbsp;</td></tr>
  <tr>
    <td colspan="4" style="padding:24px 28px 16px;">
      <p style="margin:0 0 3px;font-size:18px;font-weight:700;letter-spacing:-.01em;">{nombre_taller}</p>
      <p style="margin:0;font-size:13px;color:{C['medio']};">
        Reporte <span style="font-weight:700;color:{C['acero']};">#{vez}</span>
        &nbsp;·&nbsp; {_fecha_larga(cuando)}
      </p>
    </td>
  </tr>
  <tr>
    <th align="left"  style="{TH}padding-left:28px;">OS</th>
    <th align="left"  style="{TH}padding-left:8px;padding-right:8px;">Tejido</th>
    <th align="right" style="{TH}padding-left:8px;padding-right:8px;">Rollos</th>
    <th align="right" style="{TH}padding-right:28px;">Kg</th>
  </tr>
  {''.join(cuerpo)}
  <tr>
    <td colspan="2" style="padding:10px 8px 10px 28px;font-size:11px;font-weight:700;
        letter-spacing:.07em;text-transform:uppercase;color:{C['tenue']};">Total</td>
    <td align="right" style="padding:10px 8px;font-size:14px;font-weight:700;
        font-variant-numeric:tabular-nums;">{_rollos(tot_rollos)}</td>
    <td align="right" style="padding:10px 28px 10px 8px;font-size:14px;font-weight:700;
        font-variant-numeric:tabular-nums;">{_kg(tot_kg)}</td>
  </tr>
  {pie}
</table>"""


def construir_texto(nombre_taller, vez, cuando, filas):
    """Alternativa en texto plano: algunos clientes no muestran HTML."""
    out = [f"{nombre_taller} · Reporte #{vez} · {_fecha_larga(cuando)}", ""]
    for f in filas:
        out.append(f"  {f['os']}  {f['tejido']}/{f['ancho']}  "
                   f"{_rollos(f.get('rollos'))} rollos  {_kg(f.get('peso'))} kg")
    out += ["", f"  TOTAL  {_rollos(sum(float(f.get('rollos') or 0) for f in filas))} rollos  "
                f"{_kg(sum(float(f.get('peso') or 0) for f in filas))} kg"]
    return "\n".join(out)


def enviar_reporte(nombre_taller, vez, cuando, filas, total_asignadas=None) -> dict:
    """Best-effort: nunca lanza. El reporte del tejedor ya se guardo; si el correo
    falla no se pierde nada, pero se devuelve el motivo para no fallar en silencio
    (que es justo el problema del AppScript: avisaba 'exito' aunque no enviara)."""
    if not MAIL_ENABLED:
        return {"enviado": False, "motivo": "MAIL_ENABLED=false"}
    if not (SMTP_USER and SMTP_PASS and MAIL_TO):
        return {"enviado": False, "motivo": "faltan SMTP_USER / SMTP_PASS / MAIL_TO en .env"}
    if not filas:
        return {"enviado": False, "motivo": "sin filas que informar"}

    tot_r = sum(float(f.get("rollos") or 0) for f in filas)
    tot_k = sum(float(f.get("peso") or 0) for f in filas)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto(nombre_taller, vez, tot_r, tot_k)
    msg["From"] = MAIL_FROM
    msg["To"] = ", ".join(MAIL_TO)
    msg.attach(MIMEText(construir_texto(nombre_taller, vez, cuando, filas), "plain", "utf-8"))
    msg.attach(MIMEText(construir_html(nombre_taller, vez, cuando, filas, total_asignadas),
                        "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(MAIL_FROM, MAIL_TO, msg.as_string())
        return {"enviado": True, "para": MAIL_TO}
    except Exception as e:
        return {"enviado": False, "motivo": f"{type(e).__name__}: {e}"}
