#!/usr/bin/env python3
"""
Procesador para archivos .bmp.lzma exportados/usados por DatEditor/iAngeloQuest.

Este script soporta dos casos:
1. LZMA estándar que descomprime directo a BMP/PNG/etc.
2. Contenedor custom observado en subarea-0275*.bmp.lzma:
   - bytes 0..31   : cabecera propia del cliente/editor
   - bytes 32..36  : propiedades LZMA raw, normalmente 5d 00 00 00 02
   - bytes 37..44  : tamaño comprimido little-endian
   - bytes 45..fin : payload LZMA raw que descomprime a BMP

El script mejora estructura visual y vuelve a empacar respetando:
- mismo nombre;
- misma ruta relativa;
- mismo tamaño de imagen;
- transparencia/canal alfa cuando exista;
- cabecera custom preservada;
- sin tocar XML, stats, ataque, defensa, flags ni lógica de servidor.
"""

from __future__ import annotations

import argparse
import csv
import io
import lzma
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import Image, ImageEnhance, ImageFilter


@dataclass
class AssetDecodificado:
    imagen: Image.Image
    formato: str
    modo: str
    cabecera_custom: Optional[bytes] = None
    lzma_props: Optional[bytes] = None


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


PERFILES = {
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
    data: Dict[str, ManifestItem] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ruta = (row.get("ruta") or row.get("archivo") or "").replace("\\", "/").strip().lower()
            if not ruta:
                continue
            categoria = (row.get("categoria") or "objeto_generico").strip().lower()
            if categoria not in PERFILES:
                categoria = "objeto_generico"
            orden_raw = (row.get("orden") or "0").strip()
            data[ruta] = ManifestItem(
                categoria=categoria,
                grupo=(row.get("grupo") or "").strip(),
                orden=int(orden_raw) if orden_raw.isdigit() else 0,
            )
    return data


def manifest_para(rel: Path, manifest: Dict[str, ManifestItem]) -> ManifestItem:
    return manifest.get(str(rel).replace("\\", "/").lower(), ManifestItem())


def filtros_lzma_raw(props: bytes):
    if len(props) != 5:
        raise ValueError("Propiedades LZMA inválidas")
    prop = props[0]
    lc = prop % 9
    remainder = prop // 9
    lp = remainder % 5
    pb = remainder // 5
    dict_size = int.from_bytes(props[1:5], "little")
    return [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}]


def abrir_imagen_bytes(data: bytes) -> Optional[Tuple[Image.Image, str]]:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img, img.format or "PNG"
    except Exception:
        return None


def decodificar_asset(data: bytes) -> Optional[AssetDecodificado]:
    # Caso 1: LZMA estándar.
    try:
        plano = lzma.decompress(data)
        abierto = abrir_imagen_bytes(plano)
        if abierto:
            img, fmt = abierto
            return AssetDecodificado(img, fmt, "lzma_estandar")
    except Exception:
        pass

    # Caso 2: contenedor custom BMP LZMA raw usado por subarea*.bmp.lzma.
    if len(data) > 45:
        cabecera = data[:32]
        props = data[32:37]
        compressed_size = int.from_bytes(data[37:45], "little")
        payload = data[45:45 + compressed_size] if compressed_size > 0 else data[45:]
        try:
            plano = lzma.decompress(payload, format=lzma.FORMAT_RAW, filters=filtros_lzma_raw(props))
            abierto = abrir_imagen_bytes(plano)
            if abierto:
                img, fmt = abierto
                return AssetDecodificado(img, fmt, "dat_editor_bmp_lzma_raw", cabecera, props)
        except Exception:
            pass

    return None


