#!/usr/bin/env python3
"""
Pipeline seguro para procesar assets .lzma de iAngeloQuest.

Qué hace:
- Recorre una carpeta completa buscando archivos .lzma.
- Descomprime cada archivo.
- Detecta si el contenido descomprimido es una imagen soportada por Pillow.
- Si es imagen, normaliza a RGBA y aplica una mejora visual conservadora.
- Si NO es imagen, conserva el contenido intacto.
- Recomprime a .lzma manteniendo la misma ruta relativa y nombre.
- Genera reportes CSV.

No modifica XML, Lua, atributos de items ni lógica de servidor.
"""

from __future__ import annotations

import argparse
import csv
import io
import lzma
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageFilter


@dataclass
class Resultado:
    archivo: str
    estado: str
    tipo: str
    detalle: str


def leer_lzma(path: Path) -> bytes:
    with lzma.open(path, "rb") as f:
        return f.read()


def escribir_lzma(path: Path, data: bytes, preset: int = 6) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with lzma.open(path, "wb", preset=preset) as f:
        f.write(data)


def intentar_abrir_imagen(data: bytes) -> Optional[Image.Image]:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception:
        return None


def mejorar_imagen(img: Image.Image) -> Image.Image:
    """
    Mejora conservadora para assets visuales.
    Mantiene transparencia, no escala, no cambia proporciones.
    Para sprites pixel art evita blur y no inventa geometría.
    """
    rgba = img.convert("RGBA")

    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")

    # Mejora sutil: contraste y nitidez sin deformar.
    rgb = ImageEnhance.Contrast(rgb).enhance(1.08)
    rgb = ImageEnhance.Color(rgb).enhance(1.04)
    rgb = ImageEnhance.Sharpness(rgb).enhance(1.15)

    out = Image.merge("RGBA", (*rgb.split(), alpha))
    return out


def imagen_a_bytes(img: Image.Image, formato_original: Optional[str]) -> bytes:
    buffer = io.BytesIO()
    fmt = (formato_original or "PNG").upper()

    if fmt not in {"PNG", "WEBP", "BMP", "JPEG", "JPG", "TGA"}:
        fmt = "PNG"

    if fmt == "JPG":
        fmt = "JPEG"

    if fmt == "JPEG":
        img = img.convert("RGB")
        img.save(buffer, format=fmt, quality=95, optimize=True)
    elif fmt == "PNG":
        img.save(buffer, format="PNG", optimize=True)
    else:
        img.save(buffer, format=fmt)

    return buffer.getvalue()


def procesar_archivo(src: Path, input_root: Path, output_root: Path, backup_root: Optional[Path]) -> Resultado:
    rel = src.relative_to(input_root)
    dst = output_root / rel

    try:
        if backup_root:
            backup_path = backup_root / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup_path)

        raw = leer_lzma(src)
        img = intentar_abrir_imagen(raw)

        if img is None:
            escribir_lzma(dst, raw)
            return Resultado(str(rel), "copiado", "binario", "Contenido no reconocido como imagen; conservado intacto")

        formato = img.format
        mejorada = mejorar_imagen(img)
        nuevo_raw = imagen_a_bytes(mejorada, formato)
        escribir_lzma(dst, nuevo_raw)

        return Resultado(str(rel), "mejorado", f"imagen/{formato or 'desconocido'}", f"{img.size[0]}x{img.size[1]} RGBA preservado")

    except Exception as exc:
        return Resultado(str(rel), "error", "desconocido", repr(exc))


def escribir_reporte(path: Path, resultados: list[Resultado]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["archivo", "estado", "tipo", "detalle"])
        for r in resultados:
            writer.writerow([r.archivo, r.estado, r.tipo, r.detalle])


def main() -> None:
    parser = argparse.ArgumentParser(description="Procesa assets .lzma sin alterar lógica de servidor.")
    parser.add_argument("--input", required=True, help="Carpeta con .lzma originales")
    parser.add_argument("--output", required=True, help="Carpeta de salida con .lzma procesados")
    parser.add_argument("--backup", default=None, help="Carpeta opcional de respaldo")
    parser.add_argument("--reports", default="reportes", help="Carpeta de reportes CSV")
    args = parser.parse_args()

    input_root = Path(args.input).resolve()
    output_root = Path(args.output).resolve()
    backup_root = Path(args.backup).resolve() if args.backup else None
    reports_root = Path(args.reports).resolve()

    if not input_root.exists():
        raise SystemExit(f"No existe la carpeta de entrada: {input_root}")

    archivos = sorted(input_root.rglob("*.lzma"))
    resultados: list[Resultado] = []

    for i, archivo in enumerate(archivos, start=1):
        resultado = procesar_archivo(archivo, input_root, output_root, backup_root)
        resultados.append(resultado)
        print(f"[{i}/{len(archivos)}] {resultado.estado}: {resultado.archivo}")

    escribir_reporte(reports_root / "procesamiento_lzma.csv", resultados)

    total = len(resultados)
    mejorados = sum(1 for r in resultados if r.estado == "mejorado")
    copiados = sum(1 for r in resultados if r.estado == "copiado")
    errores = sum(1 for r in resultados if r.estado == "error")

    print("\nResumen")
    print(f"Total: {total}")
    print(f"Mejorados visualmente: {mejorados}")
    print(f"Conservados intactos: {copiados}")
    print(f"Errores: {errores}")
    print(f"Reporte: {reports_root / 'procesamiento_lzma.csv'}")


if __name__ == "__main__":
    main()
