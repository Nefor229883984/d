import importlib.util, os, subprocess, sys, re, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENG = os.path.join(ROOT, 'lang', 'engine')
TMP = tempfile.mkdtemp(prefix='brat-diff-')

spec = importlib.util.spec_from_file_location('cc', os.path.join(ENG, 'codegen_c.py'))
cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)

_tc_spec = importlib.util.spec_from_file_location('cc_toolchain', os.path.join(ENG, 'toolchain.py'))
_tc = importlib.util.module_from_spec(_tc_spec); _tc_spec.loader.exec_module(_tc)

CASES = {
    're_stack': 'print(re_match("a", "%s"))' % ("a?" * 512),
    're_stack2': 'print(re_match("aa", "%s"))' % ("a?" * 600),
    'log_json': 'log.info("say \\"hi\\" and \\\\ end")\nlog.info("a\\tb")',
    're_class_long': 'print(re_match("%s", "[%s]"))\nprint(re_match("zz", "[%s]"))' % ("q" * 300, "q" * 300, "q" * 300),
    'join_mixed': 'print([1, true, None, 2.5].join("-"))\nprint([].join(","))',
    'join_nested': 'print([[1, 2], ["a"]].join(","))\nprint([Ok(1), Err("x")].join(","))',
    'lines': 'print("a\\nb".lines())\nprint("a\\r\\nb\\rc".lines())\nprint("x\\n".lines())',
    'sort_nested': 'let mut v = [[3], [1], [2]]\nv.sort()\nprint(v)',
    'sort_str': 'let mut v = ["b", "a", "c"]\nv.sort()\nprint(v)',
    'minmax_nested': 'print([[3], [1]].min())\nprint([[3], [1]].max())',
    'index_utf8': 'print("héllo".index_of("l"))\nprint("привет".index_of("в"))\nprint("abc".index_of("z"))',
    'parse_big': 'print("99999999999999999999".parse_int())\nprint("-99999999999999999999".parse_int())',
    'sum_big': 'print([9223372036854775807, 1].sum())',
    'split_empty': 'try {\n  "abc".split("")\n} catch e {\n  print("caught")\n}',
    'case_full': 'print("Привет Мир".upper())\nprint("ΌΛΟΣ".lower())\nprint("ǅ".upper())',
    'trim_ws': 'print("\\t x \\n".trim())\nprint("[\\tx\\n]")',
    'split_nl': 'print("a\\nb".split("\\n"))',
    'chunks_oob': 'print([1, 2, 3].chunks(99))',
    'regex_alt': 'print(re_match("curl/8.0", "(curl|wget|sqlmap)"))\n'
                 'print(re_match("Mozilla/5.0", "(curl|wget|sqlmap)"))\n'
                 'print(re_match("aabc", "(a|ab)c"))\nprint(re_match("ac", "(a|ab)c"))\n'
                 'print(re_match("ab", "(a|b)+"))\nprint(re_match("zzz", "(a|b)+"))',
    'regex_grp': 'print(re_match("abd", "a(b|c)d"))\nprint(re_match("adc", "a(b|c)d"))\n'
                 'print(re_match("hello", "(hel|he)lo"))\nprint(re_match("", ""))\n'
                 'print(re_match("x", ""))',
    'regex_cls': 'print(re_match("abc123", "\\w+\\d+"))\nprint(re_match("a b", "\\w\\s\\w"))\n'
                 'print(re_match("abc", "^\\D+$"))\nprint(re_match("123", "^\\D+$"))\n'
                 'print(re_match("nospaces", "^\\S+$"))\nprint(re_match("has space", "^\\S+$"))',
    'regex_http': 'print(re_match("GET /i.html HTTP/1.1", "^(GET|POST) \\S+ HTTP/[0-9]\\.[0-9]$"))\n'
                  'print(re_match("BAD /i.html HTTP/1.1", "^(GET|POST) \\S+ HTTP/[0-9]\\.[0-9]$"))',
    'regex_bad': 'try {\n  re_match("x", "(unbalanced")\n} catch e {\n  print("bad pat")\n}',
    'regex_backtrack': 'print(re_match("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaab", "(a+)+b"))\n'
                       'print(re_match("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaac", "(a+)+b"))\n'
                       'print(re_match("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxy", "(x+x+)+y"))',
    'regex_anchor': 'print(re_match("GET", "^(GET|POST|PUT|DELETE|HEAD)$"))\n'
                    'print(re_match("Get", "^(GET|POST|PUT|DELETE|HEAD)$"))\n'
                    'print(re_match("abc123", "^[a-z]+[0-9]+$"))\n'
                    'print(re_match("nospaces", "^\\S+$"))\n'
                    'print(re_match("has space", "^\\S+$"))\n'
                    'print(re_match("10.0.0.1", "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$"))',
    'regex_empty': 'print(re_match("", ""))\nprint(re_match("x", ""))\n'
                   'print(re_match("", "^(a|b|c)*$"))\nprint(re_match("abc", "^(a|b|c)*$"))\n'
                   'print(re_match("nothing", "^(a|b|c)*$"))',
    'interp_expr': 'let v = [1, 2, 3]\nlet p = Point { x: 7 }\nlet n = 5\nlet s = "hi"\n'
                    'print("len={len(v)} f={p.x} a={n + 1} u={s.upper()} i={v[0]}")\n'
                    'print("plain {n} text")\nprint("neg={-n}")',
    'interp_json': 'print("{\\"a\\": 1}")\nprint("[1, 2]")\nlet j = "{\\"k\\": {\\"n\\": 2}}"\nprint(j)\n'
                   'print("brace { alone")\nprint("}{")',
    'arith_bool': 'print(true + 1)\nprint(-true)\nprint(true * 2)\nprint(true + true)\n'
                   'print(7 / true)\nprint(7 % true)\nprint(2.5 + true)\nprint("b{true}n")',
    'arith_wrap': 'print(9223372036854775807 + 1)\nprint(-9223372036854775808 - 1)\n'
                  'print(9223372036854775807 * 2)\nlet mn = -9223372036854775808\n'
                  'print(mn / -1)\nprint(mn % -1)\nprint(-mn)',
    'arith_concat': 'print("x" + 2.5 + "y")\nprint("a" + 0.1)\nprint("z" + None)\n'
                    'print("v" + [1, 2])\nprint("t" + Ok(1))\nprint("s" + true)\n'
                    'print(str(true))\nprint(str(1.5))\nprint(str(None))',
    'arith_vec': 'print([1] + [2])\nprint([1, 2] + [3, 4] + [5])\nprint([] + [])\n'
                 'print(7.5 % 2.0)\nprint(-7.5 % 2.0)\nprint(7.5 / 2.5)',
    'interp_call': 'let mut by_ip = {}\nby_ip.set("a", 1)\n'
                   'print("a={by_ip.get(\"a\")}")\nprint("keys={by_ip.keys().join(\",\")}")',
    'neg_index': 'let v = [1, 2, 3]\nprint(v.del(-1))\nprint(v)',
    'map_del_none': 'let mut m = {"a": None}\nprint(m.del("a"))',
    'nested_fn': 'fn outer() -> i64 {\n  fn inner(x: i64) -> i64 { return x + 1 }\n  return inner(41)\n}\nprint(outer())',
    'deep_rec': 'fn down(n: i64) -> i64 {\n  if n <= 0 { return 0 }\n  return down(n - 1)\n}\nprint(down(200))',
    'spawn_loopvar': 'let mut seen = []\nfor i in 1..4 {\n  spawn {\n    seen.push(i)\n  }\n}\njoin_all()\nseen.sort()\nprint(seen)',
    'closure_loopcap': 'let mut fns = []\nfor i in 0..3 {\n  fns.push(fn() { return i })\n}\nlet mut out = []\nfor f in fns {\n  out.push(f())\n}\nprint(out)',
    'closure_nested': 'let g = 5\nlet f = fn() {\n  let h = fn() { return g + 1 }\n  return h()\n}\nprint(f())',
    'spawn_in_closure': 'let mut total = []\nlet f = fn(n: i64) {\n  spawn {\n    total.push(n * 2)\n  }\n  join_all()\n}\nf(3)\nf(4)\ntotal.sort()\nprint(total)',
    'catch_capture': 'try {\n  raise("boom")\n} catch e {\n  let f = fn() { return e }\n  print(f())\n}',
    'defer_block': 'fn collect() {\n'
                   '    let mut loop_log = []\n'
                   '    let mut guard_log = []\n'
                   '    let mut branch_log = []\n'
                   '    for i in 1..4 {\n'
                   '      defer loop_log.push("pass")\n'
                   '    }\n'
                   '    if true {\n'
                   '      defer branch_log.push("outer")\n'
                   '      if true {\n'
                   '        defer branch_log.push("inner-1")\n'
                   '        defer branch_log.push("inner-2")\n'
                   '      }\n'
                   '    }\n'
                   '    for i in 1..3 {\n'
                   '      if true {\n'
                   '        defer guard_log.push("guard")\n'
                   '      }\n'
                   '    }\n'
                   '    return [loop_log, branch_log, guard_log]\n'
                   '  }\n'
                   '  let r = collect()\n'
                   '  print(r[0])\n'
                   '  print(r[1])\n'
                   '  print(r[2])',
    'fs_ok': 'let w = write_file("out.txt", "hello")\n'
             'match w {\n'
             '  Ok(n) => { print("wrote {n}") },\n'
             '  Err(reason) => { print("werr {reason}") }\n'
             '}\n'
             'match read_file("out.txt") {\n'
             '  Ok(t) => { print("read [{t}]") },\n'
             '  Err(reason) => { print("rerr {reason}") }\n'
             '}',
    'fs_missing': 'match read_file("nope.txt") {\n'
                  '  Ok(t) => { print("ok {len(t)}") },\n'
                  '  Err(reason) => { print("errno {reason.contains(\"[Errno 2]\")}") }\n'
                  '}',
    'fs_dotdot': 'match read_file("../outside.txt") {\n'
                 '  Ok(t) => { print("ok {len(t)}") },\n'
                 '  Err(reason) => { print("refused {reason}") }\n'
                 '}',
    'fs_write_dotdot': 'let w = write_file("../escaped.txt", "x")\n'
                       'match w {\n'
                       '  Ok(n) => { print("WROTE {n} BYTES") },\n'
                       '  Err(reason) => { print("wrefused {reason}") }\n'
                       '}',
    # The O(n) builder path: both backends must name the same result.
    'sbuf': 'let b = sbuf()\n'
            'let n = sbuf_add(b, "hello ")\n'
            'sbuf_add(b, "world")\n'
            'match sbuf_str(b) {\n'
            '  Ok(t) => { print("add={n} len={sbuf_len(b)} str=[{t}]") },\n'
            '  Err(r) => { print("err {r}") }\n'
            '}',
    'sbuf_unicode': 'let b = sbuf()\n'
                    'sbuf_add(b, "привет ")\n'
                    'sbuf_add(b, "мир")\n'
                    'match sbuf_str(b) {\n'
                    '  Ok(t) => { print("u={len(t)} [{t}]") },\n'
                    '  Err(r) => { print("uerr {r}") }\n'
                    '}',
    'fs_abs': 'match read_file("/etc/hostname") {\n'
              '  Ok(t) => { print("ok {len(t)}") },\n'
              '  Err(reason) => { print("refused {reason}") }\n'
              '}',
    'interp_str_in': 'let mut m = {}\n'
                     'm.set("k", 7)\n'
                     'print("get={m.get_or(\\"k\\", 0)}")\n'
                     'print("mid {m.get_or(\\"k\\", 0)} done")\n'
                     'let v = 5\n'
                     'print("mixed {v}-{len([1,2])}-{m.get_or(\\"k\\", 0)}")\n'
                     'print("quote \\"{v}\\" and \\\"end\\"")',
    'tcp_echo': 'let srv = tcp_listen("127.0.0.1:47311")\n'
                'match srv {\n'
                '  Ok(fd) => {\n'
                '    let cli = tcp_connect("127.0.0.1:47311")\n'
                '    match cli {\n'
                '      Ok(cfd) => {\n'
                '        let acc = tcp_accept(fd)\n'
                '        match acc {\n'
                '          Ok(afd) => {\n'
                '            tcp_send(cfd, "ping")\n'
                '            let got = tcp_recv(afd, 16)\n'
                '            match got {\n'
                '              Ok(text) => { print("echo [{text}]") },\n'
                '              Err(r) => { print("recv {r}") }\n'
                '            }\n'
                '            tcp_close(afd)\n'
                '          },\n'
                '          Err(r) => { print("accept {r}") }\n'
                '        }\n'
                '        tcp_close(cfd)\n'
                '      },\n'
                '      Err(r) => { print("connect {r}") }\n'
                '    }\n'
                '    tcp_close(fd)\n'
                '  },\n'
                '  Err(r) => { print("listen {r}") }\n'
                '}',
    'tcp_bad_addr': 'let a = tcp_listen(\"127.0.0.1:70000\")\n'
                    'match a {\n'
                    '  Ok(fd) => { print(\"bad listen\") },\n'
                    '  Err(r) => { print(\"listen rejected\") }\n'
                    '}\n'
                    'let b = tcp_connect(\"127.0.0.1:0\")\n'
                    'match b {\n'
                    '  Ok(fd) => { print(\"bad connect\") },\n'
                    '  Err(r) => { print(\"connect rejected\") }\n'
                    '}',
    'tcp_sigpipe': 'let srv = tcp_listen(\"127.0.0.1:47312\")\n'
                   'match srv {\n'
                   '  Ok(fd) => {\n'
                   '    let cli = tcp_connect(\"127.0.0.1:47312\")\n'
                   '    match cli {\n'
                   '      Ok(cfd) => {\n'
                   '        let acc = tcp_accept(fd)\n'
                   '        match acc {\n'
                   '          Ok(afd) => {\n'
                   '            tcp_close(cfd)\n'
                   '            let mut tries = 0\n'
                   '            let mut failed = false\n'
                   '            while tries < 200 && failed == false {\n'
                   '              let s = tcp_send(afd, \"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\")\n'
                   '              match s {\n'
                   '                Ok(n) => { tries = tries + 1 },\n'
                   '                Err(r) => { failed = true }\n'
                   '              }\n'
                   '            }\n'
                   '            print(\"peer gone, send refused: {failed}\")\n'
                   '            tcp_close(afd)\n'
                   '          },\n'
                   '          Err(r) => { print(\"accept {r}\") }\n'
                   '        }\n'
                   '      },\n'
                   '      Err(r) => { print(\"connect {r}\") }\n'
                   '    }\n'
                   '    tcp_close(fd)\n'
                   '  },\n'
                   '  Err(r) => { print(\"listen {r}\") }\n'
                   '}',
    'tcp_refused': 'let c = tcp_connect("127.0.0.1:47399")\n'
                   'match c {\n'
                   '  Ok(fd) => { print("unexpectedly connected"); tcp_close(fd) },\n'
                   '  Err(r) => { print("refused ok") }\n'
                   '}',
    'defer_on_unwind': 'fn boom() -> i64 {\n'
                       '  defer log.info("defer ran")\n'
                       '  raise("boom")\n'
                       '  return 0\n'
                       '}\n'
                       'let r = 0\n'
                       'try {\n'
                       '  r = boom()\n'
                       '} catch e {\n'
                       '  r = -1\n'
                       '  print("caught {e}")\n'
                       '}\n'
                       'print("after {r}")',
    'defer_nested_unwind': 'fn deep() -> i64 {\n'
                           '  defer log.warn("deep defer")\n'
                           '  raise("deep boom")\n'
                           '  return 0\n'
                           '}\n'
                           'fn mid() -> i64 {\n'
                           '  defer log.info("mid defer")\n'
                           '  return deep()\n'
                           '}\n'
                           'let r = 0\n'
                           'try {\n'
                           '  r = mid()\n'
                           '} catch e {\n'
                           '  r = -2\n'
                           '  print("nested caught {e}")\n'
                           '}\n'
                           'print("nested after {r}")',
    'interp_json_lit': 'print("{\\\"a\\\": 1}")\n'
                       'print("json {\\"k\\": {\\"n\\": 2}}")\n'
                       'print("brace { alone")\n'
                       'print("}{")\n'
                       'let v = 9\n'
                       'print("real {v} text")',
    'fs_empty': 'let w = write_file("empty.txt", "")\n'
                'match w {\n'
                '  Ok(n) => { print("wrote {n}") },\n'
                '  Err(reason) => { print("werr {reason}") }\n'
                '}\n'
                'match read_file("empty.txt") {\n'
                '  Ok(t) => { print("empty ok {len(t)} [{t}]") },\n'
                '  Err(reason) => { print("empty err {reason}") }\n'
                '}',
    'fs_dir': 'match read_file(".") {\n'
              '  Ok(t) => { print("dir ok {len(t)}") },\n'
              '  Err(reason) => { print("dir err {reason.contains(\"Is a directory\")}") }\n'
              '}',
    # Absurd repeats must refuse before malloc.
    'repeat_oom': 'try {\n'
                  '  let s = "x".repeat(2000000000)\n'
                  '  print(len(s))\n'
                  '} catch e {\n'
                  '  print(e)\n'
                  '}',
    'repeat_star_oom': 'try {\n'
                       '  let s = "xy" * 1000000000\n'
                       '  print(len(s))\n'
                       '} catch e {\n'
                       '  print(e)\n'
                       '}',
    'repeat_ok': 'print("ab".repeat(3))\nprint("z" * 4)',
    # A bare uppercase local parses as a zero-arg call; both backends must read
    # the binding as the receiver, not call it.
    'recv_upper': 'let mut V = [3, 1]\nV.push(2)\nV.sort()\nprint(V)\nprint(V[0])\n'
                  'let mut M = {}\nM.set("a", 1)\nprint(M.get("a"))\nprint(M.has("a"))\n'
                  'let mut P = Point { x: 1 }\nP.x = 9\nprint(P.x)',
}

