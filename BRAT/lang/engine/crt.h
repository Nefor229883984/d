#ifndef BRAT_RT_H
#define BRAT_RT_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <time.h>
#include <math.h>
#include <errno.h>
#if defined(_MSC_VER)
#define BTLS __declspec(thread)
#else
#define BTLS _Thread_local
#endif
#if defined(_WIN32)
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <sys/resource.h>
#endif
/* Baked memory contract: `brat build --mem N` compiles BRAT_MEM_BYTES in and
 * main() applies it as RLIMIT_AS before anything else allocates. 0 (the
 * default) means no budget. Windows has no RLIMIT_AS; the budget is silently
 * absent there rather than faked. */
#ifndef BRAT_MEM_BYTES
#define BRAT_MEM_BYTES 0
#endif
#if defined(BRAT_RT_SHARED)
#define BFN
#else
#define BFN static
#endif


#include <stdint.h>
#include <limits.h>
typedef long long bint;
#define BINT_MIN (-9223372036854775807LL - 1LL)

#define BINT_FMT "%lld"


typedef enum { V_NIL, V_BOOL, V_INT, V_FLOAT, V_STR, V_VEC, V_STRUCT, V_TAG, V_MOVED } VTag;
typedef struct Val Val;
typedef struct Vec Vec;
typedef struct Map Map;
struct Vec { Val *items; long len, cap; };

struct Map { Val *keys; Val *vals; long len, cap; const char *type;
             long *idx; long idx_cap; long idx_used; };
struct Val { VTag tag; union { bint b; bint ival; double fval; char *str; Vec *vec; Map *map; struct { char *tag; Vec *args; } tv; } as; };
typedef struct BHead { long rc; } BHead;
BFN void vraise(const char *msg);
BFN void vraisef(const char *fmt, const char *arg);
BFN int verr_pending(void);
BFN Val verr_take(void);
BFN void verr_clear(void);
BFN long utf8_cplen(const char *s);
BFN void vjprint(Val v);

BFN long veq(Val a, Val b);

BFN Val vrepeat_n(Val s, bint k);
#ifdef BRAT_RT_SHARED
BTLS Val vunwind;
#else
static BTLS Val vunwind;
#endif

static void vfatal_oom(void);

/* Check before overwriting: a failed realloc leaves the old block allocated. */

BFN void *bgrow(void *p, size_t n) {
    void *q = realloc(p, n);
    if (!q) { vfatal_oom(); }
    return q;
}

BFN long vput(char *o, const char *s) {
    long n = 0;
    while (s[n]) { o[n] = s[n]; n++; }
    return n;
}

BFN Val vnil(void) { Val v; v.tag = V_NIL; return v; }
BFN Val vbool(long b) { Val v; v.tag = V_BOOL; v.as.b = b ? 1 : 0; return v; }
BFN Val vint(bint n) { Val v; v.tag = V_INT; v.as.ival = n; return v; }
BFN Val vfloat(double n) { Val v; v.tag = V_FLOAT; v.as.fval = n; return v; }

static void *balloc(size_t n);
/* Intern table for immortal string literals: avoids one allocation per loop. */
static struct { const char *src; char *ptr; } _vlit_cache[512];
static int _vlit_n = 0;
BFN Val vlit(const char *s) {
    for (int i = 0; i < _vlit_n; i++) {
        if (_vlit_cache[i].src == s) {
            Val v; v.tag = V_STR; v.as.str = _vlit_cache[i].ptr;
            return v;
        }
    }
    size_t n = strlen(s);
    char *p = (char *)balloc(n + 1);
    memcpy(p, s, n + 1);
    ((BHead *)p - 1)->rc = -1; /* immortal: never freed, never counted */
    if (_vlit_n < 512) {
        _vlit_cache[_vlit_n].src = s;
        _vlit_cache[_vlit_n].ptr = p;
        _vlit_n++;
    }
    Val v; v.tag = V_STR; v.as.str = p;
    return v;
}

static void vfatal_oom(void) {
    fputs("brat: out of memory\n", stderr);
    abort();
}
/* Arena allocation. While an arena is entered, balloc bump-allocates from its
 * chunks; barena_leave frees all chunks added since the matching barena_enter.
 * Arena objects carry BARENA_FLAG in their refcount, so the decrement in
 * vrc_dec never reaches zero and vdrop returns early: they are never freed one
 * at a time. An arena block is laid out as [size_t usable][BHead][payload]. */
#define BARENA_FLAG ((long)1 << 62)
#define BARENA_CHUNK ((size_t)64 * 1024)
#define BARENA_HDR 32
#define BIS_ARENA(h) (((h)->rc & BARENA_FLAG) != 0)
#define BARENA_SIZE(h) (*(size_t *)((char *)(h) - sizeof(size_t)))
typedef struct BChunk { struct BChunk *prev; size_t used, cap; } BChunk;
typedef struct BMark { BChunk *top; size_t used; } BMark;
static BTLS BChunk *barena_top = 0;
static BTLS int barena_depth = 0;

BFN BMark barena_enter(void) {
    BMark m;
    m.top = barena_top;
    m.used = barena_top ? barena_top->used : 0;
    barena_depth++;
    return m;
}
BFN void barena_leave(BMark m) {
    while (barena_top && barena_top != m.top) {
        BChunk *c = barena_top;
        barena_top = c->prev;
        free(c);
    }
    if (barena_top) barena_top->used = m.used;
    if (barena_depth > 0) barena_depth--;
}
static void *barena_alloc(size_t need) {
    need = (need + 15) & ~(size_t)15;
    BChunk *c = barena_top;
    if (!c || c->used + need > c->cap) {
        size_t cap = need > BARENA_CHUNK ? need : BARENA_CHUNK;
        BChunk *nc = (BChunk *)malloc(BARENA_HDR + cap);
        if (!nc) { fputs("brat: out of memory\n", stderr); abort(); }
        nc->prev = barena_top;
        nc->used = 0;
        nc->cap = cap;
        barena_top = nc;
        c = nc;
    }
    char *p = (char *)c + BARENA_HDR + c->used;
    c->used += need;
    return p;
}
BFN void *balloc(size_t n) {
    if (barena_depth > 0) {
        char *base = (char *)barena_alloc(sizeof(size_t) + sizeof(BHead) + n);
        *(size_t *)base = n;
        BHead *ah = (BHead *)(base + sizeof(size_t));
        memset(ah, 0, sizeof(BHead) + n);
        ah->rc = 1 | BARENA_FLAG;
        return ah + 1;
    }
    BHead *h = (BHead *)calloc(1, sizeof(BHead) + n);
    if (!h) vfatal_oom();
    h->rc = 1;
    return h + 1;
}
BFN void *bgrow_rc(void *p, size_t n) {
    BHead *h = p ? (BHead *)p - 1 : 0;
    if (h && BIS_ARENA(h)) {
        size_t old = BARENA_SIZE(h);
        void *q = balloc(n);
        memcpy(q, p, old < n ? old : n);
        return q;
    }
    void *q = realloc(h, sizeof(BHead) + n);
    if (!q) { vfatal_oom(); }
    return (BHead *)q + 1;
}
/* Container arrays follow their container: an arena container takes its arrays
 * from the arena too. Arena arrays are never freed one by one, and growing one
 * copies into fresh arena space; the old space is reclaimed by barena_leave. */
BFN void *barr_new(size_t bytes, int in_arena, int zero) {
    if (in_arena) return balloc(bytes);
    void *p = zero ? calloc(1, bytes) : malloc(bytes);
    if (!p) vfatal_oom();
    return p;
}
BFN void *barr_grow(void *old, int in_arena, size_t oldb, size_t newb) {
    if (!in_arena) return bgrow(old, newb);
    void *q = balloc(newb);
    memcpy(q, old, oldb < newb ? oldb : newb);
    return q;
}

#define BARENA_MAX_NEST 64
static BTLS BMark barena_stack[BARENA_MAX_NEST];
static BTLS int barena_nest = 0;
/* Language-level entry points used by the parser's `arena { }` desugaring. */
BFN Val varena_enter(void) {
    if (barena_nest >= BARENA_MAX_NEST) { vraise("arena nested too deep"); return vunwind; }
    barena_stack[barena_nest++] = barena_enter();
    return vint(0);
}
BFN Val varena_leave(void) {
    if (barena_nest <= 0) { vraise("arena leave without enter"); return vunwind; }
    barena_leave(barena_stack[--barena_nest]);
    return vint(0);
}

BFN Val vrc_inc(Val v) {
    switch (v.tag) {
    case V_STR:   if (v.as.str)  { BHead *h = (BHead *)v.as.str - 1; if (h->rc >= 0) h->rc++; }  return v;
    case V_VEC:   if (v.as.vec)  { BHead *h = (BHead *)v.as.vec - 1; if (h->rc >= 0) h->rc++; }  return v;
    case V_STRUCT: if (v.as.map) { BHead *h = (BHead *)v.as.map - 1; if (h->rc >= 0) h->rc++; } return v;
    case V_TAG:   if (v.as.tv.args) { BHead *h = (BHead *)v.as.tv.args - 1; if (h->rc >= 0) h->rc++; } return v;
    default: return v;
    }
}
BFN void vrc_dec(Val v) {
    switch (v.tag) {
    case V_STR: {
        char *s = v.as.str;
        if (s) {
            BHead *h = (BHead *)s - 1;
            if (h->rc < 0) return; /* immortal */
            if (--h->rc == 0) free(h);
        }
        return;
    }
    case V_VEC: {
        Vec *vec = v.as.vec;
        if (vec) { BHead *h = (BHead *)vec - 1; if (--h->rc == 0) { free(vec->items); free(h); } }
        return;
    }
    case V_STRUCT: {
        Map *map = v.as.map;
        if (map) { BHead *h = (BHead *)map - 1; if (--h->rc == 0) { free(map->keys); free(map->vals); free(map->idx); free(h); } }
        return;
    }
    default: return;
    }
}
BFN void vmem_budget_apply(void) {
#if !defined(_WIN32)
    if (BRAT_MEM_BYTES > 0) {
        struct rlimit rl;
        rl.rlim_cur = (rlim_t)BRAT_MEM_BYTES;
        rl.rlim_max = (rlim_t)BRAT_MEM_BYTES;
        if (setrlimit(RLIMIT_AS, &rl) != 0) {
            fprintf(stderr, "mem budget: setrlimit(%ld) refused\n",
                    (long)BRAT_MEM_BYTES);
            exit(1);
        }
    }
#endif
}
BFN Val vstrn(const char *s, long n) { Val v; v.tag = V_STR; v.as.str = (char *)balloc((size_t)n + 1); memcpy(v.as.str, s, n); v.as.str[n] = 0; return v; }
BFN Vec *vvec(void) {
    Vec *v = (Vec *)balloc(sizeof(Vec));
    v->len = 0; v->cap = 4;
    v->items = (Val *)barr_new(sizeof(Val) * 4, BIS_ARENA((BHead *)v - 1), 0);
    return v;
}
BFN Val vstr(const char *s) {
    size_t n = strlen(s);
    Val v; v.tag = V_STR;
    v.as.str = (char *)balloc(n + 1);
    memcpy(v.as.str, s, n + 1);
    return v;
}
BFN Val vvecv(Vec *v) { Val r; r.tag = V_VEC; r.as.vec = v; return r; }
/* Container scaffolding only: elements may be aliased or static, so dropping
   them here would be a UAF. */
BFN void vdrop(Val v) {
    switch (v.tag) {
    case V_NIL:
    case V_BOOL:
    case V_INT:
    case V_FLOAT:
    case V_STR:
    case V_MOVED:
        return;
    case V_VEC: {
        Vec *vec = v.as.vec;
        if (vec && !BIS_ARENA((BHead *)vec - 1)) {
            free(vec->items);
            free((BHead *)vec - 1);
        }
        return;
    }
    case V_STRUCT: {
        Map *map = v.as.map;
        if (map && !BIS_ARENA((BHead *)map - 1)) {
            free(map->keys);
            free(map->vals);
            free(map->idx);
            free((BHead *)map - 1);
        }
        return;
    }
    case V_TAG: {
        Vec *args = v.as.tv.args;
        if (args && !BIS_ARENA((BHead *)args - 1)) {
            free(args->items);
            free((BHead *)args - 1);
        }
        return;
    }
    default:
        return;
    }
}
BFN Val vmove(Val *src) {
    Val r = *src;
    src->tag = V_MOVED;
    return r;
}
BFN Map *vmap(const char *type) {
    Map *m = (Map *)balloc(sizeof(Map));
    m->len = 0; m->cap = 4;
    int ina = BIS_ARENA((BHead *)m - 1);
    m->keys = (Val *)barr_new(sizeof(Val) * 4, ina, 0);
    m->vals = (Val *)barr_new(sizeof(Val) * 4, ina, 0);
    m->idx = (long *)barr_new(sizeof(long) * 8, ina, 1);
    m->type = type;
    m->idx_cap = 8; m->idx_used = 0;
    return m;
}

BFN unsigned long vkey_hash(Val k) {
    unsigned long h = 1469598103934665603UL;
    switch (k.tag) {
    case V_STR: {
        const unsigned char *p = (const unsigned char *)k.as.str;
        while (*p) { h ^= *p++; h *= 1099511628211UL; }
        return h;
    }
    case V_INT:
        h ^= (unsigned long)k.as.ival; h *= 1099511628211UL;
        h ^= h >> 29; return h;
    case V_FLOAT: {
        double d = k.as.fval == 0.0 ? 0.0 : k.as.fval;
        unsigned long bits;
        memcpy(&bits, &d, sizeof bits);
        h ^= bits; h *= 1099511628211UL; h ^= h >> 29; return h;
    }
    case V_BOOL:
        h ^= (unsigned long)(k.as.b ? 1 : 0); h *= 1099511628211UL; return h;
    case V_NIL:
        h ^= 0x9e3779b97f4a7c15UL; return h;
    case V_TAG: {
        const unsigned char *p = (const unsigned char *)k.as.tv.tag;
        while (*p) { h ^= *p++; h *= 1099511628211UL; }
        h ^= (unsigned long)k.as.tv.args->len; h *= 1099511628211UL;
        h ^= h >> 29; return h;
    }
    default:
        h ^= (unsigned long)k.tag; h *= 1099511628211UL; return h;
    }
}

BFN void map_idx_rebuild(Map *m) {
    long want = m->idx_cap;
    while (want < 8 || (m->len + 1) * 4 >= want * 3) want *= 2;
    if (want != m->idx_cap || m->idx == 0) {
        long *ni = (long *)barr_new(sizeof(long) * (size_t)want, BIS_ARENA((BHead *)m - 1), 1);
        if (m->idx && !BIS_ARENA((BHead *)m - 1)) free(m->idx);
        m->idx = ni; m->idx_cap = want;
    } else {
        for (long i = 0; i < m->idx_cap; i++) m->idx[i] = 0;
    }
    m->idx_used = 0;
    for (long i = 0; i < m->len; i++) {
        unsigned long h = vkey_hash(m->keys[i]);
        long mask = m->idx_cap - 1;
        long slot = (long)(h & (unsigned long)mask);
        while (m->idx[slot]) slot = (slot + 1) & mask;
        m->idx[slot] = i + 1;
        m->idx_used++;
    }
}

