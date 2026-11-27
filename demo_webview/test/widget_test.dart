import 'package:flutter_test/flutter_test.dart';

import 'package:demo_webview/main.dart';

void main() {
  testWidgets('La pantalla de inventario muestra el botón de ayuda', (tester) async {
    await tester.pumpWidget(const DemoApp());

    expect(find.text('Inventario físico (demo)'), findsOneWidget);
    expect(find.text('Ayuda'), findsOneWidget);
  });

  test('La dirección del asistente lleva los datos de la sesión', () {
    final url = Uri.parse(urlAsistente).replace(queryParameters: datosDeSesion);

    expect(url.queryParameters['embed'], 'true');
    expect(url.queryParameters['aplicacion'], 'inventario');
    expect(url.toString(), contains('sucursal=Sucursal+de+prueba'));
  });
}
