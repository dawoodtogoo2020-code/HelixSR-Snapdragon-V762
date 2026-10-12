"""Check four local vkd3d shader dumps and disassemble them for build.py.

This program does not extract shaders from a provider DLL. The user supplies
runtime dumps produced privately from their own HelixSR installation.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare(manifest_path, dump_dir, output_dir, spirv_dis):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    entries = manifest['entries']
    if len(entries) != 4 or len({item['shader_hash'] for item in entries}) != 4:
        raise ValueError('manifest must pin exactly four distinct shader hashes')
    if output_dir.exists():
        raise ValueError(f'output directory already exists: {output_dir}')
    if not spirv_dis.is_file():
        raise ValueError(f'spirv-dis is missing: {spirv_dis}')
    for item in entries:
        source = dump_dir / (item['shader_hash'] + '.spv')
        if not source.is_file():
            raise ValueError(f'missing runtime dump: {source.name}')
        actual = sha256(source)
        if actual != item['input_spv_sha256']:
            raise ValueError(f'input SHA-256 mismatch: {source.name}: {actual}')
    output_dir.mkdir(parents=True)
    results = []
    for item in entries:
        name = item['shader_hash']
        source = dump_dir / (name + '.spv')
        target = output_dir / (name + '.spvasm')
        subprocess.run([str(spirv_dis), '--raw-id', '-o', str(target), str(source)], check=True)
        actual = sha256(target)
        if actual != item['assembly_sha256']:
            raise ValueError(f'assembly SHA-256 mismatch: {target.name}: {actual}')
        results.append({'shader_hash': name, 'input_sha256': item['input_spv_sha256'],
                        'assembly_sha256': actual})
    print(json.dumps({'status': 'four_pinned_dumps_disassembled', 'entries': results}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--dump-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--spirv-dis', type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.manifest, args.dump_dir, args.output_dir, args.spirv_dis)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