BFN long map_find(Map *m, Val k) {
    if (!m->idx || m->idx_cap == 0) map_idx_rebuild(m);
    long mask = m->idx_cap - 1;
    long slot = (long)(vkey_hash(k) & (unsigned long)mask);
    long first_tomb = -1;
    for (long probes = 0; probes < m->idx_cap; probes++) {
        long e = m->idx[slot];
        if (e == 0) return -1;
        if (e == -1) { if (first_tomb < 0) first_tomb = slot; }
        else if (veq(m->keys[e - 1], k)) return e - 1;
        slot = (slot + 1) & mask;
    }
    return -1;
}
BFN void map_idx_put(Map *m, long pos) {
    if (!m->idx) map_idx_rebuild(m);
    if (m->idx_used * 4 >= m->idx_cap * 3) map_idx_rebuild(m);
    long mask = m->idx_cap - 1;
    long slot = (long)(vkey_hash(m->keys[pos]) & (unsigned long)mask);
    while (m->idx[slot] && m->idx[slot] != -1) slot = (slot + 1) & mask;
    m->idx[slot] = pos + 1;
    m->idx_used++;
}
BFN Val vstruct(Map *m) { Val r; r.tag = V_STRUCT; r.as.map = m; return r; }


BFN void vec_push(Vec *v, Val x) {
    if (v->len >= v->cap) {
        long oldcap = v->cap;
        v->cap *= 2;
        v->items = (Val *)barr_grow(v->items, BIS_ARENA((BHead *)v - 1),
                                    sizeof(Val) * (size_t)oldcap, sizeof(Val) * (size_t)v->cap);
    }
    v->items[v->len++] = x;
}
/* A tag's arguments outlive the block that built them when the tag is returned
 * (Ok/Err from an arena), so an arena-built argument list is moved to the heap.
 * Its items are copied shallowly; any arena values inside are the escape rules' job. */
BFN Vec *vtag_args_heap(Vec *a) {
    if (!a || !BIS_ARENA((BHead *)a - 1)) return a;
    int saved = barena_depth;
    barena_depth = 0;
    Vec *d = vvec();
    for (long i = 0; i < a->len; i++) vec_push(d, a->items[i]);
    barena_depth = saved;
    return d;
}
static char *_vtag_intern(const char *tag);
BFN Val vtagv_lit(const char *tag, Vec *args) {
    Val r; r.tag = V_TAG;
    r.as.tv.tag = _vtag_intern(tag);
    r.as.tv.args = vtag_args_heap(args);
    return r;
}
/* Intern table for tag names (Ok, Err, user enum variants): immortal. */
static struct { const char *src; char *ptr; } _vtag_cache[512];
static int _vtag_n = 0;
static char *_vtag_intern(const char *tag) {
    for (int i = 0; i < _vtag_n; i++) {
        if (strcmp(_vtag_cache[i].src, tag) == 0) return _vtag_cache[i].ptr;
    }
    char *p = strdup(tag);
    if (!p) vfatal_oom();
    if (_vtag_n < 512) {
        _vtag_cache[_vtag_n].src = p;
        _vtag_cache[_vtag_n].ptr = p;
        _vtag_n++;
    }
    return p;
}
BFN Val vtagv(const char *tag, Vec *args) {
    Val r; r.tag = V_TAG;
    r.as.tv.tag = _vtag_intern(tag);
    r.as.tv.args = vtag_args_heap(args);
    return r;
}

BFN void vec_push_all(Vec *dst, Vec *src) {
    for (long i = 0; i < src->len; i++) vec_push(dst, src->items[i]);
}
BFN void map_set_v(Map *m, Val k, Val v) {
    if (k.tag == V_VEC || k.tag == V_STRUCT) { vraise("bad map key"); return; }
    long at = map_find(m, k);
    if (at >= 0) { m->vals[at] = v; return; }
    if (m->len >= m->cap) {
        long oldcap = m->cap;
        int inm = BIS_ARENA((BHead *)m - 1);
        m->cap *= 2;
        m->keys = (Val *)barr_grow(m->keys, inm, sizeof(Val) * (size_t)oldcap, sizeof(Val) * (size_t)m->cap);
        m->vals = (Val *)barr_grow(m->vals, inm, sizeof(Val) * (size_t)oldcap, sizeof(Val) * (size_t)m->cap);
    }
    m->keys[m->len] = k; m->vals[m->len] = v; m->len++;
    map_idx_put(m, m->len - 1);
}

BFN void map_set(Map *m, const char *k, Val v) {
    Val key; key.tag = V_STR; key.as.str = (char *)k;
    map_set_v(m, key, v);
}
BFN Val *map_get_v(Map *m, Val k) {
    long at = map_find(m, k);
    return at >= 0 ? &m->vals[at] : 0;
}
BFN Val *map_get(Map *m, const char *k) {
    Val key; key.tag = V_STR; key.as.str = (char *)k;
    return map_get_v(m, key);
}
BFN long map_del_v(Map *m, Val k) {
    long at = map_find(m, k);
    if (at < 0) return 0;
    memmove(m->keys + at, m->keys + at + 1, sizeof(Val) * (size_t)(m->len - at - 1));
    memmove(m->vals + at, m->vals + at + 1, sizeof(Val) * (size_t)(m->len - at - 1));
    m->len--;
    
    map_idx_rebuild(m);
    return 1;
}

BFN char *vkey(Val k) {
    if (k.tag == V_STR) return strdup(k.as.str);
    if (k.tag == V_INT) { char b[32]; snprintf(b, sizeof b, BINT_FMT, (bint)k.as.ival); return strdup(b); }
    if (k.tag == V_FLOAT) { char b[64]; snprintf(b, sizeof b, "%.17g", k.as.fval); return strdup(b); }
    if (k.tag == V_BOOL) return strdup(k.as.b ? "true" : "false");
    vraise("bad map key"); return NULL;
}
BFN Val vmap_getv(Val m, Val k) {
    if (m.tag != V_STRUCT) { vraise("get of non-map"); return vunwind; }
    Val *o = map_get_v(m.as.map, k);
    return o ? *o : vnil();
}
BFN Val vmap_has(Val m, Val k) {
    if (m.tag != V_STRUCT) { vraise("has of non-map"); return vunwind; }
    Val *o = map_get_v(m.as.map, k);
    return vbool(o != 0);
}
BFN Val vmap_keys(Val m) {
    if (m.tag != V_STRUCT) { vraise("keys of non-map"); return vunwind; }
    Vec *o = vvec();
    for (long i = 0; i < m.as.map->len; i++) vec_push(o, m.as.map->keys[i]);
    return vvecv(o);
}
BFN Val vmap_del(Val m, Val k) {
    if (m.tag != V_STRUCT) { vraise("del of non-map"); return vunwind; }
    return vbool(map_del_v(m.as.map, k));
}

BFN bint vmapkey_reserved(Val k) {
    return k.tag == V_STR && k.as.str && strcmp(k.as.str, "$type") == 0;
}
BFN Val vmapkey_err(void) {
    Vec *e = vvec();
    vec_push(e, vstr("map key \"$type\" is reserved"));
    return vtagv("err", e);
}
BFN bint vmap_has_reserved(Map *m) {
    Val k; k.tag = V_STR; k.as.str = (char *)"$type";
    return map_find(m, k) >= 0;
}

BFN Val vmap_setv(Val m, Val k, Val v) {
    if (m.tag != V_STRUCT) { vraise("set of non-map"); return vunwind; }
    if (vmapkey_reserved(k)) { return vmapkey_err(); }
    map_set_v(m.as.map, k, v);
    return v;
}
BFN bint vlong(Val v);
BFN Val vset_at(Val m, Val k, Val v) {
    
    if (m.tag == V_STRUCT) {
        if (m.as.map->type[0]) { char *kk = vkey(k); vraisef("no field %s\n", kk); return vunwind; }
        if (vmapkey_reserved(k)) { return vmapkey_err(); }
        map_set_v(m.as.map, k, v);
        return v;
    }
    if (m.tag == V_VEC) {
        long i = vlong(k);
        if (i < 0 || i >= m.as.vec->len) { vraise("index out of range"); return vunwind; }
        m.as.vec->items[i] = v;
        return v;
    }
    vraise("setidx of non-indexable"); return vunwind;
}
BFN Vec *viter_vec(Val v) {
    if (v.tag == V_VEC) return v.as.vec;
    if (v.tag == V_STRUCT) {
        Vec *o = vvec();
        for (long i = 0; i < v.as.map->len; i++) vec_push(o, v.as.map->keys[i]);
        return o;
    }
    vraise("not iterable"); return NULL;
}

BFN double vnum(Val v) {
    if (v.tag == V_INT) return (double)v.as.ival;
    if (v.tag == V_FLOAT) return v.as.fval;
    if (v.tag == V_BOOL) return (double)v.as.b;
    return 0;
}
BFN int vis_true(Val v) {
    switch (v.tag) {
    case V_NIL: return 0;
    case V_BOOL: return v.as.b;
    case V_INT: return v.as.ival != 0;
    case V_FLOAT: return v.as.fval != 0;
    case V_STR: return v.as.str[0] != 0;
    case V_VEC: return v.as.vec->len != 0;
    default: return 1;
    }
}


BFN const char *vfmt_double(double x) {
    static BTLS char buf[8][48];
    static BTLS long k = 0;
    char *b = buf[k++ & 7];
    int p;
    
    for (p = 1; p <= 17; p++) {
        snprintf(b, 48, "%.*g", p, x);
        if (strtod(b, NULL) == x) break;
    }
    
    if (strchr(b, 'e') && fabs(x) != 0.0 && fabs(x) >= 1e-4 && fabs(x) < 1e16) {
        for (p = 0; p <= 17; p++) {
            snprintf(b, 48, "%.*f", p, x);
            if (strtod(b, NULL) == x) break;
        }
    }
    if (!strpbrk(b, ".eEnN")) { strcat(b, ".0"); }
    return b;
}


/* Matches the interpreter's repr quote choice. */
BFN char vquote(const char *s) {
    return (strchr(s, '\'') && !strchr(s, '"')) ? '"' : '\'';
}

BFN long vrepr_slen(const char *s) {
    long n = 0;
    for (const char *p = s; *p; p++) {
        if (*p == '\'' || *p == '\\' || *p == '\n' || *p == '\r' || *p == '\t') n += 2;
        else n += 1;
    }
    return n;
}

BFN void vprint_f(Val v, long q, FILE *f) {
    long i;
    switch (v.tag) {
    case V_NIL: fprintf(f, "None"); break;
    case V_BOOL: fprintf(f, v.as.b ? "True" : "False"); break;
    case V_INT: fprintf(f, BINT_FMT, (bint)v.as.ival); break;
    case V_FLOAT: fprintf(f, "%s", vfmt_double(v.as.fval)); break;
    case V_STR: {
        if (!q) { fprintf(f, "%s", v.as.str); break; }
        char quote = vquote(v.as.str);
        fputc(quote, f);
        for (const char *p = v.as.str; *p; p++) {
            if (*p == quote || *p == '\\') { fputc('\\', f); fputc(*p, f); }
            else if (*p == '\n') fprintf(f, "\\n");
            else if (*p == '\r') fprintf(f, "\\r");
            else if (*p == '\t') fprintf(f, "\\t");
            else fputc((unsigned char)*p, f);
        }
        fputc(quote, f);
        break;
    }
    case V_VEC:
        fprintf(f, "[");
        for (i = 0; i < v.as.vec->len; i++) { if (i) fprintf(f, ", "); vprint_f(v.as.vec->items[i], 1, f); }
        fprintf(f, "]");
        break;
    case V_STRUCT: {
        int ismap = !v.as.map->type[0];
        fprintf(f, "{");
        if (!ismap) fprintf(f, "'$type': '%s'", v.as.map->type);
        for (i = 0; i < v.as.map->len; i++) {
            if (i || !ismap) fprintf(f, ", ");
            vprint_f(v.as.map->keys[i], 1, f);
            fprintf(f, ": ");
            vprint_f(v.as.map->vals[i], 1, f);
        }
        fprintf(f, "}");
        break;
    }
    case V_TAG:
        fprintf(f, "('%s'", v.as.tv.tag);
        for (i = 0; i < v.as.tv.args->len; i++) { fprintf(f, ", "); vprint_f(v.as.tv.args->items[i], 1, f); }
        
        if (v.as.tv.args->len == 0) fprintf(f, ",");
        fprintf(f, ")");
        break;
    }
}

BFN void vprint(Val v) { vprint_f(v, 0, stdout); }
BFN void vprint_err(Val v) { vprint_f(v, 0, stderr); }

BFN long veq(Val a, Val b) {
    long i;
    if ((a.tag == V_INT || a.tag == V_FLOAT || a.tag == V_BOOL) &&
        (b.tag == V_INT || b.tag == V_FLOAT || b.tag == V_BOOL)) return vnum(a) == vnum(b);
    if (a.tag != b.tag) return 0;
    switch (a.tag) {
    case V_NIL: return 1;
    case V_STR: return !strcmp(a.as.str, b.as.str);
    case V_VEC:
        if (a.as.vec->len != b.as.vec->len) return 0;
        for (i = 0; i < a.as.vec->len; i++) if (!veq(a.as.vec->items[i], b.as.vec->items[i])) return 0;
        return 1;
    case V_STRUCT: {
        if (strcmp(a.as.map->type, b.as.map->type)) return 0;
        if (a.as.map->len != b.as.map->len) return 0;
        for (i = 0; i < a.as.map->len; i++) {
            Val *o = map_get_v(b.as.map, a.as.map->keys[i]);
            if (!o || !veq(a.as.map->vals[i], *o)) return 0;
        }
        return 1;
    }
    case V_TAG:
        if (strcmp(a.as.tv.tag, b.as.tv.tag)) return 0;
        if (a.as.tv.args->len != b.as.tv.args->len) return 0;
        for (i = 0; i < a.as.tv.args->len; i++) if (!veq(a.as.tv.args->items[i], b.as.tv.args->items[i])) return 0;
        return 1;
    default: return 0;
    }
}


