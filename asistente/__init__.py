"""Asistente de soporte para las aplicaciones de almacén.

Paquete con la lógica del asistente, sin nada de Flask ni de Streamlit:

- texto: preparación del texto y clasificador de intenciones (TF-IDF + SVM).
- imagen: clasificador de capturas de pantalla (EfficientNetB0).
- conocimiento: respuestas de la documentación, preguntas del ticket e interpretación de
  imágenes.
- service_desk: conector con Service Desk, simulado o real.
- agente: el agente híbrido que coordina todo (percibir, decidir y actuar).
"""
