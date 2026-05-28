"""
Genera docs/overview.pdf — brochure técnico de alto nivel para mostrar
a un colega o miembro del equipo. Sin .md fuente: el contenido vive
acá como HTML embebido. Pensado como "matched set" con pipeline.pdf
pero más visual y más corto (~3 páginas).

Correr en container ad-hoc:
  docker run --rm -v "${PWD}/docs:/work" -w /work python:3.12-slim sh -c "
    apt-get update -qq >/dev/null &&
    apt-get install -y -qq --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-liberation >/dev/null &&
    pip install --quiet weasyprint &&
    python _build_overview_pdf.py /work/overview.pdf
  "
"""

from __future__ import annotations

import sys
from pathlib import Path

from weasyprint import HTML, CSS


CSS_TEXT = r"""
@page {
  size: A4;
  margin: 16mm 14mm 16mm 14mm;
  @bottom-right {
    content: counter(page) " / " counter(pages);
    font-family: "Segoe UI", system-ui, sans-serif;
    font-size: 8pt;
    color: #9aa0a6;
  }
  @bottom-left {
    content: "SecondBrain · Overview técnico";
    font-family: "Segoe UI", system-ui, sans-serif;
    font-size: 8pt;
    color: #9aa0a6;
  }
}
@page :first {
  margin: 0;
  @bottom-right { content: ""; }
  @bottom-left { content: ""; }
}

html { font-size: 10pt; }
body {
  font-family: "Segoe UI", system-ui, sans-serif;
  color: #202124;
  line-height: 1.45;
  margin: 0;
}

/* ============================================================
   PORTADA
   ============================================================ */
.cover {
  page-break-after: always;
  break-after: page;
  height: 297mm;
  width: 210mm;
  background: linear-gradient(135deg, #0d2b4e 0%, #1a4480 60%, #2a6fc4 100%);
  color: #ffffff;
  position: relative;
  box-sizing: border-box;
  padding: 60mm 22mm 22mm 22mm;
}
.cover .brand {
  font-size: 9pt;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: #a8c3e6;
  margin-bottom: 6mm;
}
.cover .title {
  font-size: 56pt;
  font-weight: 800;
  margin: 0;
  line-height: 1;
  color: #ffffff;
  letter-spacing: -1.5pt;
}
.cover .accent {
  display: block;
  width: 32mm;
  height: 5px;
  background: #4d9aff;
  margin: 7mm 0 6mm 0;
}
.cover .subtitle {
  font-size: 18pt;
  font-weight: 300;
  color: #c8d8ec;
  line-height: 1.3;
  max-width: 150mm;
  margin-bottom: 8mm;
}
.cover .lead {
  font-size: 11pt;
  font-weight: 400;
  color: #d8e4f3;
  line-height: 1.55;
  max-width: 150mm;
}
.cover .pillars {
  position: absolute;
  bottom: 56mm;
  left: 22mm;
  right: 22mm;
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  gap: 4mm;
}
.cover .pillar {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-left: 3px solid #4d9aff;
  padding: 6mm 4mm;
  border-radius: 1.5mm;
}
.cover .pillar .ph {
  font-size: 9pt;
  color: #ffffff;
  font-weight: 600;
  margin-bottom: 1.5mm;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.cover .pillar .pb {
  font-size: 8.5pt;
  color: #c8d8ec;
  line-height: 1.35;
}
.cover .meta {
  position: absolute;
  bottom: 22mm;
  left: 22mm;
  right: 22mm;
  font-size: 9pt;
  color: #a8c3e6;
  line-height: 1.7;
  border-top: 1px solid #2c5180;
  padding-top: 4mm;
}
.cover .meta strong { color: #ffffff; }

/* ============================================================
   PÁGINAS DE CONTENIDO
   ============================================================ */
h1.section {
  font-size: 22pt;
  font-weight: 700;
  color: #0d2b4e;
  margin: 0 0 2mm 0;
  padding-bottom: 3mm;
  border-bottom: 2px solid #0d2b4e;
  letter-spacing: -0.3pt;
}
.section-lead {
  font-size: 9.5pt;
  color: #5f6368;
  margin: 0 0 6mm 0;
  font-style: italic;
}
h2.subsection {
  font-size: 12pt;
  font-weight: 600;
  color: #1a4480;
  margin: 6mm 0 2mm 0;
}

/* Page break entre secciones */
section.page {
  page-break-before: always;
  break-before: page;
}
section.page:first-of-type {
  page-break-before: auto;
}

/* ============================================================
   DIAGRAMA DE FLUJO (CSS puro)
   ============================================================ */
.flow {
  display: grid;
  grid-template-columns: 1fr;
  gap: 3mm;
  margin: 4mm 0;
}
.flow-row {
  display: grid;
  gap: 2.5mm;
  align-items: stretch;
}
.flow-row.r3 { grid-template-columns: 1fr 1fr 1fr; }
.flow-row.r4 { grid-template-columns: 1fr 1fr 1fr 1fr; }
.flow-row.r5 { grid-template-columns: 1fr 1fr 1fr 1fr 1fr; }
.flow-row.r1 { grid-template-columns: 1fr; }

.fcell {
  border: 1.2px solid #c8d4e2;
  border-radius: 1.5mm;
  padding: 3mm 3mm;
  background: #f8fbff;
  font-size: 8.5pt;
  line-height: 1.35;
  text-align: center;
}
.fcell .ft {
  font-weight: 600;
  color: #0d2b4e;
  display: block;
  margin-bottom: 1mm;
  font-size: 9pt;
}
.fcell.in   { background: #eef6ff; border-color: #6ea7e0; }
.fcell.proc { background: #fff7e6; border-color: #d9a341; }
.fcell.stor { background: #eaf6ee; border-color: #4d9c63; }
.fcell.out  { background: #fbe9f0; border-color: #c45881; }

.flow-arrow {
  text-align: center;
  font-size: 12pt;
  color: #4d9aff;
  font-weight: 700;
  margin: -1mm 0;
}

.banner {
  display: block;
  text-align: center;
  font-size: 9pt;
  font-weight: 600;
  color: #ffffff;
  background: #0d2b4e;
  padding: 2mm;
  border-radius: 1.5mm;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

/* ============================================================
   STACK TABLE compacta
   ============================================================ */
table.stack {
  width: 100%;
  border-collapse: collapse;
  margin: 3mm 0;
  font-size: 9pt;
}
table.stack th {
  background: #0d2b4e;
  color: #ffffff;
  text-align: left;
  padding: 2mm 3mm;
  font-weight: 600;
  font-size: 8.5pt;
}
table.stack td {
  border-bottom: 1px solid #e8eaed;
  padding: 2mm 3mm;
  vertical-align: top;
}
table.stack tr:nth-child(even) td { background: #fafbfc; }
table.stack code {
  font-family: "Consolas", monospace;
  font-size: 8.5pt;
  background: #f1f3f4;
  padding: 0.5pt 2pt;
  border-radius: 1.5pt;
  color: #b9201d;
}

/* ============================================================
   DECISIÓN CARDS
   ============================================================ */
.cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3mm;
  margin: 3mm 0;
}
.card {
  border: 1px solid #dadce0;
  border-left: 4px solid #1a73e8;
  border-radius: 1.5mm;
  padding: 3mm 4mm;
  background: #ffffff;
  font-size: 9pt;
  line-height: 1.4;
}
.card .ct {
  font-weight: 700;
  color: #0d2b4e;
  font-size: 9.5pt;
  margin-bottom: 1mm;
  display: block;
}
.card .cs {
  display: block;
  color: #5f6368;
  font-size: 8pt;
  margin-bottom: 1.5mm;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.card.green { border-left-color: #4d9c63; }
.card.amber { border-left-color: #d9a341; }
.card.red   { border-left-color: #c45881; }

/* ============================================================
   TREE de código
   ============================================================ */
pre.tree {
  background: #f8f9fa;
  border: 1px solid #e1e6ec;
  border-left: 3px solid #1a73e8;
  border-radius: 1.5mm;
  padding: 4mm 5mm;
  font-family: "Consolas", "Menlo", monospace;
  font-size: 8pt;
  line-height: 1.45;
  color: #202124;
  margin: 3mm 0;
  white-space: pre;
  overflow: hidden;
}
pre.tree .cm { color: #5f6368; }
pre.tree .hl { color: #0d6efd; font-weight: 600; }

/* ============================================================
   Roadmap row
   ============================================================ */
.roadmap {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  gap: 3mm;
  margin: 3mm 0;
}
.rstep {
  background: #f8fbff;
  border: 1px solid #c8d4e2;
  border-radius: 1.5mm;
  padding: 3mm;
  font-size: 8.5pt;
  line-height: 1.35;
}
.rstep .rn {
  display: block;
  font-size: 8pt;
  font-weight: 700;
  color: #4d9aff;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  margin-bottom: 1mm;
}
.rstep.done {
  background: #eaf6ee;
  border-color: #4d9c63;
}
.rstep.done .rn { color: #4d9c63; }
.rstep.todo {
  background: #fff7e6;
  border-color: #d9a341;
}
.rstep.todo .rn { color: #a67013; }

/* ============================================================
   Footer compacto de info
   ============================================================ */
.kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3mm;
  margin-top: 5mm;
}
.kv-block {
  border-top: 2px solid #0d2b4e;
  padding-top: 2mm;
  font-size: 9pt;
}
.kv-block .kh {
  font-weight: 700;
  color: #0d2b4e;
  font-size: 9.5pt;
  display: block;
  margin-bottom: 1.5mm;
}
.kv-block ul { margin: 0; padding-left: 4mm; }
.kv-block li { margin: 0.5mm 0; color: #3c4043; }

p { margin: 2mm 0; }
strong { color: #0d2b4e; font-weight: 600; }
"""