BFN int vcmp(Val a, Val b) {
    if (a.tag == V_STR && b.tag == V_STR) {
        int r = strcmp(a.as.str, b.as.str);
        return r < 0 ? -1 : r > 0 ? 1 : 0;
    }
    if ((a.tag == V_INT || a.tag == V_FLOAT || a.tag == V_BOOL) &&
        (b.tag == V_INT || b.tag == V_FLOAT || b.tag == V_BOOL)) {
        double x = vnum(a), y = vnum(b);
        return x < y ? -1 : x > y ? 1 : 0;
    }
    if (a.tag == V_NIL || b.tag == V_NIL) {
        if (a.tag == b.tag) return 0;
        return a.tag == V_NIL ? -1 : 1;
    }
    if (a.tag != b.tag) return a.tag < b.tag ? -1 : 1;
    if (a.tag == V_VEC) {
        Vec *x = a.as.vec, *y = b.as.vec;
        long nn = x->len < y->len ? x->len : y->len;
        for (long i = 0; i < nn; i++) {
            int c = vcmp(x->items[i], y->items[i]);
            if (c) return c;
        }
        return x->len < y->len ? -1 : x->len > y->len ? 1 : 0;
    }
    if (a.tag == V_TAG) {
        int c = strcmp(a.as.tv.tag, b.as.tv.tag);
        if (c) return c < 0 ? -1 : 1;
        Vec *x = a.as.tv.args, *y = b.as.tv.args;
        long nn = x->len < y->len ? x->len : y->len;
        for (long i = 0; i < nn; i++) {
            c = vcmp(x->items[i], y->items[i]);
            if (c) return c;
        }
        return x->len < y->len ? -1 : x->len > y->len ? 1 : 0;
    }
    if (a.tag == V_STRUCT) {
        int c = strcmp(a.as.map->type, b.as.map->type);
        if (c) return c < 0 ? -1 : 1;
        long nn = a.as.map->len < b.as.map->len ? a.as.map->len : b.as.map->len;
        for (long i = 0; i < nn; i++) {
            c = vcmp(a.as.map->keys[i], b.as.map->keys[i]);
            if (c) return c;
            c = vcmp(a.as.map->vals[i], b.as.map->vals[i]);
            if (c) return c;
        }
        return a.as.map->len < b.as.map->len ? -1 : a.as.map->len > b.as.map->len ? 1 : 0;
    }
    return 0;
}
BFN int vcontains(Val c, Val x) {
    long i;
    if (c.tag == V_VEC) {
        for (i = 0; i < c.as.vec->len; i++) if (veq(c.as.vec->items[i], x)) return 1;
        return 0;
    }
    if (c.tag == V_STR && x.tag == V_STR) return strstr(c.as.str, x.as.str) != 0;
    if (c.tag == V_STRUCT) {
        Val *o = map_get_v(c.as.map, x);
        return o != 0;
    }
    vraise("in of non-container"); return 0;
}

BFN long vsprint_len_q(Val v, long q);
BFN long vsprint_q(Val v, char *o, long q);



BFN Val vnum0(Val v) {
    if (v.tag == V_BOOL) return vint(v.as.b ? 1 : 0);
    return v;
}

#define VTXT_SLOTS 8
static BTLS char vtxt_buf[VTXT_SLOTS][512];
static BTLS long vtxt_next = 0;

BFN const char *vtxt(Val v) {
    char *b = vtxt_buf[vtxt_next++ % VTXT_SLOTS];
    if (v.tag == V_STR) { snprintf(b, 512, "%s", v.as.str); return b; }
    if (v.tag == V_INT) { snprintf(b, 512, BINT_FMT, (bint)v.as.ival); return b; }
    if (v.tag == V_FLOAT) { snprintf(b, 512, "%s", vfmt_double(v.as.fval)); return b; }
    long nn = vsprint_len_q(v, 0);
    if (nn < 511) {
        vsprint_q(v, b, 0);
        b[nn] = 0;
        return b;
    }
    
    char *heap = (char *)malloc((size_t)nn + 1);
    if (!heap) vfatal_oom();
    vsprint_q(v, heap, 0);
    heap[nn] = 0;
    static BTLS char *keep[VTXT_SLOTS];
    long k = vtxt_next % VTXT_SLOTS;
    free(keep[k]);
    keep[k] = heap;
    return heap;
}

BFN Val vadd(Val a, Val b) {
    if (a.tag == V_STR || b.tag == V_STR) {
        const char *sa = (a.tag == V_STR) ? a.as.str : vtxt(a);
        const char *sb = (b.tag == V_STR) ? b.as.str : vtxt(b);
        size_t la = strlen(sa), lb = strlen(sb);
        
        char *out = (char *)balloc(la + lb + 1);
        Val r; r.tag = V_STR;
        r.as.str = out;
        memcpy(out, sa, la);
        memcpy(out + la, sb, lb);
        out[la + lb] = 0;
        return r;
    }
    a = vnum0(a); b = vnum0(b);
    if (a.tag == V_INT && b.tag == V_INT) {

        return vint((bint)((unsigned long long)a.as.ival + (unsigned long long)b.as.ival));
    }
    if ((a.tag == V_INT || a.tag == V_FLOAT) && (b.tag == V_INT || b.tag == V_FLOAT))
        return vfloat(vnum(a) + vnum(b));
    
    if (a.tag == V_VEC && b.tag == V_VEC) {
        Vec *o = vvec();
        vec_push_all(o, a.as.vec);
        vec_push_all(o, b.as.vec);
        return vvecv(o);
    }
    vraise("type error in +"); return vunwind;
}
BFN Val vsub(Val a, Val b) {
    a = vnum0(a); b = vnum0(b);
    if (a.tag == V_INT && b.tag == V_INT)
        return vint((bint)((unsigned long long)a.as.ival - (unsigned long long)b.as.ival));
    if ((a.tag == V_INT || a.tag == V_FLOAT || a.tag == V_BOOL)
            && (b.tag == V_INT || b.tag == V_FLOAT || b.tag == V_BOOL))
        return vfloat(vnum(a) - vnum(b));
    vraise("type error in -"); return vunwind;
}
BFN Val vmul(Val a, Val b) {
    a = vnum0(a); b = vnum0(b);
    if (a.tag == V_INT && b.tag == V_INT)
        return vint((bint)((unsigned long long)a.as.ival * (unsigned long long)b.as.ival));
    if ((a.tag == V_INT || a.tag == V_FLOAT) && (b.tag == V_INT || b.tag == V_FLOAT))
        return vfloat(vnum(a) * vnum(b));
    
    if (a.tag == V_STR && b.tag == V_INT) return vrepeat_n(a, b.as.ival);
    if (a.tag == V_INT && b.tag == V_STR) return vrepeat_n(b, a.as.ival);
    vraise("type error in *"); return vunwind;
}
BFN Val vdiv(Val a, Val b) {
    a = vnum0(a); b = vnum0(b);
    if (a.tag == V_INT && b.tag == V_INT) {
        if (b.as.ival == 0) { vraise("div by zero"); return vunwind; }
        
        bint x = a.as.ival, y = b.as.ival;
        if (x == BINT_MIN && y == -1) { return vint(BINT_MIN); }
        bint q = x / y;
        if ((x % y) != 0 && ((x < 0) != (y < 0))) { q--; }
        return vint(q);
    }
    if ((a.tag == V_INT || a.tag == V_FLOAT) && (b.tag == V_INT || b.tag == V_FLOAT)) {
        double y = vnum(b);
        if (y == 0) { vraise("div by zero"); return vunwind; }
        return vfloat(vnum(a) / y);
    }
    vraise("type error in /"); return vunwind;
}
BFN Val vmod(Val a, Val b) {
    a = vnum0(a); b = vnum0(b);
    if (a.tag == V_INT && b.tag == V_INT) {
        if (b.as.ival == 0) { vraise("div by zero"); return vunwind; }
        
        bint x = a.as.ival, y = b.as.ival;
        if (x == BINT_MIN && y == -1) { return vint(0); }
        bint r = x % y;
        if (r != 0 && ((r < 0) != (y < 0))) { r += y; }
        return vint(r);
    }
    
    if ((a.tag == V_INT || a.tag == V_FLOAT) && (b.tag == V_INT || b.tag == V_FLOAT)) {
        double x = vnum(a), y = vnum(b);
        if (y == 0) { vraise("div by zero"); return vunwind; }
        double r = fmod(x, y);
        if (r != 0 && ((r < 0) != (y < 0))) { r += y; }
        return vfloat(r);
    }
    vraise("type error in %%"); return vunwind;
}
BFN Val vneg(Val a) {
    if (a.tag == V_INT) {
        if (a.as.ival == BINT_MIN) { return vint(BINT_MIN); }
        return vint((bint)(0ULL - (unsigned long long)a.as.ival));
    }
    if (a.tag == V_FLOAT) return vfloat(-a.as.fval);
    if (a.tag == V_BOOL) return vint(a.as.b ? -1 : 0);
    vraise("type error in unary -"); return vunwind;
}
BFN Val vlen(Val a) {
    if (a.tag == V_STR) {
        long n = 0;
        for (const char *q = a.as.str; *q; q += utf8_cplen(q)) n++;
        return vint(n);
    }
    if (a.tag == V_VEC) return vint(a.as.vec->len);
    if (a.tag == V_STRUCT) return vint(a.as.map->len);
    vraise("len of non-sized"); return vunwind;
}
BFN long utf8_cplen(const char *s) {
    unsigned char c = (unsigned char)*s;
    long need = 1;
    if (c < 0x80) return 1;
    if ((c & 0xE0) == 0xC0) need = 2;
    else if ((c & 0xF0) == 0xE0) need = 3;
    else if ((c & 0xF8) == 0xF0) need = 4;
    
    long have = 1;
    while (have < need && s[have]) have++;
    return have;
}
BFN const char *utf8_at(const char *s, long idx) {
    const char *p = s;
    while (idx > 0 && *p) {
        p += utf8_cplen(p);
        idx--;
    }
    return p;
}
/* Strict UTF-8, matching CPython's codec: read_file must refuse the same bytes. */
BFN int vutf8_ok(const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        unsigned c = *p;
        if (c < 0x80) { p++; continue; }
        long n;
        unsigned lo = 0x80, hi = 0xBF;
        if (c >= 0xC2 && c <= 0xDF) n = 1;
        else if (c == 0xE0) { n = 2; lo = 0xA0; }
        else if (c >= 0xE1 && c <= 0xEC) n = 2;
        else if (c == 0xED) { n = 2; hi = 0x9F; }
        else if (c >= 0xEE && c <= 0xEF) n = 2;
        else if (c == 0xF0) { n = 3; lo = 0x90; }
        else if (c >= 0xF1 && c <= 0xF3) n = 3;
        else if (c == 0xF4) { n = 3; hi = 0x8F; }
        else return 0;
        if (p[1] < lo || p[1] > hi) return 0;
        for (long i = 2; i <= n; i++) if (p[i] < 0x80 || p[i] > 0xBF) return 0;
        p += n + 1;
    }
    return 1;
}
BFN Val vat(Val a, Val i) {
    if (a.tag == V_STRUCT) {
        Val *o = map_get_v(a.as.map, i);
        if (!o) { vraise("no key"); return vunwind; }
        return *o;
    }
    bint k = (i.tag == V_INT || i.tag == V_FLOAT) ? (bint)vnum(i) : 0;
    if (i.tag != V_INT && i.tag != V_FLOAT) { vraise("index must be int"); return vunwind; }
    if (a.tag == V_STR) {
        const char *p = a.as.str;
        long n = 0;
        for (const char *q = p; *q; q += utf8_cplen(q)) n++;
        if (k < 0 || k >= n) { vraise("index out of range"); return vunwind; }
        const char *at = utf8_at(p, k);
        return vstrn(at, utf8_cplen(at));
    }
    if (a.tag == V_VEC) {
        if (k < 0 || k >= a.as.vec->len) { vraise("index out of range"); return vunwind; }
        return a.as.vec->items[k];
    }
    if (a.tag == V_TAG) {
        if (k < 1 || k > a.as.tv.args->len) { vraise("index out of range"); return vunwind; }
        return a.as.tv.args->items[k - 1];
    }
    vraise("index of non-indexable"); return vunwind;
}

static BTLS int _verr_pending;
/* _verr_exc: 1 for a runtime error, 0 for an `err` value (`raise`) that a
   callee returns like a normal value. Only exceptions survive a frame. */
static BTLS int _verr_exc;

/* Raises set a thread-local pending flag; callers check verr_pending() instead
   of longjmp, which is UB through non-volatile frames at -O2. */

BFN int verr_pending(void) { return _verr_pending; }
BFN Val verr_take(void) {
    _verr_pending = 0;
    _verr_exc = 0;
    return vunwind;
}
/* Leave one frame: clear an `err` value, keep a runtime exception pending so
   the enclosing frame (or a try) still sees it. */
BFN Val verr_leave(void) {
    if (!_verr_exc) _verr_pending = 0;
    return vunwind;
}
BFN void verr_clear(void) { _verr_pending = 0; _verr_exc = 0; }
BFN void vunwind_disarm(void) { /* no-op: pending channel has no jmp_buf */ }
BFN void vunwind_do(void) { _verr_pending = 1; _verr_exc = 1; }

/* The error owns its message and argument vector. A caught or stored error must not
   change when a later raise happens on the same thread, so nothing here is static. */
BFN void vraise(const char *msg) {
    if (_verr_pending) return;  /* first error wins: a poisoned follow-up must not replace it */
    Vec *a = vvec();
    vec_push(a, vstr(msg));
    vunwind = vtagv("err", a);
    _verr_pending = 1;
    _verr_exc = 1;
}
BFN void vraisef(const char *fmt, const char *arg) {
    char buf[256];
    snprintf(buf, sizeof buf, fmt, arg);
    long n = (long)strlen(buf);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) buf[--n] = 0;
    vraise(buf);
}
BFN void vraisev(Val v) {
    Vec *a = vvec();
    vec_push(a, v);
    vunwind = vtagv("err", a);
    _verr_pending = 1;
    _verr_exc = 0;
}
/* Language recursion budget: BRAT frames, not C frames (DESIGN-fast-fail A1).
   push returns nonzero once the budget is spent, without counting the frame. */
static BTLS long _bdepth;
static long _bbudget = 2000;
BFN void bdepth_init(void) {
    const char *e = getenv("BRAT_RECURSION_BUDGET");
    if (e && *e) {
        long v = atol(e);
        if (v > 0) _bbudget = v;
    }
}
BFN int bdepth_push(void) {
    if (_bdepth >= _bbudget) return 1;
    _bdepth++;
    return 0;
}
BFN void bdepth_pop(void) { _bdepth--; }
static long vargc_g;
static char **vargv_g;
static char vbase_g[4096];
static int vbase_ready;
BFN void vbase_set(const char *b) {
    if (!b || !*b) return;
    char res[4096];
    const char *rp = realpath(b, res);
    const char *use = rp ? rp : b;
    strncpy(vbase_g, use, sizeof vbase_g - 1);
    vbase_g[sizeof vbase_g - 1] = 0;
    vbase_ready = 1;
}
BFN void vargs_set(long c, char **v) {
    vargc_g = c;
    vargv_g = v;
    
    if (!vbase_ready) {
        char tmp[4096];
        const char *src = (c > 0 && v && v[0]) ? v[0] : ".";
        const char *slash = NULL;
        for (const char *q = src; *q; q++) if (*q == '/') slash = q;
        if (slash) {
            size_t n = (size_t)(slash - src);
            if (n == 0) n = 1;
            if (n >= sizeof tmp) n = sizeof tmp - 1;
            memcpy(tmp, src, n);
            tmp[n] = 0;
        } else {
            tmp[0] = '.'; tmp[1] = 0;
        }
        char res[4096];
        const char *rp = realpath(tmp, res);
        if (rp) { strncpy(vbase_g, rp, sizeof vbase_g - 1); vbase_g[sizeof vbase_g - 1] = 0; }
        else { strncpy(vbase_g, tmp, sizeof vbase_g - 1); vbase_g[sizeof vbase_g - 1] = 0; }
        vbase_ready = 1;
    }
}

