# Spike: resolución de Cloudflare Turnstile en Docker

**Objetivo:** validar si el captcha Cloudflare Turnstile de
`https://catalogo-vpfe.dian.gov.co/User/SearchDocument` se puede resolver
dentro de un contenedor Docker (sin GUI real), antes de construir el RPA.

**Sitekey:** `0x4AAAAAAAg1WuNb-OnOa76z`
**Señal de éxito:** el input oculto `[name="cf-turnstile-response"]` del
formulario `#search-document-form` recibe un token (~700-750 caracteres).

## Estrategia probada

`patchright` (fork de Playwright con parches anti-detección) +
`channel="chrome"` (Google Chrome real, no el Chromium de Playwright) +
Docker (`python:3.11-slim` + `google-chrome-stable` del repo oficial) +
`xvfb-run` para dar un display al modo headful.

Lógica de resolución: poll del input oculto cada ~0.25-0.3s; en paralelo se
recorren los `iframe` de `challenges.cloudflare.com` y, en cuanto aparece un
elemento con rol `checkbox`, se hace clic de inmediato (sin esperas fijas).

## Resultado: 5 corridas headful+xvfb + 1 headless (comparación)

| intento | modo | resuelto | segundos | clic_en_checkbox | error |
|---|---|---|---|---|---|
| 1 | headful+xvfb | sí | 5.75 | True | — |
| 2 | headful+xvfb | sí | 6.10 | True | — |
| 3 | headful+xvfb | sí | 5.65 | True | — |
| 4 | headful+xvfb | sí | 5.68 | True | — |
| 5 | headful+xvfb | sí | 5.76 | True | — |
| headless | headless | no | 15.96 | False | `TimeoutError` esperando `#search-document-form` (nunca cargó el formulario real) |

**Tasa de éxito headful+xvfb: 5/5 — Promedio: ~5.8 s**
**Tasa de éxito headless: 0/1**

## Conclusión / decisión

- **Headful + Xvfb es la única estrategia viable** dentro de Docker para este
  sitio. En modo headless, Cloudflare ni siquiera entrega el formulario real
  (bloquea antes de renderizarlo), así que no hay nada que resolver.
- En las 5 corridas headful fue necesario el clic de respaldo sobre el
  checkbox del iframe (no se resolvió nunca "solo" antes de los 5 s, a
  diferencia de un Chrome de escritorio sin contenedor). El clic de respaldo
  es, por lo tanto, **parte normal del flujo**, no un caso excepcional.
- El RPA se construyó sobre: `patchright` + `channel="chrome"` + headful +
  Xvfb dentro de Docker, con `timeout` duro a nivel de proceso y
  `--server-args="-screen 0 1366x768x24"` para evitar corridas colgadas (ver
  `entrypoint.sh`).

## Hallazgo adicional durante la construcción del RPA: segundo Turnstile en la descarga

La página de detalle del documento (`/Document/ShowDocumentToPublic`) tiene
un **segundo widget Turnstile, completamente independiente del de la
búsqueda**, con su propio `<div class="cf-turnstile fixed-right" data-sitekey="...">`
e input oculto `cf-turnstile-response` propio. Este widget existe desde que
carga la página de detalle (no se crea al hacer clic en "Descargar PDF").

**Síntoma inicial:** al hacer clic en `a.downloadLink` ("Descargar PDF") no
pasaba nada observable: ni `download` event, ni petición de red, ni pestaña
nueva, ni cambios en el DOM.

**Diagnóstico paso a paso:**
1. El HTML de la página reveló que `a.downloadLink` tiene
   `href="javascript:void(0)"` y un `addEventListener("click", ...)` que hace
   `e.preventDefault()`, busca el `<form id="postForm" action="/Document/DownloadPDF">`
   más cercano, y siempre muestra un modal bootbox porque la variable
   `pageHasPdfPassword` está **hardcodeada en `true`** en el script de la
   página (`"Este archivo contiene contraseña y corresponde al NIT del Emisor
   o Receptor..."`).
2. Solo al aceptar ese modal (`continueAfterPasswordNotice()`) el código
   revisa `getTurnstileValue()` (que busca CUALQUIER
   `input[name="cf-turnstile-response"]` con valor no vacío), rellena el
   campo oculto `captcha` del formulario, y llama a
   `submitPublicDownloadForm(form)`, que hace el `fetch()` real.
3. Al intentar resolver el widget de descarga de la forma habitual (recorrer
   `page.frames` y hacer clic en el `role=checkbox` del iframe de
   `challenges.cloudflare.com`) el checkbox tardaba ~9 s en aparecer en el
   árbol de accesibilidad, y el clic de Playwright fallaba sistemáticamente
   con:
   ```
   <div tabindex="-1" role="dialog" class="bootbox modal fade modal-download-info in">…</div>
   intercepts pointer events
   ```
   Es decir: aunque el widget se ve **visualmente encima** del modal (por
   z-index), el contenedor del modal bootbox sigue ocupando esa región en el
   árbol de hit-testing del navegador y absorbe el clic real. Incluso forzando
   el clic (`force=True`) el valor de `cf-turnstile-response` seguía vacío,
   porque el clic forzado de Playwright no "pasa a través" del overlay — cae
   sobre el modal igual que un clic real del usuario.

**Solución:** resolver el Turnstile de descarga **antes** de abrir el modal,
es decir, inmediatamente después de aterrizar en la página de detalle (igual
que el de búsqueda, con el mismo `TurnstileBrowserSolver`). Así, cuando luego
se hace clic en "Descargar PDF" y se acepta el modal, `getTurnstileValue()`
ya encuentra un token válido y el `fetch()` se dispara de inmediato. Validado
con éxito: `fetch` a `/Document/DownloadPDF` → `blob` → `<a download>` →
capturado por `page.expect_download()` (estrategia *a*), PDF de 68,230 bytes
empezando con `%PDF-1.6`.

Implementado en `src/rpa/dian_client.py` (resuelve el segundo captcha justo
después de `wait_for_url` al detalle, antes de llamar a `download_pdf`) y
`src/rpa/downloader.py` (las 3 estrategias de captura, en el orden
especificado).
