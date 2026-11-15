/* The explorable Earth. With "Explorable world" on (Settings), the apartment's front door opens onto the South Pole
 * and the whole planet is around it, built on the fly from OpenStreetMap through the Overpass API: buildings with
 * their real heights and colors, streets with markings, paths, railways, bridges, rivers, lakes, parks, forests and
 * single trees, walls and fences, and underground: tunnels, subway lines and stations (go down at a subway entrance
 * or a tunnel portal). The world map program on the KDE desktop (and the phone) teleports you anywhere.
 *
 * The planet is cut into areas of 0.005 degrees of latitude (about 555 m) and columns that keep them about as wide
 * everywhere, also at the poles. Each area is fetched once and kept on disk (~/.cache/lifeos/osm), one request at a
 * time with a pause between requests and a longer wait after an error, as the Overpass usage policy asks. Positions
 * are meters east (x) and south (z) on a plane touching the Earth at the current origin; walking far moves the
 * origin along. Map data (c) OpenStreetMap contributors, ODbL. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "app.h"
#include <curl/curl.h>
#include <errno.h>
#include <pthread.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#define R_EARTH 6371008.8
#define TILE_DEG 0.005
#define MAX_TILES 160
#define HOME_LAT (-89.9975)
#define HOME_LON 0.0
#define REORIGIN_M 6000.0f
#define UNDER_Y (-8.0f)
#define UNDER_H 5.0f

/* ------------------------------------------------------------------ settings and state */

bool g_world_on;
int g_draw_dist = 800;     /* meters */
int g_detail = 2;          /* 0 low .. 3 ultra */
bool g_shadows = true;

static bool outside, under;
static double o_lat = HOME_LAT, o_lon = HOME_LON;
static double OX, OY, OZ, EX, EY, EZ, NX, NY, NZ;
static int gen = 1;                      /* bumped when the origin or the detail changes: tiles rebuild */
static float home_x, home_z;             /* the apartment building, in the current frame */

/* ------------------------------------------------------------------ geometry on the sphere */

static void set_origin(double lat, double lon)
{
    o_lat = lat; o_lon = lon;
    double p = lat * M_PI / 180, l = lon * M_PI / 180;
    double ux = cos(p) * cos(l), uy = cos(p) * sin(l), uz = sin(p);
    OX = R_EARTH * ux; OY = R_EARTH * uy; OZ = R_EARTH * uz;
    EX = -sin(l); EY = cos(l); EZ = 0;
    NX = -sin(p) * cos(l); NY = -sin(p) * sin(l); NZ = cos(p);
}

static void ll2xz(double lat, double lon, float *x, float *z)
{
    double p = lat * M_PI / 180, l = lon * M_PI / 180;
    double px = R_EARTH * cos(p) * cos(l) - OX, py = R_EARTH * cos(p) * sin(l) - OY, pz = R_EARTH * sin(p) - OZ;
    *x = (float)(px * EX + py * EY + pz * EZ);
    *z = (float)-(px * NX + py * NY + pz * NZ);
}

static void xz2ll(double x, double z, double *lat, double *lon)
{
    double px = OX + EX * x - NX * z, py = OY + EY * x - NY * z, pz = OZ + EZ * x - NZ * z;
    double r = sqrt(px * px + py * py + pz * pz);
    *lat = asin(pz / r) * 180 / M_PI;
    *lon = atan2(py, px) * 180 / M_PI;
}

void out_where(float px, float pz, double *lat, double *lon) { xz2ll(px, pz, lat, lon); }

/* The grid: band b of TILE_DEG latitude, split into cols(b) columns around the Earth. */
static int nbands(void) { return (int)(180.0 / TILE_DEG + 0.5); }
static int cols(int b)
{
    double latc = -90 + (b + 0.5) * TILE_DEG;
    int n = (int)floor(360.0 * cos(latc * M_PI / 180) / TILE_DEG);
    return n < 1 ? 1 : n;
}
static void tile_of(double lat, double lon, int *b, int *k)
{
    int nb = nbands();
    int bb = (int)floor((lat + 90) / TILE_DEG);
    if (bb < 0) bb = 0;
    if (bb >= nb) bb = nb - 1;
    int n = cols(bb);
    double l = fmod(lon + 180, 360);
    if (l < 0) l += 360;
    int kk = (int)(l / (360.0 / n));
    if (kk >= n) kk = n - 1;
    *b = bb; *k = kk;
}
static void tile_bbox(int b, int k, double *s, double *w, double *n, double *e)
{
    double step = 360.0 / cols(b);
    *s = -90 + b * TILE_DEG; *n = *s + TILE_DEG;
    *w = -180 + k * step; *e = *w + step;
}

/* ------------------------------------------------------------------ meshes */

typedef struct { float x, y, z; uint8_t r, g, b, a; } Vtx;
typedef struct { Vtx *v; int n, cap; } Mesh;
typedef struct { float *xz; int n; float x0, z0, x1, z1; } Poly;
typedef struct { float ax, az, bx, bz, w; } Seg;
typedef struct { float x, z; bool portal; char name[48]; } Entr;
typedef struct { float ax, az, bx, bz; char name[64]; } Named;

enum { P_LAND, P_WATER, P_ROAD, P_MARK, NFLAT };
enum { T_EMPTY, T_WANT, T_BUSY, T_BUILT, T_READY, T_FAILED };

typedef struct {
    Mesh flat[NFLAT], solid, under;
    Poly *polys; int npolys, cpolys;
    Seg *segs; int nsegs, csegs;
    Entr *ents; int nents, cents;
    Named *names; int nnames, cnames;
    float road_m, trees, water;
} Build;

typedef struct {
    bool used;
    int b, k, state, gen;
    float cx, cz, dist;
    Uint32 retry_at;
    Build *bd;             /* worker result, until the main thread compiles it */
    GLuint lists;          /* NFLAT + 2 display lists */
    Poly *polys; int npolys;
    Seg *segs; int nsegs;
    Entr *ents; int nents;
    Named *names; int nnames;
    float road_m, trees, water;
    bool empty;
} Tile;

static Tile tiles[MAX_TILES];
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static pthread_t worker;
static bool worker_up, quit;
static volatile int net_fail;             /* consecutive failures reaching Overpass */
static volatile int net_busy;

static void mesh_v(Mesh *m, float x, float y, float z, Col c)
{
    if (m->n == m->cap) {
        int nc = m->cap ? m->cap * 2 : 1024;
        Vtx *nv = realloc(m->v, sizeof(Vtx) * (size_t)nc);
        if (!nv) return;
        m->v = nv; m->cap = nc;
    }
    Vtx *v = &m->v[m->n++];
    v->x = x; v->y = y; v->z = z; v->r = c.r; v->g = c.g; v->b = c.b; v->a = c.a;
}
static void tri(Mesh *m, float ax, float ay, float az, float bx, float by, float bz, float cx, float cy, float cz, Col c)
{
    mesh_v(m, ax, ay, az, c); mesh_v(m, bx, by, bz, c); mesh_v(m, cx, cy, cz, c);
}
static void quadm(Mesh *m, float ax, float ay, float az, float bx, float by, float bz, float cx, float cy, float cz,
                  float dx, float dy, float dz, Col c)
{
    tri(m, ax, ay, az, bx, by, bz, cx, cy, cz, c);
    tri(m, ax, ay, az, cx, cy, cz, dx, dy, dz, c);
}
static void boxm(Mesh *m, float x0, float y0, float z0, float x1, float y1, float z1, Col c)
{
    quadm(m, x0, y1, z0, x1, y1, z0, x1, y1, z1, x0, y1, z1, c);
    quadm(m, x0, y0, z1, x0, y1, z1, x1, y1, z1, x1, y0, z1, shade(c, 0.9f));
    quadm(m, x0, y0, z0, x1, y0, z0, x1, y1, z0, x0, y1, z0, shade(c, 0.83f));
    quadm(m, x1, y0, z0, x1, y0, z1, x1, y1, z1, x1, y1, z0, shade(c, 0.8f));
    quadm(m, x0, y0, z0, x0, y1, z0, x0, y1, z1, x0, y0, z1, shade(c, 0.72f));
}
static void disc(Mesh *m, float x, float y, float z, float r, int n, Col c)
{
    for (int i = 0; i < n; i++) {
        float a0 = 2 * PI_F * i / n, a1 = 2 * PI_F * (i + 1) / n;
        tri(m, x, y, z, x + cosf(a0) * r, y, z + sinf(a0) * r, x + cosf(a1) * r, y, z + sinf(a1) * r, c);
    }
}

static void mesh_free(Mesh *m) { free(m->v); m->v = NULL; m->n = m->cap = 0; }

#define PUSH(arr, n, cap, item) do { if ((n) == (cap)) { int nc_ = (cap) ? (cap) * 2 : 64; void *p_ = realloc((arr), sizeof *(arr) * (size_t)nc_); if (!p_) break; (arr) = p_; (cap) = nc_; } (arr)[(n)++] = (item); } while (0)

/* ------------------------------------------------------------------ polygons */

static float area2(const float *p, int n)
{
    float a = 0;
    for (int i = 0, j = n - 1; i < n; j = i++) a += p[2 * j] * p[2 * i + 1] - p[2 * i] * p[2 * j + 1];
    return a;
}

static bool in_tri(float px, float pz, float ax, float az, float bx, float bz, float cx, float cz)
{
    float d1 = (px - bx) * (az - bz) - (ax - bx) * (pz - bz);
    float d2 = (px - cx) * (bz - cz) - (bx - cx) * (pz - cz);
    float d3 = (px - ax) * (cz - az) - (cx - ax) * (pz - az);
    bool neg = d1 < 0 || d2 < 0 || d3 < 0, pos = d1 > 0 || d2 > 0 || d3 > 0;
    return !(neg && pos);
}

/* Ear clipping; writes up to 3*(n-2) indexes, returns how many triangles. */
static int triangulate(const float *p, int n, int *out)
{
    if (n < 3) return 0;
    int *idx = malloc(sizeof(int) * (size_t)n);
    if (!idx) return 0;
    bool ccw = area2(p, n) > 0;
    for (int i = 0; i < n; i++) idx[i] = ccw ? i : n - 1 - i;
    int m = n, t = 0, guard = 0;
    while (m > 3 && guard < 4 * n) {
        bool clipped = false;
        for (int i = 0; i < m; i++) {
            int a = idx[(i + m - 1) % m], b = idx[i], c = idx[(i + 1) % m];
            float ax = p[2 * a], az = p[2 * a + 1], bx = p[2 * b], bz = p[2 * b + 1], cx = p[2 * c], cz = p[2 * c + 1];
            float cr = (bx - ax) * (cz - az) - (bz - az) * (cx - ax);
            if (cr <= 0) continue;
            bool ear = true;
            for (int j = 0; j < m && ear; j++) {
                int v = idx[j];
                if (v == a || v == b || v == c) continue;
                if (in_tri(p[2 * v], p[2 * v + 1], ax, az, bx, bz, cx, cz)) ear = false;
            }
            if (!ear) continue;
            out[3 * t] = a; out[3 * t + 1] = b; out[3 * t + 2] = c; t++;
            memmove(idx + i, idx + i + 1, sizeof(int) * (size_t)(m - i - 1));
            m--;
            clipped = true;
            break;
        }
        guard++;
        if (!clipped) break;
    }
    if (m == 3) { out[3 * t] = idx[0]; out[3 * t + 1] = idx[1]; out[3 * t + 2] = idx[2]; t++; }
    else if (m > 3) for (int i = 1; i + 1 < m; i++) { out[3 * t] = idx[0]; out[3 * t + 1] = idx[i]; out[3 * t + 2] = idx[i + 1]; t++; }
    free(idx);
    return t;
}

