# OCR de la tabla "Detalles de Productos": diseño y evidencia

Este documento resume los hallazgos empíricos detrás de `src/ocr/table.py`.
La extracción se hace **siempre** por OCR sobre el PNG renderizado; la capa
de texto del PDF (PyMuPDF) se usa *solo* como referencia para medir
precisión (`validate.py`) y, en esta investigación, para entender cómo el
generador de la DIAN arma el HTML/PDF.

## 1. Detección de la región de la tabla

La región va desde la palabra "Detalles" (de "Detalles de Productos") hasta
el primer marcador de fin encontrado: "Notas" (Notas Finales), "Datos
Totales" (título de página 2), **"Descuentos"** (Descuentos y Recargos
Globales) o **"Referencias"**. Los dos últimos se agregaron tras encontrar,
en las facturas 3, 5, 8 y 10, una segunda tabla (descuentos globales o
referencias de pago) entre "Detalles de Productos" y "Notas Finales" — sin
ese marcador, el recorte se extendía sobre esa segunda tabla y su encabezado
("Nro. | Tipo | Código | Descripción | % | Valor") contaminaba el OCR del
cuerpo de la tabla de productos.

## 2. Encabezado de columnas: contaminación por Y, no por línea de Tesseract

`find_header_row` identifica la fila de encabezados por **proximidad vertical
real** al texto "Nro.", no por `line_num`/`block_num` de Tesseract. Motivo:
en la factura 5, Tesseract agrupó los *labels* del encabezado (y≈2096-2103)
y los *valores* de la primera fila de datos (y≈2233-2258, ~130px más abajo)
en el mismo `line_num`/`block_num`. Eso intercaló un valor de datos
("663.203,00") entre los tokens "Precio" y "unitario" en el orden
izquierda-derecha, rompiendo el emparejamiento adyacente, y además empujó
`header_bottom` (calculado como el `bottom` máximo de "header_words") muy
adentro de la fila, cortando casi todo el cuerpo antes de que el OCR lo viera.

Además, ruido de un solo carácter (bordes de celda mal binarizados, leídos
como ":", "7", etc.) aparece a veces exactamente a la misma altura Y que el
encabezado real, cayendo justo entre "Precio" y "unitario". Se filtran los
tokens de pura puntuación, y el emparejamiento "Precio"+"unitario" busca
hacia adelante dentro de una ventana de distancia (no exige adyacencia de
índice exacta), tolerando ese ruido.

## 3. Máscara de líneas: borrar solo líneas confirmadas, no cualquier trazo vertical

La detección morfológica de líneas (apertura con kernels horizontal/vertical)
sirve bien para **ubicar** las posiciones x/y de las líneas reales (los
trazos de texto nunca acumulan tanta suma de píxeles como una línea que
cruza todo el recorte). Pero usar esa misma máscara morfológica cruda para
**borrar** píxeles antes del OCR del cuerpo destruye letras con trazos
verticales largos (M, L, 1): una máscara 1×15 detecta cualquier trazo
continuo ≥15px, y un trazo de letra a 300 DPI fácilmente supera eso. La
solución: una vez confirmadas las posiciones `v_xs`/`h_ys` (por suma total,
no por píxel individual), se dibuja una máscara **quirúrgica** de líneas
delgadas exactamente en esas posiciones, y solo esa se resta de la imagen.

## 4. Agrupación de ítems: líneas huérfanas ancladas en "Nro.", no punto medio

Cada fila del cuerpo se identifica por un token numérico puro en la columna
"Nro." (su "línea ancla"). Un enfoque anterior asignaba cada palabra al
ancla numéricamente más cercana (punto medio entre anclas consecutivas).
Eso falla con alturas de fila desiguales: en la factura 4, un ítem corto de
3 líneas seguido de uno largo de 6 líneas hace que el punto medio caiga
**debajo** de la primera línea de descripción del ítem largo (que, como en
la factura 1, se renderiza *arriba* de su propia ancla por centrado
vertical de celda), fusionándola con el ítem anterior.

