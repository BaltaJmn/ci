# Publicar una app en Google Play y en la App Store

Lo que hace falta para sacar una app de la familia (Chroma, FlowTime, Quilt, MoodTraker) en las dos
tiendas, en orden, y lo que nos encontramos por el camino. Cada app tiene además su `store/` con las
respuestas concretas (fichas, formularios, capturas). Las credenciales, cómo se crean y dónde se
copian están en `~/keys/LEEME.md`, fuera de cualquier repositorio.

## 1. Una versión, dos tiendas

- La versión es una sola: `versionName` y `versionCode` de Android son el `MARKETING_VERSION` y el
  `CURRENT_PROJECT_VERSION` de iOS. Los workflows los leen del fichero de Android (`version-file`),
  así que no se pueden separar.
- `git tag vX.Y && git push origin vX.Y` lanza `release.yml`, que sube a Play (canal `alpha`) y a
  TestFlight con dos llamadas a los workflows de este repositorio. Las cuatro apps igual, también las
  que ya están en producción: producción en Play y el envío a revisión de Apple se hacen a mano.
- Un `versionCode` no se reutiliza nunca: Play y App Store Connect rechazan un build repetido.
- Sin Actions, lo mismo desde el Mac: `~/keys/play.sh aab` y `~/keys/testflight.sh`, que son
  `tienda/play.py` y `tienda/testflight.sh` de este repositorio.
- La ficha va aparte, en `listings.yml`: cada push a `store/` la comprueba (`tienda/comprobar.py`), y a
  mano (`gh workflow run listings.yml -f target=play|app-store|both`) la sube. Qué tiene que haber en
  `store/`: README, *El contrato de cada app*.

## 2. Cuentas, credenciales y secretos

| Qué | Dónde |
|---|---|
| Clave de subida de Play de cada app | `~/keys/<app>-upload.jks`, contraseña en `keystore.properties` |
| Cuenta de servicio que publica en Play | `~/keys/play-service-account.json` |
| Clave de la API de App Store Connect (rol Admin) | `~/keys/appstore-api.p8` |
| Certificados Apple Development y Apple Distribution, con su clave | `~/keys/apple-development.p12`, `~/keys/apple-distribution.p12` |
| Clave de compras integradas, para RevenueCat | `~/keys/appstore-iap.p8` |

En cada repositorio: cinco secretos de Play (`KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`,
`KEY_PASSWORD`, `PLAY_SERVICE_ACCOUNT_JSON`) y ocho de Apple (`APPSTORE_KEY_ID`,
`APPSTORE_ISSUER_ID`, `APPSTORE_PRIVATE_KEY`, `APPLE_TEAM_ID` y los dos `.p12` con su contraseña).
Los de cuenta los copia `~/keys/credenciales.sh sincronizar` a todos los repositorios a la vez:
una clave rotada a mano en un solo sitio rompe los demás en silencio.

## 3. Google Play

1. Clave de subida y `keystore.properties` (nunca en el repositorio).
2. La app en Play Console, en el navegador: formularios de contenido y categoría. Ficha, gráficos,
   capturas y contacto en todos los idiomas suben por la API: `listings.yml -f target=play`, o
   `~/keys/play.sh ficha <paquete> <repo> <web>` (con `--dry`, Play la valida y se descarta).
3. Primera subida a prueba interna como borrador (`status: draft`), y ese borrador se publica una
   vez a mano desde la consola.
4. El producto `pro_lifetime` en Play y, en RevenueCat, la app de Play con la cuenta de servicio de
   **solo lectura** (nunca la que publica), el derecho `pro` y la oferta `default`. Precio y textos
   de la compra, los de la App Store, con `tienda/compra.py` (sección 4, paso 7).
5. Prueba cerrada: **12 testers con la prueba aceptada durante 14 días seguidos** (cuentas
   personales creadas después de noviembre de 2023). Después, *Solicitar acceso a producción*, que
   pregunta cómo se reclutó a los testers y qué cambió; Google tarda hasta 7 días.