static void fill_poly(Mesh *m, const float *p, int n, float y, Col c)
{
    int *ix = malloc(sizeof(int) * 3 * (size_t)(n > 3 ? n : 3));
    if (!ix) return;
    int t = triangulate(p, n, ix);
    for (int i = 0; i < t; i++) {
        int a = ix[3 * i], b = ix[3 * i + 1], d = ix[3 * i + 2];
        tri(m, p[2 * a], y, p[2 * a + 1], p[2 * b], y, p[2 * b + 1], p[2 * d], y, p[2 * d + 1], c);
    }
    free(ix);
}

static bool point_in(const float *p, int n, float x, float z)
{
    bool in = false;
    for (int i = 0, j = n - 1; i < n; j = i++) {
        float xi = p[2 * i], zi = p[2 * i + 1], xj = p[2 * j], zj = p[2 * j + 1];
        if ((zi > z) != (zj > z) && x < (xj - xi) * (z - zi) / (zj - zi + 1e-9f) + xi) in = !in;
    }
    return in;
}

/* ------------------------------------------------------------------ tags */

static const char *tag(J *el, const char *k) { J *t = jget(el, "tags"); return t ? jstr(t, k, NULL) : NULL; }
static bool tag_is(J *el, const char *k, const char *v) { const char *s = tag(el, k); return s && !strcmp(s, v); }
static float tag_num(J *el, const char *k, float def)
{
    const char *s = tag(el, k);
    if (!s) return def;
    char *e;
    float v = strtof(s, &e);
    if (e == s) return def;
    if (strstr(e, "ft") || strchr(e, '\'')) v *= 0.3048f;
    return v;
}

static bool parse_colour(const char *s, Col *c)
{
    if (!s) return false;
    static const struct { const char *n; Col c; } named[] = {
        {"white", {235, 235, 230, 255}}, {"grey", {150, 150, 150, 255}}, {"gray", {150, 150, 150, 255}}, {"red", {170, 70, 60, 255}},
        {"brown", {140, 100, 70, 255}}, {"yellow", {220, 200, 120, 255}}, {"beige", {215, 200, 170, 255}}, {"black", {50, 50, 52, 255}},
        {"blue", {80, 110, 170, 255}}, {"green", {90, 140, 90, 255}}, {"orange", {210, 140, 70, 255}}, {"pink", {220, 170, 170, 255}},
        {"silver", {190, 190, 195, 255}}, {"tan", {200, 180, 140, 255}}, {"cream", {235, 225, 200, 255}}};
    if (s[0] == '#' && strlen(s) >= 7) {
        unsigned v;
        if (sscanf(s + 1, "%6x", &v) == 1) { *c = rgb((v >> 16) & 255, (v >> 8) & 255, v & 255); return true; }
        return false;
    }
    for (size_t i = 0; i < sizeof named / sizeof named[0]; i++) if (!strcasecmp(s, named[i].n)) { *c = named[i].c; return true; }
    return false;
}

static uint32_t hash_u(uint32_t x) { x ^= x >> 16; x *= 0x7feb352d; x ^= x >> 15; x *= 0x846ca68b; x ^= x >> 16; return x; }
static float rnd01(uint32_t *s) { *s = hash_u(*s + 0x9e3779b9u); return (*s & 0xffffff) / (float)0x1000000; }

/* ------------------------------------------------------------------ building the meshes of one area */

typedef struct { float *xz; int n, cap; } Pts;

static bool geom_pts(J *geom, Pts *o, double *lat0, double *lon0)
{
    o->n = 0;
    if (!geom || geom->t != J_ARR || geom->count < 1) return false;
    for (int i = 0; i < geom->count; i++) {
        J *g = jat(geom, i);
        double la = jnum(g, "lat", 999), lo = jnum(g, "lon", 999);
        if (la > 900) continue;
        if (o->n == 0 && lat0) { *lat0 = la; *lon0 = lo; }
        if (o->n + 1 >= o->cap) {
            int nc = o->cap ? o->cap * 2 : 64;
            float *p = realloc(o->xz, sizeof(float) * 2 * (size_t)nc);
            if (!p) return false;
            o->xz = p; o->cap = nc;
        }
        ll2xz(la, lo, &o->xz[2 * o->n], &o->xz[2 * o->n + 1]);
        o->n++;
    }
    return o->n > 0;
}

static bool closed(Pts *p)
{
    if (p->n < 4) return false;
    float dx = p->xz[0] - p->xz[2 * (p->n - 1)], dz = p->xz[1] - p->xz[2 * (p->n - 1) + 1];
    return dx * dx + dz * dz < 0.01f;
}

/* At most max points (big lakes and forests): every k-th point. */
static void thin(Pts *p, int max)
{
    if (p->n <= max) return;
    int k = (p->n + max - 1) / max, j = 0;
    for (int i = 0; i < p->n; i += k, j++) { p->xz[2 * j] = p->xz[2 * i]; p->xz[2 * j + 1] = p->xz[2 * i + 1]; }
    p->n = j;
}

static Col land_color(J *el, bool *trees, float *density)
{
    *trees = false; *density = 0;
    const char *lu = tag(el, "landuse"), *nat = tag(el, "natural"), *le = tag(el, "leisure"), *am = tag(el, "amenity");
    if (nat) {
        if (!strcmp(nat, "wood")) { *trees = true; *density = 1; return rgb(78, 112, 64); }
        if (!strcmp(nat, "scrub") || !strcmp(nat, "heath")) { *trees = true; *density = 0.25f; return rgb(120, 135, 85); }
        if (!strcmp(nat, "grassland")) return rgb(130, 165, 90);
        if (!strcmp(nat, "beach") || !strcmp(nat, "sand")) return rgb(222, 206, 160);
        if (!strcmp(nat, "glacier")) return rgb(240, 246, 252);
        if (!strcmp(nat, "bare_rock") || !strcmp(nat, "scree")) return rgb(150, 146, 140);
        if (!strcmp(nat, "wetland")) return rgb(115, 145, 120);
    }
    if (lu) {
        if (!strcmp(lu, "forest")) { *trees = true; *density = 1; return rgb(74, 108, 60); }
        if (!strcmp(lu, "grass") || !strcmp(lu, "meadow") || !strcmp(lu, "village_green") || !strcmp(lu, "recreation_ground"))
            return rgb(120, 165, 85);
        if (!strcmp(lu, "farmland") || !strcmp(lu, "farmyard")) return rgb(175, 170, 115);
        if (!strcmp(lu, "orchard") || !strcmp(lu, "vineyard")) { *trees = true; *density = 0.4f; return rgb(140, 160, 90); }
        if (!strcmp(lu, "industrial") || !strcmp(lu, "railway")) return rgb(178, 172, 168);
        if (!strcmp(lu, "commercial") || !strcmp(lu, "retail")) return rgb(196, 186, 184);
        if (!strcmp(lu, "residential")) return rgb(190, 188, 180);
        if (!strcmp(lu, "cemetery")) { *trees = true; *density = 0.15f; return rgb(120, 150, 110); }
        if (!strcmp(lu, "construction") || !strcmp(lu, "brownfield")) return rgb(170, 155, 130);
        if (!strcmp(lu, "reservoir") || !strcmp(lu, "basin")) return rgb(70, 120, 175);
        if (!strcmp(lu, "allotments")) return rgb(150, 170, 100);
        return rgb(176, 178, 160);
    }
    if (le) {
        if (!strcmp(le, "park") || !strcmp(le, "garden")) { *trees = true; *density = 0.2f; return rgb(115, 165, 85); }
        if (!strcmp(le, "pitch")) return rgb(88, 150, 80);
        if (!strcmp(le, "playground")) return rgb(205, 190, 150);
        return rgb(130, 165, 95);
    }
    if (am && !strcmp(am, "parking")) return rgb(150, 150, 152);
    return rgb(170, 172, 160);
}

static void add_tree(Build *bd, float x, float z, float h, uint32_t seed, bool conifer)
{
    Mesh *m = &bd->solid;
    uint32_t s = seed;
    Col trunk = rgb(96 + (int)(rnd01(&s) * 20), 72, 52);
    float tr = 0.12f + h * 0.012f;
    boxm(m, x - tr, 0, z - tr, x + tr, h * 0.4f, z + tr, trunk);
    int sides = g_detail >= 2 ? 6 : 4;
    Col leaf = conifer ? rgb(40 + (int)(rnd01(&s) * 20), 85 + (int)(rnd01(&s) * 25), 50) : rgb(70 + (int)(rnd01(&s) * 40), 120 + (int)(rnd01(&s) * 40), 55);
    if (conifer) {
        for (int layer = 0; layer < 2; layer++) {
            float y0 = h * (0.25f + 0.3f * layer), y1 = h * (0.75f + 0.25f * layer), r = h * (0.28f - 0.08f * layer);
            for (int i = 0; i < sides; i++) {
                float a0 = 2 * PI_F * i / sides, a1 = 2 * PI_F * (i + 1) / sides;
                float k = 0.72f + 0.28f * (0.5f + 0.5f * cosf((a0 + a1) / 2 - 0.6f));
                tri(m, x + cosf(a0) * r, y0, z + sinf(a0) * r, x, y1, z, x + cosf(a1) * r, y0, z + sinf(a1) * r, shade(leaf, k));
                tri(m, x + cosf(a0) * r, y0, z + sinf(a0) * r, x + cosf(a1) * r, y0, z + sinf(a1) * r, x, y0, z, shade(leaf, 0.6f));
            }
        }
    } else {
        float cy = h * 0.66f, r = h * 0.3f;
        const int R = g_detail >= 2 ? 3 : 2;
        for (int j = 0; j < R; j++) {
            float p0 = -PI_F / 2 + PI_F * j / R, p1 = -PI_F / 2 + PI_F * (j + 1) / R;
            for (int i = 0; i < sides; i++) {
                float a0 = 2 * PI_F * i / sides, a1 = 2 * PI_F * (i + 1) / sides;
                Col c = shade(leaf, 0.66f + 0.34f * (j + 1) / R);
                float x00 = x + cosf(a0) * cosf(p0) * r, z00 = z + sinf(a0) * cosf(p0) * r, y0 = cy + sinf(p0) * r * 0.9f;
                float x01 = x + cosf(a0) * cosf(p1) * r, z01 = z + sinf(a0) * cosf(p1) * r, y1 = cy + sinf(p1) * r * 0.9f;
                float x11 = x + cosf(a1) * cosf(p1) * r, z11 = z + sinf(a1) * cosf(p1) * r;
                float x10 = x + cosf(a1) * cosf(p0) * r, z10 = z + sinf(a1) * cosf(p0) * r;
                quadm(m, x00, y0, z00, x01, y1, z01, x11, y1, z11, x10, y0, z10, c);
            }
        }
    }
    if (g_shadows) disc(&bd->flat[P_MARK], x + h * 0.08f, 0, z + h * 0.05f, h * 0.27f, 8, rgba(0, 0, 0, 60));
    bd->trees += 1;
}

