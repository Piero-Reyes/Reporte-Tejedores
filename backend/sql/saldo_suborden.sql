-- saldo_suborden — declaracion de saldos de hilo ("puchos") por suborden.
--
-- ESTA TABLA NO LA CREA EL PORTAL SOLO. Es de propiedad COMPARTIDA:
--   * el portal escribe kg_declarado / declarado_en / declarado_por (lo que dice el tejedor)
--   * MECSA escribe kg_recibido / recibido_en / recibido_por (lo que pesa al recoger el hilo)
-- Se ejecuta UNA vez, de comun acuerdo con MECSA, y ambos lados lo anotan
-- (encargo de Planeamiento y Control de la Produccion, 2026-08-07, §4.2).
--
-- Antecedente que motiva la regla: el portal agrego logs_ingresos.fecha_inicio y
-- fecha_liquidacion sin avisar y MECSA se entero meses despues, al fallarle una
-- restauracion de respaldo. La base la escriben tres programas y no hay ningun
-- mecanismo que avise a uno cuando otro cambia el esquema.
--
-- Mientras la tabla no exista, el backend lo detecta al arrancar (to_regclass) y
-- el portal simplemente no muestra el campo de saldo. No se rompe nada.

CREATE TABLE IF NOT EXISTS saldo_suborden (
    id             SERIAL PRIMARY KEY,
    subos          TEXT        NOT NULL,   -- suborden: 'FRA1602RLK24080'
    taller         TEXT        NOT NULL,   -- codigo de 3 letras: 'FRA'
    kg_declarado   REAL        NOT NULL,   -- lo que escribe el TEJEDOR
    declarado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    declarado_por  TEXT,                   -- usuario del portal
    kg_recibido    REAL,                   -- lo llena MECSA al recoger el hilo. El portal NO lo toca
    recibido_en    TIMESTAMPTZ,            -- idem
    recibido_por   TEXT,                   -- idem
    nota           TEXT,
    UNIQUE (subos)
);

-- El portal siempre filtra por taller (deriva el taller del token, nunca del cliente).
CREATE INDEX IF NOT EXISTS saldo_suborden_taller_idx ON saldo_suborden (taller);
