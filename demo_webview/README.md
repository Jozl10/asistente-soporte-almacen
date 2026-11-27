# Demo: el asistente dentro de una app de Flutter

App de ejemplo, que no es la de la empresa, para mostrar cómo se integraría el asistente en
las apps de almacén. Tiene una pantalla de inventario con un botón **Ayuda** que abre la
pantalla de Streamlit en un WebView.

- **La dirección lleva los datos de la sesión:** `embed=true`, `aplicacion`, `sucursal` y
  `usuario`. Con ellos el asistente no pregunta quién es ni desde dónde escribe, y
  `embed=true` oculta la barra de Streamlit. En la app real saldrían del inicio de sesión.
- **Abre `/~/+/`**, la app sin el marco de Streamlit Community Cloud. Con la dirección de
  siempre, el marco se queda cargando dentro del WebView y además ocupa espacio abajo.
- **Adjuntar la captura:** en Android, el WebView no abre el selector de archivos por su
  cuenta. `setOnShowFileSelector` deja escoger una captura de la galería o tomar una foto
  con `image_picker`. Las fotos se achican a 1,600 px de ancho antes de mandarlas.

## Requisito: un WebView actualizado

La pantalla de Streamlit usa JavaScript que necesita **Chrome 112 o más nuevo** en el
WebView del dispositivo. El emulador de Android 13 trae Chrome 109 de fábrica y la página se
queda en blanco (`SyntaxError: Invalid flags supplied to RegExp constructor 'gv'`). Antes de
llevarlo a los escáneres hay que revisar qué versión de WebView traen.

## Correrlo

Se probó en un emulador del tamaño de un Zebra: 5 pulgadas, 720 × 1280 y densidad 320, que
en Flutter son 360 × 640, la misma medida de la prueba F13.

```bash
flutter pub get
flutter test
flutter run            # con el emulador abierto
```