static void scatter_trees(Build *bd, Pts *p, float density, uint32_t seed, bool conifer)
{
    static const float D[4] = {1 / 2500.f, 1 / 900.f, 1 / 450.f, 1 / 250.f};
    float x0 = 1e9f, z0 = 1e9f, x1 = -1e9f, z1 = -1e9f;
    for (int i = 0; i < p->n; i++) {
        x0 = fminf(x0, p->xz[2 * i]); x1 = fmaxf(x1, p->xz[2 * i]);
        z0 = fminf(z0, p->xz[2 * i + 1]); z1 = fmaxf(z1, p->xz[2 * i + 1]);
    }
    float a = fabsf(area2(p->xz, p->n)) / 2;
    int want = (int)(a * D[g_detail] * density);
    if (want > 1500) want = 1500;
    uint32_t s = seed;
    for (int i = 0, tries = 0; i < want && tries < want * 4; tries++) {
        float x = x0 + rnd01(&s) * (x1 - x0), z = z0 + rnd01(&s) * (z1 - z0);
        if (!point_in(p->xz, p->n, x, z)) continue;
        add_tree(bd, x, z, 6 + rnd01(&s) * 9, s, conifer);
        i++;
    }
}

static float road_width(const char *hw, Col *c, bool *major)
{
    *major = false;
    *c = rgb(82, 84, 88);
    if (!strcmp(hw, "motorway") || !strcmp(hw, "trunk")) { *major = true; *c = rgb(66, 68, 72); return 14; }
    if (!strcmp(hw, "motorway_link") || !strcmp(hw, "trunk_link")) { *c = rgb(70, 72, 76); return 7; }
    if (!strcmp(hw, "primary") || !strcmp(hw, "primary_link")) { *major = true; *c = rgb(70, 72, 76); return 11; }
    if (!strcmp(hw, "secondary") || !strcmp(hw, "secondary_link")) { *major = true; *c = rgb(74, 76, 80); return 9.5f; }
    if (!strcmp(hw, "tertiary") || !strcmp(hw, "tertiary_link")) { *major = true; return 8.5f; }
    if (!strcmp(hw, "residential") || !strcmp(hw, "unclassified") || !strcmp(hw, "living_street") || !strcmp(hw, "road")) return 7;
    if (!strcmp(hw, "service")) { *c = rgb(92, 94, 98); return 4.5f; }
    if (!strcmp(hw, "pedestrian")) { *c = rgb(170, 165, 158); return 6; }
    if (!strcmp(hw, "track")) { *c = rgb(150, 130, 100); return 3; }
    if (!strcmp(hw, "footway") || !strcmp(hw, "cycleway") || !strcmp(hw, "path") || !strcmp(hw, "bridleway") || !strcmp(hw, "steps") ||
        !strcmp(hw, "corridor")) { *c = rgb(176, 166, 148); return 2.2f; }
    if (!strcmp(hw, "construction") || !strcmp(hw, "proposed") || !strcmp(hw, "platform") || !strcmp(hw, "bus_stop")) return 0;
    return 5;
}

/* A line with width w: a quad per segment and round joins. */
static void strip(Mesh *m, Pts *p, float w, float y, Col c, bool joins)
{
    float h = w / 2;
    for (int i = 0; i + 1 < p->n; i++) {
        float ax = p->xz[2 * i], az = p->xz[2 * i + 1], bx = p->xz[2 * i + 2], bz = p->xz[2 * i + 3];
        float dx = bx - ax, dz = bz - az, l = sqrtf(dx * dx + dz * dz);
        if (l < 0.01f) continue;
        float nx = -dz / l * h, nz = dx / l * h;
        quadm(m, ax + nx, y, az + nz, bx + nx, y, bz + nz, bx - nx, y, bz - nz, ax - nx, y, az - nz, c);
        if (joins && i > 0) disc(m, ax, y, az, h, 8, c);
    }
}

static void dashes(Mesh *m, Pts *p, float off, float w, float dash, float gap, Col c)
{
    float carry = 0;
    for (int i = 0; i + 1 < p->n; i++) {
        float ax = p->xz[2 * i], az = p->xz[2 * i + 1], bx = p->xz[2 * i + 2], bz = p->xz[2 * i + 3];
        float dx = bx - ax, dz = bz - az, l = sqrtf(dx * dx + dz * dz);
        if (l < 0.01f) continue;
        float ux = dx / l, uz = dz / l, nx = -uz, nz = ux;
        float t = carry;
        while (t < l) {
            float t1 = fminf(t + dash, l);
            float sx = ax + ux * t + nx * off, sz = az + uz * t + nz * off, ex = ax + ux * t1 + nx * off, ez = az + uz * t1 + nz * off;
            quadm(m, sx + nx * w, 0, sz + nz * w, ex + nx * w, 0, ez + nz * w, ex - nx * w, 0, ez - nz * w, sx - nx * w, 0, sz - nz * w, c);
            t += dash + gap;
        }
        carry = t - l;
    }
}

static void tunnel_geo(Build *bd, Pts *p, float w, const char *name)
{
    Mesh *m = &bd->under;
    float h = w / 2;
    Col floor_c = rgb(72, 72, 76), wall = rgb(150, 144, 136), ceil_c = rgb(92, 92, 96), lamp = rgb(255, 236, 190);
    for (int i = 0; i + 1 < p->n; i++) {
        float ax = p->xz[2 * i], az = p->xz[2 * i + 1], bx = p->xz[2 * i + 2], bz = p->xz[2 * i + 3];
        float dx = bx - ax, dz = bz - az, l = sqrtf(dx * dx + dz * dz);
        if (l < 0.01f) continue;
        float nx = -dz / l * h, nz = dx / l * h;
        float y0 = UNDER_Y, y1 = UNDER_Y + UNDER_H;
        quadm(m, ax + nx, y0, az + nz, bx + nx, y0, bz + nz, bx - nx, y0, bz - nz, ax - nx, y0, az - nz, floor_c);
        quadm(m, ax + nx, y1, az + nz, bx + nx, y1, bz + nz, bx - nx, y1, bz - nz, ax - nx, y1, az - nz, ceil_c);
        quadm(m, ax + nx, y0, az + nz, bx + nx, y0, bz + nz, bx + nx, y1, bz + nz, ax + nx, y1, az + nz, shade(wall, 0.9f));
        quadm(m, ax - nx, y0, az - nz, bx - nx, y0, bz - nz, bx - nx, y1, bz - nz, ax - nx, y1, az - nz, shade(wall, 0.8f));
        for (float t = 4; t < l; t += 15) {
            float x = ax + dx / l * t, z = az + dz / l * t;
            boxm(m, x - 0.4f, y1 - 0.12f, z - 0.4f, x + 0.4f, y1 - 0.02f, z + 0.4f, lamp);
        }
        Seg s = {ax, az, bx, bz, w};
        PUSH(bd->segs, bd->nsegs, bd->csegs, s);
        if (i > 0) disc(m, ax, y0 + 0.001f, az, h, 8, floor_c);
    }
    (void)name;
}

static void add_entr(Build *bd, float x, float z, bool portal, const char *name)
{
    Entr e = {x, z, portal, ""};
    snprintf(e.name, sizeof e.name, "%s", name ? name : "");
    PUSH(bd->ents, bd->nents, bd->cents, e);
    /* on the surface: a stair kiosk, or a dark portal mouth; underground: stairs going up */
    if (!portal) {
        boxm(&bd->solid, x - 1.6f, 0, z - 1.6f, x + 1.6f, 0.9f, z + 1.6f, rgb(120, 120, 126));
        boxm(&bd->solid, x - 0.1f, 0, z - 1.8f, x + 0.1f, 3.2f, z - 1.6f, rgb(60, 60, 64));
        boxm(&bd->solid, x - 0.6f, 3.0f, z - 1.9f, x + 0.6f, 3.6f, z - 1.6f, rgb(40, 90, 170));
    } else {
        boxm(&bd->solid, x - 3, 0, z - 3, x + 3, 0.5f, z + 3, rgb(105, 100, 96));
    }
    for (int s = 0; s < 8; s++) {
        float y = UNDER_Y + s * 0.6f;
        boxm(&bd->under, x - 1.2f, UNDER_Y, z - 0.4f + s * 0.3f, x + 1.2f, y + 0.6f, z - 0.1f + s * 0.3f, rgb(140, 136, 130));
    }
    boxm(&bd->under, x - 0.08f, UNDER_Y, z - 1.2f, x + 0.08f, UNDER_Y + 3, z - 1.0f, rgb(60, 60, 64));
    boxm(&bd->under, x - 0.9f, UNDER_Y + 2.6f, z - 1.25f, x + 0.9f, UNDER_Y + 3.2f, z - 1.0f, rgb(40, 150, 80));
    Seg s = {x - 2, z, x + 2, z, 6};
    PUSH(bd->segs, bd->nsegs, bd->csegs, s);
}