COVER = """
<div class="cover">
  <div class="brand">WORKBENCH IT · POC interno</div>
  <h1 class="title">SecondBrain</h1>
  <span class="accent"></span>
  <p class="subtitle">Memoria aumentada privada,<br>100 % local.</p>
  <p class="lead">
    Indexa, procesa y consulta tu actividad personal y profesional
    —conversaciones de WhatsApp, audios, documentos, imágenes—
    usando solo modelos locales. Nada sale del equipo.
  </p>

  <div class="pillars">
    <div class="pillar">
      <div class="ph">Local</div>
      <div class="pb">LLMs, embeddings y transcripción en GPU propia. Costo recurrente $0.</div>
    </div>
    <div class="pillar">
      <div class="ph">Vault</div>
      <div class="pb">Los archivos crudos se guardan, no solo se procesan. Re-procesables a futuro.</div>
    </div>
    <div class="pillar">
      <div class="ph">Modular</div>
      <div class="pb">Cada etapa del pipeline puede crecer o reemplazarse sin tocar el resto.</div>
    </div>
    <div class="pillar">
      <div class="ph">Privado</div>
      <div class="pb">Ninguna API externa. Diseñado para datos personales sin compartir.</div>
    </div>
  </div>

  <div class="meta">
    <strong>Versión:</strong> overview técnico al 2026-05-17<br>
    <strong>Hardware:</strong> i7-10ma · 32 GB RAM · RTX 3070 Ti 8 GB VRAM · Windows 11 + WSL2 + Docker Desktop<br>
    <strong>Doc complementario:</strong> pipeline.pdf — foto técnica detallada con métricas
  </div>
</div>
"""


