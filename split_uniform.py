"""Claude V761 'split' pass: replace the non-uniform bindless-CBV indexing of the logical-wave32 overrides with two
subgroup-uniform accesses (the physical 64-lane subgroup holds exactly two logical 32-lane waves whose cout-group
values are lo and lo+1), selecting per lane. Same idea as HelixSR's own HX_W64 WAVE_DECL. Avoids the NonUniform
decoration, which made Turnip take a slow per-lane path (encoder 13 ms -> 40 ms per frame).

Precondition (validated on the Odin): physical subgroup = 64 lanes, linear lane mapping, 32-aligned partitions.
Input: the any-wave logical32 module (out-anywave). Original module is used to find the BroadcastFirst results.
usage: split_uniform.py <original.spv> <anywave.spv> <out.spv>
"""
import sys
from pathlib import Path
import importlib.util

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('t', HERE / 'wave32.py'); t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
ENUM, inst, name, rid = t.ENUM, t.inst, t.name, t.result_id


def run(original, anywave):
    _, orig = t.parse(original)
    bvals = [i[2] for i in orig if name(i) == 'OpGroupNonUniformBroadcastFirst']
    header, ins = t.parse(anywave)
    defs = {rid(i): i for i in ins if rid(i) is not None}
    next_id = header[3]
    def alloc():
        nonlocal next_id
        r = next_id; next_id += 1; return r
    globals_new = []
    def type_find(n, tail):
        for r, i in list(defs.items()):
            if name(i) == n and i[2:] == list(tail): return r
        r = alloc(); i = inst(n, r, *tail); globals_new.append(i); defs[r] = i; return r
    def const(ty, v):
        for r, i in list(defs.items()):
            if name(i) == 'OpConstant' and i[1] == ty and i[3:] == [v]: return r
        r = alloc(); i = inst('OpConstant', ty, r, v); globals_new.append(i); defs[r] = i; return r
    u32 = type_find('OpTypeInt', (32, 0)); boolean = type_find('OpTypeBool', ())
    one = const(u32, 1); scope = const(u32, ENUM['Scope']['Subgroup'])
    # descriptor runtime arrays
    arrays = set()
    for i in ins:
        if name(i) == 'OpVariable':
            ptr = defs.get(i[1]); pt = defs.get(ptr[3]) if ptr is not None else None
            if pt is not None and name(pt) == 'OpTypeRuntimeArray':
                arrays.add(i[2])
    # index ids of chains into descriptor arrays, and their (pc, B) operands
    chain_index = {}
    for i in ins:
        if name(i) in ('OpAccessChain', 'OpInBoundsAccessChain') and i[3] in arrays:
            idx = defs[i[4]]
            assert name(idx) == 'OpIAdd', 'descriptor index is not pc + B'
            ops = idx[3:5]; b = [o for o in ops if o in bvals]; pc = [o for o in ops if o not in bvals]
            assert len(b) == 1 and len(pc) == 1, 'descriptor index does not use a broadcast value'
            chain_index[i[2]] = (idx[2], pc[0], b[0])
    if not chain_index:
        return anywave, {'split_chains': 0}
    per_b = {}   # B -> (lo, hi, isHi)
    vec_cond = {}
    out = []; tainted = {}   # original pointer id -> (lo id, hi id)
    replaced_loads = 0; removed = set(chain_index)
    bool_vec = {}
    for i in ins:
        n = name(i); r = rid(i)
        if r in removed:          # the original non-uniform chain: replaced by lo/hi chains
            idx_id, pc, b = chain_index[r]; lo, hi, _ = per_b[b]
            ilo, ihi, clo, chi = alloc(), alloc(), alloc(), alloc()
            out += [inst('OpIAdd', u32, ilo, pc, lo), inst('OpIAdd', u32, ihi, pc, hi),
                    inst(n, i[1], clo, i[3], ilo, *i[5:]), inst(n, i[1], chi, i[3], ihi, *i[5:])]
            tainted[r] = (clo, chi); continue
        if n in ('OpAccessChain', 'OpInBoundsAccessChain') and i[3] in tainted:
            blo, bhi = tainted[i[3]]; clo, chi = alloc(), alloc()
            out += [inst(n, i[1], clo, blo, *i[4:]), inst(n, i[1], chi, bhi, *i[4:])]
            tainted[r] = (clo, chi); continue
        if n == 'OpLoad' and i[3] in tainted:
            plo, phi = tainted[i[3]]; llo, lhi = alloc(), alloc()
            ty = i[1]; tdef = defs[ty]
            # find which B this load derives from: walk back via tainted chain roots (single B per module here)
            b = next(iter(per_b)); lo, hi, ishi = per_b[b]
            cond = ishi
            if name(tdef) == 'OpTypeVector':
                key = (b, tdef[3])
                if key not in vec_cond:
                    bv = type_find('OpTypeVector', (boolean, tdef[3]))
                    vec_cond[key] = (bv, alloc(), True)
                cond = vec_cond[key][1]
            extra = i[4:]
            out += [inst('OpLoad', ty, llo, plo, *extra), inst('OpLoad', ty, lhi, phi, *extra),
                    inst('OpSelect', ty, r, cond, lhi, llo)]
            replaced_loads += 1; continue
        out.append(i)
        if r in bvals:            # right after B is defined: lo = BroadcastFirst(B), hi = lo + 1, isHi = B != lo
            lo, hi, ishi = alloc(), alloc(), alloc()
            out += [inst('OpGroupNonUniformBroadcastFirst', u32, lo, scope, r), inst('OpIAdd', u32, hi, lo, one),
                    inst('OpINotEqual', boolean, ishi, r, lo)]
            per_b[r] = (lo, hi, ishi)
    assert len(per_b) == 1, 'expected one broadcast value per module'
    # vector conditions: insert right after isHi
    if vec_cond:
        b = next(iter(per_b)); ishi = per_b[b][2]
        pos = next(j for j, i in enumerate(out) if rid(i) == ishi) + 1
        out[pos:pos] = [inst('OpCompositeConstruct', bv, cid, *([ishi] * defs[bv][3])) for (bb, n_), (bv, cid, _) in vec_cond.items()]
    # place new globals before the first function
    fpos = next(j for j, i in enumerate(out) if name(i) == 'OpFunction')
    out[fpos:fpos] = globals_new
    assert not any(name(i) == 'OpDecorate' and i[2] == ENUM['Decoration']['NonUniform'] for i in out)
    header[3] = next_id
    data = t.encode(header, out); t.parse(data)
    return data, {'split_chains': len(chain_index), 'loads_split': replaced_loads, 'vector_conditions': len(vec_cond)}


if __name__ == '__main__':
    data, meta = run(Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]).read_bytes())
    Path(sys.argv[3]).write_bytes(data)
    print(Path(sys.argv[3]).name, meta)
