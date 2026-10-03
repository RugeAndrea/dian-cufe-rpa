# OCR de la tabla "Detalles de Productos": diseño por celdas

Este documento resume la evidencia detrás de `src/ocr/table.py`. La
extracción se hace **siempre** por OCR sobre la imagen; la capa de texto del
PDF (PyMuPDF) se usa *solo* como referencia para medir precisión
(`validate.py`).

## Diagnóstico que definió el enfoque

Se revisaron las facturas 4 y 6 (varios ítems) con `--debug`: **todas las
filas de la tabla "Detalles de Productos" están separadas por líneas
horizontales completas** (rejilla cerrada en cada celda, confirmado también
en las facturas 1 y 3). Esto hace que "una fila de la rejilla = un ítem" sea
una regla estructural fiable, en vez de inferir límites de ítem a partir de
posiciones de palabras (el enfoque anterior, descartado).

## 1. Región de la tabla, en alta resolución

La región (de "Detalles de Productos" al primer marcador de fin: "Notas",
"Datos Totales", "Descuentos" o "Referencias") se ubica igual que antes,
sobre el PNG de página completa a 300 DPI. Pero esa región se **vuelve a
renderizar directamente desde el PDF a 600 DPI** (`page.get_pixmap(dpi=600,
clip=rect)`), convirtiendo las coordenadas de píxel a puntos PDF
(`px * 72/300`). Un recorte nativo a 600 DPI tiene tinta medible más limpia
que una imagen de 300 DPI reescalada al doble.

## 2. Celdas: contornos, no palabras

Se detectan las posiciones de línea (`v_xs`, `h_ys`) igual que antes (perfil
de suma de tinta tras apertura morfológica — una línea real domina la suma
total; un trazo de letra nunca lo hace). Con esas posiciones se dibuja una
máscara de rejilla **delgada y exacta** (no la máscara morfológica cruda,
que también marcaría trazos de M/L/1), se invierte, y se buscan los
contornos **hoja** de la jerarquía (`cv2.RETR_TREE`, contornos sin hijos):
el interior de una celda vacía no tiene nada anidado dentro, así que cada
celda real aparece como su propio contorno hoja. Cada caja se reduce 4 px
por lado para no incluir el borde.

**Hallazgo:** el recorte de 600 DPI incluye un margen en blanco antes del
borde izquierdo real de la tabla (la región de render parte del borde de la
página, no del borde de la tabla) y debajo de su última fila. Ese margen, al
no tener líneas dentro, forma su propio contorno hoja tocando el borde de la
imagen. Se descartan las cajas que tocan el borde del recorte por cualquier
lado: una celda real siempre está delimitada por una línea en los cuatro
lados; el margen nunca lo está.

## 3. Encabezado: coincidencia exacta por celda

Se recorre fila por fila hasta encontrar una con una celda de texto exactamente
"nro" (normalizado: recortado, minúsculas, sin punto final). Cada celda de
esa fila se compara por **igualdad exacta** contra las etiquetas objetivo
("código", "descripción", "cantidad", "precio unitario"). Esto resuelve de
raíz la confusión con "Precio unitario de venta": al ser celdas
independientes, el texto de esa columna nunca contiene la palabra "Precio"
(que queda en la fila fusionada de arriba, junto a "IMPUESTOS") — su celda
en la fila de encabezados real solo dice "unitario de venta", que no
coincide con "precio unitario". Ya no hace falta ninguna heurística de
distancia entre palabras ni lista de exclusión.

## 4. Filas de ítems = filas de la rejilla

Confirmado el diagnóstico (líneas horizontales completas), cada fila de
celdas después del encabezado es directamente un ítem — no se necesita
agrupar por número de ítem, línea huérfana, ni punto medio entre anclas
(heurísticas eliminadas; ver sección de limitaciones conocidas de la versión
anterior en el historial de commits). Las filas totalmente en blanco
(remanente del margen inferior) se descartan con un conteo barato de
densidad de tinta, sin necesidad de OCR.

## 5. OCR por celda

- Código y Descripción: `--oem 1 --psm 6 -c preserve_interword_spaces=1`.
- Cantidad y Precio unitario: `--psm 7` + whitelist `0123456789.,$`.

**Hallazgo (invoice 1, celda Código="0"):** para el *mismo* recorte y el
*mismo* config string, `image_to_data` (usado para obtener confianza por
palabra) devolvió `"0.0"`, mientras `image_to_string` devolvió `"0"`
correctamente. Es una diferencia real entre las dos rutas internas de
Tesseract, no un bug propio. Solución: el **texto** de cada celda viene de
`image_to_string`; `image_to_data` se usa aparte, solo para la confianza (su
texto se descarta).

### Unión de líneas de Descripción

Igual que en el diseño anterior, las líneas dentro de una celda se unen
**sin separador** entre ellas (cada línea interna conserva sus espacios
normales). Evidencia: de 23 descripciones envueltas en 2+ líneas en las 10
facturas, 22 cortan estrictamente a mitad de palabra; la única excepción
tiene un espacio invisible en el flujo crudo del PDF indistinguible
geométricamente de un corte a mitad de palabra (ver commit anterior para la
medición completa). Esta regla no cambió con el rediseño.

## 6. Limitación documentada: Tesseract funde palabras de una sola letra