6. Producción: todos los países, la última build de la biblioteca, y salida por fases si se quiere.

## 4. App Store

1. La app en App Store Connect, en la web (la API no crea apps): bundle id, SKU, **idioma principal
   inglés**. Los idiomas sin capturas propias heredan las del principal.
2. *Negocio*: acuerdos de apps gratuitas y de pago, cuenta bancaria, formularios fiscales y el
   **estado de comerciante de la UE** (DSA). Sin el acuerdo de pago, la compra no funciona ni en
   sandbox; sin el estado de comerciante, la app no sale en los 27 países de la UE.
3. *Privacidad de la app*, solo en la web: lo que declara el `PrivacyInfo.xcprivacy` más lo de
   RevenueCat (historial de compras, no vinculado, sin rastreo).
4. Ficha: categorías, edad (cuestionario), copyright, contacto y notas para la revisión, precio
   (gratis) y disponibilidad (todos los países menos China continental, que pide registro ICP).
5. Textos en todos los idiomas desde `store/app-store/<idioma>/` y capturas de iPhone de 6,9"
   (1320 x 2868) desde `store/screenshots/iphone/<idioma>/`: `listings.yml -f target=app-store`, o
   `~/keys/appstore.py ficha` y `capturas`. Las capturas solo rellenan los idiomas que no tienen y
   pierden el canal alfa que traen las del simulador (Apple lo rechaza). Las carpetas llevan región
   (`it-IT`): el script la quita donde Apple no la usa. China fuera: `appstore.py sin-china`.
6. Solo iPhone en la v1 (`TARGETED_DEVICE_FAMILY = 1`). Con iPad, Apple exige también capturas de
   iPad de 13".
7. Compra integrada: el ID lleva el bundle delante, porque no se repite entre apps de la misma
   cuenta (`com.baltajmn.<app>.pro_lifetime`). Nombre y descripción en todos los idiomas, captura
   para la revisión (`appstore.py captura-compra`; sin ella la compra se queda en
   `MISSING_METADATA`). Precio, **1,99 EUR en todas** (neto ~1,40 tras IVA y el 15 %): `compra.py
   <paquete> <producto Play> <producto Apple> 1.99` pone España de base en Apple, copia la igualación
   de Apple a Play donde la moneda coincide (127 de 174 regiones) y los textos de la compra de Apple
   a la de Play. En RevenueCat, la app de App Store con su `appl_` en la
   app, el producto en el derecho y en la oferta, y la clave de compras integradas.
8. App Group del widget, una vez por app y **en el portal** (developer.apple.com, *Identifiers >
   App Groups*): crear `group.com.baltajmn.<app>` y marcarlo en la capacidad *App Groups* del App ID
   de la app y del widget. La API registra App IDs y activa la capacidad, pero no crea grupos ni los
   asigna, y Xcode con la clave de la API tampoco. Sin él, el archive falla con "Provisioning
   profile ... doesn't match the entitlements file's value for the
   com.apple.security.application-groups entitlement". Lo hace el titular: el control de permisos
   de Claude bloquea *Register*.
9. Build: la etiqueta, o `testflight.sh`. Cuando App Store Connect la procesa,
   `appstore.py build <bundle> <número>` la pone en la versión. `ITSAppUsesNonExemptEncryption = NO` en el `Info.plist`
   evita la pregunta del cifrado. Grupo interno de TestFlight `Equipo` para instalarla en el iPhone.
10. Envío, en la web: *Añadir a revisión* en la versión, después en la primera compra integrada, y
   *Enviar a revisión* con los dos dentro. Publicación manual o automática al aprobarla. Estos clics,
   y *Cancelar envío*, los hace el titular: el control de permisos de Claude los bloquea aunque se le
   dé permiso en el chat. Claude deja todo comprobado y el navegador en la página.

