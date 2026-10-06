# ci

Todo lo que publica las apps de la familia (Chroma, FlowTime, Quilt, MoodTraker) en Google Play y en
la App Store: los workflows reutilizables de GitHub Actions y los scripts de tienda que usan, los
mismos que se lanzan desde el Mac. Una sola copia de cada cosa: un arreglo se hace aquí una vez.

El repositorio es público porque un workflow reutilizable de un repositorio público se puede llamar
desde cualquier otro, y porque no contiene ningún secreto: los secretos viven en cada repo que llama y
se pasan explícitamente, y en el Mac en el Llavero y en `~/keys` (manual en `~/keys/LEEME.md`).
`PUBLICAR.md` es el proceso entero de sacar una app en las dos tiendas.

## El contrato de cada app

Cada app tiene los mismos dos workflows, generados de la misma plantilla: solo cambian los valores
bajo `with:` (paquete, CN de la firma, tareas de Gradle, ruta del AAB, fichero de versión y web).

| Fichero | Se dispara | Hace |
|---|---|---|
| `.github/workflows/release.yml` | Etiqueta `v*`, o a mano | Play (canal `alpha`) y TestFlight con la misma versión |
| `.github/workflows/listings.yml` | Push a `store/`, o a mano | Comprueba `store/`; a mano, sube la ficha a Play, a la App Store o a las dos |

Una etiqueta sube siempre a los canales de prueba de las dos tiendas. Producción en Play y el envío a
revisión de Apple son siempre de una persona, en cada consola. A mano se puede elegir otro canal de
Play, `status: draft` (app que nunca publicó) o solo una tienda (`stores`).

Y la misma carpeta `store/`, que `tienda/comprobar.py` vigila en cada push:

```
store/listings/<idioma>/{title,short,full}.txt                         ficha de Play
store/app-store/<idioma>/{name,subtitle,keywords,promo,description}.txt  ficha de la App Store
store/whatsnew/whatsnew-<idioma>                                       notas de versión de Play
store/play/icon-512.png
store/play/feature/<idioma>.png                                        en-US obligatorio
store/screenshots/play/<idioma>/*.png                                  en-US y es-ES como mínimo
store/screenshots/iphone/<idioma>/*.png                                1320 x 2868, en-US y es-ES
```

- **Los mismos idiomas en las dos fichas y en las notas**, con la región siempre (`it-IT`, no `it`):
  `appstore.py` la quita donde Apple no la usa. Son los idiomas de la interfaz: trece en todas, y
  FlowTime además hindi.
- **Idioma por defecto inglés** en las dos tiendas. Los idiomas sin capturas propias heredan las
  inglesas.
- **Contacto**: `baltajmn@gmail.com` y la web de la app (`https://<app>.baltajmn.dev/`), que es también
  el soporte y la privacidad de la App Store. Sin teléfono en la ficha.
- **Título**: `Nombre: palabra que se busca`, elegido por tienda según su ASO (`store/aso.md` de cada
  app). El resto de la ficha dice lo mismo en las dos, salvo lo que solo existe en una plataforma.
- **Pro, compra única de 1,99 EUR** en las cuatro, igual en las dos tiendas (`compra.py`).

## Workflows

### `android-play-release.yml`

Construye el `.aab` de release, comprueba que va firmado con la clave de subida y lo publica en
Google Play. Entradas: `package-name` (obligatoria), `signer-cn`, `java-version`, `gradle-tasks`,
`aab-path`, `track`, `whatsnew-dir`, `status`.

Los secretos van uno a uno y no con `secrets: inherit`: los repositorios que llaman guardan también
secretos de otras cosas (App Store Connect, Cloudflare, Supabase) que no pintan nada en una
publicación de Play. Espera `KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD` y
`PLAY_SERVICE_ACCOUNT_JSON`. La cuenta de servicio de publicación es distinta de la de RevenueCat, que
es de solo lectura a propósito.

Si el build de la app genera configuración a partir del entorno, se le pasan las variables por el
secreto opcional `BUILD_ENV`, una `KEY=VALUE` por línea. Va como secreto y no como entrada porque las
entradas aparecen en claro en el log, y el workflow las enmascara antes de exportarlas.

`signer-cn` por defecto es `CN=Baltasar`; cada app pasa el suyo, porque las claves de subida no
comparten CN y la comprobación rechazaría un paquete bien firmado. Se mira con `keytool -printcert
-jarfile <aab>`, línea `Owner`.

