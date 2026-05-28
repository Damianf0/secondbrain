# Plan de Sprints

## Sprint 0 — Setup base ⚡ (actual)

**Objetivo:** dejar el equipo configurado, los servicios corriendo, los modelos descargados, y validar que todo funciona end-to-end con tests mínimos.

### Entregables
- [x] Estructura del proyecto en carpetas
- [x] `docker-compose.yml` con 7 servicios (postgres, qdrant, minio, ollama, whisper, backend, frontend)
- [x] Dockerfiles para backend (Python + uv) y frontend (Streamlit)
- [x] Backend FastAPI con health checks y endpoints de prueba
- [x] Streamlit panel admin con dashboard, benchmark y vault
- [x] Scripts SQL de inicialización de Postgres con schemas
- [x] Bootstrap automático de buckets MinIO y modelos Ollama
- [x] `.env.example` con todas las variables
- [x] Documentación de setup para Windows
- [ ] Validación: levantar todo, ver dashboard verde, probar LLM y embeddings

### Validación al final
- `docker compose up -d` levanta todo
- Dashboard en `http://localhost:8501` muestra todos los servicios OK
- Test LLM responde en 5-15 segundos
- Test embedding devuelve vector de 1024+ dimensiones
- Upload de archivo al Vault funciona y muestra preview

---

## Sprint 1 — Importación histórica de WhatsApp 📥

**Objetivo:** parsear exports `.txt` de WhatsApp, mapear participantes a contactos canónicos, y guardar todo en el sistema.

### Entregables
- [ ] Schema de `core.items`, `core.personas`, `media.attachments`, `processing.jobs`
- [ ] Migrations Alembic
- [ ] Parser de exports `.txt` (con o sin medios adjuntos)
- [ ] Detección de participantes y mapeo a contactos canónicos
- [ ] Vista en panel para subir export y mapear participantes
- [ ] Guardado en MySQL/Postgres + archivos en MinIO
- [ ] Listado de items ingestados en el panel

---

## Sprint 2 — Bridge WhatsApp en vivo 📲

**Objetivo:** capturar mensajes nuevos en tiempo real desde whatsapp-web.js.

### Entregables
- [ ] Container Node.js con whatsapp-web.js
- [ ] Endpoint en backend para recibir eventos del bot
- [ ] Vista de QR en panel admin
- [ ] Captura de medias (audio → Whisper, imágenes → MinIO)
- [ ] Detección de fuente correcta (mensaje propio vs entrante)

---

## Sprint 3 — Pipeline de tagging y extracción 🧠

**Objetivo:** procesar cada mensaje con LLM para extraer hechos estructurados, tono, entidades.

### Entregables
- [ ] Prompt del tagger optimizado para español argentino
- [ ] Extracción de tono (cordial, tenso, agresivo, etc.)
- [ ] Extracción de hechos (promesas, transacciones, eventos)
- [ ] Entity resolution (mapear "Juan", "Juan P", "+54 9..." a misma persona)
- [ ] Almacenamiento en `core.facts`, `core.promesas`, `core.transacciones`

---

## Sprint 4 — Embeddings y Q&A 💬

**Objetivo:** retrieval híbrido y chat funcional para las 18 queries de referencia.

### Entregables
- [ ] Embeddings de mensajes en Qdrant
- [ ] Retriever híbrido: semantic (Qdrant) + estructurado (Postgres)
- [ ] Re-ranking de resultados
- [ ] Chat en panel para probar queries
- [ ] Validación con las 18 queries de referencia

---

## Sprints futuros

- **Sprint 5:** Imágenes con tiered processing (OCR + captioning)
- **Sprint 6:** Documentos (PDFs, Word, Excel)
- **Sprint 7:** Audios (Whisper integrado en pipeline)
- **Sprint 8:** Conector Gmail
- **Sprint 9:** Mem0 / memoria estructurada avanzada
- **Sprint 10:** Briefings proactivos
- **Sprint 11+:** Knowledge graph, dinámica conversacional, salud relacional, etc.

---

## Las 18 queries de referencia

Test suite que el sistema debe responder bien para considerarse funcional:

### Capacidad 1: Recuperación temporal con persona
1. ¿Cuándo fue la última vez que hablé con Juan Pérez y de qué?
2. ¿Qué le prometí entregar al cliente Acme Clínica la semana pasada?
3. ¿De qué hablamos con Lucía en Madrid?
4. ¿Cuántas veces hablé con el cliente X este mes y sobre qué temas?

### Capacidad 2: Recuperación de info específica
5. ¿Cuál era el modelo del NAS que cotizamos para Juan?
6. ¿Qué CUIT tiene la metalúrgica con la que trabajamos el sistema en FileMaker?
7. ¿Dónde guardé la captura del error 500 que me mandó el cliente del bot?

### Capacidad 3: Estado de proyectos y compromisos
8. ¿En qué fase está el proyecto del bot de Acme Clínica?
9. ¿Qué tareas pendientes tengo de la empresa esta semana?
10. ¿Qué le debo cumplir a quién y cuándo?

### Capacidad 4: Recuperación difusa / contextual
11. Esa receta que me mandó mi vieja por WhatsApp hace unos meses, la de pollo
12. La foto del comprobante del vuelo de la aerolínea

### Capacidad 5: Análisis y síntesis
13. Resumime todo lo que pasó con la denuncia de la aerolínea
14. ¿Qué patrones hay en los reclamos de mis clientes este trimestre?

### Capacidad 6: Financieras
15. ¿Cuánto gasté en herramientas/software este mes?
16. ¿Qué pagos tengo pendientes y cuánto debo facturar este mes?

### Capacidad 7: Proactivas / briefings
17. ¿Qué tengo que hacer hoy?
18. Briefing del día: novedades importantes de las últimas 24hs
