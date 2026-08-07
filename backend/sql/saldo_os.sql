-- saldo_os — declaracion de saldos de hilo ("puchos") POR ORDEN DE SERVICIO.
--
-- Una fila por OS. `kg_declarado` es el TOTAL vigente declarado para esa OS, no
-- un incremento: el portal carga el valor actual en el formulario y el tejedor
-- lo edita, asi que el upsert sobrescribe a proposito. Si declaro 5 y luego
-- quiere 8, escribe 8.
--
-- ESTA TABLA NO LA CREA EL PORTAL SOLO. Es de propiedad COMPARTIDA:
--   * el portal escribe kg_declarado / declarado_en / declarado_por (lo que dice el tejedor)
--   * MECSA escribe kg_recibido / recibido_en / recibido_por (lo que pesa al recoger el hilo)
-- Se ejecuta UNA vez, de comun acuerdo con MECSA, y ambos lados lo anotan
-- (encargo de Planeamiento y Control de la Produccion, 2026-08-07, §4.2).
--
-- Formatos, para que MECSA los muestre bien:
--   orden          'TRI1822'    igual que guia_os.orden, mayusculas y sin espacios
--   taller         'TRI'        codigo de 3 letras (usuarios.tejedor), no el nombre comercial
--   declarado_por  'tyf'        usuario de login del portal (usuarios.usuario); no hay correo ni id
--
-- Mientras la tabla no exista, el backend lo detecta al arrancar (to_regclass) y
-- el portal no muestra la tarjeta de saldos. No se rompe nada.

CREATE TABLE IF NOT EXISTS saldo_os (
    orden          TEXT        PRIMARY KEY,  -- OS: 'TRI1822'. Una fila por OS.
    taller         TEXT        NOT NULL,     -- codigo de 3 letras: 'TRI'
    kg_declarado   REAL        NOT NULL,     -- TOTAL declarado por el TEJEDOR para esa OS
    declarado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    declarado_por  TEXT,                     -- usuario del portal
    kg_recibido    REAL,                     -- lo llena MECSA al recoger el hilo. El portal NO lo toca
    recibido_en    TIMESTAMPTZ,              -- idem
    recibido_por   TEXT,                     -- idem
    nota           TEXT
);

-- El portal siempre filtra por taller (lo deriva del token, nunca del cliente).
CREATE INDEX IF NOT EXISTS saldo_os_taller_idx ON saldo_os (taller);

-- Kilos no negativos, en la base y no solo en el formulario. Se anaden con nombre
-- explicito y solo si faltan, para que este archivo se pueda volver a ejecutar
-- sobre una tabla que ya existe sin reventar.
--
-- NO hay tope superior a proposito. Una cifra alta puede ser legitima: los
-- talleres reciclan los puchos entre OS y cargan el total donde les conviene
-- (§3.5 del encargo), asi que un CHECK rechazaria datos buenos. El portal avisa
-- cuando los kg pasan el 20% de lo programado de la OS, pero deja continuar,
-- que es justo lo que pide la §3.4.4.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'saldo_os_declarado_no_negativo') THEN
        ALTER TABLE saldo_os ADD CONSTRAINT saldo_os_declarado_no_negativo
            CHECK (kg_declarado >= 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'saldo_os_recibido_no_negativo') THEN
        ALTER TABLE saldo_os ADD CONSTRAINT saldo_os_recibido_no_negativo
            CHECK (kg_recibido IS NULL OR kg_recibido >= 0);
    END IF;
END $$;

-- saldo_suborden fue la primera version de esto, llaveada por suborden. Nunca
-- llego a tener datos ni a usarse desde ningun lado: se elimina para no dejar
-- una tabla huerfana en una base que ya arrastra un problema de propiedad
-- compartida del esquema (que fue justo el motivo de coordinar el DDL).
DROP TABLE IF EXISTS saldo_suborden;