`~/keys/appstore.py estado <bundle>` dice qué falta antes de enviar.

## 5. Lo que nos encontramos

**Firma de iOS (05-10-2026).** La primera subida a TestFlight falló con ITMS-90035, "Code failed to
satisfy specified code requirement(s)", en la app y en el widget. Sin certificado de distribución
en el llavero, Xcode firma en la nube y le pasa a `codesign` el requisito designado como argumento;
la "é" de "Jiménez", el nombre del equipo, llega descompuesta (NFD) y deja de coincidir con la del
certificado (NFC). `codesign --verify --strict` lo confirma en local. Arreglo: certificado Apple
Distribution propio en un llavero temporal. No es de un proyecto: afecta a toda app de la cuenta.

**Un certificado de desarrollo por ejecución.** Con la clave de la API, `-allowProvisioningUpdates`
crea un certificado "Created via API" en cada máquina nueva de Actions hasta topar con el límite de
Apple. Arreglo: certificado Apple Development propio, importado igual.

**Actions parado.** En un repositorio privado cada minuto de macOS cuenta como diez; el 05-10-2026
GitHub dejó de arrancar todos los trabajos de los privados con "recent account payments have
failed". Un trabajo que falla sin pasos ni registro es eso. Chroma pasó a público por esto; Quilt ya
lo era.
FlowTime pasó a público el mismo día. MoodTraker sigue privado: su build va con `testflight.sh` y su
ficha de Play con `~/keys/play.sh ficha` en local, hasta que se arregle la facturación
o pase a público. Ojo: los workflows de fichas también se paran, y en silencio: en Play, FlowTime se
quedó en 6 idiomas de 14 y MoodTraker en 5 de 13 (subidas todas el 05-10-2026).

**App Groups.** Solo existía el de Chroma, y FlowTime, Quilt y MoodTraker no firmaban para la App
Store por eso. Es el paso 8 de la sección 4.

**Una coma en el nombre de un test.** Kotlin/Native no acepta comas en los nombres entre comillas
invertidas: en la JVM pasa y en iOS no compila (`compileTestKotlinIosSimulatorArm64`). Así estuvo
MoodTraker en rojo sin que se notara en local.

**Sin máquina macOS.** Con el repositorio ya público, el trabajo esperó 15 minutos y GitHub lo canceló
con "The job was not acquired by Runner of type hosted even after multiple attempts". Se subió con
`testflight.sh`. Esa noche le pasó también a trabajos de Ubuntu: basta con relanzarlos.

**La ficha de App Store Connect se desvía.** En Chroma el idioma principal era español, China estaba
disponible, la versión en preparación era la anterior y la ficha y la compra solo tenían cinco de
trece idiomas, sin captura para la revisión. `appstore.py estado` lo enseña todo junto.

**La API de App Store Connect.** Al crear un idioma en la información de la app, Apple crea también
el de la versión, vacío, y un `POST` de ese idioma da 409: hay que leer y hacer `PATCH`.
`territoryAvailabilities` solo admite `PATCH`, no `GET` de uno.

**La primera compra no consumible** solo entra en el mismo envío que una versión. La API no la añade
(`FIRST_NON_CONSUMABLE_MUST_BE_SUBMITTED_ON_VERSION`), y si se envía la versión sola, lo normal es
un rechazo por la 2.1 al no encontrar la compra. Así salió el primer envío de Chroma: se canceló y
se reenvió con las dos.