PAGE_ARCHITECTURE = """
<section class="page">
  <h1 class="section">Arquitectura en una página</h1>
  <p class="section-lead">
    Cuatro capas independientes. Cada item (mensaje, audio, imagen, documento)
    fluye de ingesta a almacenamiento y queda disponible para el chat de Q&amp;A.
  </p>

  <div class="flow">
    <span class="banner">1. Ingesta</span>
    <div class="flow-row r3">
      <div class="fcell in"><span class="ft">Bridge live</span>whatsapp-web.js captura mensajes nuevos en tiempo real</div>
      <div class="fcell in"><span class="ft">Import histórico</span>Parser de exports .txt de WhatsApp</div>
      <div class="fcell in"><span class="ft">Uploads manuales</span>Audios, PDFs, DOCX, imágenes desde el panel</div>
    </div>

    <div class="flow-arrow">▼</div>
    <span class="banner">2. Procesamiento — worker continuo, 5 etapas</span>
    <div class="flow-row r5">
      <div class="fcell proc"><span class="ft">transcribe</span>Whisper Large V3<br><em>CPU</em></div>
      <div class="fcell proc"><span class="ft">extract</span>pdfplumber / docx / xlsx<br><em>CPU</em></div>
      <div class="fcell proc"><span class="ft">caption</span>qwen3-vl:8b<br><em>GPU · ventana 02-06</em></div>
      <div class="fcell proc"><span class="ft">embed</span>bge-m3 · 1024 dim<br><em>GPU</em></div>
      <div class="fcell proc"><span class="ft">tagger</span>qwen3:8b<br><em>GPU · facts + promesas</em></div>
    </div>

    <div class="flow-arrow">▼</div>
    <span class="banner">3. Almacenamiento</span>
    <div class="flow-row r3">
      <div class="fcell stor"><span class="ft">PostgreSQL 16</span>5 schemas (core, media, processing, analytics, audit). Modelo de entidades, jobs, attachments meta.</div>
      <div class="fcell stor"><span class="ft">Qdrant</span>Vectores 1024-dim de mensajes y facts. Búsqueda semántica con filtros.</div>
      <div class="fcell stor"><span class="ft">MinIO</span>Vault de archivos crudos (audios .opus, imágenes, PDFs). Dedup por SHA-256.</div>
    </div>

    <div class="flow-arrow">▼</div>
    <span class="banner">4. Q&amp;A (chat)</span>
    <div class="flow-row r4">
      <div class="fcell out"><span class="ft">Query understanding</span>qwen3:8b extrae personas y expande la query</div>
      <div class="fcell out"><span class="ft">Entity resolution</span>Match a personas canónicas → filtro persona_id</div>
      <div class="fcell out"><span class="ft">Retrieval híbrido</span>Busca en messages + facts, mergea por score</div>
      <div class="fcell out"><span class="ft">Generación con citas</span>qwen3:8b responde citando los fragmentos</div>
    </div>
  </div>
</section>
"""