static void building(Build *bd, J *el, Pts *p, uint32_t id, bool part)
{
    if (p->n < 3) return;
    const char *bt = tag(el, part ? "building:part" : "building");
    if (!bt) bt = "yes";
    float lv = tag_num(el, "building:levels", -1);
    float h = tag_num(el, "height", -1);
    if (h <= 0) h = lv > 0 ? lv * 3.2f + 1 : (!strcmp(bt, "house") || !strcmp(bt, "detached") || !strcmp(bt, "semidetached_house")) ? 7
             : (!strcmp(bt, "garage") || !strcmp(bt, "garages") || !strcmp(bt, "shed") || !strcmp(bt, "hut") || !strcmp(bt, "roof") || !strcmp(bt, "carport")) ? 3
             : (!strcmp(bt, "apartments")) ? 16 : (!strcmp(bt, "church") || !strcmp(bt, "cathedral")) ? 18 : 9;
    float minh = tag_num(el, "min_height", -1);
    if (minh < 0) { float ml = tag_num(el, "building:min_level", 0); minh = ml * 3.2f; }
    if (!strcmp(bt, "roof") || !strcmp(bt, "carport")) minh = fmaxf(minh, h - 0.5f);
    if (h > 900) h = 900;
    if (minh >= h) minh = h - 1;
    int layer = (int)tag_num(el, "layer", 0);
    const char *loc = tag(el, "location");
    bool undergr = layer < 0 || (loc && !strcmp(loc, "underground"));
    Col wall;
    if (!parse_colour(tag(el, "building:colour"), &wall)) {
        static const Col pal[6] = {{214, 205, 190, 255}, {196, 186, 172, 255}, {206, 180, 160, 255}, {184, 188, 192, 255}, {222, 214, 196, 255}, {172, 160, 150, 255}};
        wall = pal[id % 6];
        if (!strcmp(bt, "industrial") || !strcmp(bt, "warehouse")) wall = rgb(176, 172, 164);
        else if (!strcmp(bt, "office") || !strcmp(bt, "commercial") || !strcmp(bt, "retail")) wall = rgb(170 + (int)(id % 20), 182, 196);
        else if (!strcmp(bt, "house") || !strcmp(bt, "detached")) wall = rgb(220 - (int)(id % 30), 200 - (int)(id % 40), 175);
    }
    Col roof;
    if (!parse_colour(tag(el, "roof:colour"), &roof))
        roof = (!strcmp(bt, "house") || !strcmp(bt, "detached")) ? rgb(150, 80 + (int)(id % 30), 64) : shade(wall, 0.72f);
    Mesh *m = undergr ? &bd->under : &bd->solid;
    float y0 = undergr ? UNDER_Y : minh, y1 = undergr ? UNDER_Y + UNDER_H : h;
    if (area2(p->xz, p->n) < 0) {   /* make it counter-clockwise for outward normals */
        for (int i = 0, j = p->n - 1; i < j; i++, j--) {
            float tx = p->xz[2 * i], tz = p->xz[2 * i + 1];
            p->xz[2 * i] = p->xz[2 * j]; p->xz[2 * i + 1] = p->xz[2 * j + 1];
            p->xz[2 * j] = tx; p->xz[2 * j + 1] = tz;
        }
    }
    int windows = 0;
    for (int i = 0; i < p->n; i++) {
        int j = (i + 1) % p->n;
        float ax = p->xz[2 * i], az = p->xz[2 * i + 1], bx = p->xz[2 * j], bz = p->xz[2 * j + 1];
        float dx = bx - ax, dz = bz - az, l = sqrtf(dx * dx + dz * dz);
        if (l < 0.01f) continue;
        float nx = dz / l, nz = -dx / l;   /* outward for counter-clockwise (x east, z south) */
        float k = 0.74f + 0.2f * (0.5f + 0.5f * (nz * 0.8f - nx * 0.6f));
        quadm(m, ax, y0, az, bx, y0, bz, bx, y1, bz, ax, y1, az, shade(wall, k));
        if (!undergr && g_detail >= 2 && y1 - y0 > 4 && l > 2.5f && windows < 1500 && !part) {
            Col win = rgb(70, 86, 108);
            int floors = (int)((y1 - fmaxf(y0, 0)) / 3.2f);
            int across = (int)(l / 3.0f);
            for (int f = 0; f < floors && windows < 1500; f++) {
                float wy = fmaxf(y0, 0) + 1.0f + f * 3.2f;
                if (wy + 1.4f > y1 - 0.3f) break;
                for (int a = 0; a < across; a++) {
                    float t0 = (a + 0.5f) * l / across - 0.6f, t1 = t0 + 1.2f;
                    float sx = ax + dx / l * t0 + nx * 0.05f, sz = az + dz / l * t0 + nz * 0.05f;
                    float ex = ax + dx / l * t1 + nx * 0.05f, ez = az + dz / l * t1 + nz * 0.05f;
                    quadm(m, sx, wy, sz, ex, wy, ez, ex, wy + 1.4f, ez, sx, wy + 1.4f, sz, shade(win, k));
                    windows++;
                }
            }
        }
    }
    fill_poly(m, p->xz, p->n, y1, roof);
    if (y0 > 0.5f) fill_poly(m, p->xz, p->n, y0, shade(wall, 0.5f));
    if (!undergr && y0 < 2 && !part) {
        Poly pl = {malloc(sizeof(float) * 2 * (size_t)p->n), p->n, 1e9f, 1e9f, -1e9f, -1e9f};
        if (pl.xz) {
            memcpy(pl.xz, p->xz, sizeof(float) * 2 * (size_t)p->n);
            for (int i = 0; i < p->n; i++) {
                pl.x0 = fminf(pl.x0, p->xz[2 * i]); pl.x1 = fmaxf(pl.x1, p->xz[2 * i]);
                pl.z0 = fminf(pl.z0, p->xz[2 * i + 1]); pl.z1 = fmaxf(pl.z1, p->xz[2 * i + 1]);
            }
            PUSH(bd->polys, bd->npolys, bd->cpolys, pl);
        }
    }
    if (g_shadows && !undergr && y0 < 2) {
        /* a soft dark rim on the ground along the sunny side */
        for (int i = 0; i < p->n; i++) {
            int j = (i + 1) % p->n;
            float ax = p->xz[2 * i], az = p->xz[2 * i + 1], bx = p->xz[2 * j], bz = p->xz[2 * j + 1];
            float dx = bx - ax, dz = bz - az, l = sqrtf(dx * dx + dz * dz);
            if (l < 0.01f) continue;
            float nx = dz / l, nz = -dx / l;
            if (nz < 0.2f) continue;
            float s = fminf(h * 0.35f, 8);
            quadm(&bd->flat[P_MARK], ax, 0, az, bx, 0, bz, bx + nx * s, 0, bz + nz * s, ax + nx * s, 0, az + nz * s, rgba(0, 0, 0, 45));
        }
    }
}

static void bridge(Build *bd, Pts *p, float w, Col c, int layer)
{
    float y = 5.0f * (layer > 0 ? layer : 1);
    float h = w / 2;
    for (int i = 0; i + 1 < p->n; i++) {
        float ax = p->xz[2 * i], az = p->xz[2 * i + 1], bx = p->xz[2 * i + 2], bz = p->xz[2 * i + 3];
        float dx = bx - ax, dz = bz - az, l = sqrtf(dx * dx + dz * dz);
        if (l < 0.01f) continue;
        float nx = -dz / l * h, nz = dx / l * h;
        quadm(&bd->solid, ax + nx, y, az + nz, bx + nx, y, bz + nz, bx - nx, y, bz - nz, ax - nx, y, az - nz, c);
        quadm(&bd->solid, ax + nx, y - 0.8f, az + nz, bx + nx, y - 0.8f, bz + nz, bx + nx, y, bz + nz, ax + nx, y, az + nz, rgb(150, 146, 140));
        quadm(&bd->solid, ax - nx, y - 0.8f, az - nz, bx - nx, y - 0.8f, bz - nz, bx - nx, y, bz - nz, ax - nx, y, az - nz, rgb(140, 136, 130));
        quadm(&bd->solid, ax + nx, y, az + nz, bx + nx, y, bz + nz, bx + nx, y + 1, bz + nz, ax + nx, y + 1, az + nz, rgb(120, 122, 128));
        quadm(&bd->solid, ax - nx, y, az - nz, bx - nx, y, bz - nz, bx - nx, y + 1, bz - nz, ax - nx, y + 1, az - nz, rgb(120, 122, 128));
        for (float t = 0; t < l; t += 25) {
            float x = ax + dx / l * t, z = az + dz / l * t;
            boxm(&bd->solid, x - 0.6f, 0, z - 0.6f, x + 0.6f, y - 0.8f, z + 0.6f, rgb(160, 156, 150));
        }
    }
}

/* Joins the outer member ways of a multipolygon into rings. */
static int rings(J *members, Pts *out, int max, double *lat0, double *lon0)
{
    int nr = 0;
    Pts cur = {0};
    bool have = false;
    bool first = true;
    if (!members || members->t != J_ARR) return 0;
    for (int i = 0; i < members->count && nr < max; i++) {
        J *mb = jat(members, i);
        const char *role = jstr(mb, "role", "");
        if (strcmp(jstr(mb, "type", ""), "way") || (strcmp(role, "outer") && role[0])) continue;
        Pts w = {0};
        double la, lo;
        if (!geom_pts(jget(mb, "geometry"), &w, &la, &lo)) { free(w.xz); continue; }
        if (first) { *lat0 = la; *lon0 = lo; first = false; }
        if (!have) { cur = w; have = true; }
        else {
            float ex = cur.xz[2 * (cur.n - 1)], ez = cur.xz[2 * (cur.n - 1) + 1];
            float sx = w.xz[0], sz = w.xz[1], tx = w.xz[2 * (w.n - 1)], tz = w.xz[2 * (w.n - 1) + 1];
            bool fwd = fabsf(ex - sx) + fabsf(ez - sz) < 0.2f, rev = fabsf(ex - tx) + fabsf(ez - tz) < 0.2f;
            if (fwd || rev) {
                float *np = realloc(cur.xz, sizeof(float) * 2 * (size_t)(cur.n + w.n));
                if (np) {
                    cur.xz = np;
                    for (int k = 1; k < w.n; k++) {
                        int s = fwd ? k : w.n - 1 - k;
                        cur.xz[2 * cur.n] = w.xz[2 * s]; cur.xz[2 * cur.n + 1] = w.xz[2 * s + 1];
                        cur.n++;
                    }
                    cur.cap = cur.n;
                }
                free(w.xz);
            } else {
                out[nr++] = cur;
                cur = w;
            }
        }
        if (have && closed(&cur)) { if (nr < max) out[nr++] = cur; else free(cur.xz); have = false; memset(&cur, 0, sizeof cur); }
    }
    if (have) { if (nr < max) out[nr++] = cur; else free(cur.xz); }
    return nr;
}

static void area_feature(Build *bd, J *el, Pts *p, uint32_t id)
{
    if (p->n < 3) return;
    thin(p, 400);
    const char *b = tag(el, "building");
    if (b || tag(el, "building:part")) { building(bd, el, p, id, !b); return; }
    const char *nat = tag(el, "natural"), *ww = tag(el, "waterway"), *lu = tag(el, "landuse"), *wt = tag(el, "water");
    if ((nat && !strcmp(nat, "water")) || (ww && !strcmp(ww, "riverbank")) || wt || (lu && (!strcmp(lu, "reservoir") || !strcmp(lu, "basin")))) {
        fill_poly(&bd->flat[P_WATER], p->xz, p->n, 0, rgb(64, 112, 168));
        bd->water += fabsf(area2(p->xz, p->n)) / 2;
        return;
    }
    const char *hw = tag(el, "highway");
    if (hw && tag_is(el, "area", "yes")) { fill_poly(&bd->flat[P_ROAD], p->xz, p->n, 0, rgb(170, 165, 158)); return; }
    if (tag_is(el, "railway", "platform") || tag_is(el, "public_transport", "platform")) {
        fill_poly(&bd->flat[P_ROAD], p->xz, p->n, 0, rgb(150, 148, 146)); return;
    }
    bool trees; float dens;
    Col c = land_color(el, &trees, &dens);
    if (!lu && !nat && !tag(el, "leisure") && !tag(el, "amenity")) return;
    fill_poly(&bd->flat[P_LAND], p->xz, p->n, 0, c);
    if (trees && g_detail > 0) {
        double lat, lon;
        xz2ll(p->xz[0], p->xz[1], &lat, &lon);
        scatter_trees(bd, p, dens, id, fabs(lat) > 52 || (lu && !strcmp(lu, "forest") && (id & 1)));
    }
}

