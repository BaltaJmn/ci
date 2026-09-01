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
`gradle-tasks`, `aab-path`, `track`, `whatsnew-dir`. Los valores por defecto
sirven para un proyecto con el modulo Android en `androidApp`.

Secretos que espera en el repositorio que llama: `KEYSTORE_BASE64`,
`KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD` y `PLAY_SERVICE_ACCOUNT_JSON`.
La cuenta de servicio de publicacion es distinta de la de RevenueCat, que es de
solo lectura a proposito.

Sirve para las dos formas de firmar que uso: escribe `keystore.properties` en la
raiz y ademas exporta `KEYSTORE_PATH`, `KEYSTORE_PASSWORD`, `KEYSTORE_ALIAS` y
`KEYSTORE_ALIAS_PASSWORD`, asi que el Gradle de la app puede leer cualquiera de
las dos sin cambios.

## Lo que no esta aqui, y por que

Los workflows de tests, el despliegue web y la subida a TestFlight se quedan en
cada repositorio. Solo comparten tres lineas de preparacion (checkout, Java,
Gradle) y difieren en version de Java, maquina y tareas: un workflow con seis
entradas para ahorrar eso se lee peor que los dos ficheros que sustituye.