PAGE_STACK = """
<section class="page">
  <h1 class="section">Stack &amp; decisiones</h1>
  <p class="section-lead">
    Todo orquestado por Docker Compose. Modelos servidos por Ollama.
    Panel de control nativo en escritorio (PySide6) para no depender del browser.
  </p>

  <h2 class="subsection">Servicios</h2>
  <table class="stack">
    <thead>
      <tr><th style="width:18%">Servicio</th><th style="width:34%">Imagen</th><th>Para qué</th><th style="width:13%">Recursos</th></tr>
    </thead>
    <tbody>
      <tr><td><code>postgres</code></td><td>pgvector/pgvector:pg16</td><td>Modelo de dominio, jobs, metadata de archivos</td><td>CPU + RAM</td></tr>
      <tr><td><code>qdrant</code></td><td>qdrant/qdrant</td><td>Vectores de mensajes y facts (1024 dim, cosine)</td><td>CPU + RAM</td></tr>
      <tr><td><code>minio</code></td><td>minio/minio</td><td>Object storage (Vault de binarios)</td><td>CPU + disco</td></tr>
      <tr><td><code>ollama</code></td><td>ollama/ollama</td><td>Servidor LLM local (chat, embeddings, visión)</td><td><strong>GPU</strong></td></tr>
      <tr><td><code>whisper</code></td><td>onerahmet/.../whisper-asr</td><td>Transcripción de audios (large-v3-turbo, CPU)</td><td>CPU</td></tr>
      <tr><td><code>backend</code></td><td>Python 3.12 + FastAPI + uv</td><td>API + worker continuo</td><td>CPU + RAM</td></tr>
      <tr><td><code>frontend</code></td><td>Streamlit</td><td>Páginas de validación, debugging, uploads</td><td>CPU</td></tr>
      <tr><td><code>bridge</code></td><td>Node + whatsapp-web.js</td><td>Captura WhatsApp en vivo, descarga media</td><td>CPU</td></tr>
    </tbody>
  </table>

  <h2 class="subsection">Modelos en uso</h2>
  <table class="stack">
    <thead>
      <tr><th style="width:25%">Modelo</th><th style="width:12%">VRAM</th><th>Rol</th></tr>
    </thead>
    <tbody>
      <tr><td><code>qwen3:8b</code></td><td>5.2 GB</td><td><strong>Chat principal + tagger</strong>. Caliente 30 min con OLLAMA_KEEP_ALIVE</td></tr>
      <tr><td><code>bge-m3</code></td><td>1.2 GB</td><td><strong>Embeddings</strong> (mensajes y facts). Convive en VRAM con el chat</td></tr>
      <tr><td><code>qwen3-vl:8b</code></td><td>6.1 GB</td><td><strong>Visión</strong> (OCR, caption, entidades). Solo se carga 02-06 hs</td></tr>
      <tr><td><em>Whisper large-v3-turbo</em></td><td>CPU</td><td>Transcripción de audios (4-bit). Decisión consciente: libera GPU para el chat</td></tr>
    </tbody>
  </table>

  <h2 class="subsection">Decisiones que definen el diseño</h2>
  <div class="cards">
    <div class="card green">
      <span class="ct">100 % local, sin nube</span>
      <span class="cs">Privacidad</span>
      LLMs, embeddings, transcripción, OCR y vector DB corren en el equipo.
      Sin facturación recurrente. Sin compartir datos con terceros.
    </div>
    <div class="card">
      <span class="ct">Procesamiento tiered en cola</span>
      <span class="cs">Arquitectura</span>
      Un worker continuo dentro del backend drena 5 etapas cada 30 s.
      Auto-encadenado: <code>embed → tagger → re-embed</code>.
    </div>
    <div class="card amber">
      <span class="ct">VRAM-aware: 8 GB son el techo</span>
      <span class="cs">Hardware</span>
      Cada modelo se elige para que <code>chat + embedding</code> convivan
      sin swap. <code>qwen3:8b + bge-m3 = 7.2 GB</code>. Visión va de noche.
    </div>
    <div class="card">
      <span class="ct">Calidad &gt; LLM final</span>
      <span class="cs">Pipeline</span>
      El tagger extrae facts, promesas y transacciones como texto pre-estructurado.
      Eso embebido rankea mucho mejor que mensajes crudos.
    </div>
    <div class="card">
      <span class="ct">Vault crudo, no índice</span>
      <span class="cs">Storage</span>
      Audios, imágenes y PDFs originales se guardan en MinIO con dedup por SHA-256.
      Reprocesable cuando aparezcan modelos mejores.
    </div>
    <div class="card amber">
      <span class="ct">Entity resolution canónico</span>
      <span class="cs">Columna vertebral</span>
      "Esteban", "Esteban K" y "+54 9 223..." resuelven a la misma persona.
      Habilita queries como "última vez que hablé con X".
    </div>
  </div>
</section>
"""


