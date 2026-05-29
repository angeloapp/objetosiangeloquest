#!/usr/bin/env python3
"""
Pipeline seguro para procesar assets .lzma de iAngeloQuest.

Diseñado para packs grandes de sprites/texturas de servidores Tibia/OpenTibia.

Qué hace:
- Recorre una carpeta completa buscando archivos .lzma.
- Descomprime cada archivo.
- Detecta si el contenido descomprimido es una imagen soportada por Pillow.
- Si es imagen, normaliza a RGBA y aplica mejora visual según perfil.
- Si NO es imagen, conserva el contenido intacto.
- Recomprime a .lzma manteniendo la misma ruta relativa y nombre.
- Genera reportes CSV.

No modifica XML, Lua, atributos de items ni lógica de servidor.
No cambia itemid, attack, defense, armor, flags, movement, blocking, decay ni comportamiento.
"""

from __future__ import annotations

import argparse
import csv
import io
import lzma
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from PIL import Image, ImageEnhance, ImageFilter


@dataclass
class Resultado:
    archivo: str
    estado: str
    tipo: str
    perfil: str
    detalle: str


@dataclass
class ManifestItem:
    categoria: str = "auto"
    grupo: str = ""
    orden: Optional[int] = None


PALABRAS_PERFIL = {
    "arma": (
        "sword", "espada", "axe", "hacha", "club", "mace", "wand", "rod", "bow", "crossbow",
        "weapon", "arma", "blade", "dagger", "knife", "spear", "staff",
    ),
    "cuerpo_monstruo": (
        "corpse", "dead", "body", "monster", "creature", "decay", "decomposition",
        "cadaver", "cadaveres", "cuerpo", "monstruo", "muerte", "muerto", "descomp",
    ),
    "valla_estructura": (
        "fence", "wall", "palisade", "bars", "gate", "door", "wood", "stone",
        "valla", "cerca", "muro", "pared", "reja", "puerta", "madera", "piedra",
    ),
    "textura_suelo": (
        "ground", "floor", "tile", "grass", "sand", "dirt", "earth", "water", "lava", "ice",
        "suelo", "piso", "tile", "hierba", "arena", "tierra", "agua", "hielo",
    ),
    "efecto": (
        "effect", "magic", "spell", "fire", "poison", "energy", "spark", "glow",
        "efecto", "magia", "hechizo", "fuego", "veneno", "energia", "brillo",
    ),
}


EXTENSIONES_IMAGEN_SOPORTADAS = {"PNG", "WEBP", "BMP", "JPEG", "JPG", "TGA"}


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


def normalizar_clave(path: Path) -> str:
    return str(path).replace("\\", "/").lower()


def inferir_perfil(rel: Path, manifest: Optional[ManifestItem] = None) -> str:
    if manifest and manifest.categoria and manifest.categoria != "auto":
        return manifest.categoria.strip().lower()

    texto = normalizar_clave(rel)
    for perfil, palabras in PALABRAS_PERFIL.items():
        if any(p in texto for p in palabras):
            return perfil
    return "objeto_generico"


def cargar_manifest(path: Optional[Path]) -> Dict[str, ManifestItem]:
    """
    CSV opcional para controlar perfiles.

    Columnas aceptadas:
    ruta,categoria,grupo,orden

    Ejemplo:
    assets_originales/52925.lzma,valla_estructura,valla_rota,1
    assets_originales/52926.lzma,valla_estructura,valla_rota,2
    assets_originales/60001.lzma,cuerpo_monstruo,dragon_decay,1
    assets_originales/60002.lzma,cuerpo_monstruo,dragon_decay,2
    """
    if not path:
        return {}
    if not path.exists():
        raise SystemExit(f"No existe el manifest: {path}")

    out: Dict[str, ManifestItem] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ruta = (row.get("ruta") or row.get("archivo") or "").replace("\\", "/").strip().lower()
            if not ruta:
                continue
            orden_raw = (row.get("orden") or "").strip()
            out[ruta] = ManifestItem(
                categoria=(row.get("categoria") or "auto").strip().lower(),
                grupo=(row.get("grupo") or "").strip(),
                orden=int(orden_raw) if orden_raw.isdigit() else None,
            )
    return out