static void line_feature(Build *bd, J *el, Pts *p, uint32_t id)
{
    const char *hw = tag(el, "highway"), *rw = tag(el, "railway"), *ww = tag(el, "waterway"), *bar = tag(el, "barrier");
    bool tunnel = tag(el, "tunnel") && !tag_is(el, "tunnel", "no");
    bool br = tag(el, "bridge") && !tag_is(el, "bridge", "no");
    int layer = (int)tag_num(el, "layer", 0);
    const char *name = tag(el, "name");
    if (hw) {
        Col c; bool major;
        float w = road_width(hw, &c, &major);
        if (w <= 0) return;
        if (tunnel || (layer < 0 && !br)) {
            tunnel_geo(bd, p, fmaxf(w, 4), name);
            if (p->n >= 2) {
                add_entr(bd, p->xz[0], p->xz[1], true, name);
                add_entr(bd, p->xz[2 * (p->n - 1)], p->xz[2 * (p->n - 1) + 1], true, name);
            }
            return;
        }
        if (br) { bridge(bd, p, w, c, layer); return; }
        strip(&bd->flat[P_ROAD], p, w, 0, c, w > 3);
        if (major && g_detail >= 1) dashes(&bd->flat[P_MARK], p, 0, 0.08f, 3, 6, rgb(235, 235, 228));
        if (major && g_detail >= 2) {
            dashes(&bd->flat[P_MARK], p, w / 2 - 0.4f, 0.06f, 1000, 0, rgb(225, 225, 220));
            dashes(&bd->flat[P_MARK], p, -(w / 2 - 0.4f), 0.06f, 1000, 0, rgb(225, 225, 220));
        }
        if (w >= 4) {
            for (int i = 0; i + 1 < p->n; i++) {
                float ax = p->xz[2 * i], az = p->xz[2 * i + 1], bx = p->xz[2 * i + 2], bz = p->xz[2 * i + 3];
                bd->road_m += sqrtf((bx - ax) * (bx - ax) + (bz - az) * (bz - az));
                if (name) {
                    Named nm = {ax, az, bx, bz, ""};
                    snprintf(nm.name, sizeof nm.name, "%s", name);
                    PUSH(bd->names, bd->nnames, bd->cnames, nm);
                }
            }
        } else if (name) {
            for (int i = 0; i + 1 < p->n; i++) {
                Named nm = {p->xz[2 * i], p->xz[2 * i + 1], p->xz[2 * i + 2], p->xz[2 * i + 3], ""};
                snprintf(nm.name, sizeof nm.name, "%s", name);
                PUSH(bd->names, bd->nnames, bd->cnames, nm);
            }
        }
        return;
    }
    if (rw) {
        bool track = !strcmp(rw, "rail") || !strcmp(rw, "light_rail") || !strcmp(rw, "subway") || !strcmp(rw, "tram") ||
                     !strcmp(rw, "narrow_gauge") || !strcmp(rw, "monorail") || !strcmp(rw, "funicular");
        if (!track) return;
        if (tunnel || (!strcmp(rw, "subway") && !br && !tag_is(el, "tunnel", "no")) || layer < 0) {
            tunnel_geo(bd, p, 7, name);
            for (int s = -1; s <= 1; s += 2) dashes(&bd->under, p, s * 0.72f, 0.06f, 1000, 0, rgb(70, 70, 74));
            return;
        }
        if (br) { bridge(bd, p, 4, rgb(120, 112, 104), layer); return; }
        if (strcmp(rw, "tram")) strip(&bd->flat[P_ROAD], p, 3.2f, 0, rgb(128, 118, 106), false);
        for (int s = -1; s <= 1; s += 2) dashes(&bd->flat[P_MARK], p, s * 0.72f, 0.06f, 1000, 0, rgb(60, 60, 66));
        if (g_detail >= 2 && strcmp(rw, "tram")) dashes(&bd->flat[P_MARK], p, 0, 1.3f, 0.25f, 0.45f, rgb(98, 82, 66));
        return;
    }
    if (ww) {
        float w = !strcmp(ww, "river") ? 16 : !strcmp(ww, "canal") ? 10 : !strcmp(ww, "stream") ? 2.5f : (!strcmp(ww, "ditch") || !strcmp(ww, "drain")) ? 1.5f : 0;
        if (w <= 0 || tunnel) return;
        strip(&bd->flat[P_WATER], p, w, 0, rgb(64, 112, 168), true);
        bd->water += w * 40;
        return;
    }
    if (bar) {
        float h = !strcmp(bar, "wall") || !strcmp(bar, "city_wall") ? 2.2f : !strcmp(bar, "fence") ? 1.3f : !strcmp(bar, "hedge") ? 1.5f : 0;
        if (h <= 0) return;
        Col c = !strcmp(bar, "hedge") ? rgb(60, 110, 60) : !strcmp(bar, "fence") ? rgb(130, 112, 92) : rgb(188, 182, 172);
        for (int i = 0; i + 1 < p->n; i++) {
            float ax = p->xz[2 * i], az = p->xz[2 * i + 1], bx = p->xz[2 * i + 2], bz = p->xz[2 * i + 3];
            quadm(&bd->solid, ax, 0, az, bx, 0, bz, bx, h, bz, ax, h, az, c);
        }
    }
    (void)id;
}

static Build *build_area(int b, int k, const char *json)
{
    Build *bd = calloc(1, sizeof *bd);
    if (!bd) return NULL;
    J *root = json_parse(json);
    J *els = root ? jget(root, "elements") : NULL;
    if (!els || els->t != J_ARR) { json_free(root); return bd; }
    Pts p = {0};
    for (int i = 0; i < els->count; i++) {
        J *el = jat(els, i);
        const char *type = jstr(el, "type", "");
        uint32_t id = (uint32_t)jnum(el, "id", 0);
        if (!strcmp(type, "node")) {
            double la = jnum(el, "lat", 999), lo = jnum(el, "lon", 999);
            if (la > 900) continue;
            int tb, tk;
            tile_of(la, lo, &tb, &tk);
            if (tb != b || tk != k) continue;
            float x, z;
            ll2xz(la, lo, &x, &z);
            if (tag_is(el, "natural", "tree")) {
                uint32_t s = id;
                float h = tag_num(el, "height", 6 + rnd01(&s) * 8);
                const char *lt = tag(el, "leaf_type");
                add_tree(bd, x, z, fminf(fmaxf(h, 3), 40), id, (lt && !strcmp(lt, "needleleaved")) || fabs(la) > 58);
            } else if (tag_is(el, "railway", "subway_entrance") || (tag_is(el, "entrance", "yes") && tag_is(el, "railway", "subway_entrance"))) {
                add_entr(bd, x, z, false, tag(el, "name"));
            } else if (tag_is(el, "railway", "station") && (tag_is(el, "station", "subway") || tag_is(el, "subway", "yes"))) {
                /* the station hall underground */
                Mesh *m = &bd->under;
                boxm(m, x - 20, UNDER_Y - 0.01f, z - 6, x + 20, UNDER_Y, z + 6, rgb(170, 166, 160));
                quadm(m, x - 20, UNDER_Y + UNDER_H, z - 6, x + 20, UNDER_Y + UNDER_H, z - 6, x + 20, UNDER_Y + UNDER_H, z + 6, x - 20, UNDER_Y + UNDER_H, z + 6, rgb(200, 200, 205));
                for (int s = -1; s <= 1; s += 2)
                    quadm(m, x - 20, UNDER_Y, z + s * 6, x + 20, UNDER_Y, z + s * 6, x + 20, UNDER_Y + UNDER_H, z + s * 6, x - 20, UNDER_Y + UNDER_H, z + s * 6, rgb(90, 140, 190));
                for (int s = -1; s <= 1; s += 2)
                    quadm(m, x + s * 20, UNDER_Y, z - 6, x + s * 20, UNDER_Y, z + 6, x + s * 20, UNDER_Y + UNDER_H, z + 6, x + s * 20, UNDER_Y + UNDER_H, z - 6, rgb(90, 140, 190));
                Seg sg = {x - 18, z, x + 18, z, 12};
                PUSH(bd->segs, bd->nsegs, bd->csegs, sg);
                const char *nm = tag(el, "name");
                if (nm) {
                    Named n = {x - 18, z, x + 18, z, ""};
                    snprintf(n.name, sizeof n.name, "%s station", nm);
                    PUSH(bd->names, bd->nnames, bd->cnames, n);
                }
            }
            continue;
        }
        double la, lo;
        if (!strcmp(type, "way")) {
            if (!geom_pts(jget(el, "geometry"), &p, &la, &lo)) continue;
            int tb, tk;
            tile_of(la, lo, &tb, &tk);
            if (tb != b || tk != k) continue;
            bool area = closed(&p) && !tag(el, "highway") && !tag(el, "barrier") && !tag(el, "waterway") && !tag(el, "railway");
            if (closed(&p) && (tag_is(el, "area", "yes") || tag(el, "building"))) area = true;
            if (tag_is(el, "railway", "platform")) area = closed(&p);
            if (area) { p.n--; area_feature(bd, el, &p, id); }
            else line_feature(bd, el, &p, id);
        } else if (!strcmp(type, "relation")) {
            Pts rs[16];
            la = lo = 999;
            int nr = rings(jget(el, "members"), rs, 16, &la, &lo);
            int tb, tk;
            tile_of(la, lo, &tb, &tk);
            for (int r = 0; r < nr; r++) {
                if (tb == b && tk == k && closed(&rs[r])) { rs[r].n--; area_feature(bd, el, &rs[r], id + (uint32_t)r); }
                free(rs[r].xz);
            }
        }
    }
    /* Underground, each subway entrance opens into a small hall with a corridor to the nearest tunnel or station
     * of this area, so the stairs always lead somewhere you can walk. */
    int nseg0 = bd->nsegs, nent = bd->nents;
    for (int e = 0; e < nent; e++) {
        Entr en = bd->ents[e];
        if (en.portal) continue;
        float best = 400, cx = 0, cz = 0;
        for (int s = 0; s < nseg0; s++) {
            Seg *g = &bd->segs[s];
            if (dist2(g->ax, g->az, en.x, en.z) < 2.5f && dist2(g->bx, g->bz, en.x, en.z) < 2.5f) continue;   /* its own */
            float dx = g->bx - g->ax, dz = g->bz - g->az, l2 = dx * dx + dz * dz;
            float u = l2 > 0 ? clampf(((en.x - g->ax) * dx + (en.z - g->az) * dz) / l2, 0, 1) : 0;
            float qx = g->ax + dx * u, qz = g->az + dz * u, d = dist2(en.x, en.z, qx, qz);
            if (d < best) { best = d; cx = qx; cz = qz; }
        }
        float hall[4] = {en.x - 4, en.z + 1, en.x + 4, en.z + 1};
        Pts hp = {hall, 2, 2};
        tunnel_geo(bd, &hp, 8, NULL);
        if (best < 400 && best > 3) {
            float cor[4] = {en.x, en.z + 1, cx, cz};
            Pts cp = {cor, 2, 2};
            tunnel_geo(bd, &cp, 4, NULL);
        }
    }
    free(p.xz);
    json_free(root);
    return bd;
}

