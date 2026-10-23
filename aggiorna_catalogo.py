#!/usr/bin/env python3
"""Aggiorna il catalogo di un disco senza modificare i file del disco stesso.

Esempi:
  py -3 aggiorna_catalogo.py --disco "E:\\" --nome Rufy
  python3 aggiorna_catalogo.py --disco /Volumes/Archivio --nome Archivio
  py -3 aggiorna_catalogo.py --rigenera-manifest

Le dimensioni sono in GiB (etichettati GB per compatibilità con i JSON originali).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
MANIFEST = DATA / 'manifest.json'
EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.mpg', '.mpeg',
              '.ts', '.m2ts', '.webm', '.flv', '.vob', '.3gp', '.divx', '.iso'}
SKIP_DIRS = {'$RECYCLE.BIN', 'System Volume Information', '.Trashes',
             '.Trash-1000', '.Spotlight-V100', '.fseventsd', '.git', 'node_modules'}


def read_manifest():
    if MANIFEST.exists():
        try:
            data = json.loads(MANIFEST.read_text(encoding='utf-8'))
            return {str(e['nome']): str(e['file']) for e in data.get('dischi', [])
                    if isinstance(e, dict) and 'nome' in e and 'file' in e}
        except (OSError, ValueError, TypeError):
            pass
    return {}


def safe_file_name(name):
    cleaned = re.sub(r'[^\w.\-]+', '_', name, flags=re.UNICODE).strip('._')
    if not cleaned or cleaned in ('.', '..'):
        raise ValueError('Il nome del disco non è valido')
    return cleaned + '.json'


def save_json_atomic(path: Path, payload):
    tmp = path.with_suffix(path.suffix + '.tmp')
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def rebuild_manifest(entries):
    DATA.mkdir(exist_ok=True)
    for p in sorted(DATA.glob('*.json')):
        if p.name.lower() == 'manifest.json':
            continue
        if not any(f.lower() == p.name.lower() for f in entries.values()):
            entries[p.stem] = p.name
    entries = {name: file for name, file in entries.items()
               if (DATA / file).is_file() and Path(file).name == file and file.lower().endswith('.json')}
    save_json_atomic(MANIFEST, {'version': 2,
                                'dischi': [{'nome': name, 'file': filename} for name, filename
                                           in sorted(entries.items(), key=lambda v: v[0].casefold())]})
    print(f'Manifest rigenerato: {len(entries)} hard disk in {MANIFEST}')


def resolve_catalog_name(requested, entries):
    for old in entries:
        if old.casefold() == requested.casefold():
            return old, entries[old]
    for old, filename in entries.items():
        if Path(filename).stem.casefold() == requested.casefold():
            return old, filename
    candidate = safe_file_name(requested)
    reserved = {n.casefold() for n in entries.values()}
    if candidate.casefold() in reserved:
        raise ValueError('Nome file già usato: scegli un altro nome per il disco.')
    return requested, candidate


def scan_disk(path: Path, name: str, manifest):
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f'Il disco non è accessibile: {path}')
    catalog_name, filename = resolve_catalog_name(name, manifest)
    print(f'\nScansione di {path} come «{catalog_name}»…', flush=True)
    catalog = []
    errors = 0

    def onerror(exc):
        nonlocal errors
        errors += 1
        print(f'  Avviso: cartella non accessibile: {exc}', file=sys.stderr)

    for current, dirs, filenames in os.walk(path, topdown=True, followlinks=False, onerror=onerror):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not Path(current, d).is_symlink()]
        for filename_part in filenames:
            if Path(filename_part).suffix.lower() not in EXTENSIONS:
                continue
            full = Path(current, filename_part)
            try:
                if full.is_symlink():
                    continue
                size = full.stat().st_size / (1024 ** 3)
                catalog.append({'nome': filename_part,
                                'dimensione': f'{size:.2f} GB',
                                'percorso_completo': str(full)})
            except OSError as exc:
                errors += 1
                print(f'  Avviso: file non leggibile: {full} ({exc})', file=sys.stderr)
    catalog.sort(key=lambda f: f['percorso_completo'].casefold())
    disk = shutil.disk_usage(path)
    info = {'totale': f'{disk.total / 1024 ** 3:.2f} GB',
            'libero': f'{disk.free / 1024 ** 3:.2f} GB'}
    save_json_atomic(DATA / filename, {'catalogo_video': catalog, 'informazioni_disco': info})
    manifest[catalog_name] = filename
    print(f'Completato: {len(catalog)} video · {info["libero"]} liberi · {errors} avvisi')
    return len(catalog)


def windows_drives():
    if os.name != 'nt':
        raise RuntimeError('--tutti è disponibile soltanto in Windows.')
    disks = []
    system = os.environ.get('SystemDrive', 'C:').upper()
    for letter in string.ascii_uppercase:
        path = f'{letter}:\\'
        if not os.path.isdir(path) or path[:2].upper() == system:
            continue
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(path)
        if drive_type not in (2, 3):
            continue
        label = ctypes.create_unicode_buffer(256)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(path), label, len(label), None, None, None, None, 0)
        name = label.value.strip() if ok and label.value.strip() else f'Disco_{letter}'
        disks.append((Path(path), name))
    return disks


def main():
    parser = argparse.ArgumentParser(description='Aggiorna i JSON del catalogo Vault senza modificare gli HDD.')
    parser.add_argument('--disco', help='Percorso del disco o della cartella da indicizzare')
    parser.add_argument('--nome', help='Nome del disco da mostrare nella web app')
    parser.add_argument('--tutti', action='store_true', help='Scansiona i volumi Windows accessibili, escluso quello di sistema')
    parser.add_argument('--rigenera-manifest', action='store_true', help='Rigenera il solo elenco JSON')
    args = parser.parse_args()
    if sum([bool(args.disco), args.tutti, args.rigenera_manifest]) != 1:
        parser.error('Scegli una sola azione: --disco, --tutti oppure --rigenera-manifest')
    manifest = read_manifest()
    try:
        if args.disco:
            root = Path(args.disco).expanduser().resolve()
            name = args.nome or root.name or 'Archivio'
            scan_disk(root, name, manifest)
        elif args.tutti:
            drives = windows_drives()
            if not drives:
                print('Nessun disco esterno o secondario accessibile trovato.')
            for root, name in drives:
                try:
                    scan_disk(root, name, manifest)
                except OSError as exc:
                    print(f'Errore su {root}: {exc}', file=sys.stderr)
        rebuild_manifest(manifest)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'ERRORE: {exc}', file=sys.stderr)
        return 1
    print('Puoi ora ricaricare la pagina e usare il pulsante Aggiorna (↻).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
