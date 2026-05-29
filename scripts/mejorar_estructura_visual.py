#!/usr/bin/env python3
"""
Mejora estructural visual para assets .lzma de iAngeloQuest.

Objetivo:
- Mejorar la lectura visual de sprites/texturas sin tocar datos de juego.
- Mantener el mismo archivo .lzma, misma ruta relativa y mismo tamaño de imagen.
- Respetar alfa/transparencia.
- Aplicar perfiles distintos: valla/estructura, cuerpo_monstruo, arma, textura_suelo, efecto, objeto_generico.

Uso:
python scripts/mejorar_estructura_visual.py --input assets_originales --output assets_mejorados --manifest manifests/ejemplo_valla_cuerpo.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import lzma
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import Image, ImageEnhance, ImageFilter


@dataclass
class ManifestItem:
    categoria: str = "objeto_generico"
    grupo: str = ""
    orden: int = 0


@dataclass
class Resultado:
    archivo: str
    estado: str
    perfil: str
    detalle: str


PERFILES_VALIDOS = {
    "arma",
    "objeto_ataque",
    "valla_estructura",
    "cuerpo_monstruo",
    "textura_suelo",
    "efecto",
    "objeto_generico",
}


def cargar_manifest(path: Optional[Path]) -> Dict[str, ManifestItem]:
    if not path:
        return {}
    if not path.exists():
        raise SystemExit(f"No existe el manifest: {path}")

    data: Dict[str, ManifestItem] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ruta = (row.get("ruta") or row.get("archivo") or "").replace("\\", "/").strip().lower()
            if not ruta:
                continue
            categoria = (row.get("categoria") or "objeto_generico").strip().lower()
            if categoria not in PERFILES_VALIDOS:
                categoria = "objeto_generico"
            orden_raw = (row.get("orden") or "0").strip()
            data[ruta] = ManifestItem(
                categoria=categoria,
                grupo=(row.get("grupo") or "").strip(),
                orden=int(orden_raw) if orden_raw.isdigit() else 0,
            )
    return data


def clave(path: Path) -> str:
    return str(path).replace("\\", "/").lower()


def buscar_manifest(rel: Path, manifest: Dict[str, ManifestItem]) -> ManifestItem:
    rel_key = clave(rel)
    return manifest.get(rel_key, ManifestItem())


def leer_lzma(path: Path) -> bytes:
    with lzma.open(path, "rb") as f:
        return f.read()


def escribir_lzma(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with lzma.open(path, "wb", preset=6) as f:
        f.write(data)


def abrir_imagen(data: bytes) -> Optional[Image.Image]:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception:
        return None


def guardar_imagen(img: Image.Image, formato: Optional[str]) -> bytes:
    fmt = (formato or "PNG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt not in {"PNG", "WEBP", "BMP", "JPEG", "TGA"}:
        fmt = "PNG"
    out = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(out, format="JPEG", quality=95, optimize=True)
    elif fmt == "PNG":
        img.save(out, format="PNG", optimize=True)
    else:
        img.save(out, format=fmt)
    return out.getvalue()


def bbox_alpha(rgba: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    return rgba.getchannel("A").getbbox()


def ajustar_base(rgba: Image.Image, contraste: float, saturacion: float, brillo: float, nitidez: float) -> Image.Image:
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(contraste)
    rgb = ImageEnhance.Color(rgb).enhance(saturacion)
    rgb = ImageEnhance.Brightness(rgb).enhance(brillo)
    rgb = ImageEnhance.Sharpness(rgb).enhance(nitidez)
    return Image.merge("RGBA", (*rgb.split(), alpha))


def cuantizar_pixelart(rgba: Image.Image, colores: int = 48) -> Image.Image:
    """Reduce ruido visual manteniendo alfa y look pixel-art."""
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    pal = rgb.quantize(colors=max(8, min(colores, 256)), method=Image.Quantize.MEDIANCUT)
    rgb2 = pal.convert("RGB")
    return Image.merge("RGBA", (*rgb2.split(), alpha))


def aplicar_luz_superior(rgba: Image.Image, fuerza: float = 0.10) -> Image.Image:
    """Agrega lectura volumétrica: arriba/izquierda más claro, abajo/derecha más oscuro."""
    rgba = rgba.copy().convert("RGBA")
    bbox = bbox_alpha(rgba)
    if not bbox:
        return rgba
    x0, y0, x1, y1 = bbox
    w = max(1, x1 - x0)
    h = max(1, y1 - y0)
    pix = rgba.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b, a = pix[x, y]
            if a == 0:
                continue
            nx = (x - x0) / w
            ny = (y - y0) / h
            luz = (1.0 - nx) * 0.45 + (1.0 - ny) * 0.55
            factor = 1.0 + ((luz - 0.5) * 2.0 * fuerza)
            pix[x, y] = (
                max(0, min(255, int(r * factor))),
                max(0, min(255, int(g * factor))),
                max(0, min(255, int(b * factor))),
                a,
            )
    return rgba


def reforzar_silueta(rgba: Image.Image, intensidad: float = 0.45) -> Image.Image:
    """Refuerza bordes internos para lectura en tiles oscuros/claros sin cambiar dimensiones."""
    rgba = rgba.copy().convert("RGBA")
    alpha = rgba.getchannel("A")
    edges = alpha.filter(ImageFilter.FIND_EDGES)
    pix = rgba.load()
    epix = edges.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pix[x, y]
            if a == 0 or epix[x, y] == 0:
                continue
            pix[x, y] = (
                int(r * (1 - intensidad) + 14 * intensidad),
                int(g * (1 - intensidad) + 12 * intensidad),
                int(b * (1 - intensidad) + 16 * intensidad),
                a,
            )
    return rgba


def enfocar_metal(rgba: Image.Image) -> Image.Image:
    out = ajustar_base(rgba, contraste=1.18, saturacion=1.06, brillo=1.02, nitidez=1.28)
    out = aplicar_luz_superior(out, fuerza=0.13)
    out = cuantizar_pixelart(out, colores=56)
    return reforzar_silueta(out, intensidad=0.46)


def mejorar_valla(rgba: Image.Image) -> Image.Image:
    out = ajustar_base(rgba, contraste=1.14, saturacion=1.05, brillo=1.00, nitidez=1.20)
    out = aplicar_luz_superior(out, fuerza=0.09)
    out = cuantizar_pixelart(out, colores=40)
    return reforzar_silueta(out, intensidad=0.38)


def mejorar_cuerpo_monstruo(rgba: Image.Image, orden: int) -> Image.Image:
    """Respeta descomposición gradual: frames posteriores más apagados y más terrosos."""
    paso = max(0, min(orden - 1, 8))
    out = ajustar_base(
        rgba,
        contraste=1.08 + paso * 0.012,
        saturacion=max(0.72, 0.98 - paso * 0.035),
        brillo=max(0.76, 1.00 - paso * 0.022),
        nitidez=1.13,
    )
    out = aplicar_luz_superior(out, fuerza=0.07)
    out = cuantizar_pixelart(out, colores=44)
    return reforzar_silueta(out, intensidad=0.28)


def mejorar_textura_suelo(rgba: Image.Image) -> Image.Image:
    """Texturas tileables: mejora suave, sin borde artificial fuerte."""
    out = ajustar_base(rgba, contraste=1.05, saturacion=1.03, brillo=1.01, nitidez=1.06)
    return cuantizar_pixelart(out, colores=64)


def mejorar_efecto(rgba: Image.Image) -> Image.Image:
    out = ajustar_base(rgba, contraste=1.10, saturacion=1.18, brillo=1.04, nitidez=1.08)
    out = aplicar_luz_superior(out, fuerza=0.06)
    return cuantizar_pixelart(out, colores=72)


def mejorar_generico(rgba: Image.Image) -> Image.Image:
    out = ajustar_base(rgba, contraste=1.10, saturacion=1.04, brillo=1.00, nitidez=1.16)
    out = aplicar_luz_superior(out, fuerza=0.08)
    out = cuantizar_pixelart(out, colores=52)
    return reforzar_silueta(out, intensidad=0.32)


def mejorar_por_perfil(img: Image.Image, item: ManifestItem) -> Image.Image:
    rgba = img.convert("RGBA")
    perfil = item.categoria
    if perfil in {"arma", "objeto_ataque"}:
        return enfocar_metal(rgba)
    if perfil == "valla_estructura":
        return mejorar_valla(rgba)
    if perfil == "cuerpo_monstruo":
        return mejorar_cuerpo_monstruo(rgba, item.orden)
    if perfil == "textura_suelo":
        return mejorar_textura_suelo(rgba)
    if perfil == "efecto":
        return mejorar_efecto(rgba)
    return mejorar_generico(rgba)


def procesar_archivo(src: Path, input_root: Path, output_root: Path, backup_root: Optional[Path], manifest: Dict[str, ManifestItem]) -> Resultado:
    rel = src.relative_to(input_root)
    item = buscar_manifest(rel, manifest)
    dst = output_root / rel

    try:
        raw = leer_lzma(src)
        img = abrir_imagen(raw)
        if img is None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            escribir_lzma(dst, raw)
            return Resultado(str(rel), "conservado", item.categoria, "No es imagen reconocible; binario intacto")

        if backup_root:
            bkp = backup_root / rel
            bkp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, bkp)

        original_size = img.size
        formato = img.format
        mejorada = mejorar_por_perfil(img, item)

        # Garantía estricta: mismo tamaño que imagen original.
        if mejorada.size != original_size:
            mejorada = mejorada.resize(original_size, Image.Resampling.NEAREST)

        escribir_lzma(dst, guardar_imagen(mejorada, formato))
        return Resultado(str(rel), "mejorado", item.categoria, f"{original_size[0]}x{original_size[1]} formato={formato or 'PNG'}")
    except Exception as exc:
        return Resultado(str(rel), "error", item.categoria, repr(exc))


def escribir_reporte(path: Path, resultados: list[Resultado]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["archivo", "estado", "perfil", "detalle"])
        for r in resultados:
            w.writerow([r.archivo, r.estado, r.perfil, r.detalle])


def main() -> None:
    parser = argparse.ArgumentParser(description="Mejora estructura visual de assets .lzma.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--backup", default=None)
    parser.add_argument("--reports", default="reportes")
    args = parser.parse_args()

    input_root = Path(args.input).resolve()
    output_root = Path(args.output).resolve()
    backup_root = Path(args.backup).resolve() if args.backup else None
    reports_root = Path(args.reports).resolve()
    manifest = cargar_manifest(Path(args.manifest).resolve() if args.manifest else None)

    if not input_root.exists():
        raise SystemExit(f"No existe la carpeta de entrada: {input_root}")

    archivos = sorted(input_root.rglob("*.lzma"))
    resultados: list[Resultado] = []

    for i, archivo in enumerate(archivos, start=1):
        r = procesar_archivo(archivo, input_root, output_root, backup_root, manifest)
        resultados.append(r)
        print(f"[{i}/{len(archivos)}] {r.estado}: {r.archivo} ({r.perfil})")

    escribir_reporte(reports_root / "estructura_visual_lzma.csv", resultados)
    print("\nResumen")
    print(f"Total: {len(resultados)}")
    print(f"Mejorados: {sum(1 for r in resultados if r.estado == 'mejorado')}")
    print(f"Conservados: {sum(1 for r in resultados if r.estado == 'conservado')}")
    print(f"Errores: {sum(1 for r in resultados if r.estado == 'error')}")
    print(f"Reporte: {reports_root / 'estructura_visual_lzma.csv'}")


if __name__ == "__main__":
    main()
