# DIAN CUFE RPA + OCR

RPA que descarga la representación gráfica (PDF) de facturas electrónicas de
la DIAN a partir de su CUFE, resolviendo Cloudflare Turnstile dos veces por
factura. Un pipeline de OCR separado extrae encabezado y tabla de productos
de esos PDF (siempre por imagen, nunca leyendo el texto del PDF) y los deja
en CSV, con validación cruzada y métricas de tiempo.

## Resultados

**RPA — 10/10 CUFE descargados**

| | |
|---|---|
| Promedio | 9.80 s |
| Mediana | 9.90 s |
| Mínimo | 8.79 s |
| Máximo | 10.83 s |

**OCR — precisión por campo (10 facturas)**

| campo | precisión |
|---|---|
| número de factura / fecha emisión / NIT emisor | 100% |
| código de producto | 93.0% |
| descripción (CER) | 0.79% (error de caracteres, no exacto) |
| cantidad | 100% |
| precio unitario | 100% |
| CUFE (CER, solo validación) | 1.15% |

Resultados completos, PDFs de muestra, imágenes OCR'd y CSVs reales de esta
corrida están en [`samples/`](samples/) (sin necesidad de volver a consultar
la DIAN).

## Inicio rápido

Único requisito: **Docker Desktop**.

```bash
git clone https://github.com/RugeAndrea/dian-cufe-rpa.git
cd dian-cufe-rpa
docker compose build

# Numeral 1 + 2 completos (descarga de la DIAN real + OCR)
docker compose run --rm rpa python -m src.main all

# Solo numeral 2 (OCR), sobre los PDF de muestra ya incluidos, sin tocar la DIAN
docker compose run --rm rpa python -m src.main ocr --input-dir samples/pdfs

# Tests
docker compose run --rm rpa pytest -q
```

Otros comandos útiles:

```bash
# Solo el CUFE 1, forzando descarga y grabando video de la corrida
docker compose run --rm rpa python -m src.main rpa --only 1 --force --record-video

# Solo OCR, con recortes/máscaras de depuración en output/debug
docker compose run --rm rpa python -m src.main ocr --debug
```

## Arquitectura

```
data/input.csv (n, cufe, nit)
        │
        ▼
┌───────────────────────── RPA (src/rpa) ─────────────────────────┐
│ Turnstile (búsqueda) → llenar CUFE/NIT → Buscar →                │
│ /Document/ShowDocumentToPublic → Turnstile (descarga, ANTES de   │
│ abrir el modal) → clic "Descargar PDF" → expect_download →       │
│ PDF cifrado (contraseña = NIT) → output/pdfs/{n}_{cufe}.pdf      │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────── OCR (src/ocr) ──────────────────────────┐
│ PyMuPDF + NIT → PNG 300 DPI (página completa, para ubicar         │
│ regiones) → encabezado: OCR + regex → tabla: recorte re-          │
│ renderizado a 600 DPI → celdas por contornos de rejilla →         │
│ columnas por texto exacto de celda → OCR por celda                │
│ (whitelist código / cantidad / precio) → normalización →          │
│ cruce contable con Subtotal → DataFrame → CSV                     │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
output/csv/{n}_{numero_factura}.csv + consolidado.csv
output/metrics/{rpa,ocr}_metrics.json, ocr_validation.json
```

### Estructura de carpetas

```
src/
  rpa/        captcha, cliente DIAN, descarga de PDF, config
  ocr/        exporter, header, table (celdas), normalize, validate, pipeline
  common/     logging, métricas (cronómetro de etapas)
  main.py     CLI: rpa | ocr | all
data/input.csv          10 CUFE + NIT
samples/                PDFs, imágenes, CSVs y métricas de una corrida real (versionado)
docs/
  spike_turnstile.md    investigación y evidencia del captcha
  ocr_tabla.md           diseño y evidencia del OCR de tabla por celdas
tests/                  normalizadores, regex de encabezado, integración factura 1
```

## Decisiones técnicas (y por qué)

- **patchright + Chrome real, headful + Xvfb dentro de Docker.** El spike
  (`docs/spike_turnstile.md`) probó 5/5 éxitos en headful+Xvfb contra 0/1 en
  headless puro (Cloudflare ni siquiera entrega el formulario real en
  headless). No es una preferencia, es el único modo que funciona aquí.