static void build_free(Build *bd)
{
    if (!bd) return;
    for (int i = 0; i < NFLAT; i++) mesh_free(&bd->flat[i]);
    mesh_free(&bd->solid); mesh_free(&bd->under);
    for (int i = 0; i < bd->npolys; i++) free(bd->polys[i].xz);
    free(bd->polys); free(bd->segs); free(bd->ents); free(bd->names);
    free(bd);
}

/* ------------------------------------------------------------------ fetching (worker thread) */

static const char *cache_dir(void)
{
    static char dir[512];
    if (dir[0]) return dir;
    const char *x = getenv("XDG_CACHE_HOME"), *home = getenv("HOME");
    if (x && *x) snprintf(dir, sizeof dir, "%s/lifeos/osm", x);
    else snprintf(dir, sizeof dir, "%s/.cache/lifeos/osm", home ? home : "/tmp");
    char tmp[512];
    snprintf(tmp, sizeof tmp, "%s", dir);
    for (char *p = tmp + 1; *p; p++) if (*p == '/') { *p = 0; mkdir(tmp, 0755); *p = '/'; }
    mkdir(tmp, 0755);
    return dir;
}

typedef struct { char *b; size_t n; } Buf;
static size_t on_data(char *d, size_t s, size_t n, void *u)
{
    Buf *b = u;
    size_t k = s * n;
    if (b->n + k > 64u << 20) return 0;
    char *nb = realloc(b->b, b->n + k + 1);
    if (!nb) return 0;
    memcpy(nb + b->n, d, k);
    b->b = nb; b->n += k; b->b[b->n] = 0;
    return k;
}

static const char *SERVERS[] = {"https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
                                "https://maps.mail.ru/osm/tools/overpass/api/interpreter"};
static int server;

static char *fetch(int b, int k, int *status)
{
    double s, w, n, e;
    tile_bbox(b, k, &s, &w, &n, &e);
    char bb[128], q[4096];
    snprintf(bb, sizeof bb, "%.6f,%.6f,%.6f,%.6f", s, w, n, e);
    snprintf(q, sizeof q,
             "[out:json][timeout:90];("
             "way[\"building\"](%s);way[\"building:part\"](%s);relation[\"building\"](%s);"
             "way[\"highway\"](%s);way[\"railway\"](%s);way[\"waterway\"](%s);way[\"barrier\"~\"^(wall|fence|hedge|city_wall)$\"](%s);"
             "way[\"natural\"~\"^(water|wood|scrub|heath|grassland|beach|sand|glacier|bare_rock|scree|wetland)$\"](%s);"
             "relation[\"natural\"~\"^(water|wood)$\"](%s);"
             "way[\"landuse\"](%s);relation[\"landuse\"~\"^(forest|grass|meadow|reservoir)$\"](%s);"
             "way[\"leisure\"~\"^(park|garden|pitch|playground|common)$\"](%s);way[\"amenity\"=\"parking\"](%s);"
             "node[\"natural\"=\"tree\"](%s);node[\"railway\"=\"subway_entrance\"](%s);node[\"railway\"=\"station\"](%s);"
             ");out geom qt;",
             bb, bb, bb, bb, bb, bb, bb, bb, bb, bb, bb, bb, bb, bb, bb, bb);
    CURL *c = curl_easy_init();
    if (!c) return NULL;
    char *esc = curl_easy_escape(c, q, 0);
    size_t pl = strlen(esc) + 8;
    char *post = malloc(pl);
    snprintf(post, pl, "data=%s", esc);
    curl_free(esc);
    Buf buf = {0};
    curl_easy_setopt(c, CURLOPT_URL, SERVERS[server]);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, post);
    curl_easy_setopt(c, CURLOPT_USERAGENT, "LifeOS-Apartment/1.0 (explorable world; https://github.com/SoulVoid0/ai-lifeos)");
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, on_data);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &buf);
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 15L);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 120L);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(c, CURLOPT_ACCEPT_ENCODING, "");
    CURLcode rc = curl_easy_perform(c);
    long code = 0;
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    curl_easy_cleanup(c);
    free(post);
    *status = rc == CURLE_OK ? (int)code : -1;
    if (rc != CURLE_OK || code != 200 || !buf.b || !strstr(buf.b, "\"elements\"")) { free(buf.b); return NULL; }
    /* Overpass reports a timeout or overload inside a 200 answer */
    if (strstr(buf.b, "\"remark\"") && strstr(buf.b, "runtime error")) { free(buf.b); *status = 504; return NULL; }
    return buf.b;
}

static char *read_all(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n <= 0) { fclose(f); return NULL; }
    char *b = malloc((size_t)n + 1);
    if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
    if (b) b[n] = 0;
    fclose(f);
    return b;
}

static void *worker_main(void *arg)
{
    (void)arg;
    Uint32 next_net = 0;
    pthread_mutex_lock(&mu);
    while (!quit) {
        Tile *best = NULL;
        Uint32 now = SDL_GetTicks();
        for (int i = 0; i < MAX_TILES; i++) {
            Tile *t = &tiles[i];
            if (!t->used || (t->state != T_WANT && !(t->state == T_FAILED && now >= t->retry_at))) continue;
            if (!best || t->dist < best->dist) best = t;
        }
        if (!best) { struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts); ts.tv_nsec += 300000000; if (ts.tv_nsec >= 1000000000) { ts.tv_sec++; ts.tv_nsec -= 1000000000; } pthread_cond_timedwait(&cv, &mu, &ts); continue; }
        int b = best->b, k = best->k, g = gen;
        best->state = T_BUSY;
        pthread_mutex_unlock(&mu);

        char path[700];
        snprintf(path, sizeof path, "%s/%d_%d.json", cache_dir(), b, k);
        char *json = read_all(path);
        int status = 200;
        if (!json) {
            while (!quit && SDL_GetTicks() < next_net) SDL_Delay(100);
            net_busy = 1;
            json = fetch(b, k, &status);
            net_busy = 0;
            Uint32 pause = 1500;
            if (json) {
                net_fail = 0;
                char tmp[720];
                snprintf(tmp, sizeof tmp, "%s.tmp", path);
                FILE *f = fopen(tmp, "wb");
                if (f) { fwrite(json, 1, strlen(json), f); fclose(f); rename(tmp, path); }
            } else {
                net_fail++;
                server = (server + 1) % (int)(sizeof SERVERS / sizeof SERVERS[0]);
                pause = status == 429 ? 30000 : status == 504 || status == 503 ? 15000 : 5000;
            }
            next_net = SDL_GetTicks() + pause;
        }
        Build *bd = NULL;
        if (json) {
            pthread_mutex_lock(&mu);
            bool still = g == gen;
            pthread_mutex_unlock(&mu);
            if (still) bd = build_area(b, k, json);
            free(json);
        }
        pthread_mutex_lock(&mu);
        for (int i = 0; i < MAX_TILES; i++) {
            Tile *t = &tiles[i];
            if (!t->used || t->b != b || t->k != k || t->state != T_BUSY) continue;
            if (!json) { t->state = T_FAILED; t->retry_at = SDL_GetTicks() + 20000 + 10000 * (Uint32)(net_fail > 6 ? 6 : net_fail); }
            else if (g != gen || !bd) t->state = T_WANT;
            else { t->bd = bd; bd = NULL; t->gen = g; t->state = T_BUILT; }
        }
        pthread_mutex_unlock(&mu);
        build_free(bd);
        pthread_mutex_lock(&mu);
    }
    pthread_mutex_unlock(&mu);
    return NULL;
}

/* ------------------------------------------------------------------ the main thread side */

static void tile_drop(Tile *t)
{
    if (t->lists) glDeleteLists(t->lists, NFLAT + 2);
    build_free(t->bd);
    for (int i = 0; i < t->npolys; i++) free(t->polys[i].xz);
    free(t->polys); free(t->segs); free(t->ents); free(t->names);
    memset(t, 0, sizeof *t);
}

static void compile(GLuint list, Mesh *m)
{
    glNewList(list, GL_COMPILE);
    if (m->n) {
        glBegin(GL_TRIANGLES);
        for (int i = 0; i < m->n; i++) {
            Vtx *v = &m->v[i];
            glColor4ub(v->r, v->g, v->b, v->a);
            glVertex3f(v->x, v->y, v->z);
        }
        glEnd();
    }
    glEndList();
}

static void home_pos(void) { ll2xz(HOME_LAT, HOME_LON, &home_x, &home_z); }

static void rebuild_all(void)
{
    pthread_mutex_lock(&mu);
    gen++;
    for (int i = 0; i < MAX_TILES; i++) if (tiles[i].used && tiles[i].state != T_BUSY) tile_drop(&tiles[i]);
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
}

void out_settings_changed(void) { if (outside) rebuild_all(); }

static void start_worker(void)
{
    if (worker_up) return;
    static bool curl_ok;
    if (!curl_ok) { curl_global_init(CURL_GLOBAL_DEFAULT); curl_ok = true; }
    quit = false;
    if (pthread_create(&worker, NULL, worker_main, NULL) == 0) worker_up = true;
}

bool out_active(void) { return outside; }
bool out_underground(void) { return outside && under; }

void out_go_outside(float *px, float *pz, float *yaw)
{
    set_origin(HOME_LAT, HOME_LON);
    home_pos();
    rebuild_all();
    outside = true; under = false;
    /* in front of the building's door (the building spans z 3..21, its door faces north) */
    *px = home_x; *pz = home_z + 1.2f; *yaw = PI_F;
    start_worker();
}

void out_teleport(double lat, double lon, float *px, float *pz)
{
    lat = fmax(-89.9999, fmin(89.9999, lat));
    set_origin(lat, lon);
    home_pos();
    rebuild_all();
    outside = true; under = false;
    *px = 0; *pz = 0;
    start_worker();
}

void out_go_home(void)
{
    outside = false; under = false;
    rebuild_all();
}