BFN void vnorm(char *s) {
    int abs = (*s == '/');
    char *comps[256];
    int n = 0;
    char *cur = s;
    while (*cur) {
        while (*cur == '/') cur++;
        char *seg = cur;
        while (*cur && *cur != '/') cur++;
        size_t len = (size_t)(cur - seg);
        if (len == 0) continue;
        if (len == 1 && seg[0] == '.') continue;
        if (len == 2 && seg[0] == '.' && seg[1] == '.') {
            if (n > 0) n--;
            continue;
        }
        if (n < (int)(sizeof comps / sizeof *comps)) comps[n++] = seg;
    }
    char *o = s;
    if (abs) *o++ = '/';
    for (int i = 0; i < n; i++) {
        if (i) *o++ = '/';
        char *c = comps[i];
        while (*c && *c != '/') *o++ = *c++;
    }
    *o = 0;
}

/* Confine `p` to the program directory (vbase_g); callers must still re-verify
   the opened fd because realpath() and fopen() are not atomic (TOCTOU). */
BFN const char *vconfine(const char *p) {
    const char *free_env = getenv("BRAT_FILES");
    if (free_env && *free_env) return p;
    if (!p) return NULL;
    if (!vbase_ready || !vbase_g[0]) return NULL; /* base must be set first */

    static BTLS char res[8192];
    size_t bl = strlen(vbase_g);
    if (p[0] == '/') snprintf(res, sizeof res, "%s", p);
    else snprintf(res, sizeof res, "%s/%s", vbase_g, p);

    char real[8192];
    if (realpath(res, real)) snprintf(res, sizeof res, "%s", real);
    else vnorm(res);
    if (strncmp(res, vbase_g, bl) == 0 && (res[bl] == '/' || res[bl] == 0)) return res;
    return NULL;
}

/* Re-check an open fd against the base dir via /proc. Fails closed where /proc is
   missing: a confinement check that cannot run must not pass. */
static int _vfd_confined(int fd) {
#if defined(__linux__)
    char proc[64];
    char target[PATH_MAX];
    snprintf(proc, sizeof proc, "/proc/self/fd/%d", fd);
    ssize_t n = readlink(proc, target, sizeof target - 1);
    if (n < 0) return 0;
    target[n] = 0;
    size_t bl = strlen(vbase_g);
    return strncmp(target, vbase_g, bl) == 0 && (target[bl] == '/' || target[bl] == 0);
#else
    (void)fd;
    return 0;
#endif
}
BFN Val vargs(void) {
    Vec *o = vvec();
    for (long i = 1; i < vargc_g; i++) vec_push(o, vstr(vargv_g[i]));
    return vvecv(o);
}
BFN Val vexit(Val v) {
    exit((int)vlong(v));
}
BFN void veprint(Vec *a) {
    for (long i = 0; i < a->len; i++) {
        if (i) fprintf(stderr, " ");
        vprint_f(a->items[i], 0, stderr);
    }
    fprintf(stderr, "\n");
    fflush(stderr);
}
BFN Val vtime_ms(void) {
#if defined(_WIN32)
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    unsigned long long t = ((unsigned long long)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    return vint((bint)(t / 10000ULL));
#else
    struct timespec ts;
    timespec_get(&ts, TIME_UTC);
    
    return vint((bint)((unsigned long long)ts.tv_sec * 1000ULL
                       + (unsigned long long)ts.tv_nsec / 1000000ULL));
#endif
}
BFN Val vsleep_ms(Val v) {
    bint ms = vlong(v);
    if (ms < 0) ms = 0;
#if defined(_WIN32)
    Sleep((DWORD)ms);
#else
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
#endif
    return vint(0);
}
BFN Val venv_get(Val v) {
    if (v.tag != V_STR) return vnil();
    const char *key = v.as.str;
    /* Mirror brat/runtime.py: a credential-shaped name is refused, never returned. */
    char *upper = (char *)malloc(strlen(key) + 1);
    if (!upper) vfatal_oom();
    size_t i = 0;
    for (; key[i]; i++) upper[i] = (char)toupper((unsigned char)key[i]);
    upper[i] = 0;
    static const char *deny[] = {"SECRET", "KEY", "TOKEN", "PASS", "PASSWORD", "CREDENTIAL", "PRIVATE", 0};
    int blocked = 0;
    for (int b = 0; deny[b] && !blocked; b++) blocked = strstr(upper, deny[b]) != 0;
    free(upper);
    if (blocked) {
        char *o = (char *)malloc(strlen(key) * 2 + 32);
        if (!o) vfatal_oom();
        long n = vput(o, "env_get refused: ");
        n += vsprint_q(v, o + n, 1);
        o[n] = 0;
        Vec *e = vvec();
        vec_push(e, vstrn(o, n));
        free(o);
        return vtagv("err", e);
    }
    const char *r = getenv(key);
    return r ? vstr(r) : vnil();
}

typedef struct { char kind; char q; char ch; const char *body; } RTok;

static int rtok_match(RTok *t, char ch) {
    if (t->kind == 'l') return ch == t->ch;
    if (t->kind == 'd') return 1;
    if (t->kind == 's') {
        const char *body = t->body;
        int neg = 0, found = 0;
        if (*body == '^') { neg = 1; body++; }
        while (*body) {
            if (body[1] == '-' && body[2]) {
                if (ch >= body[0] && ch <= body[2]) { found = 1; break; }
                body += 3;
            } else {
                if (*body == ch) { found = 1; break; }
                body++;
            }
        }
        return neg ? !found : found;
    }
    return 0;
}


typedef struct { char op; char q; char ch; const char *body; long x, y; } RInst;
typedef struct { RInst *p; long n, cap; const char *pat; long i, plen; int err; int empty_seq; } RProg;

static long r_emit(RProg *g, char op, long x, long y) {
    if (g->n >= g->cap) {
        g->cap = g->cap ? g->cap * 2 : 64;
        g->p = (RInst *)bgrow(g->p, sizeof(RInst) * (size_t)g->cap);
    }
    RInst *in = &g->p[g->n];
    in->op = op; in->q = 0; in->ch = 0; in->body = NULL; in->x = x; in->y = y;
    return g->n++;
}

static long r_alt(RProg *g);



static long r_atom(RProg *g) {
    long sp = r_emit(g, 's', 0, 0);
    long body = -1;
    if (g->i >= g->plen) { g->err = 1; return -1; }
    char c = g->pat[g->i];
    if (c == '(') {
        g->i++;
        body = r_alt(g);
        if (g->err) return -1;
        if (g->i >= g->plen || g->pat[g->i] != ')') { g->err = 1; return -1; }
        g->i++;
    } else if (c == '[') {
        
        long j = g->i + 1;
        long m = j;
        while (m < g->plen && g->pat[m] != ']') m++;
        char *buf = (char *)malloc((size_t)(m - j + 2));
        if (!buf) vfatal_oom();
        int k = 0;
        if (j < g->plen && g->pat[j] == '^') buf[k++] = g->pat[j++];
        while (j < g->plen && g->pat[j] != ']') buf[k++] = g->pat[j++];
        buf[k] = 0;
        g->i = j + 1;
        body = r_emit(g, 'c', 0, 0);
        g->p[body].q = 's';
        g->p[body].body = buf;
    } else if (c == '.') {
        g->i++;
        body = r_emit(g, 'c', 0, 0);
        g->p[body].q = 'd';
    } else if (c == '^') {
        g->i++;
        body = r_emit(g, 'b', 0, 0);
    } else if (c == '$') {
        g->i++;
        body = r_emit(g, 'e', 0, 0);
    } else if (c == '\\') {
        char e = (g->i + 1 < g->plen) ? g->pat[g->i + 1] : '\\';
        g->i += 2;
        body = r_emit(g, 'c', 0, 0);
        if (e == 'd') { g->p[body].q = 's'; g->p[body].body = "0-9"; }
        else if (e == 'D') { g->p[body].q = 's'; g->p[body].body = "^0-9"; }
        else if (e == 'w') { g->p[body].q = 's'; g->p[body].body = "a-zA-Z0-9_"; }
        else if (e == 'W') { g->p[body].q = 's'; g->p[body].body = "^a-zA-Z0-9_"; }
        else if (e == 's') { g->p[body].q = 's'; g->p[body].body = " \t\n\r"; }
        else if (e == 'S') { g->p[body].q = 's'; g->p[body].body = "^ \t\n\r"; }
        else if (e == 'n') { g->p[body].q = 'l'; g->p[body].ch = '\n'; }
        else if (e == 't') { g->p[body].q = 'l'; g->p[body].ch = '\t'; }
        else if (e == 'r') { g->p[body].q = 'l'; g->p[body].ch = '\r'; }
        else { g->p[body].q = 'l'; g->p[body].ch = e; }
    } else {
        g->i++;
        body = r_emit(g, 'c', 0, 0);
        g->p[body].q = 'l';
        g->p[body].ch = c;
    }
    if (g->err) return -1;
    char q = 0;
    if (g->i < g->plen) {
        char cq = g->pat[g->i];
        if (cq == '*' || cq == '+' || cq == '?') { q = cq; g->i++; }
    }
    if (!q) {
        g->p[sp].op = 'j';
        
        g->p[sp].x = body;
        return sp;
    }
    if (q == '*') {
        long jmp = r_emit(g, 'j', 0, 0);
        g->p[jmp].x = sp;
        g->p[sp].x = body;
        g->p[sp].y = g->n;
        return sp;
    }
    if (q == '+') {
        
        g->p[sp].op = 'j';
        g->p[sp].x = body;
        long sp2 = r_emit(g, 's', 0, 0);
        g->p[sp2].x = body;
        g->p[sp2].y = g->n;
        return sp;
    }
    
    g->p[sp].x = body;
    g->p[sp].y = g->n;
    return sp;
}

static long r_seq(RProg *g) {
    
    long entry = r_emit(g, 'j', 0, 0);
    long first_atom = -1;
    while (g->i < g->plen && !g->err) {
        char c = g->pat[g->i];
        if (c == '|' || c == ')') break;
        long a = r_atom(g);
        if (g->err) return -1;
        if (first_atom < 0) first_atom = a;
    }
    if (g->err) return -1;
    g->p[entry].x = (first_atom < 0) ? entry : first_atom;
    g->empty_seq = (first_atom < 0);
    return entry;
}

static long r_alt(RProg *g) {
    
    long head = r_emit(g, 's', 0, 0);
    r_seq(g);
    if (g->err) return -1;
    if (!(g->i < g->plen && g->pat[g->i] == '|')) {
        g->p[head].op = 'j';
        g->p[head].x = head + 1;
        return head;
    }
    long j1 = r_emit(g, 'j', 0, 0);   
    long cap = g->plen + 2;
    long *sps = (long *)malloc(sizeof(long) * (size_t)cap);
    long *jmps = (long *)malloc(sizeof(long) * (size_t)cap);
    if (!sps || !jmps) { free(sps); free(jmps); vfatal_oom(); }
    int ns = 0, nj = 0;
    while (g->i < g->plen && g->pat[g->i] == '|') {
        g->i++;
        long sp = r_emit(g, 's', 0, 0);
        sps[ns++] = sp;
        long s0 = r_seq(g);
        if (g->err) { free(sps); free(jmps); return -1; }
        long j = r_emit(g, 'j', 0, 0);
        jmps[nj++] = j;
        if (g->empty_seq) g->p[s0].x = j;   
    }
    long fail = r_emit(g, 'f', 0, 0);   
    long exit = g->n;
    g->p[head].x = head + 1;
    g->p[head].y = (ns > 0) ? sps[0] : fail;
    g->p[j1].x = exit;
    for (int k = 0; k < ns; k++) {
        g->p[sps[k]].x = sps[k] + 1;
        g->p[sps[k]].y = (k + 1 < ns) ? sps[k + 1] : fail;
    }
    for (int k = 0; k < nj; k++) g->p[jmps[k]].x = exit;
    free(sps); free(jmps);
    return head;
}


static void re_close(RInst *p, long pc, const char *s, long si, long *out, long *nout, long *seen) {
    long stack[512];
#define RSTACK_MAX 512
    int sp = 0;
    stack[sp++] = pc;
    while (sp > 0) {
        long q = stack[--sp];
        if (q < 0 || seen[q]) continue;
        seen[q] = 1;
        RInst *in = &p[q];
        
        if (in->op == 'j') { if (sp < RSTACK_MAX) stack[sp++] = in->x; }
        else if (in->op == 's') { if (sp + 2 <= RSTACK_MAX) { stack[sp++] = in->x; stack[sp++] = in->y; } }
        else if (in->op == 'b') { if (si == 0 && sp < RSTACK_MAX) stack[sp++] = q + 1; }
        else if (in->op == 'e') { if (s[si] == 0 && sp < RSTACK_MAX) stack[sp++] = q + 1; }
        else if (in->op == 'f') {  }
        else out[(*nout)++] = q;   
    }
}

BFN int vre_match(Val sv, Val pv) {
    if (sv.tag != V_STR || pv.tag != V_STR) { vraise("re_match wants str"); return 0; }
    const char *s = sv.as.str, *pat = pv.as.str;
    RProg g;
    g.p = NULL; g.n = 0; g.cap = 0; g.pat = pat; g.i = 0; g.plen = (long)strlen(pat); g.err = 0;
    g.empty_seq = 0;
    long start = r_alt(&g);
    if (g.err || start < 0 || g.i != g.plen) { vraise("bad regex pattern"); return 0; }
    r_emit(&g, 'm', 0, 0);
    for (long k = 0; k < g.n; k++) {
        if (g.p[k].op == 'j' && g.p[k].x == k) g.p[k].x = g.n - 1;
    }
    long nprog = g.n;
    long *list = (long *)malloc(sizeof(long) * (size_t)(nprog + 1));
    long *next = (long *)malloc(sizeof(long) * (size_t)(nprog + 1));
    long *seen = (long *)malloc(sizeof(long) * (size_t)(nprog + 1));
    if (!list || !next || !seen) { free(list); free(next); free(seen); vfatal_oom(); }
    long slen = (long)strlen(s);
    for (long from = 0; from <= slen; from++) {
        long nlist = 0;
        memset(seen, 0, sizeof(long) * (size_t)(nprog + 1));
        re_close(g.p, start, s, from, list, &nlist, seen);
        for (long si = from; ; si++) {
            
            for (long k = 0; k < nlist; k++) {
                if (g.p[list[k]].op == 'm') { free(list); free(next); free(seen); free(g.p); return 1; }
            }
            if (!s[si] || nlist == 0) break;
            long nn = 0;
            memset(seen, 0, sizeof(long) * (size_t)(nprog + 1));
            for (long k = 0; k < nlist; k++) {
                RInst *in = &g.p[list[k]];
                if (in->op != 'c') continue;
                if (in->q == 'l') { if (s[si] != in->ch) continue; }
                else if (in->q == 'd') {  }
                else {
                    RTok t; t.kind = 's'; t.q = 0; t.ch = 0; t.body = in->body;
                    if (!rtok_match(&t, s[si])) continue;
                }
                re_close(g.p, list[k] + 1, s, si + 1, next, &nn, seen);
            }
            long *tmp = list; list = next; next = tmp;
            nlist = nn;
        }
        if (nlist > 0) {
            for (long k = 0; k < nlist; k++) {
                if (g.p[list[k]].op == 'm') { free(list); free(next); free(seen); free(g.p); return 1; }
            }
        }
    }
    free(list); free(next); free(seen); free(g.p);
    return 0;
}

BFN Val vquest(Val v) {
    if (v.tag == V_TAG && !strcmp(v.as.tv.tag, "err")) {
        vunwind = v;
        vunwind_do();
    }
    return v;
}
BFN Val vquest_ok(Val v) {
    if (v.tag == V_TAG && (!strcmp(v.as.tv.tag, "ok") || !strcmp(v.as.tv.tag, "some")))
        return v.as.tv.args->len ? v.as.tv.args->items[0] : vnil();
    return v;
}

BFN double vtime(void) {
    static double t0 = 0;
    double now = (double)clock() / CLOCKS_PER_SEC;
    if (!t0) t0 = now;
    return now;
}

BFN long vsprint_len(Val v);
BFN long vsprint(Val v, char *o);
BFN long vsprint_len_q(Val v, long q);
BFN long vsprint_q(Val v, char *o, long q);

BFN Val vfield(Val v, const char *k) {
    if (v.tag == V_STRUCT) {
        Val *o = map_get(v.as.map, k);
        if (!o) { vraisef("no field %s\n", k); return vunwind; }
        return *o;
    }
    vraisef("no field %s\n", k); return vunwind;
}


BFN Val verr(const char *msg) {
    Vec *e = vvec();
    vec_push(e, vstr(msg));
    return vtagv("err", e);
}

BFN Val verr_os(const char *rp) {
    char m[9000];
    snprintf(m, sizeof m, "[Errno %d] %s: '%s'", errno, strerror(errno), rp);
    return verr(m);
}
BFN Val vok1(const char *s) {
    Vec *a = vvec();
    vec_push(a, vstr(s));
    return vtagv("ok", a);
}
BFN Val vreadfile(Val p) {
    if (p.tag != V_STR) return verr("read_file wants a string path");
    const char *rp = vconfine(p.as.str);
    if (!rp) return verr("path outside the program directory");
    errno = 0;
    FILE *f = fopen(rp, "rb");
    if (!f) return verr_os(rp);
    if (!_vfd_confined(fileno(f))) { fclose(f); return verr("path escaped during open"); }
    char buf[4096];
    size_t n;
    size_t total = 0;
    char *data = NULL;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) {
        data = (char *)bgrow(data, total + n + 1);
        memcpy(data + total, buf, n);
        total += n;
    }
    
    int failed = ferror(f);
    int rerr = errno;
    fclose(f);
    if (failed) { errno = rerr; return verr_os(rp); }
    
    if (!data) return vok1("");
    data[total] = 0;
    if (!vutf8_ok(data)) { free(data); return verr("invalid utf-8"); }
    Vec *a = vvec();
    vec_push(a, vstr(data));
    free(data);
    return vtagv("ok", a);
}