**Cuenta nueva, 2.1 *Information Needed* (06-10-2026).** Al primer envío de Chroma, Apple contestó
que la cuenta tiene poco historial y pidió un vídeo grabado en un iPhone de verdad, con la última
versión de iOS, desde que se abre la app y pasando por la compra, y siete respuestas: propósito y
público, cómo se usa, servicios externos, diferencias por región, sector regulado y qué se compra y
dónde. Hay que contestar en el Centro de resoluciones y ponerlo también en las notas. Para no
repetir la vuelta, cada app lleva desde el principio esas siete respuestas en las notas
(`store/formularios.md`, plantilla en el de Chroma) y su vídeo como adjunto de la información para
la revisión (`appstore.py adjunto <bundle> <vídeo>`). Tras contestar, *Volver a enviar a revisión de
apps* sigue desactivado hasta pulsar *Actualizar revisión* en la página de la versión rechazada.

**Acciones en Node 20.** `actions/checkout`, `setup-java` y `setup-gradle` v4 avisan de que GitHub
las fuerza a Node 24: los workflows de aquí van en v5.

**Una dependencia que solo existe en Android tumba la pantalla en iOS.** FlowTime 2.2.2 (59) se
cerraba al abrir Ajustes en el iPhone: su ViewModel pedía `FocusMode`, declarado solo en el módulo
de Koin de Android. Compila igual y revienta al abrir la pantalla (SIGABRT en
`InstanceFactory.create`). Antes de subir a TestFlight, abrir cada pantalla en el simulador. El
informe de un fallo de TestFlight se lee por la API: `betaFeedbackCrashSubmissions/{id}/crashLog`.

**El precio en TestFlight sale de EE. UU.** Si el iPhone aún no ha iniciado sesión en la tienda de
pruebas, StoreKit da el precio de EE. UU. y la hoja de compra el de España (Quilt: $3.99 en el
botón y 4,99 € en la hoja). La app no falla; si se ve en el vídeo, se explica en las notas.

**Precio y notas con la app ya en revisión (06-10-2026).** Con la versión y la compra en *Waiting for
Review*, Apple deja cambiar el precio de la compra (`compra.py`) y las notas para la revisión
(`appStoreReviewDetail`, por la API). Al bajar Pro a 1,99, las notas de las tres en revisión dicen
el precio nuevo y que el vídeo se grabó con el anterior.

**La captura de la compra tiene que tener tamaño de iPhone.** Una de otra medida sube, pero se queda
en `FAILED` con `IMAGE_INCORRECT_DIMENSIONS` y la compra vuelve a `MISSING_METADATA`: 1320 x 2868.
Borrar esa captura fallida dio 500 la primera vez y funcionó al reintentar; sin borrarla, Apple no
deja crear otra (409). `appstore.py captura-compra ... --reemplazar` cambia la de una compra que aún
no se ha enviado.

## 6. Estado de cada app (06-10-2026)

| App | Google Play | App Store |
|---|---|---|
| Chroma | Prueba cerrada, build 15; producción como pronto el 13-10 | 1.0.12 (15) y Chroma Pro reenviadas el 06-10 con vídeo y respuestas; publicación manual |
| FlowTime | Producción, 2.2.2 (59); ficha en los 14 idiomas | 2.2.2 con la build 60 (Ajustes arreglado en iOS), compras con captura a 1,99 y privacidad publicada; falta el vídeo y enviarla |
| Quilt | Producción, 1.8 (9) | 1.8 (9) y Quilt Pro enviadas el 06-10 con vídeo y respuestas; publicación manual |
| MoodTraker | Prueba cerrada, build 2; ficha en los 13 idiomas | 1.0 (2) y MoodTraker Pro en *Waiting for Review* desde el 07-10; capturas y textos nuevos esperan a la versión siguiente |

La subida a TestFlight por el workflow compartido quedó probada el 06-10-2026 con Quilt y FlowTime
(firma con los dos `.p12`, archive, export y subida).

"Lista" quiere decir: textos en todos los idiomas, capturas de 6,9", compras en `READY_TO_SUBMIT`
con su captura para la revisión, privacidad publicada, China fuera, idioma principal inglés y
RevenueCat comprobado (clave `appl_` del código, producto en el derecho y en la oferta actual).