/* Moves the origin along when walking far, so positions stay precise. */
bool out_reorigin(float *px, float *pz)
{
    if (!outside || under || hypotf(*px, *pz) < REORIGIN_M) return false;
    double lat, lon;
    xz2ll(*px, *pz, &lat, &lon);
    set_origin(lat, lon);
    home_pos();
    rebuild_all();
    *px = 0; *pz = 0;
    return true;
}

void out_update(float dt, float px, float pz)
{
    (void)dt;
    if (!outside) return;
    static float acc;
    acc += dt;
    pthread_mutex_lock(&mu);
    /* compile finished areas: a couple per frame */
    Uint32 t0 = SDL_GetTicks();
    for (int i = 0; i < MAX_TILES && SDL_GetTicks() - t0 < 8; i++) {
        Tile *t = &tiles[i];
        if (!t->used || t->state != T_BUILT || !t->bd) continue;
        if (t->gen != gen) { build_free(t->bd); t->bd = NULL; t->state = T_WANT; continue; }
        Build *bd = t->bd;
        t->lists = glGenLists(NFLAT + 2);
        for (int p = 0; p < NFLAT; p++) compile(t->lists + p, &bd->flat[p]);
        compile(t->lists + NFLAT, &bd->solid);
        compile(t->lists + NFLAT + 1, &bd->under);
        t->polys = bd->polys; t->npolys = bd->npolys; bd->polys = NULL; bd->npolys = 0;
        t->segs = bd->segs; t->nsegs = bd->nsegs; bd->segs = NULL;
        t->ents = bd->ents; t->nents = bd->nents; bd->ents = NULL;
        t->names = bd->names; t->nnames = bd->nnames; bd->names = NULL;
        t->road_m = bd->road_m; t->trees = bd->trees; t->water = bd->water;
        t->empty = !bd->solid.n && !bd->flat[P_LAND].n && !bd->flat[P_ROAD].n && !bd->under.n;
        build_free(bd);
        t->bd = NULL;
        t->state = T_READY;
    }
    if (acc < 0.4f) { pthread_mutex_unlock(&mu); return; }
    acc = 0;
    /* which areas are wanted: everything within the draw distance */
    float rad = (float)g_draw_dist + 150;
    for (int i = 0; i < MAX_TILES; i++) tiles[i].dist = 1e9f;
    int want_b[MAX_TILES], want_k[MAX_TILES]; float want_d[MAX_TILES]; int nw = 0;
    float step = 140;
    for (float dz = -rad; dz <= rad; dz += step)
        for (float dx = -rad; dx <= rad; dx += step) {
            float d = sqrtf(dx * dx + dz * dz);
            if (d > rad) continue;
            double lat, lon;
            xz2ll(px + dx, pz + dz, &lat, &lon);
            int b, k;
            tile_of(lat, lon, &b, &k);
            int j;
            for (j = 0; j < nw; j++) if (want_b[j] == b && want_k[j] == k) break;
            if (j < nw) { if (d < want_d[j]) want_d[j] = d; continue; }
            if (nw < MAX_TILES) { want_b[nw] = b; want_k[nw] = k; want_d[nw] = d; nw++; }
        }
    for (int j = 0; j < nw; j++) {
        Tile *t = NULL;
        for (int i = 0; i < MAX_TILES; i++) if (tiles[i].used && tiles[i].b == want_b[j] && tiles[i].k == want_k[j]) { t = &tiles[i]; break; }
        if (!t) {
            for (int i = 0; i < MAX_TILES; i++) if (!tiles[i].used) { t = &tiles[i]; break; }
            if (!t) {
                /* evict the farthest one that isn't busy */
                float far = -1;
                for (int i = 0; i < MAX_TILES; i++)
                    if (tiles[i].state != T_BUSY && tiles[i].dist > far) { far = tiles[i].dist; t = &tiles[i]; }
                if (!t || far < want_d[j]) continue;
                tile_drop(t);
            }
            t->used = true; t->b = want_b[j]; t->k = want_k[j]; t->state = T_WANT;
        }
        t->dist = want_d[j];
    }
    /* drop areas far behind */
    for (int i = 0; i < MAX_TILES; i++) if (tiles[i].used && tiles[i].dist > 1e8f && tiles[i].state != T_BUSY) tile_drop(&tiles[i]);
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
}

/* ------------------------------------------------------------------ the home building at the pole */

static void home_draw(void)
{
    float x = home_x, z = home_z;
    Col wall = rgb(206, 196, 182), trim = rgb(90, 84, 80);
    box(x - 12, 0, z + 3, x + 12, 10, z + 21, wall);
    box(x - 12.3f, 10, z + 2.7f, x + 12.3f, 10.6f, z + 21.3f, trim);
    for (int f = 0; f < 3; f++)
        for (int i = -4; i <= 4; i++) {
            if (f == 0 && i == 0) continue;
            float wx = x + i * 2.6f, wy = 1.0f + f * 3.1f;
            glow_quad(v3(wx - 0.7f, wy + 1.5f, z + 2.98f), v3(wx + 0.7f, wy + 1.5f, z + 2.98f), v3(wx + 0.7f, wy, z + 2.98f), v3(wx - 0.7f, wy, z + 2.98f),
                      NOW_HOURS > 7 && NOW_HOURS < 18 ? rgb(120, 150, 180) : rgb(255, 214, 140));
        }
    box(x - 0.8f, 0, z + 2.9f, x + 0.8f, 2.3f, z + 3.0f, rgb(110, 70, 45));
    box(x - 1.6f, 2.4f, z + 2.4f, x + 1.6f, 2.6f, z + 3.0f, trim);
    text3d(F_TITLE, v3(x + 2.0f, 3.3f, z + 2.95f), v3(-1, 0, 0), v3(0, -1, 0), 0.9f / text_h(F_TITLE), "LifeOS", rgb(40, 60, 90));
    /* the ceremonial South Pole marker nearby */
    float mx, mz;
    ll2xz(-90, 0, &mx, &mz);
    if (dist2(mx, mz, x, z) < 2000) {
        prism(mx, 0, mz, 0.12f, 0.12f, 1.2f, 8, rgb(200, 200, 205));
        ball(mx, 1.35f, mz, 0.22f, rgb(225, 225, 235));
        static const Col flags[6] = {{180, 40, 50, 255}, {40, 80, 160, 255}, {230, 230, 230, 255}, {40, 140, 70, 255}, {230, 190, 40, 255}, {200, 90, 40, 255}};
        for (int i = 0; i < 12; i++) {
            float a = 2 * PI_F * i / 12, fx = mx + cosf(a) * 4, fz = mz + sinf(a) * 4;
            box(fx - 0.04f, 0, fz - 0.04f, fx + 0.04f, 2.6f, fz + 0.04f, rgb(190, 190, 195));
            box(fx, 2.0f, fz - 0.02f, fx + 0.8f, 2.5f, fz + 0.02f, flags[i % 6]);
        }
    }
}

/* ------------------------------------------------------------------ drawing */

Col out_ground_color(void)
{
    double lat = o_lat;
    if (fabs(lat) > 62) return rgb(232, 238, 244);
    if (fabs(lat) > 50) return rgb(150, 160, 120);
    return rgb(160, 168, 128);
}

void out_draw(float px, float pz, float yaw)
{
    if (!outside) return;
    float rad = (float)g_draw_dist + 300;
    float fx = sinf(yaw), fz = cosf(yaw);
    pthread_mutex_lock(&mu);
    if (under) {
        glEnable(GL_DEPTH_TEST);
        for (int i = 0; i < MAX_TILES; i++) {
            Tile *t = &tiles[i];
            if (t->state == T_READY && t->lists) glCallList(t->lists + NFLAT + 1);
        }
        pthread_mutex_unlock(&mu);
        return;
    }
    glDisable(GL_DEPTH_TEST);
    Col g = out_ground_color();
    /* a grid, not one big quad: haze is worked out per corner */
    glBegin(GL_QUADS);
    glColor4ub(g.r, g.g, g.b, 255);
    const int G = 24;
    float cell = 2 * rad / G, gx = floorf(px / cell) * cell - rad, gz = floorf(pz / cell) * cell - rad;
    for (int j = 0; j < G; j++)
        for (int i = 0; i < G; i++) {
            float x0 = gx + i * cell, z0 = gz + j * cell;
            glVertex3f(x0, 0, z0); glVertex3f(x0 + cell, 0, z0); glVertex3f(x0 + cell, 0, z0 + cell); glVertex3f(x0, 0, z0 + cell);
        }
    glEnd();
    glEnable(GL_BLEND);
    for (int p = 0; p < NFLAT; p++)
        for (int i = 0; i < MAX_TILES; i++) {
            Tile *t = &tiles[i];
            if (t->state == T_READY && t->lists && !t->empty && t->dist < rad) glCallList(t->lists + p);
        }
    glDisable(GL_BLEND);
    glEnable(GL_DEPTH_TEST);
    for (int i = 0; i < MAX_TILES; i++) {
        Tile *t = &tiles[i];
        if (t->state == T_READY && t->lists && !t->empty && t->dist < rad) glCallList(t->lists + NFLAT);
    }
    pthread_mutex_unlock(&mu);
    if (dist2(px, pz, home_x, home_z) < rad + 50) home_draw();
    (void)fx; (void)fz;
}

/* ------------------------------------------------------------------ walking */