#if !defined(_WIN32)
#include <sys/time.h>
#endif

/* Socket slots are shared by all spawn threads, so the table has a lock. It is
 * never held across a blocking call: a slot is read under the lock, and the
 * call runs on the fd after the lock is released. */
#if defined(_WIN32)
static CRITICAL_SECTION _vsk_cs;
static int _vsk_cs_ready = 0;
static void _vsk_lock(void) {
    if (!_vsk_cs_ready) { InitializeCriticalSection(&_vsk_cs); _vsk_cs_ready = 1; }
    EnterCriticalSection(&_vsk_cs);
}
static void _vsk_unlock(void) { LeaveCriticalSection(&_vsk_cs); }
#else
static pthread_mutex_t _vsk_mu = PTHREAD_MUTEX_INITIALIZER;
static void _vsk_lock(void) { pthread_mutex_lock(&_vsk_mu); }
static void _vsk_unlock(void) { pthread_mutex_unlock(&_vsk_mu); }
#endif

#define _VSK_MAX 64
#define _VSK_TIMEOUT_S 30        /* connected sockets: a silent peer frees its thread */
#define _VRECV_MAX (1L << 20)    /* one tcp_recv returns at most 1 MiB */

static int _vsk[_VSK_MAX];
static int _vsk_ready = 0;

/* Callers hold the lock. */
static void _vsk_ensure(void) {
    if (_vsk_ready) return;
    for (int i = 0; i < _VSK_MAX; i++) _vsk[i] = -1;
    _vsk_ready = 1;
}
static int _vsk_put(int fd) {
    int slot = -1;
    _vsk_lock();
    _vsk_ensure();
    for (int i = 1; i < _VSK_MAX; i++) {
        if (_vsk[i] < 0) { _vsk[i] = fd; slot = i; break; }
    }
    _vsk_unlock();
    return slot;
}
static int _vsk_get(long slot) {
    int fd = -1;
    _vsk_lock();
    _vsk_ensure();
    if (slot >= 1 && slot < _VSK_MAX) fd = _vsk[slot];
    _vsk_unlock();
    return fd;
}
/* Takes the fd out of its slot atomically; the caller closes it. */
static int _vsk_take(long slot) {
    int fd = -1;
    _vsk_lock();
    _vsk_ensure();
    if (slot >= 1 && slot < _VSK_MAX && _vsk[slot] >= 0) {
        fd = _vsk[slot];
        _vsk[slot] = -1;
    }
    _vsk_unlock();
    return fd;
}

/* A write to a peer that has gone away must return EPIPE, not kill the process.
 * Linux and Windows take MSG_NOSIGNAL / have no SIGPIPE; macOS needs the socket option. */
static void _vsk_nosigpipe(int fd) {
#if defined(__APPLE__)
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, (const char *)&one, sizeof one);
#else
    (void)fd;
#endif
}
#if defined(MSG_NOSIGNAL)
#define _VSEND_FLAGS MSG_NOSIGNAL
#else
#define _VSEND_FLAGS 0
#endif

/* Connected sockets only. The listening socket gets no timeout, or an idle server would die. */
static void _vsk_timeouts(int fd) {
#if defined(_WIN32)
    DWORD ms = _VSK_TIMEOUT_S * 1000;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, (const char *)&ms, sizeof ms);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, (const char *)&ms, sizeof ms);
#else
    struct timeval tv;
    tv.tv_sec = _VSK_TIMEOUT_S;
    tv.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
#endif
}

static Val _vok_int(long n) { Vec *a = vvec(); vec_push(a, vint(n)); return vtagv("ok", a); }
static Val _verr_c(const char *m) { Vec *a = vvec(); vec_push(a, vstr(m)); return vtagv("err", a); }

/* "host:port". Returns 0 for anything malformed: a host that does not fit is
 * refused, not cut short; the port must be decimal digits giving 1..65535. */
static int _vhost_port(const char *c, char *host, size_t hcap, int *port) {
    const char *colon = strrchr(c, ':');
    if (!colon || colon == c) return 0;
    size_t hl = (size_t)(colon - c);
    if (hl >= hcap) return 0;
    memcpy(host, c, hl);
    host[hl] = 0;
    const char *p = colon + 1;
    if (!*p) return 0;
    long v = 0;
    for (; *p; p++) {
        if (*p < '0' || *p > '9') return 0;
        v = v * 10 + (*p - '0');
        if (v > 65535) return 0;
    }
    if (v < 1) return 0;
    *port = (int)v;
    return 1;
}

typedef struct { char *buf; long len, cap; int used; } VSbuf;
static VSbuf _vsb[64];
static int _vsb_next = 1;

static VSbuf *_vsb_get(long h) {
    if (h < 1 || h >= 64) return NULL;
    if (!_vsb[h].used) return NULL;
    return &_vsb[h];
}
BFN Val vsbuf(void) {
    for (int i = 1; i < 64; i++) {
        if (!_vsb[i].used) {
            _vsb[i].used = 1;
            _vsb[i].len = 0;
            _vsb[i].cap = 16;
            _vsb[i].buf = (char *)malloc((size_t)_vsb[i].cap);
            if (!_vsb[i].buf) { _vsb[i].used = 0; vfatal_oom(); }
            _vsb[i].buf[0] = 0;
            return vint(i);
        }
    }
    return _verr_c("too many builders");
}
BFN Val vsbuf_add(Val h, Val part) {
    VSbuf *b = _vsb_get((long)vlong(h));
    if (!b) return _verr_c("bad builder");
    if (part.tag != V_STR) return _verr_c("sbuf_add wants a string");
    size_t pl = strlen(part.as.str);
    while (b->len + (long)pl + 1 > b->cap) {
        b->cap *= 2;
        b->buf = (char *)bgrow(b->buf, (size_t)b->cap);
    }
    memcpy(b->buf + b->len, part.as.str, pl);
    b->len += (long)pl;
    b->buf[b->len] = 0;
    return vint(b->len);
}
BFN Val vsbuf_len(Val h) {
    VSbuf *b = _vsb_get((long)vlong(h));
    if (!b) return _verr_c("bad builder");
    return vint(b->len);
}
BFN Val vsbuf_str(Val h) {
    VSbuf *b = _vsb_get((long)vlong(h));
    if (!b) return _verr_c("bad builder");
    Vec *a = vvec();
    vec_push(a, vstrn(b->buf, b->len));
    return vtagv("ok", a);
}
BFN Val vtcp_listen(Val addr) {
    if (addr.tag != V_STR) return _verr_c("tcp_listen wants a string address");
    char host[256]; int port = 0;
    if (!_vhost_port(addr.as.str, host, sizeof host, &port))
        return _verr_c("address wants host:port with port 1..65535");
    unsigned long ip = inet_addr(host);
    if (ip == INADDR_NONE) return _verr_c("host must be an IPv4 address");
    int fd = (int)socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return _verr_c("socket failed");
    _vsk_nosigpipe(fd);
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof one);
    struct sockaddr_in sa;
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;
    sa.sin_port = htons((unsigned short)port);
    sa.sin_addr.s_addr = (in_addr_t)ip;
    if (bind(fd, (struct sockaddr *)&sa, sizeof sa) != 0) { close(fd); return _verr_c("bind failed"); }
    if (listen(fd, 16) != 0) { close(fd); return _verr_c("listen failed"); }
    int slot = _vsk_put(fd);
    if (slot < 0) { close(fd); return _verr_c("too many sockets"); }
    return _vok_int(slot);
}
BFN Val vtcp_connect(Val addr) {
    if (addr.tag != V_STR) return _verr_c("tcp_connect wants a string address");
    char host[256]; int port = 0;
    if (!_vhost_port(addr.as.str, host, sizeof host, &port))
        return _verr_c("address wants host:port with port 1..65535");
    unsigned long ip = inet_addr(host);
    if (ip == INADDR_NONE) return _verr_c("host must be an IPv4 address");
    int fd = (int)socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return _verr_c("socket failed");
    _vsk_nosigpipe(fd);
    _vsk_timeouts(fd);
    struct sockaddr_in sa;
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;
    sa.sin_port = htons((unsigned short)port);
    sa.sin_addr.s_addr = (in_addr_t)ip;
    if (connect(fd, (struct sockaddr *)&sa, sizeof sa) != 0) { close(fd); return _verr_c("connect failed"); }
    int slot = _vsk_put(fd);
    if (slot < 0) { close(fd); return _verr_c("too many sockets"); }
    return _vok_int(slot);
}
BFN Val vtcp_accept(Val slot) {
    int lfd = _vsk_get((long)vlong(slot));
    if (lfd < 0) return _verr_c("bad socket slot");
    int fd = (int)accept(lfd, NULL, NULL);
    if (fd < 0) return _verr_c("accept failed");
    _vsk_nosigpipe(fd);
    _vsk_timeouts(fd);
    int ns = _vsk_put(fd);
    if (ns < 0) { close(fd); return _verr_c("too many sockets"); }
    return _vok_int(ns);
}

BFN Val vtcp_recv(Val slot, Val n) {
    long want = (long)vlong(n);
    if (want < 0 || want > _VRECV_MAX) return _verr_c("recv wants 0 <= n <= 1048576");
    int fd = _vsk_get((long)vlong(slot));
    if (fd < 0) return _verr_c("bad socket slot");
    char *buf = (char *)malloc((size_t)want + 1);
    if (!buf) vfatal_oom();
    long got = (long)recv(fd, buf, (size_t)want, 0);
    if (got < 0) { free(buf); return _verr_c("recv failed"); }
    buf[got] = 0;
    if (!vutf8_ok(buf)) { free(buf); return verr("invalid utf-8"); }
    Vec *a = vvec();
    vec_push(a, vstrn(buf, got));
    free(buf);
    return vtagv("ok", a);
}
BFN Val vtcp_send(Val slot, Val data) {
    if (data.tag != V_STR) return _verr_c("tcp_send wants string data");
    int fd = _vsk_get((long)vlong(slot));
    if (fd < 0) return _verr_c("bad socket slot");
    const char *p = data.as.str;
    size_t left = strlen(p);
    size_t done = 0;
    while (left > 0) {
#if defined(_WIN32)
        int k = send(fd, p + done, (int)left, 0);
#else
        ssize_t k = send(fd, p + done, left, _VSEND_FLAGS);
#endif
        if (k < 0) {
            if (errno == EINTR) continue;
            return _verr_c("send failed");
        }
        if (k == 0) return _verr_c("send made no progress");
        done += (size_t)k;
        left -= (size_t)k;
    }
    return _vok_int((long)done);
}
BFN Val vtcp_close(Val slot) {
    int fd = _vsk_take((long)vlong(slot));
    if (fd < 0) return _verr_c("bad socket slot");
    close(fd);
    return _vok_int(0);
}
/* own(x): a deep copy that lives on the heap, so it survives the arena that
 * made x. Arena allocation is suspended for the copy. */
static Val vown_copy(Val v) {
    switch (v.tag) {
    case V_STR:
        return v.as.str ? vstr(v.as.str) : v;
    case V_VEC: {
        if (!v.as.vec) return v;
        Vec *dst = vvec();
        for (long i = 0; i < v.as.vec->len; i++) vec_push(dst, vown_copy(v.as.vec->items[i]));
        return vvecv(dst);
    }
    case V_STRUCT: {
        if (!v.as.map) return v;
        Map *src = v.as.map;
        Map *dst = vmap(src->type);
        for (long i = 0; i < src->len; i++)
            map_set_v(dst, vown_copy(src->keys[i]), vown_copy(src->vals[i]));
        return vstruct(dst);
    }
    case V_TAG: {
        Vec *args = v.as.tv.args;
        if (!args) return vtagv(v.as.tv.tag, NULL);
        Vec *dst = vvec();
        for (long i = 0; i < args->len; i++) vec_push(dst, vown_copy(args->items[i]));
        return vtagv(v.as.tv.tag, dst);
    }
    default:
        return v;
    }
}
BFN Val vown(Val v) {
    int saved = barena_depth;
    barena_depth = 0;
    Val r = vown_copy(v);
    barena_depth = saved;
    return r;
}