- **Captura del PDF con `page.expect_download()`.** El flujo real es
  `fetch()` → blob → `<a download>` sintético; `expect_download` lo
  intercepta de forma nativa. Hay una segunda captura por red
  (content-type `application/pdf`) como respaldo, documentada en
  `docs/spike_turnstile.md`.
- **Segundo Turnstile en la página de detalle, resuelto ANTES de abrir el
  modal de descarga.** El modal bootbox intercepta los clics sobre el
  widget aunque se vea renderizado encima; resolverlo apenas carga la
  página evita el problema por completo.
- **El PDF se guarda cifrado, tal como lo entrega la DIAN.** Se abre solo
  para verificar (PyMuPDF soporta AES nativamente, a diferencia de pypdf
  en este entorno); la contraseña es el NIT usado en la búsqueda.
- **OCR sobre imágenes, nunca sobre el texto del PDF.** La capa de texto
  (PyMuPDF `find_tables()`) se usa exclusivamente para *medir* precisión
  en `validate.py`, nunca para extraer el dato que se entrega.
- **Tabla de productos por celdas, no por posición de palabras.** Se
  confirmó con `--debug` que las 10 facturas tienen líneas horizontales
  completas entre ítems; la tabla se vuelve a renderizar desde el PDF a
  600 DPI y las celdas se detectan como contornos hoja de la máscara de
  rejilla. Ver `docs/ocr_tabla.md` para la comparación contra el enfoque
  anterior (por palabra) y sus fallas.
- **Whitelist de caracteres para Código** (alfanumérico en mayúsculas):
  elimina por construcción acentos y letras alucinadas por Tesseract.
- **Validación cruzada con el Subtotal.** Σ(cantidad×precio) se compara
  contra el "Subtotal" OCR'd de "Datos Totales" (página 2), tolerancia $1,
  como chequeo objetivo adicional a `requiere_revision`.

## Métricas

| etapa | promedio | mediana | mín | máx |
|---|---|---|---|---|
| RPA (por factura) | 9.80 s | 9.90 s | 8.79 s | 10.83 s |
| OCR (por factura) | 6.79 s | 6.23 s | 5.63 s | 10.42 s |

Rendimiento OCR: 8.84 facturas/min. **Proyección combinada RPA+OCR a 1.000
facturas: 16.59 s/factura → ~4.61 horas.**

## Restricciones y limitaciones conocidas

- **Cloudflare Turnstile** se resuelve de forma fiable solo en headful+Xvfb
  (ver spike, 5/5 vs 0/1).
- **Segundo Turnstile** en la página de detalle, independiente del de
  búsqueda; debe resolverse antes de abrir el modal de descarga.
- **El NIT de búsqueda es obligatorio**: es la contraseña del PDF.
- **Procesamiento secuencial**, un CUFE/factura a la vez (sin paralelismo).
- **Errores de 1 carácter de Tesseract no son detectables de forma
  objetiva**: confusiones tipo "S"↔"5" o la fusión de palabras de una sola
  letra ("L"+"O") no vienen acompañadas de confianza baja ni rompen el
  cuadre del subtotal — el umbral de confianza calibrado en esta muestra
  dio 0 (ver `docs/ocr_tabla.md`), y la alerta `requiere_revision` tiene
  recall 0 para este tipo de error específico.
- **Umbral de confianza calibrado sobre solo 10 facturas**: es un punto de
  partida razonable, no una constante universal.
- **No se implementó continuación de tabla a la página 2** (ningún caso de
  las 10 facturas lo necesitó).

## Configuración

Copiar `.env.example` a `.env` para sobreescribir valores por defecto
(timeouts, DPI, umbrales, rutas). Ver el archivo para la lista completa.

Para agregar más CUFE: añadir filas a `data/input.csv` con columnas
`n,cufe,nit`.

## Documentación adicional

- [`docs/spike_turnstile.md`](docs/spike_turnstile.md) — investigación y
  evidencia de la resolución de Turnstile.
- [`docs/ocr_tabla.md`](docs/ocr_tabla.md) — diseño del OCR de tabla por
  celdas, evidencia de cada decisión y limitaciones documentadas.
