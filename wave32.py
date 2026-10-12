"""Original, narrowly scoped logical-Wave32 SPIR-V transformation prototype.

No GPU execution, runtime override installation, or private-source conversion.
Independent spirv-val acceptance and physical subgroup64 are mandatory externally.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent
GRAMMAR_PIN = '048ead37f9bf16613b54a1cae0f671007bd032cbf0026a21c620dcec8ba70e83'
INVENTORY_PIN = 'c7c9e48729feb56d55dc67ae33f907aad35384d99771c822a467dd4ca0220b0f'
MAX_BYTES = 8 * 1024 * 1024

class Reject(ValueError): pass
def need(condition, reason):
    if not condition: raise Reject(reason)
def sha(data): return hashlib.sha256(data).hexdigest()
def pinned_grammar():
    data = (ROOT / 'spirv.core.grammar.json').read_bytes()
    need(sha(data) == GRAMMAR_PIN, 'grammar pin mismatch')
    g = json.loads(data)
    op = {x['opname']:x['opcode'] for x in g['instructions']}
    info = {x['opcode']:x for x in g['instructions']}
    enums = {x['kind']:{v['enumerant']:int(v['value'],0) if isinstance(v['value'],str) else v['value']
                       for v in x.get('enumerants',[])} for x in g['operand_kinds']}
    return op, info, enums
OP, INFO, ENUM = pinned_grammar()
def inst(name, *args): return [((len(args)+1)<<16)|OP[name], *args]
def args(i): return i[1:]
def name(i): return INFO[i[0]&65535]['opname']
def result_id(i):
    for n, operand in enumerate(INFO[i[0]&65535].get('operands',[])):
        if operand['kind']=='IdResult': return i[n+1]
    return None
def parse(data):
    need(20 <= len(data) <= MAX_BYTES and len(data)%4==0, 'invalid module size')
    words = list(struct.unpack('<'+'I'*(len(data)//4),data))
    need(words[0]==0x07230203 and words[4]==0, 'invalid SPIR-V header')
    need(0x10300 <= words[1] <= 0x10600, 'requires SPIR-V 1.3 through 1.6')
    need(1 < words[3] < 1000000, 'ID bound outside limit')
    out, ids, p = [], set(), 5
    while p < len(words):
        count, opcode = words[p]>>16, words[p]&65535
        need(count and p+count<=len(words) and opcode in INFO, 'unknown/truncated instruction')
        i = words[p:p+count]
        minimum = 1 + sum(x.get('quantifier') not in ('?','*') for x in INFO[opcode].get('operands',[]))
        need(count>=minimum,'instruction shorter than grammar operands')
        # Grammar-known opcode is necessary but not a complete standards validation.
        rid = result_id(i)
        if rid is not None:
            need(0 < rid < words[3] and rid not in ids, 'duplicate/out-of-bound result ID')
            ids.add(rid)
        out.append(i); p+=count
    need(p==len(words) and len(out)<200000, 'instruction limit')
    return words[:5], out
def encode(header, instructions):
    words = header + [x for i in instructions for x in i]
    need(len(words)*4<=MAX_BYTES, 'output size limit')
    return struct.pack('<'+'I'*len(words),*words)

def transform(data):
    header, original = parse(data)
    definitions = {result_id(i):i for i in original if result_id(i) is not None}
    entries = [i for i in original if name(i)=='OpEntryPoint']
    need(len(entries)==1 and entries[0][1]==ENUM['ExecutionModel']['GLCompute'], 'one compute entry required')
    need(sum(name(i)=='OpMemoryModel' for i in original)==1, 'memory model missing/duplicate')
    need(any(name(i)=='OpExecutionMode' and i[2]==ENUM['ExecutionMode']['LocalSize'] for i in original), 'literal LocalSize required')
    allowed = {'OpGroupNonUniformBroadcastFirst','OpGroupNonUniformBroadcast','OpGroupNonUniformShuffle'}
    preserved = {'OpGroupNonUniformShuffleXor','OpGroupNonUniformQuadSwap'}
    for i in original:
        n=name(i)
        if INFO[i[0]&65535]['class'] in ('Group','Non-Uniform') or n.startswith('OpSubgroup'):
            need(n in allowed|preserved, 'unsupported subgroup operation '+n)
        if n in ('OpExecutionMode','OpExecutionModeId'):
            need(i[2] not in {ENUM['ExecutionMode'].get(x) for x in
                 ('SubgroupSize','SubgroupsPerWorkgroup','SubgroupsPerWorkgroupId')}, 'fixed subgroup execution mode requires separate review')
    builtin = {}
    relevant = {v:k for k,v in ENUM['BuiltIn'].items() if k.startswith('Subgroup') or k=='NumSubgroups'}
    for i in original:
        if name(i)=='OpMemberDecorate' and i[3]==ENUM['Decoration']['BuiltIn']:
            need(i[4] not in relevant, 'member-decorated subgroup builtin unsupported')
        if name(i)=='OpDecorate' and i[2]==ENUM['Decoration']['BuiltIn'] and i[3] in relevant:
            b=relevant[i[3]]
            need(b in ('SubgroupLocalInvocationId','SubgroupSize'), 'unsupported subgroup builtin '+b)
            need(b not in builtin, 'duplicate subgroup builtin')
            builtin[b]=i[1]
    additions, decorations, capabilities = [], [], []
    next_id=header[3]
    def alloc():
        nonlocal next_id
        need(next_id<1000000,'generated ID bound')
        r=next_id;next_id+=1;return r
    def global_inst(n,*a):
        i=inst(n,*a);additions.append(i);definitions[result_id(i)]=i;return result_id(i)
    def type_find(n, tail):
        for rid,i in definitions.items():
            if name(i)==n and i[2:]==list(tail): return rid
        return global_inst(n,alloc(),*tail)
    u32=type_find('OpTypeInt',(32,0)); boolean=type_find('OpTypeBool',())
    vec4=type_find('OpTypeVector',(u32,4))
    def constant(v):
        for rid,i in definitions.items():
            if name(i)=='OpConstant' and i[1]==u32 and i[3:]==[v]:return rid
        return global_inst('OpConstant',u32,alloc(),v)
    zero,c31,c32,scope=map(constant,(0,31,32,ENUM['Scope']['Subgroup']))
    cmask,c1,c2,c3,c5=map(constant,(0xFFFFFFE0,1,2,3,5))
    true_id=next((rid for rid,i in definitions.items() if name(i)=='OpConstantTrue' and i[1]==boolean),None)
    if true_id is None:true_id=global_inst('OpConstantTrue',boolean,alloc())
    lane=builtin.get('SubgroupLocalInvocationId')
    new_lane=lane is None
    if new_lane:
        ptr=type_find('OpTypePointer',(ENUM['StorageClass']['Input'],u32))
        lane=global_inst('OpVariable',ptr,alloc(),ENUM['StorageClass']['Input'])
        decorations.append(inst('OpDecorate',lane,ENUM['Decoration']['BuiltIn'],ENUM['BuiltIn']['SubgroupLocalInvocationId']))
    for b,var in builtin.items():
        v=definitions.get(var)
        need(v is not None and name(v)=='OpVariable' and v[3]==ENUM['StorageClass']['Input'], 'invalid subgroup builtin variable')
        ptr=definitions.get(v[1])
        need(ptr is not None and name(ptr)=='OpTypePointer' and ptr[3]==u32, 'subgroup builtin must be uint32 scalar')
        # Reject aliases/escape uses; this implementation only rewrites direct loads.
        for i in original:
            if name(i) in ('OpAccessChain','OpInBoundsAccessChain','OpPtrAccessChain','OpCopyObject','OpStore','OpFunctionCall'):
                need(var not in i[1:], 'subgroup builtin pointer escapes direct-load scope')
    def emit(seq,n,*a):
        r=alloc();seq.append(inst(n,*a[:1],r,*a[1:]));return r
    def raw_lane(seq):return emit(seq,'OpLoad',u32,lane)
    def half_base(seq):return emit(seq,'OpBitwiseAnd',u32,raw_lane(seq),cmask)
    def scope_check(i):
        d=definitions.get(i[3]);need(d is not None and name(d)=='OpConstant' and d[1]==u32 and d[3:]==[3], 'only constant Subgroup scope allowed')
    def shape(typ):
        t=definitions.get(typ);need(t is not None,'unknown collective type')
        if name(t)=='OpTypeVector':
            need(t[3] in (2,3,4),'unsupported vector shape');scalar=t[2]
        else:scalar=typ
        s=definitions.get(scalar)
        need(s is not None and name(s) in ('OpTypeInt','OpTypeFloat','OpTypeBool'),'unsupported collective type')
        return t
    changed, counts = [], {'broadcast_first':0,'lane_read':0,'lane_index':0,'lane_size':0,
                           'preserved_xor4':0,'preserved_horizontal_quad_swap':0}
    for i in original:
        n=name(i);a=args(i)
        if n=='OpLoad' and i[3] in builtin.values():
            need(i[1]==u32,'wrong builtin load type')
            if i[3]==builtin.get('SubgroupLocalInvocationId'):
                raw=alloc();changed.append([i[0],i[1],raw,*i[3:]])
                changed.append(inst('OpBitwiseAnd',u32,i[2],raw,c31));counts['lane_index']+=1
            else:
                need(len(i)==4,'memory-qualified subgroup-size load unsupported')
                changed.append(inst('OpCopyObject',u32,i[2],c32));counts['lane_size']+=1
        elif n in preserved:
            need(len(i)==6,'malformed preserved subgroup operation');scope_check(i);shape(i[1])
            target=definitions.get(i[5])
            expected=4 if n=='OpGroupNonUniformShuffleXor' else 0
            need(target is not None and name(target)=='OpConstant' and target[1]==u32 and target[3:]==[expected],
                 'only constant XOR4 / horizontal QuadSwap supported')
            changed.append(i)
            counts['preserved_xor4' if expected==4 else 'preserved_horizontal_quad_swap']+=1
        elif n in allowed:
            scope_check(i);typ,rid,sc,value=i[1:5];shape(typ)
            seq=[];base=half_base(seq)
            if n=='OpGroupNonUniformBroadcastFirst':
                need(len(i)==5,'malformed BroadcastFirst')
                ballot=emit(seq,'OpGroupNonUniformBallot',vec4,scope,true_id)
                word=emit(seq,'OpShiftRightLogical',u32,base,c5)
                ms=[]
                for k,ck in enumerate((zero,c1,c2,c3)):
                    comp=emit(seq,'OpCompositeExtract',u32,ballot,k)
                    here=emit(seq,'OpIEqual',boolean,word,ck)
                    ms.append(emit(seq,'OpSelect',u32,here,comp,zero))
                masked=emit(seq,'OpCompositeConstruct',vec4,*ms)
                first=emit(seq,'OpGroupNonUniformBallotFindLSB',u32,scope,masked)
                seq.append(inst('OpGroupNonUniformShuffle',typ,rid,scope,value,first))
                counts['broadcast_first']+=1
            else:
                need(len(i)==6,'malformed lane read')
                target=i[5];targetdef=definitions.get(target)
                need(targetdef is not None and targetdef[1]==u32,'lane target must be uint32')
                physical=emit(seq,'OpIAdd',u32,target,base)
                shuffled=emit(seq,'OpGroupNonUniformShuffle',typ,scope,value,physical)
                valid=emit(seq,'OpULessThan',boolean,target,c32)
                t=shape(typ)
                if name(t)=='OpTypeVector':
                    boolvec=type_find('OpTypeVector',(boolean,t[3]))
                    valid=emit(seq,'OpCompositeConstruct',boolvec,*([valid]*t[3]))
                undef=global_inst('OpUndef',typ,alloc())
                seq.append(inst('OpSelect',typ,rid,valid,shuffled,undef))
                counts['lane_read']+=1
            changed.extend(seq)
        else:changed.append(i)
    need(sum(counts.values())>0,'module has no supported Wave32 operations to transform')
    existingcaps={i[1] for i in original if name(i)=='OpCapability'}
    for c in ('GroupNonUniform','GroupNonUniformBallot','GroupNonUniformShuffle'):
        val=ENUM['Capability'][c]
        if val not in existingcaps:capabilities.append(inst('OpCapability',val))
    # Capabilities precede extensions/imports/memory model; annotations precede types.
    cap_end=next((j for j,i in enumerate(changed) if name(i)!='OpCapability'),len(changed))
    changed[cap_end:cap_end]=capabilities
    type_start=next(j for j,i in enumerate(changed) if INFO[i[0]&65535]['class']=='Type-Declaration')
    changed[type_start:type_start]=decorations
    function_start=next(j for j,i in enumerate(changed) if name(i)=='OpFunction')
    changed[function_start:function_start]=additions
    if new_lane:
        for i in changed:
            if name(i)=='OpEntryPoint':i.append(lane);i[0]+=1<<16
    header[3]=next_id
    output=encode(header,changed)
    parse(output)
    return output, {'operations':counts,'new_builtin_lane_variable':new_lane,
                    'physical_subgroup_size_required':64,'logical_subgroup_size':32,
                    'compiler_validated':False,'gpu_validated':False,
                    'original_instructions':len(original),'transformed_instructions':len(changed)}

