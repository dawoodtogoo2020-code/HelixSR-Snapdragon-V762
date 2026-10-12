"""Build the four V762 overrides from locally supplied raw-ID SPIR-V assembly.

This tool has no game, device, provider, or download access. All outputs are
written only after every input and transformed module passes validation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

import wave32
import split_uniform

STEMS = ('1ad412dd861a9c17', '22397a04d7ae4cf4',
         '649a6778b026d390', '6883f2d69d8704f2')
MAX_ASM = 2_000_000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def tool(command):
    result = subprocess.run(command, shell=False, capture_output=True, text=True,
                            timeout=120, check=False)
    require(result.returncode == 0,
            f"tool failed ({result.returncode}): {Path(command[0]).name}: {result.stderr[:1500]}")
    return result


def recover_original_header(assembly, assembled):
    """spirv-as rewrites generator and bound; recover them from raw-ID disassembly."""
    text = assembly.decode('utf-8')
    head = '\n'.join(text.splitlines()[:6])
    require(re.search(r'^; Version: 1\.3$', head, re.M), 'SPIR-V 1.3 required')
    generator = re.search(r'^; Generator: Unknown\((\d+)\); (\d+)$', head, re.M)
    bound = re.search(r'^; Bound: (\d+)$', head, re.M)
    require(generator and bound and re.search(r'^; Schema: 0$', head, re.M),
            'raw disassembly header missing')
    require(not re.search(r'%[A-Za-z_]', text),
            'raw numeric IDs required: use spirv-dis --raw-id')
    vendor, version, original_bound = int(generator[1]), int(generator[2]), int(bound[1])
    require(vendor < 65536 and version < 65536 and original_bound < 1_000_000,
            'header field outside allowed range')
    data = bytearray(assembled)
    _, spv_version, _, assembled_bound, schema = struct.unpack_from('<5I', data)
    require(spv_version == 0x10300 and schema == 0 and original_bound >= assembled_bound,
            'assembly/version/bound mismatch')
    struct.pack_into('<II', data, 8, (vendor << 16) | version, original_bound)
    return bytes(data)


def build(manifest_path, assembly_dir, output_dir, assembler, validator):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    require(manifest.get('schema') == 1 and manifest.get('physical_subgroup_size') == 64,
            'manifest requires physical subgroup size 64')
    entries = manifest.get('entries')
    require(isinstance(entries, list) and len(entries) == 4 and
            tuple(x.get('shader_hash') for x in entries) == STEMS,
            'manifest must list exactly the four V762 shaders in order')
    require(assembly_dir.is_dir() and not assembly_dir.is_symlink(), 'invalid assembly directory')
    require(not output_dir.exists() and output_dir.parent.is_dir(),
            'output directory must be new, with existing parent')
    require(assembler.is_file() and validator.is_file(), 'assembler and validator required')
    inputs = []
    for item in entries:
        name = item['shader_hash'] + '.spvasm'
        path = assembly_dir / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_ASM,
                f'missing or unsafe input: {name}')
        data = path.read_bytes()
        require(digest(data) == item['assembly_sha256'], f'assembly pin mismatch: {name}')
        inputs.append((item, data, path))
    outputs = []
    with tempfile.TemporaryDirectory(prefix='v762-', dir=output_dir.parent) as temporary:
        scratch = Path(temporary)
        for item, assembly, source in inputs:
            stem = item['shader_hash']
            assembled_path = scratch / (stem + '.assembled.spv')
            tool([str(assembler), '--preserve-numeric-ids', '--target-env', 'spv1.3',
                  '-o', str(assembled_path), str(source)])
            original = recover_original_header(assembly, assembled_path.read_bytes())
            require(digest(original) == item['input_spv_sha256'],
                    f'converted input hash mismatch: {stem}')
            original_path = scratch / (stem + '.input.spv')
            original_path.write_bytes(original)
            tool([str(validator), '--target-env', 'spv1.3', str(original_path)])
            logical, wave_meta = wave32.transform(original)
            final, split_meta = split_uniform.run(original, logical)
            minimum = item['min_split_chains']
            require(minimum in (0, 1) and split_meta['split_chains'] >= minimum and
                    (minimum == 0 or split_meta['loads_split'] > 0),
                    f'CBV split count mismatch: {stem}')
            require(digest(final) == item['output_spv_sha256'],
                    f'V762 output hash mismatch: {stem}')
            final_path = scratch / (stem + '.final.spv')
            final_path.write_bytes(final)
            tool([str(validator), '--target-env', 'spv1.3', str(final_path)])
            outputs.append((stem, final, wave_meta, split_meta))
    output_dir.mkdir()
    receipt = {'schema': 1, 'status': 'four_outputs_hash_matched_and_spirv_validated',
               'physical_subgroup_size_required': 64, 'runtime_tested_here': False,
               'assembler_command': 'spirv-as --preserve-numeric-ids --target-env spv1.3',
               'validator_command': 'spirv-val --target-env spv1.3',
               'modules': []}
    for stem, final, wave_meta, split_meta in outputs:
        (output_dir / (stem + '.spv')).write_bytes(final)
        receipt['modules'].append({'shader_hash': stem, 'sha256': digest(final),
                                   'logical32': wave_meta, 'split_uniform': split_meta})
    (output_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--assembly-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--spirv-as', type=Path, required=True)
    parser.add_argument('--spirv-val', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.manifest, args.assembly_dir, args.output_dir,
                       args.spirv_as, args.spirv_val)
        print(json.dumps({'status': result['status'],
                          'hashes': [x['sha256'] for x in result['modules']]}))
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError, UnicodeError) as error:
        parser.exit(2, f'Rejected: {error}\n')