BFN Val vwritefile(Val p, Val t) {
    if (p.tag != V_STR || t.tag != V_STR) return verr("write_file wants string path and text");
    const char *rp = vconfine(p.as.str);
    if (!rp) return verr("path outside the program directory");
    errno = 0;
    FILE *f = fopen(rp, "wb");
    if (!f) return verr_os(rp);
    if (!_vfd_confined(fileno(f))) { fclose(f); return verr("path escaped during open"); }
    size_t n = strlen(t.as.str);
    fwrite(t.as.str, 1, n, f);
    fclose(f);
    Vec *a = vvec();
    vec_push(a, vint((bint)n));
    return vtagv("ok", a);
}
BFN Val vstr_of(Val v) {
    if (v.tag == V_STR) return vstr(v.as.str);
    
    long n = vsprint_len_q(v, 0);
    char *b = (char *)malloc(n + 1);
    vsprint_q(v, b, 0);
    b[n] = 0;
    Val r = vstrn(b, n);
    free(b);
    return r;
}
BFN Val vsplit(Val s, Val sep) {
    Vec *out = vvec();
    if (s.tag != V_STR) { vraise("no method split"); return vunwind; }
    const char *p = s.as.str;
    if (sep.tag != V_STR) {
        while (*p) {
            while (*p == ' ' || *p == '\t' || *p == '\n') p++;
            if (!*p) break;
            const char *q = p;
            while (*q && *q != ' ' && *q != '\t' && *q != '\n') q++;
            vec_push(out, vstrn(p, q - p));
            p = q;
        }
        return vvecv(out);
    }
    size_t dl = strlen(sep.as.str);
    if (!dl) { vec_push(out, vstr(p)); return vvecv(out); }
    const char *q;
    while ((q = strstr(p, sep.as.str)) != 0) {
        vec_push(out, vstrn(p, q - p));
        p = q + dl;
    }
    vec_push(out, vstr(p));
    return vvecv(out);
}
BFN Val vtrim(Val s) {
    if (s.tag != V_STR) { vraise("no method trim"); return vunwind; }
    const char *p = s.as.str;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
    long n = strlen(p);
    while (n > 0 && (p[n-1] == ' ' || p[n-1] == '\t' || p[n-1] == '\n' || p[n-1] == '\r')) n--;
    return vstrn(p, n);
}

BFN long vcp_next(const char *p, unsigned *out) {
    unsigned c = (unsigned char)p[0];
    long n = 1;
    if (c >= 0xF0) { c &= 0x07; n = 4; }
    else if (c >= 0xE0) { c &= 0x0F; n = 3; }
    else if (c >= 0xC0) { c &= 0x1F; n = 2; }
    /* p[n-1] may be past the NUL when the sequence is truncated. */
    long have = 1;
    while (have < n && p[have]) have++;
    n = have;
    for (long i = 1; i < n; i++) c = (c << 6) | ((unsigned char)p[i] & 0x3F);
    *out = c;
    return n;
}


BFN long vcp_put(unsigned cp, char *out) {
    if (cp < 0x80) { out[0] = (char)cp; return 1; }
    if (cp < 0x800) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        out[0] = (char)(0xE0 | (cp >> 12));
        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (char)(0xF0 | (cp >> 18));
    out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}


BFN unsigned vcp_upper(unsigned c) {
    if (c >= 'a' && c <= 'z') return c - 32;
    if (c >= 0xE0 && c <= 0xFE && c != 0xF7) return c - 32;        
    if (c >= 0x100 && c <= 0x17F && (c & 1)) return c - 1;         
    if (c >= 0x1C4 && c <= 0x1CC && (c & 1)) return c - 1;         
    if (c >= 0x391 && c <= 0x3AB && c != 0x3A2) {                  
        if (c >= 0x3AC) return c - 0x40;                           
        return c - 32;
    }
    if (c >= 0x430 && c <= 0x44F) return c - 32;                   
    if (c >= 0x450 && c <= 0x45F) return c - 80;                   
    return 0;
}


BFN unsigned vcp_lower(unsigned c) {
    if (c >= 'A' && c <= 'Z') return c + 32;
    if (c >= 0xC0 && c <= 0xDE && c != 0xD7) return c + 32;
    if (c >= 0x100 && c <= 0x17F && !(c & 1)) return c + 1;
    if (c >= 0x1C4 && c <= 0x1CC && !(c & 1)) return c + 1;
    
    if (c >= 0x391 && c <= 0x3A9 && c != 0x3A2) return c + 32;
    if (c >= 0x386 && c <= 0x38F) {
        
        switch (c) {
        case 0x386: case 0x388: case 0x389: case 0x38A:
        case 0x38C: case 0x38E: case 0x38F:
            return c + 0x40;
        default:
            return 0;
        }
    }
    if (c >= 0x400 && c <= 0x40F) return c + 80;
    if (c >= 0x410 && c <= 0x42F) return c + 32;
    return 0;
}


BFN unsigned vcp_lower_ctx(unsigned c, unsigned prev, const char *rest) {
    unsigned out = vcp_lower(c);
    if (!out) return 0;
    if (out == 0x3C3) {
        
        int prev_cased = (prev >= 'A' && prev <= 'Z') || (prev >= 0x391 && prev <= 0x3A9)
                      || (prev >= 0x410 && prev <= 0x44F);
        unsigned next = 0;
        if (*rest) vcp_next(rest, &next);
        int next_cased = (next >= 'a' && next <= 'z') || (next >= 'A' && next <= 'Z')
                      || (next >= 0x3B1 && next <= 0x3C9) || (next >= 0x430 && next <= 0x44F);
        if (prev_cased && !next_cased) return 0x3C2;
    }
    return out;
}

BFN Val vcase(Val s, int up) {
    if (s.tag != V_STR) { vraise("no method case"); return vunwind; }
    size_t len = strlen(s.as.str) + 8;
    char *o = (char *)malloc(len);
    if (!o) vfatal_oom();
    long on = 0;
    unsigned prev = 0;
    for (const char *p = s.as.str; *p; ) {
        unsigned cp = 0;
        long n = vcp_next(p, &cp);
        const char *rest = p + n;
        if (up && cp == 0xDF) {
            
            if (on + 4 >= (long)len) { len = len * 2 + 8; o = (char *)bgrow(o, len); }
            o[on++] = 'S'; o[on++] = 'S';
            p += n;
            prev = cp;
            continue;
        }
        unsigned m = up ? vcp_upper(cp) : vcp_lower_ctx(cp, prev, rest);
        if (!m) { m = cp; }
        if (m == cp) {
            if (on + 4 >= (long)len) { len = len * 2 + 8; o = (char *)bgrow(o, len); }
            memcpy(o + on, p, (size_t)n);
            on += n;
        } else {
            if (on + 5 >= (long)len) { len = len * 2 + 8; o = (char *)bgrow(o, len); }
            on += vcp_put(m, o + on);
        }
        prev = cp;
        p += n;
    }
    o[on] = 0;
    Val r; r.tag = V_STR; r.as.str = o;
    return r;
}
BFN long vaffix(Val s, Val p, int pre, int suf) {
    if (s.tag != V_STR || p.tag != V_STR) { vraise("no method affix"); return 0; }
    if (pre) return !strncmp(s.as.str, p.as.str, strlen(p.as.str));
    long ls = strlen(s.as.str), lp = strlen(p.as.str);
    if (suf) return lp <= ls && !strcmp(s.as.str + ls - lp, p.as.str);
    return strstr(s.as.str, p.as.str) != 0;
}
BFN Val vreplace(Val s, Val a, Val b) {
    if (s.tag != V_STR || a.tag != V_STR || b.tag != V_STR) { vraise("no method replace"); return vunwind; }
    const char *p = s.as.str, *pat = a.as.str, *rep = b.as.str;
    size_t pl = strlen(pat), rl = strlen(rep);
    if (!pl) {
        size_t sl = strlen(p);
        char *o = (char *)malloc((sl + 1) * (rl + 1) + 1);
        if (!o) vfatal_oom();
        char *w = o;
        strcpy(w, rep); w += rl;
        for (size_t i = 0; i < sl; i++) { *w++ = p[i]; memcpy(w, rep, rl); w += rl; }
        *w = 0;
        Val r; r.tag = V_STR; r.as.str = o;
        return r;
    }
    size_t total = strlen(p) + 1;
    char *o = (char *)malloc(total);
    if (!o) vfatal_oom();
    size_t n = 0;
    const char *q;
    while ((q = strstr(p, pat)) != 0) {
        size_t chunk = (size_t)(q - p);
        size_t need = n + chunk + rl + 1;
        if (need > total) { total = need * 2; o = (char *)bgrow(o, total); }
        memcpy(o + n, p, chunk); n += chunk;
        memcpy(o + n, rep, rl); n += rl;
        p = q + pl;
    }
    size_t rest = strlen(p) + 1;
    if (n + rest > total) o = (char *)bgrow(o, n + rest);
    memcpy(o + n, p, rest);
    Val r; r.tag = V_STR; r.as.str = o;
    return r;
}
BFN Val vindex_of(Val s, Val sub) {
    if (s.tag != V_STR || sub.tag != V_STR) { vraise("no method index_of"); return vunwind; }
    char *p = strstr(s.as.str, sub.as.str);
    if (!p) return vnil();
    
    long cps = 0;
    for (const char *q = s.as.str; q < p; ) {
        unsigned cp = 0;
        q += vcp_next(q, &cp);
        cps++;
    }
    return vint(cps);
}
/* Match brat/runtime.py MAX_STR: refuse absurd repeats before malloc. */
#ifndef BRAT_MAX_STR
#define BRAT_MAX_STR ((size_t)1 << 30)
#endif
BFN Val vrepeat_n(Val s, bint k) {
    if (s.tag != V_STR) { vraise("no method repeat"); return vunwind; }
    size_t sl = strlen(s.as.str);
    if (k <= 0 || !sl) return vstr("");
    if ((size_t)k > (size_t)-1 / sl) { vraise("out of memory"); return vunwind; }
    size_t total = sl * (size_t)k;
    if (total > BRAT_MAX_STR) { vraise("out of memory"); return vunwind; }
    char *o = (char *)malloc(total + 1);
    if (!o) { vraise("out of memory"); return vunwind; }
    for (bint i = 0; i < k; i++) memcpy(o + (size_t)i * sl, s.as.str, sl);
    o[total] = 0;
    Val r; r.tag = V_STR; r.as.str = o;
    return r;
}
BFN Val vrepeat(Val s, Val n) { return vrepeat_n(s, vlong(n)); }

BFN Val vlines(Val s) {
    if (s.tag != V_STR) { vraise("no method lines"); return vunwind; }
    Vec *o = vvec();
    if (!s.as.str[0]) return vvecv(o);
    const char *p = s.as.str;
    const char *start = p;
    for (;;) {
        if (*p == '\n' || *p == '\r') {
            vec_push(o, vstrn(start, (long)(p - start)));
            if (*p == '\r' && p[1] == '\n') p++;
            p++;
            start = p;
            if (!*start) break;
            continue;
        }
        if (*p == 0) {
            vec_push(o, vstrn(start, (long)(p - start)));
            break;
        }
        p++;
    }
    return vvecv(o);
}

