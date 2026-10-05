# ci

Workflows reutilizables de GitHub Actions compartidos entre mis proyectos.

No hay codigo aqui, solo YAML. El repositorio es publico porque un workflow
reutilizable de un repositorio publico se puede llamar desde cualquier otro, y
porque no contiene ningun secreto: los secretos siguen viviendo en cada repo que
llama, y se pasan explicitamente en la llamada.

## `android-play-release.yml`

Construye el `.aab` de release, comprueba que va firmado con la clave de subida y
lo publica en Google Play.

Lo llama, en el repositorio de la app:

```yaml
jobs:
  release:
    uses: BaltaJmn/ci/.github/workflows/android-play-release.yml@main
    with:
      package-name: com.ejemplo.app
      whatsnew-dir: store/whatsnew
    secrets:
      KEYSTORE_BASE64: ${{ secrets.KEYSTORE_BASE64 }}
      KEYSTORE_PASSWORD: ${{ secrets.KEYSTORE_PASSWORD }}
      KEY_ALIAS: ${{ secrets.KEY_ALIAS }}
      KEY_PASSWORD: ${{ secrets.KEY_PASSWORD }}
      PLAY_SERVICE_ACCOUNT_JSON: ${{ secrets.PLAY_SERVICE_ACCOUNT_JSON }}
```

Uno a uno y no `secrets: inherit`: los repositorios que llaman guardan tambien
secretos de otras cosas (App Store Connect, Cloudflare, Supabase) que no pintan
nada en una publicacion de Play.

Si el build de la app genera configuracion a partir del entorno, se le pasan las
variables por el secreto opcional `BUILD_ENV`, una `KEY=VALUE` por linea. Va como
secreto y no como entrada porque las entradas aparecen en claro en el log, y el
workflow las enmascara antes de exportarlas.

Entradas: `package-name` (obligatoria), `signer-cn`, `java-version`,
`gradle-tasks`, `aab-path`, `track`, `whatsnew-dir`, `status`. Los valores por
defecto sirven para un proyecto con el modulo Android en `androidApp`.

`signer-cn` por defecto es `CN=Baltasar`. Una app cuya clave de subida lleve otro
CN tiene que pasarlo, o la comprobacion de firma falla aunque el paquete este bien
firmado: se mira con `keytool -printcert -jarfile <aab>`, linea `Owner`.

`status` es `completed` salvo en una app que todavia no tiene ninguna version
publicada en ningun canal: ahi Play solo acepta `draft`, y la version se lanza
despues desde la consola.

Secretos que espera en el repositorio que llama: `KEYSTORE_BASE64`,
`KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD` y `PLAY_SERVICE_ACCOUNT_JSON`.
La cuenta de servicio de publicacion es distinta de la de RevenueCat, que es de
solo lectura a proposito.

Sirve para las dos formas de firmar que uso: escribe `keystore.properties` en la
raiz y ademas exporta `KEYSTORE_PATH`, `KEYSTORE_PASSWORD`, `KEYSTORE_ALIAS` y
`KEYSTORE_ALIAS_PASSWORD`, asi que el Gradle de la app puede leer cualquiera de
las dos sin cambios.

## `ios-testflight-release.yml`

Archiva la app de iOS, la firma con los certificados propios del equipo y la sube
a App Store Connect, que la pasa a TestFlight. La version y el build son el
`versionName` y el `versionCode` de Android, asi que una etiqueta sube lo mismo a
las dos tiendas.

```yaml
jobs:
  testflight:
    uses: BaltaJmn/ci/.github/workflows/ios-testflight-release.yml@main
    secrets:
      APPSTORE_KEY_ID: ${{ secrets.APPSTORE_KEY_ID }}
      APPSTORE_ISSUER_ID: ${{ secrets.APPSTORE_ISSUER_ID }}
      APPSTORE_PRIVATE_KEY: ${{ secrets.APPSTORE_PRIVATE_KEY }}
      APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
      APPLE_DEVELOPMENT_P12: ${{ secrets.APPLE_DEVELOPMENT_P12 }}
      APPLE_DEVELOPMENT_P12_PASSWORD: ${{ secrets.APPLE_DEVELOPMENT_P12_PASSWORD }}
      APPLE_DISTRIBUTION_P12: ${{ secrets.APPLE_DISTRIBUTION_P12 }}
      APPLE_DISTRIBUTION_P12_PASSWORD: ${{ secrets.APPLE_DISTRIBUTION_P12_PASSWORD }}
```

Entradas: `project` (`iosApp/iosApp.xcodeproj`), `scheme` (`iosApp`),
`version-file` (`androidApp/build.gradle.kts`; FlowTime pasa
`build-logic/plugins/src/main/java/Config.kt`) y `java-version`.

Los dos `.p12` son a proposito, y cada uno evita un fallo distinto:

- **Desarrollo.** Con la clave de la API, `-allowProvisioningUpdates` crea
  certificado y perfiles. En una maquina recien creada no hay identidad de
  desarrollo, asi que crearia un certificado nuevo en cada ejecucion hasta topar
  con el limite de Apple.
- **Distribucion.** Sin certificado de distribucion en el llavero, Xcode firma en
  la nube, y en este equipo eso no sube: le pasa a `codesign` el requisito
  designado como argumento, con el nombre del certificado dentro, y la "e" con
  tilde de "Jimenez" se descompone por el camino (NFD). La firma deja de cumplir
  su propio requisito y App Store Connect la rechaza con ITMS-90035.

Los perfiles si los sigue creando Xcode con la clave de la API: ningun
`.mobileprovision` vive en un secreto. Los certificados, su contrasena y como se
renuevan estan en `~/keys/LEEME.md`, y `~/keys/testflight.sh` hace lo mismo desde
el Mac cuando Actions no puede (sin maquina macOS libre, o un repositorio privado
con la facturacion parada).

## Lo que no esta aqui, y por que

Los workflows de tests y el despliegue web se quedan en cada repositorio. Solo
comparten tres lineas de preparacion (checkout, Java, Gradle) y difieren en
version de Java, maquina y tareas: un workflow con seis entradas para ahorrar eso
se lee peor que los dos ficheros que sustituye. La subida a TestFlight si vino
aqui: era el mismo fichero en las cuatro apps, y el arreglo de la firma hubo que
hacerlo cuatro veces.