Regla verificada en los datos: como mucho **una** línea huérfana queda arriba
de su propia ancla; cualquier otra huérfana entre dos anclas es continuación
del ítem **anterior**. Excepción verificada en la factura 8: una descripción
de 4 líneas centra su ancla de forma que **dos** líneas quedan arriba — por
eso, cuando no existe ítem previo (es el primer ítem del documento), TODAS
las huérfanas pendientes se asignan a él (no solo la última), ya que no hay
a dónde más asignarlas.

## 5. Unión de líneas de Descripción: sin espacio, por evidencia, no por suposición

Se midió la capa de texto real (PyMuPDF) de las 10 facturas: de 23
descripciones envueltas en 2+ líneas, 22 cortan estrictamente a mitad de
palabra (p. ej. "UNILATERA" + "L" → "UNILATERAL"). La única excepción
("...RAPIDA VIH" + "1 Y 2...") tiene, en el flujo de caracteres crudo del
PDF (`page.get_text("rawdict")`), un espacio final invisible (ancho ~2pt,
sin tinta) — pero la distancia geométrica al margen derecho de esa línea
(4.46pt) es **estadísticamente idéntica** a la de cortes confirmados a mitad
de palabra en la misma factura (4.44pt para "CPN HEMOGRAMA I (HEM" +
"OGLOBINA"). Es decir: esa información solo existe en el flujo interno del
PDF, nunca en la imagen renderizada — ningún heurístico geométrico sobre el
OCR puede recuperarla. Por eso la regla implementada es **unir siempre sin
espacio** entre líneas (correcta en ~96% de los casos observados, 22/23).

## 6. Fusión Tesseract de palabras cortas adyacentes ("L"+"O" → "LO")

Verificado en la factura 1: aunque exista un espacio visible entre "L" y
"O", Tesseract las lee como un solo token "LO" en psm 6/11/4, a cualquier
escala (2x-4x) y con `preserve_interword_spaces=1`. La causa no es el ancho
del espacio sino la heurística interna de Tesseract para palabras de una
sola letra. Solución: medir los espacios reales entre componentes conectados
(OpenCV), calibrando el umbral con los espacios que Tesseract **sí** usó
correctamente para separar otras palabras en la misma línea; si un token
tiene un hueco interno comparable, se corta ahí y cada mitad se re-OCRiza
por separado (con margen y upscale) en vez de adivinar cómo partir el string.

## 7. Fusión Nro.+Código ("1"+"88143" → "188143")

Verificado en la factura 6: el OCR del cuerpo a veces lee el número de ítem
y el código como un solo token cuando están muy cerca, y el centro de ese
token cae dentro de la columna "Código" — por lo que la columna "Nro." nunca
ve un dígito y el ancla del ítem se pierde. Se detecta cualquier palabra cuyo
cuadro cruce el límite Nro./Código y empiece con un dígito, y se parte en
dos: el primer carácter (número de ítem, en las facturas de este lote
siempre 1 dígito) y el resto (código). No es un corte proporcional al conteo
de caracteres: el dígito "1" es más angosto que el resto, así que el punto
de corte real está más cerca del 17% del ancho que de 1/6.

## Limitaciones conocidas (no resueltas, ver `ocr_validation.json`)

- OCR de un solo carácter en aislamiento puede confundir letras con dígitos
  ("O" → "0", "S" → "5", "I" → "1") en campos de texto libre (código,
  descripción) — no hay whitelist aplicable ahí como sí la hay en
  cantidad/precio.
- Ruido geométrico ocasional (fragmentos de texto irreconocible al final de
  una descripción) en facturas con una sola línea de producto corta; queda
  correctamente señalado por `confianza_min` bajo y `requiere_revision=True`.
- La detección de ancla "Nro." puede fallar si el dígito se funde con
  texto vecino de forma distinta a los dos patrones descritos arriba
  (verificado como causa de conteo de ítems incompleto en la factura 6).