def guardar_imagen(img: Image.Image, formato: str) -> bytes:
    out = io.BytesIO()
    fmt = (formato or "PNG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt == "BMP":
        img.save(out, format="BMP")
    elif fmt == "PNG":
        img.save(out, format="PNG", optimize=True)
    elif fmt == "JPEG":
        img.convert("RGB").save(out, format="JPEG", quality=95, optimize=True)
    else:
        img.save(out, format=fmt)
    return out.getvalue()


def reempacar(asset: AssetDecodificado, imagen: Image.Image) -> bytes:
    plano = guardar_imagen(imagen, asset.formato)

    if asset.modo == "dat_editor_bmp_lzma_raw":
        assert asset.cabecera_custom is not None
        assert asset.lzma_props is not None
        payload = lzma.compress(plano, format=lzma.FORMAT_RAW, filters=filtros_lzma_raw(asset.lzma_props))
        return asset.cabecera_custom + asset.lzma_props + len(payload).to_bytes(8, "little") + payload

    return lzma.compress(plano)


def ajustar(rgba: Image.Image, contraste: float, saturacion: float, brillo: float, nitidez: float) -> Image.Image:
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(contraste)
    rgb = ImageEnhance.Color(rgb).enhance(saturacion)
    rgb = ImageEnhance.Brightness(rgb).enhance(brillo)
    rgb = ImageEnhance.Sharpness(rgb).enhance(nitidez)
    return Image.merge("RGBA", (*rgb.split(), alpha))


def cuantizar(rgba: Image.Image, colores: int) -> Image.Image:
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    pal = rgb.quantize(colors=max(8, min(256, colores)), method=Image.Quantize.MEDIANCUT)
    rgb = pal.convert("RGB")
    return Image.merge("RGBA", (*rgb.split(), alpha))


def luz_superior(rgba: Image.Image, fuerza: float) -> Image.Image:
    rgba = rgba.copy().convert("RGBA")
    bbox = rgba.getchannel("A").getbbox()
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
            light = (1 - nx) * 0.45 + (1 - ny) * 0.55
            factor = 1 + ((light - 0.5) * 2 * fuerza)
            pix[x, y] = (
                max(0, min(255, int(r * factor))),
                max(0, min(255, int(g * factor))),
                max(0, min(255, int(b * factor))),
                a,
            )
    return rgba


def contorno_interno(rgba: Image.Image, intensidad: float) -> Image.Image:
    rgba = rgba.copy().convert("RGBA")
    edges = rgba.getchannel("A").filter(ImageFilter.FIND_EDGES)
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


def mejorar(img: Image.Image, item: ManifestItem) -> Image.Image:
    rgba = img.convert("RGBA")
    perfil = item.categoria

    if perfil in {"arma", "objeto_ataque"}:
        out = ajustar(rgba, 1.18, 1.07, 1.02, 1.26)
        out = luz_superior(out, 0.12)
        out = cuantizar(out, 56)
        return contorno_interno(out, 0.46)

    if perfil == "valla_estructura":
        out = ajustar(rgba, 1.14, 1.04, 1.00, 1.20)
        out = luz_superior(out, 0.08)
        out = cuantizar(out, 42)
        return contorno_interno(out, 0.38)

    if perfil == "cuerpo_monstruo":
        paso = max(0, min(item.orden - 1, 8))
        out = ajustar(rgba, 1.08 + paso * 0.012, max(0.72, 0.98 - paso * 0.035), max(0.76, 1.00 - paso * 0.022), 1.13)
        out = luz_superior(out, 0.07)
        out = cuantizar(out, 44)
        return contorno_interno(out, 0.28)

    if perfil == "textura_suelo":
        out = ajustar(rgba, 1.05, 1.03, 1.01, 1.06)
        return cuantizar(out, 64)

    if perfil == "efecto":
        out = ajustar(rgba, 1.10, 1.18, 1.04, 1.08)
        out = luz_superior(out, 0.06)
        return cuantizar(out, 72)

    out = ajustar(rgba, 1.10, 1.04, 1.00, 1.16)
    out = luz_superior(out, 0.08)
    out = cuantizar(out, 52)
    return contorno_interno(out, 0.32)


def procesar_archivo(src: Path, input_root: Path, output_root: Path, backup_root: Optional[Path], manifest: Dict[str, ManifestItem]) -> Resultado:
    rel = src.relative_to(input_root)
    item = manifest_para(rel, manifest)
    dst = output_root / rel

    try:
        data = src.read_bytes()
        asset = decodificar_asset(data)
        if asset is None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            return Resultado(str(rel), "conservado", item.categoria, "No se reconoció imagen en LZMA; binario intacto")

        if backup_root:
            backup = backup_root / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup)

        original_size = asset.imagen.size
        mejorada = mejorar(asset.imagen, item)
        if mejorada.size != original_size:
            mejorada = mejorada.resize(original_size, Image.Resampling.NEAREST)

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(reempacar(asset, mejorada))
        return Resultado(str(rel), "mejorado", item.categoria, f"{asset.modo} {asset.formato} {original_size[0]}x{original_size[1]}")

    except Exception as exc:
        return Resultado(str(rel), "error", item.categoria, repr(exc))


def escribir_reporte(path: Path, resultados: list[Resultado]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["archivo", "estado", "perfil", "detalle"])
        for r in resultados:
            writer.writerow([r.archivo, r.estado, r.perfil, r.detalle])


def main() -> None:
    parser = argparse.ArgumentParser(description="Mejora .bmp.lzma custom sin alterar lógica del servidor.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--backup", default=None)
    parser.add_argument("--reports", default="reportes")
    args = parser.parse_args()

    input_root = Path(args.input).resolve()
    output_root = Path(args.output).resolve()
    backup_root = Path(args.backup).resolve() if args.backup else None
    manifest = cargar_manifest(Path(args.manifest).resolve() if args.manifest else None)

    archivos = sorted(input_root.rglob("*.lzma"))
    resultados: list[Resultado] = []
    for i, archivo in enumerate(archivos, 1):
        r = procesar_archivo(archivo, input_root, output_root, backup_root, manifest)
        resultados.append(r)
        print(f"[{i}/{len(archivos)}] {r.estado}: {r.archivo} ({r.perfil})")

    reports = Path(args.reports).resolve()
    escribir_reporte(reports / "bmp_lzma_custom.csv", resultados)
    print(f"Reporte: {reports / 'bmp_lzma_custom.csv'}")


if __name__ == "__main__":
    main()