TS = re.compile(r'"ts": [\d.]+')
bad = 0
for name, body in CASES.items():
    src = 'fn main() -> i64 {\n  ' + body.replace('\n', '\n  ') + '\n  return 0\n}\n'
    path = os.path.join(TMP, 'd.brat')
    open(path, 'w', encoding='utf-8').write(src)
    cf = os.path.join(TMP, 'd.c')
    exe = os.path.join(TMP, 'd.exe')
    try:
        open(cf, 'w', encoding='utf-8').write(cc.emit(src, TMP))
        rb = subprocess.run(_tc.resolve_cc().argv(cf, exe, static=True) + ["-O2", "-lm", "-I", ENG],
                            capture_output=True, text=True, timeout=300)
        if rb.returncode != 0:
            print('%-14s BUILD FAIL %s' % (name, (rb.stdout + rb.stderr).strip().splitlines()[-1][:110]))
            bad += 1
            continue
    except Exception as e:
        print('%-14s EMIT FAIL %s' % (name, e))
        bad += 1
        continue
    a = subprocess.run([sys.executable, '-X', 'utf8', os.path.join(ENG, 'brat.py'), path], capture_output=True, text=True)
    b = subprocess.run([exe], capture_output=True, text=True)
    na = TS.sub('"ts": T', a.stdout.replace('\r\n', '\n'))
    nb = TS.sub('"ts": T', b.stdout.replace('\r\n', '\n'))
    if na == nb and a.returncode == b.returncode:
        print('%-14s ok   %s' % (name, na.replace('\n', ' | ')[:70]))
    else:
        bad += 1
        print('%-14s DIFF  rc i=%d n=%d' % (name, a.returncode, b.returncode))
        print('   interp: %r' % na.replace('\n', ' | ')[:150])
        print('   native: %r' % nb.replace('\n', ' | ')[:150])
print('---- diffs:', bad)
sys.exit(1 if bad else 0)