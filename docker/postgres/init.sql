-- =====================================================
-- SecondBrain - Inicialización de PostgreSQL
-- =====================================================
-- Crea schemas separados por dominio para mejor modularidad
-- y habilita extensiones necesarias
-- =====================================================

-- Extensiones
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS unaccent;

-- =====================================================
-- Schemas por dominio
-- =====================================================

-- core: items, personas, empresas, proyectos (entidades principales)
CREATE SCHEMA IF NOT EXISTS core;

-- media: metadata de archivos (los binarios viven en MinIO)
CREATE SCHEMA IF NOT EXISTS media;

-- processing: cola de jobs, history de procesamiento
CREATE SCHEMA IF NOT EXISTS processing;

-- analytics: dinámicas conversacionales, salud relacional
CREATE SCHEMA IF NOT EXISTS analytics;

-- audit: logs sensibles, history de cambios importantes
CREATE SCHEMA IF NOT EXISTS audit;

-- Permisos para el usuario de la app
GRANT ALL ON SCHEMA core TO secondbrain;
GRANT ALL ON SCHEMA media TO secondbrain;
GRANT ALL ON SCHEMA processing TO secondbrain;
GRANT ALL ON SCHEMA analytics TO secondbrain;
GRANT ALL ON SCHEMA audit TO secondbrain;

-- search_path por defecto: la app usa core como principal,
-- public sigue disponible por compatibilidad con Alembic
ALTER DATABASE secondbrain SET search_path TO core, media, processing, analytics, audit, public;

-- Configuración de búsqueda full-text en español
-- (combina spanish + unaccent para que "café" matchee "cafe")
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'spanish_unaccent') THEN
        CREATE TEXT SEARCH CONFIGURATION spanish_unaccent (COPY = spanish);
        ALTER TEXT SEARCH CONFIGURATION spanish_unaccent
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, spanish_stem;
    END IF;
END
$$;

-- Verificación
SELECT 'Schemas creados:' AS info;
SELECT schema_name FROM information_schema.schemata
WHERE schema_name IN ('core', 'media', 'processing', 'analytics', 'audit')
ORDER BY schema_name;

SELECT 'Extensiones instaladas:' AS info;
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('vector', 'pg_trgm', 'uuid-ossp', 'unaccent')
ORDER BY extname;
