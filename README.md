# Proyecto final — Asistente de soporte para las aplicaciones de almacén

Versión web del asistente del Avance 2. Un agente híbrido coordina el clasificador de
intenciones (TF-IDF + SVM), el clasificador de capturas de pantalla (EfficientNetB0) y
Service Desk. Los servicios de inferencia salen de una API REST hecha con Flask, y la
pantalla, hecha con Streamlit, le habla a esa API. El piloto sigue cubriendo Cliente recoge e
Inventario físico.

## Qué hay aquí

```
asistente/            la lógica, sin nada de Flask ni de Streamlit
  texto.py            los cuatro pasos de preparación del texto y el clasificador de intenciones
  imagen.py           el clasificador de capturas y la validación de la imagen
  ocr.py              lee el texto de la alerta en la captura (Tesseract, en el mismo servidor)
  conocimiento.py     respuestas de la documentación, preguntas del ticket e interpretación de imágenes
  service_desk.py     conector con Service Desk: simulado (folios SIM-) o real (HTTP)
  agente.py           el agente híbrido: percibir, decidir (capa reactiva y deliberativa) y actuar
api/app.py            la API REST de Flask, con manejo de errores y monitoreo
interfaz/app.py       la pantalla de Streamlit (con su propio requirements.txt)
interfaz/ejemplos/    tres capturas de ejemplo, con los datos personales tapados
despliegue/           scripts y servicios para montar la API en la EC2 con el túnel
entrenar_texto.py     vuelve a entrenar el clasificador de intenciones
calibrar_umbral.py    escoge el umbral del "no sé" con validación cruzada
evaluar_ocr.py        mide el OCR con las capturas reales (solo números agregados)
pruebas/              pruebas automáticas (pytest)
datos/                ejemplos generados con apoyo de IA y revisados
modelos/              modelos entrenados (no se suben) y la lista de clases de imagen
```

## Cómo decide el agente

Cada mensaje pasa por tres pasos:

1. **Percibir.** El clasificador de texto saca la intención y el de imágenes la clase de la
   captura. Si el usuario escribe desde una app, solo cuentan las intenciones de esa app.
2. **Decidir**, en dos capas:
   - **Reactiva:** reglas inmediatas. Saluda, da la solución de la documentación, pregunta si
     se resolvió y permite cancelar.
   - **Deliberativa:** cuando hay que levantar un ticket, arma un plan con los datos que
     faltan, lo cumple pregunta por pregunta y lo rehace si llega algo nuevo, como una
     captura a media conversación.
3. **Actuar.** Contesta, pregunta o levanta el ticket con la herramienta de Service Desk. Si
   Service Desk no responde, avisa y ofrece reintentar.

### Cuándo dice "no sé"

- **Mensaje del usuario:** si la mejor intención no llega a **−0.5**, se trata como "otro" y
  el agente pide decirlo con otras palabras. El valor lo escoge `calibrar_umbral.py` con
  validación cruzada en la parte de entrenamiento, y se guarda en
  `modelos/umbral_texto.json`.
  - En la parte de prueba sube la accuracy de 0.886 a 0.895 sin rechazar ninguna frase que
    estuviera bien.
  - "La pantalla se puso en blanco" ya no sale como despedida.
- **Solo la captura:** el OCR lee la alerta (solo en las capturas de la app) y el agente
  clasifica ese texto, pero le pide una puntuación de **0.2**. El texto de una alerta se
  parece poco a como escriben los usuarios.
  - Con las 37 capturas reales que tienen ticket etiquetado, ese umbral quitó los 6 errores:
    acierta 10 y en las otras 27 pide la descripción. Lo mide `evaluar_ocr.py`.
- **Con OCR, el plan pide la captura antes que el mensaje de error:** si de la captura se
  lee el mensaje, ya no se pregunta.

La API no guarda nada entre mensajes: el estado de la conversación entra y sale en cada
petición. Cada respuesta trae una `traza` con lo que percibió, qué capa decidió y qué hizo.