bool out_collide(float *x, float *z, float r)
{
    bool hit = false;
    pthread_mutex_lock(&mu);
    if (under) {
        /* stay inside the tunnels and halls */
        float best = 1e9f, bx = *x, bz = *z, bw = 0;
        for (int i = 0; i < MAX_TILES; i++) {
            Tile *t = &tiles[i];
            if (t->state != T_READY) continue;
            for (int s = 0; s < t->nsegs; s++) {
                Seg *g = &t->segs[s];
                float dx = g->bx - g->ax, dz = g->bz - g->az, l2 = dx * dx + dz * dz;
                float u = l2 > 0 ? ((*x - g->ax) * dx + (*z - g->az) * dz) / l2 : 0;
                u = clampf(u, 0, 1);
                float cx = g->ax + dx * u, cz = g->az + dz * u, d = dist2(*x, *z, cx, cz) - g->w / 2;
                if (d < best) { best = d; bx = cx; bz = cz; bw = g->w; }
            }
        }
        if (best < 1e8f && best > -r) {
            float d = dist2(*x, *z, bx, bz), lim = fmaxf(bw / 2 - r, 0.05f);
            if (d > 1e-4f) { *x = bx + (*x - bx) / d * lim; *z = bz + (*z - bz) / d * lim; hit = true; }
        }
        pthread_mutex_unlock(&mu);
        return hit;
    }
    for (int i = 0; i < MAX_TILES; i++) {
        Tile *t = &tiles[i];
        if (t->state != T_READY || t->dist > 400) continue;
        for (int pi = 0; pi < t->npolys; pi++) {
            Poly *p = &t->polys[pi];
            if (*x < p->x0 - r || *x > p->x1 + r || *z < p->z0 - r || *z > p->z1 + r) continue;
            /* nearest edge point */
            float best = 1e9f, nx = 0, nz = 0;
            for (int a = 0, b = p->n - 1; a < p->n; b = a++) {
                float ax = p->xz[2 * b], az = p->xz[2 * b + 1], ex = p->xz[2 * a], ez = p->xz[2 * a + 1];
                float dx = ex - ax, dz = ez - az, l2 = dx * dx + dz * dz;
                float u = l2 > 0 ? clampf(((*x - ax) * dx + (*z - az) * dz) / l2, 0, 1) : 0;
                float cx = ax + dx * u, cz = az + dz * u, d = dist2(*x, *z, cx, cz);
                if (d < best) { best = d; nx = cx; nz = cz; }
            }
            bool in = point_in(p->xz, p->n, *x, *z);
            if (in || best < r) {
                float d = best > 1e-4f ? best : 1e-4f;
                float ux = (*x - nx) / d, uz = (*z - nz) / d;
                if (in) { ux = -ux; uz = -uz; }
                *x = nx + ux * r; *z = nz + uz * r;
                hit = true;
            }
        }
    }
    pthread_mutex_unlock(&mu);
    /* the home building */
    if (*x > home_x - 12 - r && *x < home_x + 12 + r && *z > home_z + 3 - r && *z < home_z + 21 + r) {
        float dl = *x - (home_x - 12 - r), dr = home_x + 12 + r - *x, dn = *z - (home_z + 3 - r), ds = home_z + 21 + r - *z;
        float m = fminf(fminf(dl, dr), fminf(dn, ds));
        if (m == dl) *x = home_x - 12 - r; else if (m == dr) *x = home_x + 12 + r; else if (m == dn) *z = home_z + 3 - r; else *z = home_z + 21 + r;
        hit = true;
    }
    return hit;
}

/* What's in front: OT_HOME (the apartment's door), OT_DOWN (stairs down), OT_UP (stairs up). */
int out_target(float px, float pz, float fx, float fz, char *label, size_t n)
{
    if (!outside) return OT_NONE;
    if (!under) {
        float dx = home_x - px, dz = home_z + 3 - pz, d = sqrtf(dx * dx + dz * dz);
        if (d < 4.0f && (dx * fx + dz * fz) / (d + 1e-4f) > 0.5f) { snprintf(label, n, "Go inside (home)"); return OT_HOME; }
    }
    int kind = OT_NONE;
    float best = under ? 5 : 4.5f;
    pthread_mutex_lock(&mu);
    for (int i = 0; i < MAX_TILES; i++) {
        Tile *t = &tiles[i];
        if (t->state != T_READY) continue;
        for (int e = 0; e < t->nents; e++) {
            Entr *en = &t->ents[e];
            float d = dist2(px, pz, en->x, en->z);
            if (d >= best) continue;
            best = d;
            kind = under ? OT_UP : OT_DOWN;
            if (under) snprintf(label, n, "Climb up%s%s", en->name[0] ? " to " : "", en->name);
            else snprintf(label, n, "%s%s%s", en->portal ? "Walk into the tunnel" : "Go down to the subway", en->name[0] ? ": " : "", en->name);
        }
    }
    pthread_mutex_unlock(&mu);
    return kind;
}

/* Down to the nearest tunnel point, or back up to the surface. */
bool out_use(int kind, float *px, float *pz)
{
    if (kind == OT_UP) { under = false; return true; }
    if (kind != OT_DOWN) return false;
    float best = 200, bx = *px, bz = *pz;
    pthread_mutex_lock(&mu);
    /* down the stairs of the entrance you're at: you arrive at their foot, in its hall */
    for (int i = 0; i < MAX_TILES; i++) {
        Tile *t = &tiles[i];
        if (t->state != T_READY) continue;
        for (int e = 0; e < t->nents; e++)
            if (!t->ents[e].portal && dist2(*px, *pz, t->ents[e].x, t->ents[e].z) < 6) {
                *px = t->ents[e].x; *pz = t->ents[e].z - 2;
                pthread_mutex_unlock(&mu);
                under = true;
                return true;
            }
    }
    for (int i = 0; i < MAX_TILES; i++) {
        Tile *t = &tiles[i];
        if (t->state != T_READY) continue;
        for (int s = 0; s < t->nsegs; s++) {
            Seg *g = &t->segs[s];
            float dx = g->bx - g->ax, dz = g->bz - g->az, l2 = dx * dx + dz * dz;
            float u = l2 > 0 ? clampf(((*px - g->ax) * dx + (*pz - g->az) * dz) / l2, 0, 1) : 0;
            float cx = g->ax + dx * u, cz = g->az + dz * u, d = dist2(*px, *pz, cx, cz);
            if (d < best) { best = d; bx = cx; bz = cz; }
        }
    }
    pthread_mutex_unlock(&mu);
    if (best >= 200) return false;
    *px = bx; *pz = bz;
    under = true;
    return true;
}

/* The street (or station) you're on, if any. */
const char *out_place(float px, float pz)
{
    static char name[64];
    name[0] = 0;
    float best = under ? 40 : 18;
    pthread_mutex_lock(&mu);
    for (int i = 0; i < MAX_TILES; i++) {
        Tile *t = &tiles[i];
        if (t->state != T_READY || t->dist > 700) continue;
        for (int s = 0; s < t->nnames; s++) {
            Named *g = &t->names[s];
            float dx = g->bx - g->ax, dz = g->bz - g->az, l2 = dx * dx + dz * dz;
            float u = l2 > 0 ? clampf(((px - g->ax) * dx + (pz - g->az) * dz) / l2, 0, 1) : 0;
            float d = dist2(px, pz, g->ax + dx * u, g->az + dz * u);
            if (d < best) { best = d; snprintf(name, sizeof name, "%s", g->name); }
        }
    }
    pthread_mutex_unlock(&mu);
    return name;
}

/* Loading state for the HUD: areas still coming, and whether the map server can't be reached. */
int out_loading(bool *offline)
{
    int n = 0;
    pthread_mutex_lock(&mu);
    for (int i = 0; i < MAX_TILES; i++)
        if (tiles[i].used && tiles[i].state != T_READY && tiles[i].dist < g_draw_dist) n++;
    pthread_mutex_unlock(&mu);
    *offline = net_fail >= 2;
    return n;
}

/* For sounds: how busy the surroundings are (roads, trees, water), 0..1 each. */
void out_env(float px, float pz, float *traffic, float *trees, float *water)
{
    float r = 0, t = 0, w = 0;
    pthread_mutex_lock(&mu);
    for (int i = 0; i < MAX_TILES; i++) {
        Tile *tl = &tiles[i];
        if (tl->state != T_READY || tl->dist > 500) continue;
        float k = 1 - tl->dist / 500;
        r += tl->road_m * k; t += tl->trees * k; w += tl->water * k;
    }
    pthread_mutex_unlock(&mu);
    (void)px; (void)pz;
    *traffic = clampf(r / 6000, 0, 1);
    *trees = clampf(t / 300, 0, 1);
    *water = clampf(w / 40000, 0, 1);
}

bool out_polar(void) { return fabs(o_lat) > 62; }

/* ------------------------------------------------------------------ saving */

void out_save(const char *path, float px, float pz, float yaw)
{
    double lat, lon;
    xz2ll(px, pz, &lat, &lon);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "outside=%d\nunder=%d\nlat=%.7f\nlon=%.7f\nyaw=%.3f\nworld_on=%d\n", outside, under, lat, lon, yaw, g_world_on);
    fclose(f);
}

bool out_load(const char *path, float *px, float *pz, float *yaw)
{
    FILE *f = fopen(path, "r");
    if (!f) return false;
    char line[128];
    int o = 0, u = 0;
    double lat = HOME_LAT, lon = HOME_LON;
    float y = 0;
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "outside=%d", &o) == 1) continue;
        if (sscanf(line, "under=%d", &u) == 1) continue;
        if (sscanf(line, "lat=%lf", &lat) == 1) continue;
        if (sscanf(line, "lon=%lf", &lon) == 1) continue;
        if (sscanf(line, "yaw=%f", &y) == 1) continue;
        int w;
        if (sscanf(line, "world_on=%d", &w) == 1) g_world_on = w;
    }
    fclose(f);
    if (!o || !g_world_on) return false;
    out_teleport(lat, lon, px, pz);
    *yaw = y;
    under = u;   /* the tunnels load first; collision keeps you in them */
    return true;
}

void out_shutdown(void)
{
    if (!worker_up) return;
    pthread_mutex_lock(&mu);
    quit = true;
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
    pthread_join(worker, NULL);
    worker_up = false;
}

/* The sun's height over the horizon (degrees) where you are, right now: the real sun, so the South Pole has its
 * months-long day in the southern summer and night in the winter, and Tokyo is dark while home is lit. */
float out_sun_elev(void)
{
    time_t t = time(NULL);
    struct tm g;
    gmtime_r(&t, &g);
    double n = g.tm_yday + 1, utc = g.tm_hour + g.tm_min / 60.0 + g.tm_sec / 3600.0;
    double decl = -23.44 * cos(2 * M_PI / 365.0 * (n + 10)) * M_PI / 180;
    double ha = (15 * (utc + o_lon / 15.0 - 12)) * M_PI / 180, la = o_lat * M_PI / 180;
    double s = sin(la) * sin(decl) + cos(la) * cos(decl) * cos(ha);
    return (float)(asin(s < -1 ? -1 : s > 1 ? 1 : s) * 180 / M_PI);
}

/* The sky for a sun height: night, blue hour, a low orange sun, then day. k is how bright the world is lit. */
Col out_sky(float *k)
{
    static const struct { float e; Col c; float k; } K[] = {
        {-90, {12, 16, 34, 255}, 0.86f}, {-12, {12, 16, 34, 255}, 0.86f}, {-5, {40, 40, 80, 255}, 0.88f},
        {0, {236, 140, 96, 255}, 0.92f}, {6, {190, 190, 210, 255}, 0.97f}, {12, {150, 196, 240, 255}, 1.0f},
        {90, {150, 196, 240, 255}, 1.0f},
    };
    float e = out_sun_elev();
    for (int i = 0; i < 6; i++)
        if (e >= K[i].e && e <= K[i + 1].e) {
            float t = (e - K[i].e) / (K[i + 1].e - K[i].e);
            if (k) *k = K[i].k + (K[i + 1].k - K[i].k) * t;
            Col a = K[i].c, b = K[i + 1].c;
            return (Col){(uint8_t)(a.r + (b.r - a.r) * t), (uint8_t)(a.g + (b.g - a.g) * t), (uint8_t)(a.b + (b.b - a.b) * t), 255};
        }
    if (k) *k = 1;
    return K[0].c;
}
