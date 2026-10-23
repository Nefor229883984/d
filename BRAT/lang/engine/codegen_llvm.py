import ctypes
import os
import time

from llvmlite import binding, ir

binding.initialize_native_target()
binding.initialize_native_asmprinter()


def build():
    mod = ir.Module(name='brat')
    i64 = ir.IntType(64)
    add = ir.Function(mod, ir.FunctionType(i64, [i64, i64]), name='add')
    b = ir.IRBuilder(add.append_basic_block(name='e'))
    b.ret(b.add(add.args[0], add.args[1], name='s'))
    fib = ir.Function(mod, ir.FunctionType(i64, [i64]), name='fib')
    n = fib.args[0]
    e, a, c = fib.append_basic_block('e'), fib.append_basic_block('a'), fib.append_basic_block('c')
    b = ir.IRBuilder(e)
    b.cbranch(b.icmp_signed('<', n, ir.Constant(i64, 2)), a, c)
    b = ir.IRBuilder(a)
    b.ret(n)
    b = ir.IRBuilder(c)
    x = b.call(fib, [b.sub(n, ir.Constant(i64, 1))])
    y = b.call(fib, [b.sub(n, ir.Constant(i64, 2))])
    b.ret(b.add(x, y))
    route = ir.Function(mod, ir.FunctionType(i64, [i64]), name='route')
    code = route.args[0]
    e, hit, miss, els, out = (route.append_basic_block(nm) for nm in ('e', 'hit', 'miss', 'els', 'out'))
    b = ir.IRBuilder(e)
    sw = b.switch(code, els)
    sw.add_case(ir.Constant(i64, 200), hit)
    sw.add_case(ir.Constant(i64, 500), miss)
    b = ir.IRBuilder(hit)
    b.branch(out)
    b = ir.IRBuilder(miss)
    b.branch(out)
    b = ir.IRBuilder(els)
    b.branch(out)
    b = ir.IRBuilder(out)
    phi = b.phi(i64, name='r')
    phi.add_incoming(ir.Constant(i64, 1), hit)
    phi.add_incoming(ir.Constant(i64, 2), miss)
    phi.add_incoming(ir.Constant(i64, 0), els)
    b.ret(phi)
    total = ir.Function(mod, ir.FunctionType(i64, [i64]), name='sum_to')
    n2 = total.args[0]
    e2, loop, body, done = (total.append_basic_block(nm) for nm in ('e', 'loop', 'body', 'done'))
    b = ir.IRBuilder(e2)
    b.branch(loop)
    b = ir.IRBuilder(loop)
    pi = b.phi(i64, name='i')
    pa = b.phi(i64, name='acc')
    pi.add_incoming(ir.Constant(i64, 0), e2)
    pa.add_incoming(ir.Constant(i64, 0), e2)
    b.cbranch(b.icmp_signed('<', pi, n2), body, done)
    b = ir.IRBuilder(body)
    ni = b.add(pi, ir.Constant(i64, 1))
    na = b.add(pa, pi)
    b.branch(loop)
    pi.add_incoming(ni, body)
    pa.add_incoming(na, body)
    b = ir.IRBuilder(done)
    b.ret(pa)
    return mod


def jit(mod):
    asm = binding.parse_assembly(str(mod))
    asm.verify()
    tm = binding.Target.from_default_triple().create_target_machine()
    engine = binding.create_mcjit_compiler(asm, tm)
    engine.finalize_object()
    engine.run_static_constructors()
    return engine


def call_i64(engine, name, *args):
    ptr = engine.get_function_address(name)
    f = ctypes.CFUNCTYPE(ctypes.c_int64, *([ctypes.c_int64] * len(args)))(ptr)
    return f(*args)


def main():
    import sys as _sys
    # stdout is UTF-8 regardless of the console locale, same contract as brat.py.
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    mod = build()
    print(str(mod))
    engine = jit(mod)
    a = call_i64(engine, 'add', 40, 2)
    f = call_i64(engine, 'fib', 20)
    assert a == 42, a
    assert f == 6765, f
    assert call_i64(engine, 'route', 200) == 1
    assert call_i64(engine, 'route', 500) == 2
    assert call_i64(engine, 'route', 404) == 0
    assert call_i64(engine, 'sum_to', 100) == 4950
    print('LLVM-JIT OK: add(40,2)=%d fib(20)=%d' % (a, f))
    n = 20000
    t0 = time.perf_counter()
    got = call_i64(engine, 'sum_to', n)
    tjit = time.perf_counter() - t0
    assert got == n * (n - 1) // 2, got
    import subprocess
    import sys
    import tempfile as _tf
    probe = 'let t = 0\nfor i in 0..%d { t = t + i }\nprint(t)' % n
    _tdir = _tf.mkdtemp(prefix='brat-llvm-')
    probe_path = os.path.join(_tdir, 'brat_bench_sum.brat')
    open(probe_path, 'w').write(probe)
    brat = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'brat.py')
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, brat, probe_path],
                       capture_output=True, text=True, timeout=300)
    tinterp = time.perf_counter() - t0
    assert p.returncode == 0 and str(n * (n - 1) // 2) in p.stdout, p.stderr[-300:]
    print('NOTE n=%d: jit=%.4fs vs interpreter-PROCESS=%.2fs — this includes Python'
          ' startup and parsing, so it is NOT a speedup claim for BRAT.' % (n, tjit, tinterp))
    tm2 = binding.Target.from_default_triple().create_target_machine()
    obj = tm2.emit_object(binding.parse_assembly(str(mod)))
    opath = os.path.join(_tdir, 'brat_native.o')
    with open(opath, 'wb') as f:
        f.write(obj)
    assert os.path.getsize(opath) > 100, os.path.getsize(opath)
    print('OBJECT %s bytes=%d magic=%s' % (opath, os.path.getsize(opath), obj[:4].hex()))


if __name__ == '__main__':
    main()