BFN Val vsplit_once(Val s, Val sep) {
    if (s.tag != V_STR || sep.tag != V_STR) { vraise("no method split_once"); return vunwind; }
    if (!sep.as.str[0]) {
        Vec *o = vvec();
        vec_push(o, vstr(""));
        vec_push(o, vstr(s.as.str));
        return vvecv(o);
    }
    char *p = strstr(s.as.str, sep.as.str);
    if (!p) return vnil();
    Vec *o = vvec();
    vec_push(o, vstrn(s.as.str, (long)(p - s.as.str)));
    vec_push(o, vstr(p + strlen(sep.as.str)));
    return vvecv(o);
}
BFN Val vparse_int(Val s) {
    if (s.tag != V_STR) { Vec *a = vvec(); return vtagv("err", a); }
    const char *p = s.as.str;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r' || *p == '\f' || *p == '\v') p++;
    const char *q = p;
    if (*q == '+' || *q == '-') q++;
    if (!(*q >= '0' && *q <= '9')) { Vec *a = vvec(); vec_push(a, vstr("not a number")); return vtagv("err", a); }
    unsigned long long acc = 0;
    while (*q >= '0' && *q <= '9') { acc = acc * 10ULL + (unsigned long long)(*q - '0'); q++; }
    while (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r' || *q == '\f' || *q == '\v') q++;
    Vec *a = vvec();
    if (*q) { vec_push(a, vstr("not a number")); return vtagv("err", a); }
    
    bint v = (bint)acc;
    if (*p == '-') v = -v;
    vec_push(a, vint(v));
    return vtagv("ok", a);
}
BFN Val vjoin(Val v, Val sep) {
    if (v.tag != V_VEC || sep.tag != V_STR) { vraise("no method join"); return vunwind; }
    Vec *w = v.as.vec;
    size_t total = 1;
    long i;
    for (i = 0; i < w->len; i++) total += (size_t)vsprint_len_q(w->items[i], 0);
    if (w->len > 1) total += strlen(sep.as.str) * (size_t)(w->len - 1);
    char *out = (char *)malloc(total + 1);
    if (!out) vfatal_oom();
    long on = 0;
    for (i = 0; i < w->len; i++) {
        if (i) { size_t sl = strlen(sep.as.str); memcpy(out + on, sep.as.str, sl); on += (long)sl; }
        on += vsprint_q(w->items[i], out + on, 0);
    }
    out[on] = 0;
    Val r; r.tag = V_STR; r.as.str = out;
    return r;
}
BFN Val vtagof(Val v) {
    if (v.tag == V_NIL) return vstr("none");
    if (v.tag == V_TAG) return vstr(v.as.tv.tag);
    return vstr("plain");
}
BFN bint vlong(Val v) {
    if (v.tag == V_INT) return v.as.ival;
    if (v.tag == V_FLOAT) return (bint)v.as.fval;
    vraise("not a number"); return 0;
}
BFN Vec *vvec_of(Val v) {
    if (v.tag != V_VEC) { vraise("not a vec"); return NULL; }
    return v.as.vec;
}
BFN int vis_err(Val v) {
    return v.tag == V_TAG && !strcmp(v.as.tv.tag, "err");
}
BFN int vis_none(Val v) {
    return v.tag == V_NIL;
}

BFN const char *vtypename(Val v) {
    switch (v.tag) {
    case V_NIL: return "none";
    case V_BOOL: return "bool";
    case V_INT: return "i64";
    case V_FLOAT: return "f64";
    case V_STR: return "str";
    case V_VEC: return "vec";
    case V_STRUCT: return v.as.map->type && v.as.map->type[0] ? v.as.map->type : "map";
    case V_TAG: return v.as.tv.tag ? v.as.tv.tag : "tag";
    }
    return "value";
}
BFN Val vunwrap(Val v) {
    if (v.tag == V_TAG && (!strcmp(v.as.tv.tag, "ok") || !strcmp(v.as.tv.tag, "some")))
        return v.as.tv.args->len ? v.as.tv.args->items[0] : vnil();
    if (v.tag == V_TAG && !strcmp(v.as.tv.tag, "err")) {
        vunwind = v;
        vunwind_do();
    }
    
    vraisef("unwrap of %s (want Ok/Err/Some)", vtypename(v)); return vunwind;
    return vnil();
}
BFN int vor_fb(Val v) {
    return v.tag == V_NIL;
}
BFN const char *vname(Val v) {
    if (v.tag != V_STR) { vraise("want str"); return NULL; }
    return v.as.str;
}
BFN long vsprint_q(Val v, char *o, long q) {
    long n = 0, i;
    char tmp[64];
    switch (v.tag) {
    case V_NIL: memcpy(o, "None", 4); return 4;
    case V_BOOL: memcpy(o, v.as.b ? "True" : "False", v.as.b ? 4 : 5); return v.as.b ? 4 : 5;
    case V_INT: n = snprintf(tmp, sizeof tmp, BINT_FMT, (bint)v.as.ival); memcpy(o, tmp, n); return n;
    case V_FLOAT: { const char *s = vfmt_double(v.as.fval); n = (long)strlen(s); memcpy(o, s, n); return n; }
    case V_STR: {
        if (!q) { n = strlen(v.as.str); memcpy(o, v.as.str, n); return n; }
        char quote = vquote(v.as.str);
        o[n++] = quote;
        for (const char *p = v.as.str; *p; p++) {
            if (*p == quote || *p == '\\') { o[n++] = '\\'; o[n++] = *p; }
            else if (*p == '\n') { o[n++] = '\\'; o[n++] = 'n'; }
            else if (*p == '\r') { o[n++] = '\\'; o[n++] = 'r'; }
            else if (*p == '\t') { o[n++] = '\\'; o[n++] = 't'; }
            else o[n++] = *p;
        }
        o[n++] = quote;
        return n;
    }
    case V_VEC:
        o[n++] = '[';
        for (i = 0; i < v.as.vec->len; i++) {
            if (i) { o[n++] = ','; o[n++] = ' '; }
            n += vsprint_q(v.as.vec->items[i], o + n, 1);
        }
        o[n++] = ']';
        return n;
    case V_STRUCT: {
        int ismap = !v.as.map->type[0];
        o[n++] = '{';
        if (!ismap) {
            n += vput(o + n, "'$type': '");
            n += vput(o + n, v.as.map->type);
            o[n++] = '\'';
        }
        for (i = 0; i < v.as.map->len; i++) {
            if (i || !ismap) { o[n++] = ','; o[n++] = ' '; }
            n += vsprint_q(v.as.map->keys[i], o + n, 1);
            o[n++] = ':'; o[n++] = ' ';
            n += vsprint_q(v.as.map->vals[i], o + n, 1);
        }
        o[n++] = '}';
        return n;
    }
    case V_TAG: {
        o[0] = '('; o[1] = '\''; n = 2;
        n += vput(o + n, v.as.tv.tag);
        o[n++] = '\'';
        for (i = 0; i < v.as.tv.args->len; i++) {
            o[n++] = ','; o[n++] = ' ';
            n += vsprint_q(v.as.tv.args->items[i], o + n, 1);
        }
        if (v.as.tv.args->len == 0) o[n++] = ',';
        o[n++] = ')';
        return n;
    }
    }
    return 0;
}

BFN long vsprint(Val v, char *o) { return vsprint_q(v, o, 0); }

BFN long vsprint_len_q(Val v, long q) {
    long n = 0, i;
    char tmp[64];
    switch (v.tag) {
    case V_NIL: return 4;
    case V_BOOL: return v.as.b ? 4 : 5;
    case V_INT: return snprintf(tmp, sizeof tmp, BINT_FMT, (bint)v.as.ival);
    case V_FLOAT: return (long)strlen(vfmt_double(v.as.fval));
    case V_STR: return q ? vrepr_slen(v.as.str) + 2 : (long)strlen(v.as.str);
    case V_VEC:
        n = 2;
        for (i = 0; i < v.as.vec->len; i++) {
            if (i) n += 2;
            n += vsprint_len_q(v.as.vec->items[i], 1);
        }
        return n;
    case V_STRUCT: {
        int ismap = !v.as.map->type[0];
        n = 1;
        if (!ismap) n += 11 + (long)strlen(v.as.map->type);
        for (i = 0; i < v.as.map->len; i++) {
            if (i || !ismap) n += 2;
            n += vsprint_len_q(v.as.map->keys[i], 1) + 2 + vsprint_len_q(v.as.map->vals[i], 1);
        }
        return n + 1;
    }
    case V_TAG: {
        n = 3 + (long)strlen(v.as.tv.tag);
        for (i = 0; i < v.as.tv.args->len; i++) n += 2 + vsprint_len_q(v.as.tv.args->items[i], 1);
        if (v.as.tv.args->len == 0) n += 1;
        return n + 1;
    }
    }
    return 0;
}

BFN long vsprint_len(Val v) { return vsprint_len_q(v, 0); }
BFN void vlogv(const char *lvl, Vec *a) {
    long total = 0, i;
    for (i = 0; i < a->len; i++) {
        if (i) total += 1;
        total += vsprint_len(a->items[i]);
    }
    char *buf = (char *)malloc(total + 1);
    if (!buf) vfatal_oom();
    long n = 0;
    for (i = 0; i < a->len; i++) {
        if (i) buf[n++] = ' ';
        n += vsprint(a->items[i], buf + n);
    }
    buf[n] = 0;
    
    printf("{\"ts\": %.7f, \"lvl\": \"%s\", \"msg\": \"", vtime(), lvl);
    for (char *p = buf; *p; p++) {
        if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
        else if (*p == '\n') printf("\\n");
        else if (*p == '\r') printf("\\r");
        else if (*p == '\t') printf("\\t");
        else if ((unsigned char)*p < 0x20) printf("\\u%04x", (unsigned char)*p);
        else putchar((unsigned char)*p);
    }
    printf("\"}\n");
    fflush(stdout);
    free(buf);
}
BFN Val vpush(Val v, Val x) {
    Vec *w = vvec_of(v);
    vec_push(w, x);
    return vint(w->len);
}
BFN Val vpop(Val v) {
    Vec *w = vvec_of(v);
    if (!w->len) return vnil();
    return w->items[--w->len];
}
BFN Val vfirst(Val v) {
    Vec *w = vvec_of(v);
    if (!w->len) return vnil();
    return w->items[0];
}
BFN Val vlast(Val v) {
    Vec *w = vvec_of(v);
    if (!w->len) return vnil();
    return w->items[w->len - 1];
}
static int vcmp_q(const void *pa, const void *pb) {
    return vcmp(*(const Val *)pa, *(const Val *)pb);
}
BFN Val vsort(Val v) {
    Vec *w = vvec_of(v);
    if (w->len > 1) qsort(w->items, (size_t)w->len, sizeof(Val), vcmp_q);
    return v;
}
BFN Val vrev(Val v) {
    Vec *w = vvec_of(v);
    Vec *o = vvec();
    for (long i = w->len - 1; i >= 0; i--) vec_push(o, w->items[i]);
    return vvecv(o);
}
BFN Val vins(Val v, Val i, Val x) {
    Vec *w = vvec_of(v);
    long k = vlong(i);
    
    if (k < 0) k = (k < -w->len) ? 0 : k + w->len;
    if (k > w->len) k = w->len;
    vec_push(w, x);
    memmove(w->items + k + 1, w->items + k, sizeof(Val) * (size_t)(w->len - 1 - k));
    w->items[k] = x;
    return vint(w->len);
}
BFN Val vdel_at(Val v, Val i) {
    Vec *w = vvec_of(v);
    long k = vlong(i);
    
    if (k < 0) {
        if (k < -w->len) { vraise("index out of range"); return vunwind; }
        k += w->len;
    }
    if (k < 0 || k >= w->len) { vraise("index out of range"); return vunwind; }
    Val r = w->items[k];
    memmove(w->items + k, w->items + k + 1, sizeof(Val) * (size_t)(w->len - 1 - k));
    w->len--;
    return r;
}
BFN Val vslice(Val v, Val a, Val b) {
    Vec *w = vvec_of(v);
    long n = w->len, x = vlong(a), y = vlong(b);
    if (x < 0) x = (x < -n) ? 0 : x + n;
    if (x < 0) x = 0;
    if (x > n) x = n;
    if (y < 0) y = (y < -n) ? 0 : y + n;
    if (y < 0) y = 0;
    if (y > n) y = n;
    Vec *o = vvec();
    for (long i = x; i < y; i++) vec_push(o, w->items[i]);
    return vvecv(o);
}
BFN Val vsum(Val v) {
    Vec *w = vvec_of(v);
    
    unsigned long long acc = 0;
    double dl = 0;
    int isf = 0;
    for (long i = 0; i < w->len; i++) {
        Val x = w->items[i];
        if (x.tag == V_INT || x.tag == V_BOOL) acc += (unsigned long long)x.as.ival;
        else if (x.tag == V_FLOAT) { dl += x.as.fval; isf = 1; }
        else { vraise("sum of non-numeric"); return vunwind; }
    }
    if (isf) return vfloat(dl + (double)(bint)acc);
    return vint((bint)acc);
}
BFN Val vmin(Val v) {
    Vec *w = vvec_of(v);
    if (!w->len) return vnil();
    Val r = w->items[0];
    for (long i = 1; i < w->len; i++) if (vcmp(w->items[i], r) < 0) r = w->items[i];
    return r;
}
BFN Val vmax(Val v) {
    Vec *w = vvec_of(v);
    if (!w->len) return vnil();
    Val r = w->items[0];
    for (long i = 1; i < w->len; i++) if (vcmp(w->items[i], r) > 0) r = w->items[i];
    return r;
}
BFN Val venum(Val v) {
    Vec *w = vvec_of(v);
    Vec *o = vvec();
    for (long i = 0; i < w->len; i++) {
        Vec *p = vvec();
        vec_push(p, vint(i));
        vec_push(p, w->items[i]);
        vec_push(o, vvecv(p));
    }
    return vvecv(o);
}
BFN Val vmap_get_or(Val m, Val k, Val d) {
    if (m.tag != V_STRUCT) { vraise("get_or of non-map"); return vunwind; }
    Val *o = map_get_v(m.as.map, k);
    return o ? *o : d;
}
BFN Val vmap_items(Val m) {
    if (m.tag != V_STRUCT) { vraise("items of non-map"); return vunwind; }
    Vec *o = vvec();
    for (long i = 0; i < m.as.map->len; i++) {
        Vec *p = vvec();
        vec_push(p, m.as.map->keys[i]);
        vec_push(p, m.as.map->vals[i]);
        vec_push(o, vvecv(p));
    }
    return vvecv(o);
}
BFN Val vmap_vals(Val m) {
    if (m.tag != V_STRUCT) { vraise("vals of non-map"); return vunwind; }
    Vec *o = vvec();
    for (long i = 0; i < m.as.map->len; i++) vec_push(o, m.as.map->vals[i]);
    return vvecv(o);
}
BFN Val vcopy(Val v) {
    if (v.tag == V_VEC) {
        Vec *o = vvec();
        for (long i = 0; i < v.as.vec->len; i++) vec_push(o, v.as.vec->items[i]);
        return vvecv(o);
    }
    if (v.tag == V_STRUCT) {
        Map *m = vmap(v.as.map->type);
        for (long i = 0; i < v.as.map->len; i++) map_set_v(m, v.as.map->keys[i], v.as.map->vals[i]);
        return vstruct(m);
    }
    if (v.tag == V_STR) return v;
    vraise("copy of non-container"); return vunwind;
}
BFN Val vchars(Val s) {
    if (s.tag != V_STR) { vraise("no method chars"); return vunwind; }
    Vec *o = vvec();
    for (const char *p = s.as.str; *p; p += utf8_cplen(p)) vec_push(o, vstrn(p, utf8_cplen(p)));
    return vvecv(o);
}
BFN Val vstr_slice(Val s, Val a, Val b) {
    if (s.tag != V_STR) { vraise("no method slice"); return vunwind; }
    long n = 0;
    for (const char *q = s.as.str; *q; q += utf8_cplen(q)) n++;
    long x = vlong(a), y = vlong(b);
    if (x < 0) x = (x < -n) ? 0 : x + n;
    if (x < 0) x = 0;
    if (x > n) x = n;
    if (y < 0) y = (y < -n) ? 0 : y + n;
    if (y < 0) y = 0;
    if (y > n) y = n;
    if (y <= x) return vstr("");
    const char *p = utf8_at(s.as.str, x);
    const char *q = utf8_at(s.as.str, y);
    return vstrn(p, (long)(q - p));
}
BFN Val vparse_float(Val s) {
    if (s.tag != V_STR) { Vec *a = vvec(); return vtagv("err", a); }
    char *e;
    const char *p = s.as.str;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r' || *p == '\f' || *p == '\v') p++;
    double v = strtod(p, &e);
    while (*e == ' ' || *e == '\t' || *e == '\n' || *e == '\r' || *e == '\f' || *e == '\v') e++;
    Vec *a = vvec();
    
    if (e == p || *e || (p[0] == '0' && (p[1] == 'x' || p[1] == 'X'))) { vec_push(a, vstr("not a float")); return vtagv("err", a); }
    vec_push(a, vfloat(v));
    return vtagv("ok", a);
}
BFN Val vzip(Val a, Val b) {
    Vec *x = vvec_of(a);
    Vec *y = vvec_of(b);
    long n = x->len < y->len ? x->len : y->len;
    Vec *o = vvec();
    for (long i = 0; i < n; i++) {
        Vec *p = vvec();
        vec_push(p, x->items[i]);
        vec_push(p, y->items[i]);
        vec_push(o, vvecv(p));
    }
    return vvecv(o);
}
BFN Val vchunks(Val v, Val n) {
    Vec *w = vvec_of(v);
    long k = vlong(n);
    if (k <= 0) { vraise("chunks wants n > 0"); return vunwind; }
    Vec *o = vvec();
    for (long i = 0; i < w->len; i += k) {
        Vec *p = vvec();
        for (long j = i; j < i + k && j < w->len; j++) vec_push(p, w->items[j]);
        vec_push(o, vvecv(p));
    }
    return vvecv(o);
}

BFN void vjprint(Val v) {
    long i;
    switch (v.tag) {
    case V_NIL: printf("null"); break;
    case V_BOOL: printf(v.as.b ? "true" : "false"); break;
    case V_INT: printf(BINT_FMT, (bint)v.as.ival); break;
    case V_FLOAT: printf("%s", vfmt_double(v.as.fval)); break;
    case V_STR:
        printf("\"");
        for (const char *p = v.as.str; *p; p++) {
            if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
            else if (*p == '\n') printf("\\n");
            else if (*p == '\r') printf("\\r");
            else if (*p == '\t') printf("\\t");
            else if ((unsigned char)*p < 0x20) printf("\\u%04x", (unsigned char)*p);
            else putchar((unsigned char)*p);
        }
        printf("\"");
        break;
    case V_VEC:
        printf("[");
        for (i = 0; i < v.as.vec->len; i++) { if (i) printf(", "); vjprint(v.as.vec->items[i]); }
        printf("]");
        break;
    case V_STRUCT: {
        int ismap = !v.as.map->type[0];
        printf("{");
        if (!ismap) printf("\"$type\": \"%s\"", v.as.map->type);
        for (i = 0; i < v.as.map->len; i++) {
            if (i || !ismap) printf(", ");
            vjprint(v.as.map->keys[i]);
            printf(": ");
            vjprint(v.as.map->vals[i]);
        }
        printf("}");
        break;
    }
    case V_TAG: {
        
        printf("[");
        printf("\"%s\"", v.as.tv.tag);
        for (i = 0; i < v.as.tv.args->len; i++) { printf(", "); vjprint(v.as.tv.args->items[i]); }
        printf("]");
        break;
    }
    default:
        vprint(v);
        break;
    }
}

BFN void vspan_items(Val v) {
    if (v.tag == V_VEC) {
        long i;
        for (i = 0; i < v.as.vec->len; i++) { if (i) printf(", "); vjprint(v.as.vec->items[i]); }
    } else {
        vjprint(v);
    }
}

BFN void vspan(Val v) {
    printf("{\"ts\": %.7f, \"span\": [", vtime());
    vspan_items(v);
    printf("]}\n");
    fflush(stdout);
}

BFN void vjson_str(const char *s) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"') fputs("\\\"", stdout);
        else if (*p == '\\') fputs("\\\\", stdout);
        else if (*p == '\n') fputs("\\n", stdout);
        else if (*p == '\r') fputs("\\r", stdout);
        else if (*p == '\t') fputs("\\t", stdout);
        else if (*p == '\b') fputs("\\b", stdout);
        else if (*p == '\f') fputs("\\f", stdout);
        else if (*p < 0x20) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

