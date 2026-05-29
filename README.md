# Objetos iAngeloQuest

Repositorio preparado para procesar assets `.lzma` del servidor iAngelo Quest sin alterar lógica de juego.

## Objetivo

Mejorar visualmente sprites, texturas e imágenes comprimidas en LZMA respetando:

- mismo nombre de archivo;
- misma ruta relativa;
- mismo formato `.lzma` cuando sea posible;
- canal alfa/transparencia cuando el contenido sea imagen;
- sin modificar XML, scripts, atributos de ataque, defensa, armor, flags, itemid, actionid o comportamiento del servidor.

## Estructura recomendada

```txt
assets_originales/   # colocar aquí los .lzma originales
assets_mejorados/    # salida automática del script
backups/             # respaldo opcional
reportes/            # CSV de archivos procesados, errores y resumen
scripts/             # herramientas de procesamiento
```

## Flujo seguro

```bash
python scripts/procesar_lzma_assets.py --input assets_originales --output assets_mejorados --backup backups --reports reportes
```

El script procesa imágenes cuando el contenido del `.lzma` es reconocible como PNG/JPG/WebP/BMP/TGA. Si detecta datos binarios no visuales, los copia/recomprime sin alterar su contenido.
