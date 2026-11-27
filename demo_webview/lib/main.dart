// Demo: una app de almacén de ejemplo que abre el asistente de soporte en un WebView.
//
// No es la app de la empresa. Solo muestra cómo se integraría el asistente: la app ya sabe
// qué aplicación es, la sucursal y el usuario, y los manda en la dirección para que el
// asistente no los pregunte.
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';

// `/~/+/` es la app sin el marco de Streamlit Community Cloud. Con la dirección de siempre,
// el marco se queda cargando dentro del WebView de Android y además ocupa espacio abajo.
const urlAsistente = 'https://asistente-soporte-almacen.streamlit.app/~/+/';

/// Datos de la sesión. En la app real saldrían del inicio de sesión.
/// `embed=true` oculta la barra de Streamlit.
const datosDeSesion = {
  'embed': 'true',
  'aplicacion': 'inventario',
  'sucursal': 'Sucursal de prueba',
  'usuario': 'almacenista.prueba',
};

void main() => runApp(const DemoApp());

class DemoApp extends StatelessWidget {
  const DemoApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Inventario (demo)',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorSchemeSeed: Colors.blueGrey, useMaterial3: true),
      home: const PantallaInventario(),
    );
  }
}

/// Pantalla de ejemplo de una app de inventario, con el botón de ayuda.
class PantallaInventario extends StatelessWidget {
  const PantallaInventario({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Inventario físico (demo)')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('${datosDeSesion['sucursal']} · ${datosDeSesion['usuario']}',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 12),
          const Card(
            child: ListTile(
              leading: Icon(Icons.inventory_2_outlined),
              title: Text('Conteo de la ubicación A-01'),
              subtitle: Text('Escanea la ubicación y después cada artículo'),
            ),
          ),
          const SizedBox(height: 12),
          const TextField(
            decoration: InputDecoration(
              labelText: 'Código escaneado',
              prefixIcon: Icon(Icons.qr_code_scanner),
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 24),
          Text('¿Algo no funciona? Toca Ayuda para hablar con el asistente.',
              style: Theme.of(context).textTheme.bodyMedium),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        icon: const Icon(Icons.support_agent),
        label: const Text('Ayuda'),
        onPressed: () => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => const PantallaAsistente()),
        ),
      ),
    );
  }
}

/// El asistente de Streamlit dentro de un WebView.
class PantallaAsistente extends StatefulWidget {
  const PantallaAsistente({super.key});

  @override
  State<PantallaAsistente> createState() => _PantallaAsistenteState();
}

class _PantallaAsistenteState extends State<PantallaAsistente> {
  late final WebViewController controlador;
  var cargando = true;

  @override
  void initState() {
    super.initState();
    controlador = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setNavigationDelegate(NavigationDelegate(
        onPageStarted: (_) => setState(() => cargando = true),
        onPageFinished: (_) => setState(() => cargando = false),
      ))
      ..loadRequest(Uri.parse(urlAsistente).replace(queryParameters: datosDeSesion));

    // En Android el WebView no abre el selector de archivos por su cuenta: sin esto, el
    // botón para adjuntar la captura no hace nada.
    final plataforma = controlador.platform;
    if (plataforma is AndroidWebViewController) {
      plataforma.setOnShowFileSelector(elegirImagen);
    }
  }

  /// Deja escoger una captura de la galería o tomar una foto, y se la pasa a la página.
  Future<List<String>> elegirImagen(FileSelectorParams parametros) async {
    final origen = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (context) => SafeArea(
        child: Wrap(children: [
          ListTile(
            leading: const Icon(Icons.photo_library_outlined),
            title: const Text('Elegir una captura'),
            onTap: () => Navigator.pop(context, ImageSource.gallery),
          ),
          ListTile(
            leading: const Icon(Icons.photo_camera_outlined),
            title: const Text('Tomar una foto'),
            onTap: () => Navigator.pop(context, ImageSource.camera),
          ),
        ]),
      ),
    );
    if (origen == null) return [];
    // La API acepta hasta 8 MB; las fotos de la cámara se achican antes de mandarlas.
    final imagen = await ImagePicker().pickImage(
      source: origen,
      maxWidth: 1600,
      imageQuality: 85,
    );
    if (imagen == null) return [];
    return [Uri.file(imagen.path).toString()];
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Ayuda'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Volver a cargar',
            onPressed: controlador.reload,
          ),
        ],
      ),
      body: Stack(children: [
        WebViewWidget(controller: controlador),
        if (cargando) const LinearProgressIndicator(),
      ]),
    );
  }
}