BFN void vfieldset(Val s, const char *k, Val v) {
    Val *o;
    if (s.tag != V_STRUCT || !(o = map_get(s.as.map, k))) { vraise("bad field set"); return; }
    *o = v;
}
BFN Val vcall_any(Val fn, Vec *a);
BFN Val vfold(Val obj, Val init, Val fn) {
    Vec *w = vvec_of(obj);
    Val acc = init;
    Vec *args = vvec();
    vec_push(args, init);
    vec_push(args, init);
    for (long i = 0; i < w->len; i++) {
        args->items[0] = acc;
        args->items[1] = w->items[i];
        acc = vcall_any(fn, args);
    }
    return acc;
}
BFN Val vpred(Val obj, Val fn, const char *meth) {
    Vec *w = vvec_of(obj);
    Vec *one = vvec();
    vec_push(one, vnil());
    for (long i = 0; i < w->len; i++) {
        one->items[0] = w->items[i];
        Val r = vcall_any(fn, one);
        if (meth[0] == 'a' && meth[1] == 'n' && vis_true(r)) return vbool(1);
        if (meth[0] == 'a' && meth[1] == 'l' && !vis_true(r)) return vbool(0);
        if (meth[0] == 'f' && vis_true(r)) return w->items[i];
    }
    if (meth[0] == 'f') return vnil();
    return vbool(meth[1] == 'l' ? 1 : 0);
}
BFN Val vpos(Val v, Val x) {
    Vec *w = vvec_of(v);
    for (long i = 0; i < w->len; i++) if (veq(w->items[i], x)) return vint(i);
    return vnil();
}

#define BRT_VDFR_MAX 1024
BTLS Vec **vdfr;
BTLS long vdfsp;
BTLS long vdfcap;
BFN void vdfpush(void) {
    if (vdfsp >= vdfcap) {
        long nc = vdfcap ? vdfcap * 2 : BRT_VDFR_MAX;
        Vec **np = realloc(vdfr, (size_t)nc * sizeof(Vec *));
        if (!np) { vraise("out of memory"); return; }
        vdfr = np;
        vdfcap = nc;
    }
    vdfr[vdfsp++] = NULL;
}
BFN void vdfadd(Val clo) {
    if (vdfsp <= 0) { vraise("defer outside a function"); return; }
    if (!vdfr[vdfsp - 1]) { vdfr[vdfsp - 1] = vvec(); }
    vec_push(vdfr[vdfsp - 1], clo);
}

BFN void vdfreset(long base) {
    while (vdfsp > base) {
        Vec *f = vdfr[--vdfsp];
        if (!f) continue;
        for (long i = f->len - 1; i >= 0; --i) {
            /* Run the defer even while an error is unwinding, then restore it. */
            int sp = _verr_pending, se = _verr_exc;
            _verr_pending = 0;
            Val r = vcall_any(f->items[i], vvec());
            (void)r;
            _verr_pending = sp;
            _verr_exc = se;
        }
    }
}
static BTLS Val vsortkey_fn;
static BTLS Vec *vsortby_buf;
static int vsortby_q(const void *pa, const void *pb) {
    if (!vsortby_buf) vsortby_buf = vvec();
    Vec *one = vsortby_buf;
    one->len = 0;
    vec_push(one, *(Val *)pa);
    Val ka = vcall_any(vsortkey_fn, one);
    one->len = 0;
    vec_push(one, *(Val *)pb);
    Val kb = vcall_any(vsortkey_fn, one);
    int r = vcmp(ka, kb);
    return r;
}
BFN Val vsort_by(Val v, Val fn) {
    Vec *w = vvec_of(v);
    vsortkey_fn = fn;
    if (w->len > 1) qsort(w->items, (size_t)w->len, sizeof(Val), vsortby_q);
    return v;
}
#define VARG(a, i) ((a)->len > (i) ? (a)->items[(i)] : vnil())
BFN Val vbuiltin(Val obj, const char *meth, Vec *a) {
    if (obj.tag == V_STR) {
        if (!strcmp(meth, "split")) { if (VARG(a, 0).tag == V_STR && !VARG(a, 0).as.str[0]) { vraise("empty separator"); return vunwind; } return vsplit(obj, VARG(a, 0)); }
        if (!strcmp(meth, "lines")) return vlines(obj);
        if (!strcmp(meth, "trim")) return vtrim(obj);
        if (!strcmp(meth, "lower")) return vcase(obj, 0);
        if (!strcmp(meth, "upper")) return vcase(obj, 1);
        if (!strcmp(meth, "starts_with")) return vbool(vaffix(obj, VARG(a, 0), 1, 0));
        if (!strcmp(meth, "ends_with")) return vbool(vaffix(obj, VARG(a, 0), 0, 1));
        if (!strcmp(meth, "contains")) return vbool(vaffix(obj, VARG(a, 0), 0, 0));
        if (!strcmp(meth, "parse_int")) return vparse_int(obj);
        if (!strcmp(meth, "replace")) return vreplace(obj, VARG(a, 0), VARG(a, 1));
        if (!strcmp(meth, "index_of")) return vindex_of(obj, VARG(a, 0));
        if (!strcmp(meth, "repeat")) return vrepeat(obj, VARG(a, 0));
        if (!strcmp(meth, "split_once")) return vsplit_once(obj, VARG(a, 0));
        if (!strcmp(meth, "copy")) return obj;
        if (!strcmp(meth, "chars")) return vchars(obj);
        if (!strcmp(meth, "parse_float")) return vparse_float(obj);
        if (!strcmp(meth, "slice")) return vstr_slice(obj, VARG(a, 0), VARG(a, 1));
    }
    if (obj.tag == V_VEC) {
        if (!strcmp(meth, "join")) return vjoin(obj, VARG(a, 0));
        if (!strcmp(meth, "push")) return vpush(obj, VARG(a, 0));
        if (!strcmp(meth, "pop")) return vpop(obj);
        if (!strcmp(meth, "first")) return vfirst(obj);
        if (!strcmp(meth, "last")) return vlast(obj);
        if (!strcmp(meth, "sort")) return vsort(obj);
        if (!strcmp(meth, "rev")) return vrev(obj);
        if (!strcmp(meth, "ins")) return vins(obj, VARG(a, 0), VARG(a, 1));
        if (!strcmp(meth, "del")) return vdel_at(obj, VARG(a, 0));
        if (!strcmp(meth, "slice")) return vslice(obj, VARG(a, 0), VARG(a, 1));
        if (!strcmp(meth, "sum")) return vsum(obj);
        if (!strcmp(meth, "min")) return vmin(obj);
        if (!strcmp(meth, "max")) return vmax(obj);
        if (!strcmp(meth, "enumerate")) return venum(obj);
        if (!strcmp(meth, "copy")) return vcopy(obj);
        if (!strcmp(meth, "zip")) return vzip(obj, VARG(a, 0));
        if (!strcmp(meth, "chunks")) return vchunks(obj, VARG(a, 0));
        if (!strcmp(meth, "slice")) return vslice(obj, VARG(a, 0), VARG(a, 1));
        if (!strcmp(meth, "pos")) return vpos(obj, VARG(a, 0));
    }
    if (obj.tag == V_STRUCT) {
        if (!strcmp(meth, "get")) return vmap_getv(obj, VARG(a, 0));
        if (!strcmp(meth, "has")) return vmap_has(obj, VARG(a, 0));
        if (!strcmp(meth, "keys")) return vmap_keys(obj);
        if (!strcmp(meth, "del")) return vmap_del(obj, VARG(a, 0));
        if (!strcmp(meth, "set")) return vmap_setv(obj, VARG(a, 0), VARG(a, 1));
        if (!strcmp(meth, "items")) return vmap_items(obj);
        if (!strcmp(meth, "vals")) return vmap_vals(obj);
        if (!strcmp(meth, "copy")) return vcopy(obj);
        if (!strcmp(meth, "get_or")) return vmap_get_or(obj, VARG(a, 0), VARG(a, 1));
    }
    vraisef("no method %s\n", meth); return vunwind;
}

typedef struct BTask BTask;
struct BTask {
    Val clo;
    Val result;
    int err;
    BTask *next;
};
static BTask *g_done = 0;

/* Lazy long-lived pool: one OS thread per spawn segfaults around 300. */
#define BPOOL_MAX 32
static int g_pool_n = 0;
#if defined(_WIN32)
static HANDLE g_pool[BPOOL_MAX];
#else
static pthread_t g_pool[BPOOL_MAX];
#endif
static BTask *g_q_head = 0;
static BTask *g_q_tail = 0;
static long g_q_nleft = 0;
static long g_parked = 0;
static BTLS int g_btask_self;

#if defined(_WIN32)
static CRITICAL_SECTION btlock;
static int btlock_init;
static void btask_lock(void) {
    if (!btlock_init) { InitializeCriticalSection(&btlock); btlock_init = 1; }
    EnterCriticalSection(&btlock);
}
static void btask_unlock(void) { LeaveCriticalSection(&btlock); }
#else
#include <sched.h>
static pthread_mutex_t btlock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_q_cond = PTHREAD_COND_INITIALIZER;
static pthread_cond_t g_q_idle = PTHREAD_COND_INITIALIZER;
static void btask_lock(void) { pthread_mutex_lock(&btlock); }
static void btask_unlock(void) { pthread_mutex_unlock(&btlock); }
#endif

static int bpool_ncpu(void) {
#if defined(_WIN32)
    return 2;
#else
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return (n >= 1) ? (int)n : 1;
#endif
}

#if defined(_WIN32)
static DWORD WINAPI bpool_worker(void *p);
#else
static void *bpool_worker(void *p);
#endif

static void bpool_start_locked(void) {
    if (g_pool_n) return;
    int n = bpool_ncpu();
    if (n > BPOOL_MAX) n = BPOOL_MAX;
    if (n < 1) n = 1;
    g_pool_n = n;
    for (int i = 0; i < n; i++) {
#if defined(_WIN32)
        g_pool[i] = CreateThread(NULL, 0, bpool_worker, NULL, 0, NULL);
        if (!g_pool[i]) { g_pool_n = i; return; }
#else
        if (pthread_create(&g_pool[i], NULL, bpool_worker, NULL) != 0) {
            g_pool_n = i;
            return;
        }
#endif
    }
}

BFN Val verr_own(Val v) {
    if (v.tag != V_TAG || strcmp(v.as.tv.tag, "err")) return v;
    Vec *a = vvec();
    for (long i = 0; i < v.as.tv.args->len; i++) vec_push(a, v.as.tv.args->items[i]);
    return vtagv("err", a);
}

static void bpool_wake(void) {
#if !defined(_WIN32)
    pthread_cond_signal(&g_q_cond);
#endif
}

static void bpool_idle_wake(void) {
#if !defined(_WIN32)
    pthread_cond_broadcast(&g_q_idle);
#endif
}

static void btask_run(BTask *t) {
    int outer = g_btask_self;
    g_btask_self = 1;
    verr_clear();
    Vec *a = vvec();
    t->result = vcall_any(t->clo, a);
    if (verr_pending()) {
        t->err = 1;
        t->result = verr_own(verr_take());
    } else if (t->result.tag == V_TAG && !strcmp(t->result.as.tv.tag, "err")) {
        t->err = 1;
    }
    g_btask_self = outer;

    btask_lock();
    g_q_nleft--;
    t->next = g_done;
    g_done = t;
    bpool_idle_wake();
    btask_unlock();
}

#if defined(_WIN32)
static DWORD WINAPI bpool_worker(void *p) {
#else
static void *bpool_worker(void *p) {
#endif
    (void)p;
    for (;;) {
        btask_lock();
        while (g_q_head == 0) {
#if defined(_WIN32)
            btask_unlock();
            Sleep(0);
            btask_lock();
#else
            pthread_cond_wait(&g_q_cond, &btlock);
#endif
        }
        BTask *t = g_q_head;
        g_q_head = t->next;
        if (!g_q_head) g_q_tail = 0;
        btask_unlock();
        btask_run(t);
    }
#if defined(_WIN32)
    return 0;
#else
    return NULL;
#endif
}

BFN void vspawn(Val clo) {
    if (clo.tag != V_TAG || strcmp(clo.as.tv.tag, "closure") || clo.as.tv.args->len != 2) {
        vraise("spawn wants closure"); return;
    }

    BTask *t = (BTask *)calloc(1, sizeof(BTask));
    if (!t) { vraise("spawn failed: out of memory"); return; }
    t->clo = clo;
    btask_lock();
    bpool_start_locked();
    t->next = 0;
    if (g_q_tail) g_q_tail->next = t;
    else g_q_head = t;
    g_q_tail = t;
    g_q_nleft++;
    bpool_wake();
    btask_unlock();
}

BFN void vjoin_all(void) {
    int self = g_btask_self;
    btask_lock();
    if (self) g_parked++;
    for (;;) {
        /* Wait until every task but the parked join_all callers is done, running
           queued tasks here: otherwise a nested join_all starves its children. */
        long target = self ? g_parked : 0;
        if (g_q_head == 0 && g_q_nleft <= target) break;
        if (g_q_head) {
            BTask *t = g_q_head;
            g_q_head = t->next;
            if (!g_q_head) g_q_tail = 0;
            btask_unlock();
            btask_run(t);
            btask_lock();
            continue;
        }
#if defined(_WIN32)
        btask_unlock();
        Sleep(0);
        btask_lock();
#else
        pthread_cond_wait(&g_q_idle, &btlock);
#endif
    }
    BTask *list = g_done;
    g_done = 0;
    if (self) g_parked--;
    btask_unlock();
    int err = 0;
    Val errv = vnil();
    while (list) {
        BTask *nx = list->next;
        if (list->err && !err) { err = 1; errv = list->result; }
        free(list);
        list = nx;
    }
    if (err) { vunwind = errv; vunwind_do(); }
}

#endif
