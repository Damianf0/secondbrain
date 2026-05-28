"""
Convierte docs/pipeline.md → docs/pipeline.pdf

Pipeline:
  Markdown → HTML (lib `markdown` con extensiones)
  HTML → PDF (lib `weasyprint`, layout vía CSS)

Pensado para correr dentro de un container Python ad-hoc:
  pip install markdown weasyprint pygments
"""

from __future__ import annotations

import sys
from pathlib import Path

import markdown
from weasyprint import HTML, CSS


CSS_TEXT = r"""
/* ============================================================
   Página
   ============================================================ */
@page {
  size: A4;
  margin: 22mm 18mm 20mm 18mm;

  @bottom-right {
    content: counter(page) " / " counter(pages);
    font-family: "Segoe UI", -apple-system, system-ui, sans-serif;
    font-size: 8.5pt;
    color: #9aa0a6;
  }
  @bottom-left {
    content: "SecondBrain · Pipeline técnico";
    font-family: "Segoe UI", -apple-system, system-ui, sans-serif;
    font-size: 8.5pt;
    color: #9aa0a6;
  }
}

@page :first {
  margin: 0;
  @bottom-right { content: ""; }
  @bottom-left { content: ""; }
}

/* ============================================================
   Tipografía base
   ============================================================ */
html { font-size: 10pt; }
body {
  font-family: "Segoe UI", -apple-system, "Helvetica Neue", system-ui,
               "Noto Color Emoji", "Segoe UI Emoji", "Apple Color Emoji", sans-serif;
  color: #202124;
  line-height: 1.5;
  orphans: 3;
  widows: 3;
}

p { margin: 5pt 0; text-align: left; }
strong { color: #0d2b4e; font-weight: 600; }
em { color: #3c4043; }

/* ============================================================
   Encabezados
   ============================================================ */
h1, h2, h3, h4, h5 {
  font-family: "Segoe UI", -apple-system, system-ui, sans-serif;
  color: #0d2b4e;
  page-break-after: avoid;
  break-after: avoid;
}

/* H1 general (estilos visuales). El page-break se aplica solo al H1
   del markdown — el de la portada usa su propia clase y no hereda. */
h1 {
  font-size: 22pt;
  font-weight: 700;
  margin: 0 0 4pt 0;
  padding-bottom: 8pt;
  border-bottom: 2px solid #0d2b4e;
}
/* H1 hijo directo de body = el del markdown (no el de la portada que va dentro de .cover) */
body > h1 {
  page-break-before: always;
  break-before: page;
}

/* Cada sección H2 abre página propia. Hace que el doc se lea como capítulos. */
h2 {
  font-size: 15pt;
  font-weight: 600;
  margin: 0 0 8pt 0;
  padding-bottom: 4pt;
  border-bottom: 1px solid #c8d4e2;
  page-break-before: always;
  break-before: page;
}

h3 {
  font-size: 11.5pt;
  font-weight: 600;
  margin: 14pt 0 4pt 0;
  color: #1a4480;
}

h4 {
  font-size: 10.5pt;
  font-weight: 600;
  margin: 10pt 0 3pt 0;
  color: #324a64;
}

/* ============================================================
   Bloques
   ============================================================ */
blockquote {
  margin: 8pt 0;
  padding: 7pt 10pt;
  background: #f4f8fc;
  border-left: 3px solid #1a73e8;
  color: #3c4043;
  font-size: 9.5pt;
  page-break-inside: avoid;
}
blockquote p { margin: 2pt 0; }

hr {
  border: none;
  border-top: 1px solid #dadce0;
  margin: 12pt 0;
}

/* ============================================================
   Código
   ============================================================ */
code {
  font-family: "Consolas", "SF Mono", "Menlo", "Courier New", monospace;
  font-size: 8.5pt;
  background: #f1f3f4;
  padding: 1pt 3pt;
  border-radius: 2pt;
  color: #b9201d;
  white-space: nowrap;
}

pre {
  background: #f8f9fa;
  border: 1px solid #e1e6ec;
  border-left: 3px solid #1a73e8;
  border-radius: 2pt;
  padding: 7pt 9pt;
  font-size: 8pt;
  line-height: 1.35;
  page-break-inside: avoid;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow: hidden;
  margin: 8pt 0;
}
pre code {
  background: transparent;
  color: #202124;
  padding: 0;
  font-size: 8pt;
  white-space: pre-wrap;
}

/* ============================================================
   Tablas
   ============================================================ */
table {
  border-collapse: collapse;
  width: 100%;
  margin: 8pt 0;
  font-size: 9pt;
  page-break-inside: avoid;
  table-layout: auto;
}
th {
  background: #0d2b4e;
  color: #ffffff;
  text-align: left;
  padding: 4pt 6pt;
  font-weight: 600;
  font-size: 8.5pt;
  border: none;
}
td {
  border-bottom: 1px solid #e8eaed;
  padding: 3pt 6pt;
  vertical-align: top;
  word-wrap: break-word;
}
tr:nth-child(even) td { background: #fafbfc; }
table code { font-size: 8pt; }

/* ============================================================
   Listas
   ============================================================ */
ul, ol { margin: 4pt 0 6pt 20pt; padding: 0; }
li { margin: 1pt 0; }
li > p { margin: 1pt 0; }
ul ul, ol ol, ul ol, ol ul { margin-top: 2pt; margin-bottom: 2pt; }

/* ============================================================
   Links
   ============================================================ */
a { color: #1a73e8; text-decoration: none; word-break: break-word; }

/* ============================================================
   Portada — única página sin márgenes globales para hacer hero
   ============================================================ */
.cover {
  page-break-after: always;
  break-after: page;
  height: 297mm;
  width: 210mm;
  background: linear-gradient(135deg, #0d2b4e 0%, #1a4480 100%);
  color: #ffffff;
  position: relative;
  box-sizing: border-box;
  padding: 70mm 24mm 24mm 24mm;
}
.cover .brand {
  font-size: 9pt;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: #a8c3e6;
  margin-bottom: 4mm;
}
.cover .title {
  font-size: 42pt;
  font-weight: 700;
  margin: 0;
  padding: 0;
  border: none;
  line-height: 1.05;
  color: #ffffff;
}
.cover .subtitle {
  font-size: 16pt;
  font-weight: 300;
  color: #c8d8ec;
  margin-top: 6mm;
  line-height: 1.3;
  max-width: 140mm;
}
.cover .meta {
  position: absolute;
  bottom: 22mm;
  left: 24mm;
  right: 24mm;
  font-size: 9.5pt;
  color: #a8c3e6;
  line-height: 1.7;
  border-top: 1px solid #2c5180;
  padding-top: 5mm;
}
.cover .meta strong { color: #ffffff; }
.cover .accent-bar {
  position: absolute;
  left: 24mm;
  top: 50mm;
  width: 28mm;
  height: 4px;
  background: #4d9aff;
}

/* ============================================================
   Misc
   ============================================================ */
img { max-width: 100%; }

/* Evita huérfanas: que un encabezado quede solo al final de página. */
h2 + p, h2 + ul, h2 + ol, h2 + table, h2 + pre, h2 + blockquote { page-break-before: avoid; }
h3 + p, h3 + ul, h3 + ol, h3 + table { page-break-before: avoid; }
"""


