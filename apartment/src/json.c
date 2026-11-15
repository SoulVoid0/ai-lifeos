/* A small JSON reader (enough for the LifeOS API) and a string escaper for request bodies. */
#include "app.h"
#include <stdlib.h>

typedef struct { const char *p; int depth; } P;

static void ws(P *p) { while (*p->p == ' ' || *p->p == '\n' || *p->p == '\r' || *p->p == '\t') p->p++; }

static bool parse_value(P *p, J *out);

static void put_utf8(char **o, unsigned cp)
{
    char *s = *o;
    if (cp < 0x80) *s++ = (char)cp;
    else if (cp < 0x800) { *s++ = (char)(0xC0 | (cp >> 6)); *s++ = (char)(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { *s++ = (char)(0xE0 | (cp >> 12)); *s++ = (char)(0x80 | ((cp >> 6) & 0x3F)); *s++ = (char)(0x80 | (cp & 0x3F)); }
    else { *s++ = (char)(0xF0 | (cp >> 18)); *s++ = (char)(0x80 | ((cp >> 12) & 0x3F)); *s++ = (char)(0x80 | ((cp >> 6) & 0x3F)); *s++ = (char)(0x80 | (cp & 0x3F)); }
    *o = s;
}

static unsigned hex4(const char *s)
{
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = s[i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
        else return 0xFFFFFFFFu;
    }
    return v;
}

static char *parse_string(P *p)
{
    if (*p->p != '"') return NULL;
    const char *s = ++p->p;
    size_t n = 0;
    while (s[n] && s[n] != '"') { if (s[n] == '\\' && s[n + 1]) n++; n++; }
    if (!s[n]) return NULL;
    char *out = malloc(n + 1), *o = out;
    while (*p->p && *p->p != '"') {
        char c = *p->p++;
        if (c != '\\') { *o++ = c; continue; }
        c = *p->p++;
        switch (c) {
        case 'n': *o++ = '\n'; break;
        case 't': *o++ = '\t'; break;
        case 'r': *o++ = '\r'; break;
        case 'b': *o++ = '\b'; break;
        case 'f': *o++ = '\f'; break;
        case 'u': {
            unsigned cp = hex4(p->p);
            if (cp == 0xFFFFFFFFu) { free(out); return NULL; }
            p->p += 4;
            if (cp >= 0xD800 && cp < 0xDC00 && p->p[0] == '\\' && p->p[1] == 'u') {
                unsigned lo = hex4(p->p + 2);
                if (lo >= 0xDC00 && lo < 0xE000) { cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00); p->p += 6; }
            }
            put_utf8(&o, cp);
            break;
        }
        default: *o++ = c;
        }
    }
    *o = 0;
    if (*p->p == '"') p->p++;
    return out;
}

static bool parse_container(P *p, J *out, bool obj)
{
    out->t = obj ? J_OBJ : J_ARR;
    p->p++;
    int cap = 0;
    ws(p);
    if (*p->p == (obj ? '}' : ']')) { p->p++; return true; }
    for (;;) {
        ws(p);
        char *key = NULL;
        if (obj) {
            key = parse_string(p);
            if (!key) return false;
            ws(p);
            if (*p->p != ':') { free(key); return false; }
            p->p++;
        }
        if (out->count == cap) {
            cap = cap ? cap * 2 : 8;
            out->items = realloc(out->items, sizeof(J) * cap);
            if (obj) out->keys = realloc(out->keys, sizeof(char *) * cap);
        }
        J *it = &out->items[out->count];
        memset(it, 0, sizeof *it);
        if (obj) out->keys[out->count] = key;
        out->count++;
        if (!parse_value(p, it)) return false;
        ws(p);
        if (*p->p == ',') { p->p++; continue; }
        if (*p->p == (obj ? '}' : ']')) { p->p++; return true; }
        return false;
    }
}

static bool parse_value(P *p, J *out)
{
    ws(p);
    if (++p->depth > 64) return false;
    bool ok = true;
    char c = *p->p;
    if (c == '{' || c == '[') ok = parse_container(p, out, c == '{');
    else if (c == '"') { out->t = J_STR; out->s = parse_string(p); ok = out->s != NULL; }
    else if (!strncmp(p->p, "true", 4)) { out->t = J_BOOL; out->n = 1; p->p += 4; }
    else if (!strncmp(p->p, "false", 5)) { out->t = J_BOOL; out->n = 0; p->p += 5; }
    else if (!strncmp(p->p, "null", 4)) { out->t = J_NULL; p->p += 4; }
    else {
        char *end;
        out->t = J_NUM;
        out->n = strtod(p->p, &end);
        ok = end != p->p;
        p->p = end;
    }
    p->depth--;
    return ok;
}

static void free_inner(J *j)
{
    if (!j) return;
    free(j->s);
    for (int i = 0; i < j->count; i++) {
        free_inner(&j->items[i]);
        if (j->keys) free(j->keys[i]);
    }
    free(j->items);
    free(j->keys);
}

J *json_parse(const char *text)
{
    if (!text) return NULL;
    J *j = calloc(1, sizeof *j);
    P p = {text, 0};
    if (!parse_value(&p, j)) { free_inner(j); free(j); return NULL; }
    return j;
}

void json_free(J *j) { if (j) { free_inner(j); free(j); } }

J *jget(J *o, const char *key)
{
    if (!o || o->t != J_OBJ) return NULL;
    for (int i = 0; i < o->count; i++) if (!strcmp(o->keys[i], key)) return &o->items[i];
    return NULL;
}

J *jat(J *a, int i) { return a && (a->t == J_ARR || a->t == J_OBJ) && i >= 0 && i < a->count ? &a->items[i] : NULL; }

const char *jstr(J *o, const char *key, const char *def)
{
    J *v = key ? jget(o, key) : o;
    return v && v->t == J_STR ? v->s : def;
}

double jnum(J *o, const char *key, double def)
{
    J *v = key ? jget(o, key) : o;
    return v && (v->t == J_NUM || v->t == J_BOOL) ? v->n : def;
}

bool jbool(J *o, const char *key, bool def)
{
    J *v = key ? jget(o, key) : o;
    if (!v) return def;
    if (v->t == J_BOOL || v->t == J_NUM) return v->n != 0;
    return def;
}

void json_escape(char *out, size_t n, const char *s)
{
    size_t o = 0;
    if (!n) return;
    out[o++] = '"';
    for (; *s && o + 8 < n; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { out[o++] = '\\'; out[o++] = (char)c; }
        else if (c == '\n') { out[o++] = '\\'; out[o++] = 'n'; }
        else if (c == '\r') { out[o++] = '\\'; out[o++] = 'r'; }
        else if (c == '\t') { out[o++] = '\\'; out[o++] = 't'; }
        else if (c < 0x20) o += (size_t)snprintf(out + o, n - o, "\\u%04x", c);
        else out[o++] = (char)c;
    }
    out[o++] = '"';
    out[o] = 0;
}