def buscar_manifest(rel: Path, input_root: Path, manifest: Dict[str, ManifestItem]) -> Optional[ManifestItem]:
    rel_key = normalizar_clave(rel)
    full_like = normalizar_clave(input_root.name / rel)
    return manifest.get(rel_key) or manifest.get(full_like)


def numero_final(path: Path) -> Optional[int]:
    nums = re.findall(r"\d+", path.stem)
    if not nums:
        return None
    return int(nums[-1])


def alpha_bbox(img: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    rgba = img.convert("RGBA")
    return rgba.getchannel("A").getbbox()


def reforzar_contorno(rgba: Image.Image, color=(18, 16, 20, 255), intensidad: float = 0.55) -> Image.Image:
    """
    Refuerza bordes internos sin expandir demasiado la silueta.
    Útil para objetos y armas; NO se usa en texturas tileables.
    """
    rgba = rgba.convert("RGBA")
    alpha = rgba.getchannel("A")
    borde = alpha.filter(ImageFilter.FIND_EDGES)
    pix = rgba.load()
    bpix = borde.load()
    w, h = rgba.size

    for y in range(h):
        for x in range(w):
            if pix[x, y][3] == 0:
                continue
            if bpix[x, y] > 0:
                r, g, b, a = pix[x, y]
                nr = int(r * (1 - intensidad) + color[0] * intensidad)
                ng = int(g * (1 - intensidad) + color[1] * intensidad)
                nb = int(b * (1 - intensidad) + color[2] * intensidad)
                pix[x, y] = (nr, ng, nb, a)
    return rgba


def ajustar_rgba(rgba: Image.Image, contraste: float, color: float, brillo: float, nitidez: float) -> Image.Image:
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(contraste)
    rgb = ImageEnhance.Color(rgb).enhance(color)
    rgb = ImageEnhance.Brightness(rgb).enhance(brillo)
    rgb = ImageEnhance.Sharpness(rgb).enhance(nitidez)
    return Image.merge("RGBA", (*rgb.split(), alpha))


def oscurecer_por_descomposicion(rgba: Image.Image, orden: Optional[int]) -> Image.Image:
    """
    Para cadáveres/descomposición: conserva la progresión visual.
    Si hay orden en manifest o número final, los frames posteriores quedan levemente más apagados.
    No cambia dimensiones ni transparencia.
    """
    if orden is None:
        return ajustar_rgba(rgba, contraste=1.07, color=0.96, brillo=0.98, nitidez=1.12)

    paso = max(0, min(orden - 1, 8))
    brillo = 1.00 - (paso * 0.018)
    color = 0.98 - (paso * 0.025)
    contraste = 1.06 + (paso * 0.01)
    out = ajustar_rgba(rgba, contraste=contraste, color=max(0.72, color), brillo=max(0.78, brillo), nitidez=1.10)
    return out


def mejorar_imagen(img: Image.Image, perfil: str, rel: Path, manifest_item: Optional[ManifestItem]) -> Image.Image:
    """
    Mejora visual conservadora por perfil.
    - No redimensiona.
    - No recorta.
    - No altera el canal alfa salvo que el formato original no tenga alfa.
    - Para texturas/suelo evita contornos fuertes para no romper tiling.
    """
    rgba = img.convert("RGBA")
    orden = manifest_item.orden if manifest_item else numero_final(rel)

    if perfil in {"textura_suelo", "textura", "suelo", "ground"}:
        return ajustar_rgba(rgba, contraste=1.05, color=1.03, brillo=1.01, nitidez=1.06)

    if perfil in {"valla_estructura", "estructura", "valla", "cerca", "muro"}:
        out = ajustar_rgba(rgba, contraste=1.12, color=1.05, brillo=1.00, nitidez=1.18)
        return reforzar_contorno(out, intensidad=0.42)

    if perfil in {"cuerpo_monstruo", "cadaver", "corpse", "decay", "monstruo_decay"}:
        out = oscurecer_por_descomposicion(rgba, orden)
        return reforzar_contorno(out, intensidad=0.30)

    if perfil in {"arma", "weapon", "objeto_ataque", "ataque"}:
        out = ajustar_rgba(rgba, contraste=1.16, color=1.08, brillo=1.02, nitidez=1.22)
        return reforzar_contorno(out, intensidad=0.48)

    if perfil in {"efecto", "magic", "hechizo"}:
        return ajustar_rgba(rgba, contraste=1.10, color=1.16, brillo=1.03, nitidez=1.10)

    out = ajustar_rgba(rgba, contraste=1.09, color=1.04, brillo=1.00, nitidez=1.14)
    return reforzar_contorno(out, intensidad=0.36)


def imagen_a_bytes(img: Image.Image, formato_original: Optional[str]) -> bytes:
    buffer = io.BytesIO()
    fmt = (formato_original or "PNG").upper()

    if fmt not in EXTENSIONES_IMAGEN_SOPORTADAS:
        fmt = "PNG"
    if fmt == "JPG":
        fmt = "JPEG"

    if fmt == "JPEG":
        img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=95, optimize=True)
    elif fmt == "PNG":
        img.save(buffer, format="PNG", optimize=True)
    else:
        img.save(buffer, format=fmt)

    return buffer.getvalue()