COVER_HTML = """
<div class="cover">
  <div class="brand">SecondBrain · POC interno</div>
  <h1 class="title">SecondBrain</h1>
  <div class="accent-bar"></div>
  <div class="subtitle">
    Pipeline técnico — Documento de referencia<br>
    para evaluación con el equipo
  </div>
  <div class="meta">
    <strong>Versión:</strong> snapshot al 2026-05-17 (post-migración a bge-m3)<br>
    <strong>Datos:</strong> medidos sobre la instalación real, no estimados<br>
    <strong>Hardware:</strong> i7-10th gen · 32 GB RAM · RTX 3070 Ti 8 GB VRAM · Windows 11 + WSL2 + Docker Desktop
  </div>
</div>
"""


def main(md_path: Path, pdf_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")

    html_body = markdown.markdown(
        md_text,
        extensions=[
            "tables",
            "fenced_code",
            "codehilite",
            "sane_lists",
            "smarty",
            "toc",
        ],
        extension_configs={
            "codehilite": {"css_class": "highlight", "noclasses": True},
            "toc": {"toc_depth": "2-3"},
        },
        output_format="html5",
    )

    full_html = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>SecondBrain — Pipeline técnico</title>
</head>
<body>
  {COVER_HTML}
  {html_body}
</body>
</html>"""

    HTML(string=full_html, base_url=str(md_path.parent)).write_pdf(
        target=str(pdf_path),
        stylesheets=[CSS(string=CSS_TEXT)],
    )
    print(f"OK: {pdf_path}  ({pdf_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    md = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/pipeline.md")
    pdf = Path(sys.argv[2] if len(sys.argv) > 2 else "/work/pipeline.pdf")
    main(md, pdf)
