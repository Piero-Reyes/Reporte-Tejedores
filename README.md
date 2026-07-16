# Portal de Tejedores — backend

Reemplaza el AppScript de reporte de stock por tejedor.

## Correr

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8020
```

| ruta | quién | qué |
|---|---|---|
| <http://127.0.0.1:8020/> | tejedor | portal de reporte de stock |
| <http://127.0.0.1:8020/admin> | admin | alta/edición/baja de accesos de tejedores |

El backend sirve ambas páginas y la API desde el mismo origen (sin CORS).

## Accesos

`/api/login` acepta tejedores y admins, y devuelve `rol`. Cada página exige el suyo:
`/api/stock` requiere `es_tejedor`; `/api/admin/*` requiere `es_admin` (verificado: un
tejedor recibe 403 en el panel).

**Por qué existe este panel:** OC_Hilo ya gestiona usuarios, pero su
`guardarUsuarioForm()` solo manda `{usuario, clave, esAdmin, secciones, activo}` — nunca
`esTejedor`/`tejedor`. Su `login()` sí los lee, y el esquema los tiene desde una
migración, pero **no hay forma de crear una cuenta de tejedor desde OC_Hilo**. Por eso
había 0. Alternativa a este panel: agregar esos dos campos al formulario de OC_Hilo.

El panel valida: el taller debe existir en `guia_os`, no se puede convertir un admin en
tejedor, y un usuario nuevo exige contraseña.

## Cómo encaja con lo que ya existe

No toca el backend de OC_Hilo (`10.0.1.13:8010`). Comparte solo la base de datos:

| tabla | rol |
|---|---|
| `guia_os` | detalle de subórdenes. `estado='PENDIENTE'` = las que el tejedor reporta |
| `logs_ingresos` | log append-only de reportes, sellado por `vez` (correlativo por taller) |
| `usuarios` | identidad. Reusa `es_tejedor` / `tejedor` / `clave_hash` / `token` |

El AppScript escribía en `logs_ingresos` y OC_Hilo lee de ahí (`getStockTejedor` →
`ultimaVez`). Este backend ocupa el lugar del AppScript con la misma semántica, así
que OC_Hilo no se entera del cambio.

**Un envío = un `vez`, con todas las filas.** No es guardado por fila: OC_Hilo lee el
último `vez` como un reporte completo. Guardar fila por fila generaría N `vez` y rompería
esa lectura.

Aritmética heredada del AppScript, respetada tal cual:

```
peso_con_mas_rep = guia_os.consumo + peso_reportado     (acumula sobre lo despachado)
```

## Decisiones que vale la pena conocer

- **El `taller` sale del token, nunca del cliente.** Era el agujero del AppScript
  (`?tej=FAM&identifier=123456789` en la URL, editable) y de `getStockTejedor`
  (acepta cualquier `taller` en el body, incluso sin token válido).
- **Contar round-trips, no optimizar SQL.** La DB está en `us-west-2` y el servidor en
  Perú: ~170 ms por viaje, y abrir conexión cuesta ~1 s. La query corre en 23 ms.
  De ahí el pool + `autocommit` + resolver token y stock en una sola query.
  Resultado: 8 s (AppScript) → 0.20 s.
- **`estado` viene sucio**: existe `'cERRADO'` además de `'CERRADO'`. De ahí el
  `upper(trim(estado))`.
- **`ancho` viene inconsistente** en `os_externas` (`'90'` vs `'90.00'`). No afecta aquí
  porque `guia_os.suborden` ya trae la clave armada, pero ojo si algún día se arma
  `subos` por concatenación.
- **`vez` sin unique constraint**: el único índice de `logs_ingresos` es `id`. El
  correlativo lo protege un `pg_advisory_xact_lock` por taller.

## Identidad — compatible con OC_Hilo (verificado)

`crear_clave_hash()` / `verificar_clave()` son idénticas a `_hash_clave()` / `_verif_clave()`
del backend de OC_Hilo:

- **pbkdf2-hmac-sha256, 100 000 iteraciones**, salt de 16 bytes.
- Formato `salt_hex$hash_hex` (97 chars).
- Token: `os.urandom(24).hex()` → 48 chars (igual que `_token_nuevo()`).

Probado en ambos sentidos: una cuenta creada en OC_Hilo valida aquí, y una creada aquí
valida en OC_Hilo. **Un solo sistema de identidad** — si cambias el hash en un lado,
cámbialo en el otro.

Las cuentas de tejedor se crean desde OC_Hilo (`guardarUsuario`) con `es_tejedor=1` y
`tejedor='FAM'` (el **código de 3 letras**, no el nombre: `logs_ingresos.taller` usa
`FAM`/`LRK`/`FRA`/`RCA`/`TRI`, mientras `os_externas.tejedor` usa nombres completos).

## Pendientes de seguridad

1. **La clave del admin está en el código.** `_init_db()` de OC_Hilo siembra
   `admin` / `Mecsa2026`, y esa sigue siendo la clave real en producción (verificado).
   Está en el repo: cualquiera con acceso al código entra como admin. Cambiarla.
2. **Rotar la contraseña de Supabase** (se expuso en chat).
3. **`getStockTejedor` de OC_Hilo no valida el token**: acepta cualquier `taller` en el
   body, incluso con un token basura. Este portal no lo usa, pero el agujero sigue ahí
   mientras esa API esté accesible.
4. **CORS abierto** (`allow_origins=["*"]`) — restringir al origen real al desplegar.

## El fallo silencioso de LRK (24/06)

El propio backend de OC_Hilo lo deja anotado:

```python
_LOGS_INGRESOS_DESDE_SHEET = False  # el AppScript de los tejedores escribe los reportes directo a Supabase.
#   (Recomendado: reintento+alerta en el .gs para que un fallo no quede silencioso, como pasó con LRK el 24/06.)
```

En el AppScript, `saveNewStock` dispara y no comprueba nada: si la escritura falla, el
tejedor ve "éxito" igual y el reporte se pierde en silencio. Este portal no hace eso —
el `POST /api/stock` es transaccional y el front muestra el error real si algo falla.
Sigue faltando reintento automático.

## Fuera de alcance (fase 2)

El AppScript además hace: reporte de **Falla**, **alertas por correo/WhatsApp**
(`sendAlertEmail`, `sendWspMessage`), **subida de archivos** (`upload`) y escribe a
**BigQuery** (`getMovSegBQ`, `createDatasetIfDoesntExist`). Nada de eso está acá, así que
**el AppScript no se puede apagar todavía**.