PAGE_CODE = """
<section class="page">
  <h1 class="section">Mapa de código &amp; roadmap</h1>
  <p class="section-lead">
    Repositorio organizado por responsabilidad. Cuatro grandes piezas:
    backend, frontend, bridge, panel de escritorio.
  </p>

  <pre class="tree"><span class="cm"># Repositorio</span>
secondbrain/
├── <span class="hl">backend/</span>                  Python 3.12 · FastAPI · SQLAlchemy 2 · uv
│   ├── app/
│   │   ├── config.py             pydantic-settings (modelo, ventanas, batch)
│   │   ├── main.py               lifespan: arranca el worker continuo
│   │   ├── models/               core, tagging, media, processing
│   │   ├── routers/              bridge, chat, tagger, embeddings,
│   │   │                         transcribe, extract, images, panel,
│   │   │                         worker, imports, contacts, conversations
│   │   └── services/
│   │       ├── <span class="hl">queue_worker.py</span>  ★ drena las 5 etapas, async
│   │       ├── tagger.py         qwen3:8b → facts/promesas/transacciones
│   │       ├── embedder.py       bge-m3 → Qdrant messages + facts
│   │       ├── retriever.py      búsqueda híbrida + filtros (persona, fecha)
│   │       ├── chat.py           query expansion + generación con citas
│   │       ├── ollama_client.py  wrapper con flag force_cpu (reserva)
│   │       ├── qdrant_client.py  collections, upsert, search
│   │       ├── minio_client.py   Vault: raw / derived / exports
│   │       └── whisper_client.py, whatsapp_parser.py, vcard_parser.py
│   └── alembic/versions/         4 migraciones acumuladas
│
├── <span class="hl">bridge/</span>                   Node.js · whatsapp-web.js
│   └── (sesión persistente en data/bridge/, POST al backend)
│
├── <span class="hl">frontend/</span>                 Streamlit · validación y uploads
│   └── pages/1..13_*.py          dashboard, import, contactos, chat, etc.
│
├── <span class="hl">panel/</span>                    ★ PySide6 · escritorio
│   └── secondbrain_panel/
│       ├── main_window.py        7 tabs: sistema, servicios, worker,
│       │                         colas, chats, tagger, configuración
│       ├── docker_client.py      wrapper docker compose
│       └── api_client.py         httpx async al backend
│
├── docker-compose.yml            8 servicios + init jobs
└── docs/                         sprints.md · architecture.md · pipeline.md
</pre>

  <h2 class="subsection">Roadmap — dónde estamos parados</h2>
  <div class="roadmap">
    <div class="rstep done"><span class="rn">Sprint 0-1 ✓</span>Setup, modelos, schema base, parser WhatsApp</div>
    <div class="rstep done"><span class="rn">Sprint 2-3 ✓</span>Bridge live, contactos, tagger end-to-end</div>
    <div class="rstep done"><span class="rn">Sprint 4 ✓</span>Embeddings, retriever, chat funcional</div>
    <div class="rstep done"><span class="rn">Sprint 5-7 ✓</span>Imágenes, documentos, audios</div>
  </div>
  <div class="roadmap">
    <div class="rstep done"><span class="rn">Worker continuo ✓</span>Drena 5 colas async cada 30 s</div>
    <div class="rstep done"><span class="rn">Panel escritorio ✓</span>PySide6 + endpoints /api/panel</div>
    <div class="rstep done"><span class="rn">Migración bge-m3 ✓</span>A/B con datos reales · 2026-05-16</div>
    <div class="rstep todo"><span class="rn">Sprint 8 →</span>Conector Gmail (IMAP + parser MIME)</div>
  </div>

  <div class="kv">
    <div class="kv-block">
      <span class="kh">Lo que ya hace bien</span>
      <ul>
        <li>Ingesta automática de WhatsApp en vivo</li>
        <li>Pipeline de 5 etapas con re-encadenado</li>
        <li>Chat con citas en ~2 s (warm)</li>
        <li>Entity resolution para personas canónicas</li>
        <li>Panel de orquestación nativo</li>
      </ul>
    </div>
    <div class="kv-block">
      <span class="kh">Próximas decisiones a discutir</span>
      <ul>
        <li>Conector Gmail (Sprint 8)</li>
        <li>Memoria estructurada / knowledge graph</li>
        <li>Briefings proactivos (¿qué tengo hoy?)</li>
        <li>UI productiva (reemplazar Streamlit)</li>
        <li>¿Upgrade de GPU a 16 GB?</li>
      </ul>
    </div>
  </div>
</section>
"""


HTML_DOC = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>SecondBrain — Overview técnico</title>
</head>
<body>
  {COVER}
  {PAGE_ARCHITECTURE}
  {PAGE_STACK}
  {PAGE_CODE}
</body>
</html>"""


def main(pdf_path: Path) -> None:
    HTML(string=HTML_DOC).write_pdf(
        target=str(pdf_path),
        stylesheets=[CSS(string=CSS_TEXT)],
    )
    print(f"OK: {pdf_path}  ({pdf_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    pdf = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/overview.pdf")
    main(pdf)
