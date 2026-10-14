# Avance de proyecto 1 — Asistente de soporte para las aplicaciones de almacén.

Es un chatbot basado en reglas, hecho en Python 3.10 con NLTK. El piloto cubre las aplicaciones de Cliente recoge e
Inventario físico.


## Qué hay aquí

- `chatbot_soporte.ipynb`: todo el chatbot, en el orden de la arquitectura. Sesión, base de
  conocimiento, preparación del texto con NLTK, identificación de la intención, flujo de la
  conversación y ticket, las pruebas y el chat interactivo.
- `requirements.txt`: solo NLTK, para correrlo fuera de Google Colab.

## Correrlo en Google Colab

1. En Colab, *Archivo → Abrir cuaderno → GitHub* y pegar la dirección del repositorio (o
   *Subir* el `.ipynb`).
2. *Entorno de ejecución → Ejecutar todas*. Colab ya trae NLTK; la primera celda descarga el
   paquete `punkt_tab` que NLTK usa para separar el texto en palabras.
3. La sección 6 corre las diez conversaciones de prueba y los resultados. 
4. La última celda abre el chat interactivo. Se escribe `salir` para terminar.

## Correrlo en local

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt jupyter
jupyter notebook chatbot_soporte.ipynb
```