Verificado de nuevo en el nuevo recorte a 600 DPI: pese al espacio visible
entre "L" y "O" ("...UNILATERA" + "L O PIEZA..."), Tesseract las lee como un
solo token "LO". Se probaron exhaustivamente `psm` 3/4/6/11/12 × `oem` 1/3 ×
`preserve_interword_spaces` 0/1 (20 combinaciones) sobre la celda real a 600
DPI: **ninguna separa "L" de "O"**. No es un problema de resolución ni de
configuración — es una heurística interna de Tesseract para palabras de una
sola letra. Por instrucción explícita, no se reintrodujo la separación por
componentes conectados (que sí lo resolvía) porque el nuevo enfoque por
celdas debía simplificar el código; queda documentado como restricción no
resuelta de forma general, afecta a 1 de 23 líneas envueltas observadas.

## 7. Modelo de Tesseract: tessdata_best vs. el paquete por defecto

Medido sobre las 10 facturas completas:

| métrica | modelo por defecto (fast) | tessdata_best |
|---|---|---|
| código (% exacto) | 73.0% | 86.0% |
| descripción (% exacto) | 81.7% | 85.0% |
| descripción CER | 0.79% | 0.57% |
| cantidad / precio | 100% / 100% | 100% / 100% |
| CUFE CER (solo validación) | 1.15% | **60%** |
| tiempo promedio OCR/factura | 6.07s | 8.38s (+38%) |
| confianza mínima, factura 1 (perfecta) | 80.7 (pasa el umbral 80) | 35.3 (no pasa) |

`tessdata_best` mejora código y descripción, pero con dos costos serios:
hunde la confianza reportada tan por debajo de 80 que **ninguna fila pasa el
umbral fijo**, incluida la factura 1 (perfecta) — con el umbral de 80 fijo
por instrucción explícita, esto inutiliza `requiere_revision` como señal
(todo queda marcado para revisión, incluso lo correcto). Además empeora
drásticamente el reconocimiento del CUFE (1.15% → 60% CER) y es ~38% más
lento. **Se mantiene el modelo por defecto** como el que tiene confianza
calibrada de forma utilizable contra un umbral fijo; `tessdata_best` queda
disponible vía `OCR_TESSDATA_DIR=/usr/share/tessdata-best` para quien
prefiera priorizar código/descripción sobre la señal de confianza.

## 8. Whitelist de Código y requiere_revision objetivo (ajuste final)

**Whitelist de Código** (`ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-./`): al
restringir el alfabeto reconocible, Tesseract ya no puede alucinar acentos
ni letras fuera de ese conjunto. Subió código de 73.0% a **93.0%** sobre las
10 facturas (corrigió "SPONÍ1"→"SPON1", "Cc40112"→"C40112",
"85121502"-casos). Lo que la whitelist **no puede** corregir son
confusiones entre caracteres que SÍ están en el alfabeto permitido ("S"↔"5",
inserciones como "E8S90304" por "E890304") — restricción documentada, sin
regla específica por factura.

**requiere_revision** dejó de ser solo un umbral de confianza fijo. Ahora:
`formato inválido` (código no matchea el whitelist, descripción vacía,
cantidad/precio no numéricos o ≤0) **O** `no cuadra el subtotal`
(Σcantidad×precio vs. "Subtotal" de "Datos Totales"/página 2, OCR'd con el
mismo `psm` del encabezado, tolerancia $1) **O** `confianza_min <
umbral_calibrado`.

**Calibración del umbral de confianza:** se probaron todos los cortes
candidatos (cada confianza distinta observada) contra el acierto real
(comparado con la capa de texto del PDF) en las 10 facturas, maximizando
aciertos menos errores de clasificación. Resultado: **el umbral óptimo en
esta muestra es 0** (equivalente a desactivar el chequeo de confianza) —
los errores reales restantes (confusiones de un carácter como "S"/"5",
palabras fundidas como "L"+"O") no vienen acompañados de confianza baja; en
varios casos Tesseract está igual de "seguro" estando mal que estando bien.
Esto es una limitación real del enfoque de confianza-por-palabra para este
tipo de error, calibrada sobre una muestra de solo 10 facturas, no una
constante universal.

**Nota de corrección:** la primera versión de `_valid_format` exigía código
no vacío, lo que marcaba como "formato inválido" la factura 7 (donde el
código real de la DIAN SÍ está vacío) — un falso positivo por regla
demasiado estricta, no un hallazgo real. Se corrigió: código vacío es
válido; solo un código PRESENTE que no respete el alfabeto whitelist cuenta
como formato inválido.

**Precisión/recall de la alerta** (sobre las 10 facturas, 13 ítems, 5 con
error real contra la referencia), tras la corrección: **0 filas marcadas**,
por lo que la precisión queda indefinida (0/0) y el recall es **0.0**
(0 de 5). Los 5 ítems con error real (2 en la factura 4 por una palabra sin
espacio, 1 en la 1 por la fusión "L"+"O", 1 en la 6 y 1 en la 9 por
confusión de caracteres en el código) no quedan marcados: el subtotal cuadra
igual (el error está en texto, no en los montos), el formato es sintácticamente
válido (son caracteres permitidos, solo equivocados), y el umbral de
confianza calibrado dio 0 (sección anterior). Documentado como limitación
real: con los tres chequeos objetivos implementados, el mecanismo de alerta
tiene recall nulo para errores de 1-2 caracteres en campos de texto — detecta
bien problemas estructurales (formato, cuadre contable) pero no sustituye una
revisión muestral para precisión de caracteres.

## Limitaciones conocidas (no resueltas de forma general)

- Fusión de palabras de una sola letra en Tesseract (sección 6).
- Confusiones carácter-por-carácter en código/descripción cuando el valor es
  alfanumérico corto (S↔5, I↔1, inserción de acentos o letras falsas) — no
  hay whitelist aplicable ahí como sí la hay en cantidad/precio.
- Ningún PDF del lote necesitó continuar la tabla a la página 2; esa ruta
  (`continues_next_page`) se reporta pero no se implementó el merge
  multi-página.