`status` es `completed` salvo en una app que todavía no tiene ninguna versión publicada en ningún
canal: ahí Play solo acepta `draft`, y la versión se lanza después desde la consola.

Sirve para las dos formas de firmar que uso: escribe `keystore.properties` en la raíz y además
exporta `KEYSTORE_PATH`, `KEYSTORE_PASSWORD`, `KEYSTORE_ALIAS` y `KEYSTORE_ALIAS_PASSWORD`, así que el
Gradle de la app puede leer cualquiera de las dos sin cambios.

### `ios-testflight-release.yml`

Archiva la app de iOS, la firma con los certificados propios del equipo y la sube a App Store Connect,
que la pasa a TestFlight. La versión y el build son el `versionName` y el `versionCode` de Android,
así que una etiqueta sube lo mismo a las dos tiendas. Entradas: `project` (`iosApp/iosApp.xcodeproj`),
`scheme` (`iosApp`), `version-file` (`androidApp/build.gradle.kts`; FlowTime pasa
`build-logic/plugins/src/main/java/Config.kt`) y `java-version`. Secretos: `APPSTORE_KEY_ID`,
`APPSTORE_ISSUER_ID`, `APPSTORE_PRIVATE_KEY`, `APPLE_TEAM_ID` y los dos `.p12` con su contraseña.

Los dos `.p12` son a propósito, y cada uno evita un fallo distinto:

- **Desarrollo.** Con la clave de la API, `-allowProvisioningUpdates` crea certificado y perfiles. En
  una máquina recién creada no hay identidad de desarrollo, así que crearía un certificado nuevo en
  cada ejecución hasta topar con el límite de Apple.
- **Distribución.** Sin certificado de distribución en el llavero, Xcode firma en la nube, y en este
  equipo eso no sube: le pasa a `codesign` el requisito designado como argumento, con el nombre del
  certificado dentro, y la "e" con tilde de "Jimenez" se descompone por el camino (NFD). La firma deja
  de cumplir su propio requisito y App Store Connect la rechaza con ITMS-90035.

Los perfiles sí los sigue creando Xcode con la clave de la API: ningún `.mobileprovision` vive en un
secreto. Los certificados, su contraseña y cómo se renuevan están en `~/keys/LEEME.md`.

### `store-listings.yml`

Comprueba `store/` con `comprobar.py` y, si `target` es `play`, `app-store` o `both`, sube la ficha:
textos, gráficos, capturas y contacto a Play en una sola edición; textos y capturas de iPhone a la
versión en preparación de la App Store (si la versión está en revisión, falla diciéndolo y no cambia
nada). Entradas: `package-name`, `website`, `target`. Secretos, todos opcionales:
`PLAY_SERVICE_ACCOUNT_JSON` y los tres `APPSTORE_*`.

## `tienda/`: los scripts

Python sin dependencias (firma con `openssl`), así que corren igual en el Mac (3.9 de Xcode) que en
Actions. Leen las credenciales del entorno en CI y del Llavero y `~/keys` en el Mac. `~/keys/play.sh`,
`~/keys/appstore.py` y `~/keys/testflight.sh` son enlaces o envoltorios de estos.

| Script | Para |
|---|---|
| `comprobar.py <repo>` | El contrato de `store/`: idiomas iguales, topes de cada tienda, gráficos y capturas |
| `play.py estado\|ficha\|aab` | Play: canales y fichas; la ficha entera en una edición (`--dry` la valida y la tira); un AAB a un canal de prueba |
| `appstore.py estado\|ficha\|capturas\|...` | App Store Connect: qué falta antes de enviar, ficha, capturas, compras, adjuntos, versión y build |
| `compra.py <paquete> <producto Play> <producto Apple> [precio]` | La compra igual en las dos tiendas: textos de Apple copiados a Play y, con precio, base en España e igualación de Apple aplicada a Play donde la moneda coincide |
| `testflight.sh <repo> [fichero de versión]` | Lo mismo que `ios-testflight-release.yml`, desde el Mac, cuando Actions no puede |

## Lo que no está aquí, y por qué

Los workflows de tests y el despliegue web se quedan en cada repositorio. Solo comparten tres líneas
de preparación (checkout, Java, Gradle) y difieren en versión de Java, máquina y tareas: un workflow
con seis entradas para ahorrar eso se lee peor que los dos ficheros que sustituye.