## Rutas de la API

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/salud` | Si está arriba, qué modelos cargó y el modo de Service Desk |
| POST | `/intencion` | Inferencia de texto: `{"texto": "...", "aplicacion": "inventario"}` |
| POST | `/imagen` | Inferencia de imagen: archivo `imagen` (PNG, JPG o WEBP, hasta 8 MB) |
| POST | `/conversacion` | El agente: `mensaje`, `imagen` opcional, `sesion` y `estado` |
| GET | `/metricas` | Peticiones, errores y tiempos por ruta, y eventos del agente |
| POST | `/service-desk/tickets` | Service Desk simulado: recibe un ticket como el web service |
| GET | `/service-desk/tickets` | Service Desk simulado: tickets levantados |
| POST | `/service-desk/disponibilidad` | Prueba de falla (solo con `PERMITIR_SIMULAR_FALLAS=1`) |

Los errores siempre regresan JSON con `error` y `mensaje`, con el código HTTP que toca: 400
si la entrada no sirve, 404, 405, 413 si el archivo es muy grande, 415 si no es imagen, 422
si al ticket le faltan campos, 503 si Service Desk no responde y 500 sin detalles internos.

## La pantalla

La pantalla no clasifica nada: manda cada mensaje, y la captura si hay, a `/conversacion` y
muestra lo que contesta el agente. Está pensada primero para la pantalla del escáner Zebra.

- **Dentro de la app de Flutter (WebView):** la dirección trae los datos de la sesión y el
  asistente no los pregunta, por ejemplo
  `?embed=true&aplicacion=inventario&sucursal=...&usuario=...`. Con `embed=true` se oculta
  la barra de Streamlit.
- **Página pública de demostración:** sin esos datos. El asistente los pregunta cuando le
  hacen falta y cada respuesta trae el desplegable "Cómo lo decidió el asistente". Hay tres
  capturas de ejemplo para probar sin tener una del escáner.
- Si la API no contesta, tarda demasiado o regresa un error, la pantalla lo dice con palabras
  claras y ofrece reintentar.

La dirección de la API se toma del secreto `API_URL` de Streamlit o de la variable de entorno
`API_URL`.

## Datos

Los datos crudos (las descripciones de los tickets y las capturas que se adjuntan a ellos)
son confidenciales. Van en `datos-privados/`, que está en el `.gitignore` y no se sube.

Los modelos entrenados tampoco se suben: el de texto se genera con `entrenar_texto.py` y el
de imágenes con el notebook del Avance 2.

La bitácora de la API (`bitacora/api.log`) guarda método, ruta, código y tiempo de cada
petición, pero no lo que escribe el usuario.

## Correrlo

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
brew install tesseract                # en Ubuntu: sudo apt install tesseract-ocr
mkdir -p tessdata && curl -sSL -o tessdata/spa.traineddata \
  https://github.com/tesseract-ocr/tessdata_best/raw/main/spa.traineddata
venv/bin/python entrenar_texto.py     # genera modelos/clasificador_intenciones.joblib
venv/bin/python calibrar_umbral.py    # genera modelos/umbral_texto.json
venv/bin/python -m pytest             # pruebas
venv/bin/python -m api.app            # API en http://127.0.0.1:5000
venv/bin/pip install -r interfaz/requirements.txt
API_URL=http://127.0.0.1:5000 venv/bin/streamlit run interfaz/app.py   # pantalla en :8501
```

En el servidor se usa gunicorn:

```bash
venv/bin/gunicorn -w 1 --threads 4 -b 0.0.0.0:5000 "api.app:create_app()"
```

Variables de entorno: `SERVICE_DESK_MODO` (`simulado` o `real`), `SERVICE_DESK_URL`,
`RUTA_MODELOS`, `RUTA_BITACORA`, `UMBRAL_TEXTO` (reemplaza al calibrado),
`TAMANO_MAXIMO_MB`, `PERMITIR_SIMULAR_FALLAS`, `CLAVE_ADMIN`, `USAR_OCR` (`0` lo apaga) y
`RUTA_TESSDATA`. Si Tesseract no está instalado, el OCR se apaga solo y `/salud` lo indica. Con `CLAVE_ADMIN` definida, la lista de tickets
simulados y la prueba de falla piden el encabezado `X-Clave-Admin`, para que nadie lea desde
fuera lo que escribieron otros usuarios. En el servidor público siempre se define.

## Despliegue

- **Pantalla:** Streamlit Community Cloud, desde este repo (`interfaz/app.py`, con su
  `interfaz/requirements.txt`). Es la URL pública.
- **API:** una EC2 t3.small con Ubuntu 24.04 en AWS. La API solo escucha en
  `127.0.0.1:5000` y sale a internet por un túnel rápido de Cloudflare
  (`https://<algo>.trycloudflare.com`). La máquina no abre ningún puerto, salvo SSH para una
  sola IP.
- La dirección del túnel va en el secreto `API_URL` de Streamlit. Cambia cada vez que el
  túnel arranca, por ejemplo al prender la máquina, y entonces hay que actualizar el secreto.

| Archivo de `despliegue/` | Dónde se corre | Qué hace |
|---|---|---|
| `subir.sh` | Mac | Copia el código y los modelos a la EC2, sin los datos privados, y reinicia la API |
| `preparar_servidor.sh` | EC2, una vez | Instala Tesseract, cloudflared y las librerías, y deja los dos servicios |
| `asistente-api.service` | EC2 | El servicio de la API, con gunicorn |
| `tunel-cloudflare.service` | EC2 | El servicio del túnel |
| `direccion_tunel.sh` | EC2 | Muestra la dirección actual del túnel |

En el servidor, las variables van en `/etc/asistente-api.env`: Service Desk simulado, la
prueba de falla y la clave de administración, que se genera ahí y no está en el repo.

## Repositorio público

https://github.com/Jozl10/asistente-soporte-almacen, el mismo del Avance 1. La fuente
sigue siendo esta carpeta: lo que cambie aquí se copia encima del repo y se sube.

`chatbot_soporte.ipynb`, en la raíz del repo, es el chatbot basado en reglas del Avance 1. Se
queda ahí para que sigan sirviendo los enlaces del reporte de ese avance. Se abre en Google
Colab (*Archivo → Abrir cuaderno → GitHub*) y se corre con *Ejecutar todas*. Colab ya trae
NLTK.
