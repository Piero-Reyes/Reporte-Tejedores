-- saldo_os — declaracion de saldos de hilo ("puchos") POR ORDEN DE SERVICIO.
--
-- Reemplaza a saldo_suborden, que estaba llaveada por suborden. El dato se
-- consolida por OS: Achorado 3.0 lo suma al total de cada OS, y el tejedor lo
-- declara una sola vez por OS aunque esa OS tenga varias subordenes.
--
-- ESTA TABLA NO LA CREA EL PORTAL SOLO. Es de propiedad COMPARTIDA:
--   * el portal escribe kg_declarado / declarado_en / declarado_por (lo que dice el tejedor)
--   * MECSA escribe kg_recibido / recibido_en / recibido_por (lo que pesa al recoger el hilo)
-- Se ejecuta UNA vez, de comun acuerdo con MECSA, y ambos lados lo anotan
-- (encargo de Planeamiento y Control de la Produccion, 2026-08-07, §4.2).
--
-- Mientras la tabla no exista, el backend lo detecta al arrancar (to_regclass) y
-- el portal no muestra la tarjeta de saldos. No se rompe nada.

CREATE TABLE IF NOT EXISTS saldo_os (
    orden          TEXT        PRIMARY KEY,  -- OS: 'TRI1822'. Una fila por OS.
    taller         TEXT        NOT NULL,     -- codigo de 3 letras: 'TRI'
    kg_declarado   REAL        NOT NULL,     -- lo que escribe el TEJEDOR
    declarado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    declarado_por  TEXT,                     -- usuario del portal
    kg_recibido    REAL,                     -- lo llena MECSA al recoger el hilo. El portal NO lo toca
    recibido_en    TIMESTAMPTZ,              -- idem
    recibido_por   TEXT,                     -- idem
    nota           TEXT
);

-- El portal siempre filtra por taller (lo deriva del token, nunca del cliente).
CREATE INDEX IF NOT EXISTS saldo_os_taller_idx ON saldo_os (taller);

-- saldo_suborden fue la primera version de esto, llaveada por suborden. Nunca
-- llego a tener datos ni a usarse desde ningun lado: se elimina para no dejar
-- una tabla huerfana en una base que ya arrastra un problema de propiedad
-- compartida del esquema (que fue justo el motivo de coordinar el DDL).
DROP TABLE IF EXISTS saldo_suborden;