def procesar_archivo(
    src: Path,
    input_root: Path,
    output_root: Path,
    backup_root: Optional[Path],
    manifest: Dict[str, ManifestItem],
    dry_run: bool,
) -> Resultado:
    rel = src.relative_to(input_root)
    dst = output_root / rel
    manifest_item = buscar_manifest(rel, input_root, manifest)
    perfil = inferir_perfil(rel, manifest_item)

    try:
        raw = leer_lzma(src)
        img = intentar_abrir_imagen(raw)

        if img is None:
            if not dry_run:
                if backup_root:
                    backup_path = backup_root / rel
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, backup_path)
                escribir_lzma(dst, raw)
            return Resultado(str(rel), "conservado", "binario", perfil, "No es imagen reconocible; contenido intacto")

        formato = img.format
        bbox = alpha_bbox(img)
        detalle_base = f"{img.size[0]}x{img.size[1]} formato={formato or 'desconocido'} bbox={bbox}"

        if not dry_run:
            if backup_root:
                backup_path = backup_root / rel
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, backup_path)

            mejorada = mejorar_imagen(img, perfil, rel, manifest_item)
            nuevo_raw = imagen_a_bytes(mejorada, formato)
            escribir_lzma(dst, nuevo_raw)

        return Resultado(str(rel), "mejorado" if not dry_run else "analizado", f"imagen/{formato or 'desconocido'}", perfil, detalle_base)

    except Exception as exc:
        return Resultado(str(rel), "error", "desconocido", perfil, repr(exc))


def escribir_reporte(path: Path, resultados: Iterable[Resultado]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["archivo", "estado", "tipo", "perfil", "detalle"])
        for r in resultados:
            writer.writerow([r.archivo, r.estado, r.tipo, r.perfil, r.detalle])


def main() -> None:
    parser = argparse.ArgumentParser(description="Procesa assets .lzma sin alterar lógica de servidor.")
    parser.add_argument("--input", required=True, help="Carpeta con .lzma originales")
    parser.add_argument("--output", required=True, help="Carpeta de salida con .lzma procesados")
    parser.add_argument("--backup", default=None, help="Carpeta opcional de respaldo")
    parser.add_argument("--reports", default="reportes", help="Carpeta de reportes CSV")
    parser.add_argument("--manifest", default=None, help="CSV opcional: ruta,categoria,grupo,orden")
    parser.add_argument("--dry-run", action="store_true", help="Analiza sin escribir archivos de salida")
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
        resultado = procesar_archivo(archivo, input_root, output_root, backup_root, manifest, args.dry_run)
        resultados.append(resultado)
        print(f"[{i}/{len(archivos)}] {resultado.estado}: {resultado.archivo} ({resultado.perfil})")

    escribir_reporte(reports_root / "procesamiento_lzma.csv", resultados)

    total = len(resultados)
    mejorados = sum(1 for r in resultados if r.estado == "mejorado")
    analizados = sum(1 for r in resultados if r.estado == "analizado")
    conservados = sum(1 for r in resultados if r.estado == "conservado")
    errores = sum(1 for r in resultados if r.estado == "error")

    print("\nResumen")
    print(f"Total: {total}")
    print(f"Mejorados visualmente: {mejorados}")
    print(f"Analizados dry-run: {analizados}")
    print(f"Conservados intactos: {conservados}")
    print(f"Errores: {errores}")
    print(f"Reporte: {reports_root / 'procesamiento_lzma.csv'}")


if __name__ == "__main__":
    main()
