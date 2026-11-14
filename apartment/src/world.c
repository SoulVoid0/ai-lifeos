/* The apartment: floor plan, walls, furniture, the spots the crew uses, and what the player can interact with.
 *
 *   x: 0 ........................................ 24   (meters, east)
 *   z=0  +--------+-----+----------+-----------+
 *        | Master |Bath | AI bed 1 | AI bed 2  |
 *   z=7  +--d-----+--d--+--d-------+--d--------+
 *        |               Hallway               |
 *   z=9  +--[ open ]--+--[ ]--+--d-------------+
 *        | Living     |Kitchen|  Work room     |
 *   z=16 +------------+-------+----------------+
 *
 * Static geometry is compiled into one display list (rebuilt when the user redecorates); screens, windows and
 * anything animated are drawn every frame by world_draw_dynamic().
 */
#include "app.h"
#include <stdlib.h>
#include <time.h>

World W;
Shop SHOP;

const char *ROOM_KEYS[NROOMS] = {"living", "master", "kitchen", "bath", "ai1", "ai2", "work", "hall"};
const char *ROOM_NAMES[NROOMS] = {"Living room", "Master bedroom", "Kitchen", "Bathroom", "AI bedroom 1",
                                  "AI bedroom 2", "Work room", "Hallway"};

#define H 2.7f      /* ceiling */
#define T 0.2f      /* wall thickness */
#define HT (T / 2)

static const Rect ROOM_RECT[NROOMS] = {
    {0, 9, 10, 16}, {0, 0, 8, 7}, {10, 9, 15.5f, 16}, {8, 0, 11.5f, 7},
    {11.5f, 0, 17.5f, 7}, {17.5f, 0, 24, 7}, {15.5f, 9, 24, 16}, {0, 7, 24, 9},
};

/* Doors on the hallway walls. axis 0: wall along x at z = c, opening from a to b. */
typedef struct { int room; float c, a, b, top; } Door;
static const Door DOORS[] = {
    {R_MASTER, 7, 5.6f, 6.8f, 2.1f}, {R_BATH, 7, 9.2f, 10.3f, 2.1f}, {R_AI1, 7, 12.2f, 13.4f, 2.1f},
    {R_AI2, 7, 18.2f, 19.4f, 2.1f}, {R_LIVING, 9, 2.5f, 7.0f, 2.3f}, {R_KITCHEN, 9, 11.0f, 13.4f, 2.3f},
    {R_WORK, 9, 16.4f, 17.6f, 2.1f},
};
#define NDOORS (int)(sizeof DOORS / sizeof DOORS[0])

/* Windows on the outside walls: side 0 north (z=0), 1 south (z=16), 2 west (x=0), 3 east (x=24). */
typedef struct { int side; float a, b, y0, y1; } Win;
static const Win WINS[] = {
    {0, 3.4f, 4.8f, 1.0f, 2.1f}, {0, 9.3f, 10.3f, 1.6f, 2.1f}, {0, 13.6f, 15.4f, 2.0f, 2.5f},
    {0, 19.8f, 21.6f, 2.0f, 2.5f}, {1, 0.8f, 2.8f, 1.0f, 2.2f}, {1, 7.2f, 8.6f, 1.0f, 2.2f},
    {1, 13.6f, 14.5f, 1.2f, 2.0f}, {3, 10.4f, 12.0f, 1.0f, 2.1f}, {3, 13.2f, 14.8f, 1.0f, 2.1f},
    {2, 4.6f, 6.2f, 1.0f, 2.1f}, {3, 3.0f, 4.6f, 2.0f, 2.5f},
};
#define NWINS (int)(sizeof WINS / sizeof WINS[0])

/* Work room islands and AI bunk beds. */
static const float ISLAND_X[3] = {17.3f, 19.75f, 22.2f};
#define ISLAND_Z 12.0f
static const float BUNK_X[6] = {12.4f, 14.5f, 16.6f, 18.6f, 20.75f, 22.9f};

/* Extra per-spot routing (approach points walked through before reaching the spot). */
typedef struct { int n; float x[3], z[3]; } Approach;
static Approach APPROACH[MAX_SPOTS];

static Col C_TRIM, C_WOOD, C_DARKWOOD, C_METAL, C_WHITE, C_BLACK, C_CEIL;

/* ------------------------------------------------------------------ shop helpers */

int shop_find(const char *id)
{
    for (int i = 0; i < SHOP.nitems; i++) if (!strcmp(SHOP.items[i].id, id)) return i;
    return -1;
}

bool lay_has(const char *id)
{
    int i = shop_find(id);
    if (i < 0) return false;
    return SHOP.lay.decor[i] || SHOP.lay.extra[i];
}

static void add_item(const char *id, const char *name, const char *kind, int cost, Col c)
{
    if (SHOP.nitems >= MAX_ITEMS || shop_find(id) >= 0) return;
    Item *it = &SHOP.items[SHOP.nitems++];
    memset(it, 0, sizeof *it);
    snprintf(it->id, sizeof it->id, "%s", id);
    snprintf(it->name, sizeof it->name, "%s", name);
    snprintf(it->kind, sizeof it->kind, "%s", kind);
    it->cost = cost;
    it->owned = cost == 0;
    it->rgb = c;
}

void shop_defaults(void)
{
    /* What everyone owns; the full catalog comes from the backend. */
    memset(&SHOP, 0, sizeof SHOP);
    add_item("paint:cream", "Cream paint", "paint", 0, rgb(232, 224, 206));
    add_item("paint:white", "White paint", "paint", 0, rgb(236, 236, 232));
    add_item("paint:stone", "Stone gray paint", "paint", 0, rgb(176, 176, 170));
    add_item("floor:oak", "Oak floor", "floor", 0, rgb(176, 132, 88));
    add_item("floor:tile", "White tile", "floor", 0, rgb(214, 216, 218));
    add_item("floor:carpet_gray", "Gray carpet", "floor", 0, rgb(128, 130, 136));
    add_item("couch:slate", "Slate couch", "couch", 0, rgb(84, 92, 108));
    static const char *walls[NROOMS] = {"paint:cream", "paint:white", "paint:white", "paint:white", "paint:stone",
                                        "paint:stone", "paint:white", "paint:cream"};
    static const char *floors[NROOMS] = {"floor:oak", "floor:carpet_gray", "floor:tile", "floor:tile",
                                         "floor:carpet_gray", "floor:carpet_gray", "floor:oak", "floor:oak"};
    for (int r = 0; r < NROOMS; r++) { SHOP.lay.wall[r] = shop_find(walls[r]); SHOP.lay.floor[r] = shop_find(floors[r]); }
    SHOP.lay.couch = shop_find("couch:slate");
}

static Col item_col(int idx, Col def) { return idx >= 0 && idx < SHOP.nitems ? SHOP.items[idx].rgb : def; }
static const char *item_id(int idx) { return idx >= 0 && idx < SHOP.nitems ? SHOP.items[idx].id : ""; }

/* ------------------------------------------------------------------ geometry registration */

static void add_collider(float x0, float z0, float x1, float z1, bool wall)
{
    if (W.ncolliders < MAX_COLLIDERS) { Rect r = {x0, z0, x1, z1}; W.colliders[W.ncolliders++] = r; }
    if (wall && W.nwalls < MAX_COLLIDERS) { Rect r = {x0, z0, x1, z1}; W.walls[W.nwalls++] = r; }
}

static int add_spot(int kind, int room, float x, float y, float z, float yaw, int pose, int owner)
{
    if (W.nspots >= MAX_SPOTS) return -1;
    Spot *s = &W.spots[W.nspots];
    s->kind = kind; s->room = room; s->pos = v3(x, y, z); s->yaw = yaw; s->pose = pose;
    s->owner = owner; s->occupant = -1;
    APPROACH[W.nspots].n = 0;
    return W.nspots++;
}

static void approach(int spot, float x, float z)
{
    if (spot < 0) return;
    Approach *a = &APPROACH[spot];
    if (a->n < 3) { a->x[a->n] = x; a->z[a->n] = z; a->n++; }
}

int spot_approach(int spot, float *xs, float *zs)
{
    if (spot < 0) return 0;
    Approach *a = &APPROACH[spot];
    for (int i = 0; i < a->n; i++) { xs[i] = a->x[i]; zs[i] = a->z[i]; }
    return a->n;
}

static void add_inter(int kind, float x, float y, float z, float reach, int ref, const char *label)
{
    if (W.ninters >= MAX_INTERS) return;
    Inter *i = &W.inters[W.ninters++];
    i->kind = kind; i->pos = v3(x, y, z); i->reach = reach; i->ref = ref; i->label = label;
}

/* ------------------------------------------------------------------ queries */

int room_at(float x, float z)
{
    for (int r = 0; r < NROOMS; r++) {
        const Rect *q = &ROOM_RECT[r];
        if (r == R_HALL) continue;
        if (x >= q->x0 && x <= q->x1 && z >= q->z0 && z <= q->z1) {
            /* the wall line between room and hallway belongs to the hallway */
            if ((z > 6.9f && z < 7.0f) || (z > 9.0f && z < 9.1f)) break;
            return r;
        }
    }
    if (x >= 0 && x <= 24 && z >= 6.9f && z <= 9.1f) return R_HALL;
    return -1;
}

bool collide_circle(float *px, float *pz, float r)
{
    bool hit = false;
    for (int i = 0; i < W.ncolliders; i++) {
        const Rect *q = &W.colliders[i];
        float cx = clampf(*px, q->x0, q->x1), cz = clampf(*pz, q->z0, q->z1);
        float dx = *px - cx, dz = *pz - cz, d2 = dx * dx + dz * dz;
        if (d2 >= r * r) continue;
        hit = true;
        if (d2 > 1e-8f) {
            float d = sqrtf(d2), push = r - d;
            *px += dx / d * push; *pz += dz / d * push;
        } else {
            /* center inside the box: leave by the nearest side */
            float l = *px - q->x0, rr = q->x1 - *px, t = *pz - q->z0, b = q->z1 - *pz;
            float m = fminf(fminf(l, rr), fminf(t, b));
            if (m == l) *px = q->x0 - r; else if (m == rr) *px = q->x1 + r;
            else if (m == t) *pz = q->z0 - r; else *pz = q->z1 + r;
        }
    }
    return hit;
}

bool sight_clear(float ax, float az, float bx, float bz)
{
    float d = dist2(ax, az, bx, bz);
    int n = (int)(d / 0.25f) + 1;
    for (int i = 1; i < n; i++) {
        float t = (float)i / n, x = lerpf(ax, bx, t), z = lerpf(az, bz, t);
        for (int w = 0; w < W.nwalls; w++) {
            const Rect *q = &W.walls[w];
            if (x > q->x0 && x < q->x1 && z > q->z0 && z < q->z1) return false;
        }
    }
    return true;
}

void door_points(int room, V3 *inside, V3 *outside)
{
    for (int i = 0; i < NDOORS; i++) {
        if (DOORS[i].room != room) continue;
        float cx = (DOORS[i].a + DOORS[i].b) / 2, cz = DOORS[i].c;
        float into = cz < 8 ? -0.8f : 0.8f;     /* north rooms are at smaller z */
        *inside = v3(cx, 0, cz + into);
        *outside = v3(cx, 0, cz - into);
        return;
    }
    *inside = *outside = v3(12, 0, 8);
}

V3 room_hub(int room)
{
    switch (room) {
    case R_LIVING: return v3(7.6f, 0, 11.0f);
    case R_MASTER: return v3(5.0f, 0, 3.5f);
    case R_KITCHEN: return v3(11.0f, 0, 11.0f);
    case R_BATH: return v3(9.8f, 0, 4.5f);
    case R_AI1: return v3(14.5f, 0, 4.0f);
    case R_AI2: return v3(20.75f, 0, 4.0f);
    case R_WORK: return v3(16.0f, 0, 10.3f);
    default: return v3(12, 0, 8);
    }
}

/* ------------------------------------------------------------------ sky */

Col sky_color(float *k_out)
{
    static const struct { float h; Col c; float k; } K[] = {
        {0, {12, 16, 34, 255}, 0.86f}, {5, {14, 18, 40, 255}, 0.86f}, {6.2f, {236, 150, 110, 255}, 0.92f},
        {8, {150, 196, 240, 255}, 1.0f}, {17, {150, 196, 240, 255}, 1.0f}, {19, {236, 128, 86, 255}, 0.94f},
        {20.5f, {40, 40, 80, 255}, 0.88f}, {24, {12, 16, 34, 255}, 0.86f},
    };
    float h = NOW_HOURS;
    for (int i = 0; i < 7; i++) {
        if (h >= K[i].h && h <= K[i + 1].h) {
            float t = (h - K[i].h) / (K[i + 1].h - K[i].h);
            if (k_out) *k_out = lerpf(K[i].k, K[i + 1].k, t);
            return mix(K[i].c, K[i + 1].c, t);
        }
    }
    if (k_out) *k_out = 1;
    return K[0].c;
}

/* ------------------------------------------------------------------ walls */

/* The solid spans of one room side, after cutting out the doors on that line. */
static int door_cuts(float line_c, bool along_x, float lo, float hi, float *cuts_a, float *cuts_b, float *tops)
{
    int n = 0;
    if (!along_x) return 0;
    for (int i = 0; i < NDOORS; i++) {
        if (fabsf(DOORS[i].c - line_c) > 0.01f) continue;
        if (DOORS[i].b <= lo || DOORS[i].a >= hi) continue;
        cuts_a[n] = DOORS[i].a; cuts_b[n] = DOORS[i].b; tops[n] = DOORS[i].top; n++;
    }
    /* sort by a */
    for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
            if (cuts_a[j] < cuts_a[i]) {
                float t = cuts_a[i]; cuts_a[i] = cuts_a[j]; cuts_a[j] = t;
                t = cuts_b[i]; cuts_b[i] = cuts_b[j]; cuts_b[j] = t;
                t = tops[i]; tops[i] = tops[j]; tops[j] = t;
            }
    return n;
}

/* One inner face of a room. side: 0 north (z0), 1 south (z1), 2 west (x0), 3 east (x1). */
static void room_side(int room, int side, Col paint)
{
    const Rect *q = &ROOM_RECT[room];
    bool along_x = side < 2;
    float line = side == 0 ? q->z0 : side == 1 ? q->z1 : side == 2 ? q->x0 : q->x1;
    float face = side == 0 ? line + HT : side == 1 ? line - HT : side == 2 ? line + HT : line - HT;
    float lo = (along_x ? q->x0 : q->z0) + HT, hi = (along_x ? q->x1 : q->z1) - HT;
    float ca[8], cb[8], top[8];
    int n = door_cuts(line, along_x, lo, hi, ca, cb, top);
    float k = side == 0 || side == 1 ? 0.93f : 0.84f;
    Col p = shade(paint, k), base = shade(paint, 0.55f);
    float cur = lo;
    for (int i = 0; i <= n; i++) {
        float end = i < n ? fmaxf(lo, ca[i]) : hi;
        if (end > cur + 0.001f) {
            if (along_x) {
                quad(v3(cur, 0, face), v3(end, 0, face), v3(end, H, face), v3(cur, H, face), p);
                float o = side == 0 ? 0.02f : -0.02f;
                box(cur, 0, fminf(face, face + o * 2), end, 0.09f, fmaxf(face, face + o * 2), base);
            } else {
                quad(v3(face, 0, cur), v3(face, 0, end), v3(face, H, end), v3(face, H, cur), p);
                float o = side == 2 ? 0.02f : -0.02f;
                box(fminf(face, face + o * 2), 0, cur, fmaxf(face, face + o * 2), 0.09f, end, base);
            }
        }
        if (i < n) {
            float a = fmaxf(lo, ca[i]), b = fminf(hi, cb[i]);
            /* lintel above the opening */
            if (along_x) quad(v3(a, top[i], face), v3(b, top[i], face), v3(b, H, face), v3(a, H, face), p);
            cur = b;
        }
    }
}

static void door_frames(void)
{
    for (int i = 0; i < NDOORS; i++) {
        const Door *d = &DOORS[i];
        float z0 = d->c - HT - 0.02f, z1 = d->c + HT + 0.02f;
        box(d->a - 0.06f, 0, z0, d->a, d->top, z1, C_TRIM);
        box(d->b, 0, z0, d->b + 0.06f, d->top, z1, C_TRIM);
        box(d->a - 0.06f, d->top, z0, d->b + 0.06f, d->top + 0.06f, z1, C_TRIM);
        /* the threshold */
        box(d->a, 0, d->c - HT, d->b, 0.012f, d->c + HT, shade(C_TRIM, 0.8f));
    }
}

static void wall_colliders(void)
{
    /* outside */
    add_collider(-T, -T, 24 + T, HT, true);
    add_collider(-T, 16 - HT, 24 + T, 16 + T, true);
    add_collider(-T, -T, HT, 16 + T, true);
    add_collider(24 - HT, -T, 24 + T, 16 + T, true);
    /* hallway walls with their doors */
    float lines[2] = {7, 9};
    for (int l = 0; l < 2; l++) {
        float cur = 0;
        float ca[8], cb[8], tp[8];
        int n = door_cuts(lines[l], true, 0, 24, ca, cb, tp);
        for (int i = 0; i <= n; i++) {
            float end = i < n ? ca[i] : 24;
            if (end > cur) add_collider(cur, lines[l] - HT, end, lines[l] + HT, true);
            if (i < n) cur = cb[i];
        }
    }
    /* partitions */
    float north[3] = {8, 11.5f, 17.5f}, south[2] = {10, 15.5f};
    for (int i = 0; i < 3; i++) add_collider(north[i] - HT, 0, north[i] + HT, 7, true);
    for (int i = 0; i < 2; i++) add_collider(south[i] - HT, 9, south[i] + HT, 16, true);
}

static void window_frames(void)
{
    for (int i = 0; i < NWINS; i++) {
        const Win *w = &WINS[i];
        float f = 0.04f;
        switch (w->side) {
        case 0:
            box(w->a - f, w->y0 - f, HT, w->b + f, w->y0, HT + 0.12f, C_WHITE);
            box(w->a - f, w->y1, HT, w->b + f, w->y1 + f, HT + 0.05f, C_WHITE);
            box(w->a - f, w->y0, HT, w->a, w->y1, HT + 0.05f, C_WHITE);
            box(w->b, w->y0, HT, w->b + f, w->y1, HT + 0.05f, C_WHITE);
            box((w->a + w->b) / 2 - 0.02f, w->y0, HT, (w->a + w->b) / 2 + 0.02f, w->y1, HT + 0.04f, C_WHITE);
            break;
        case 1:
            box(w->a - f, w->y0 - f, 16 - HT - 0.12f, w->b + f, w->y0, 16 - HT, C_WHITE);
            box(w->a - f, w->y1, 16 - HT - 0.05f, w->b + f, w->y1 + f, 16 - HT, C_WHITE);
            box(w->a - f, w->y0, 16 - HT - 0.05f, w->a, w->y1, 16 - HT, C_WHITE);
            box(w->b, w->y0, 16 - HT - 0.05f, w->b + f, w->y1, 16 - HT, C_WHITE);
            box((w->a + w->b) / 2 - 0.02f, w->y0, 16 - HT - 0.04f, (w->a + w->b) / 2 + 0.02f, w->y1, 16 - HT, C_WHITE);
            break;
        case 2:
            box(HT, w->y0 - f, w->a - f, HT + 0.12f, w->y0, w->b + f, C_WHITE);
            box(HT, w->y1, w->a - f, HT + 0.05f, w->y1 + f, w->b + f, C_WHITE);
            box(HT, w->y0, w->a - f, HT + 0.05f, w->y1, w->a, C_WHITE);
            box(HT, w->y0, w->b, HT + 0.05f, w->y1, w->b + f, C_WHITE);
            break;
        default:
            box(24 - HT - 0.12f, w->y0 - f, w->a - f, 24 - HT, w->y0, w->b + f, C_WHITE);
            box(24 - HT - 0.05f, w->y1, w->a - f, 24 - HT, w->y1 + f, w->b + f, C_WHITE);
            box(24 - HT - 0.05f, w->y0, w->a - f, 24 - HT, w->y1, w->a, C_WHITE);
            box(24 - HT - 0.05f, w->y0, w->b, 24 - HT, w->y1, w->b + f, C_WHITE);
        }
    }
}

/* ------------------------------------------------------------------ floors and ceiling */

static void floor_room(int room)
{
    const Rect *q = &ROOM_RECT[room];
    int fi = SHOP.lay.floor[room];
    Col c = item_col(fi, rgb(170, 130, 90));
    const char *id = item_id(fi);
    float x0 = q->x0 + HT, x1 = q->x1 - HT, z0 = q->z0 + HT, z1 = q->z1 - HT;
    if (room == R_HALL) { x0 = q->x0 + HT; x1 = q->x1 - HT; z0 = 7 - HT; z1 = 9 + HT; }
    glBegin(GL_QUADS);
    if (strstr(id, "oak") || strstr(id, "walnut")) {
        /* planks along x */
        int i = 0;
        for (float z = z0; z < z1; z += 0.22f, i++) {
            float e = fminf(z + 0.22f, z1);
            Col p = shade(c, 0.92f + 0.08f * ((i * 7) % 5) / 4.0f);
            glColor3ub(p.r, p.g, p.b);
            glVertex3f(x0, 0, z); glVertex3f(x0, 0, e); glVertex3f(x1, 0, e); glVertex3f(x1, 0, z);
        }
    } else if (strstr(id, "tile") || strstr(id, "marble") || strstr(id, "checker")) {
        float s = strstr(id, "marble") ? 1.0f : 0.5f;
        bool checker = strstr(id, "checker") != NULL;
        int ix = 0;
        for (float x = x0; x < x1; x += s, ix++) {
            int iz = 0;
            for (float z = z0; z < z1; z += s, iz++) {
                float ex = fminf(x + s, x1), ez = fminf(z + s, z1);
                Col p = checker ? (((ix + iz) & 1) ? rgb(232, 232, 228) : c) : shade(c, ((ix + iz) & 1) ? 0.95f : 1.0f);
                glColor3ub(p.r, p.g, p.b);
                glVertex3f(x, 0, z); glVertex3f(x, 0, ez); glVertex3f(ex, 0, ez); glVertex3f(ex, 0, z);
            }
        }
    } else {
        glColor3ub(c.r, c.g, c.b);
        glVertex3f(x0, 0, z0); glVertex3f(x0, 0, z1); glVertex3f(x1, 0, z1); glVertex3f(x1, 0, z0);
    }
    glEnd();
}

static void ceiling(void)
{
    quad(v3(0, H, 0), v3(24, H, 0), v3(24, H, 16), v3(0, H, 16), C_CEIL);
    /* walls' top edges are hidden by the ceiling; give each room a light fixture */
    for (int r = 0; r < NROOMS; r++) {
        const Rect *q = &ROOM_RECT[r];
        if (r == R_HALL) {
            for (float x = 3; x < 24; x += 6) boxc(x, H - 0.05f, 8, 0.5f, 0.05f, 0.5f, rgb(250, 246, 230));
            continue;
        }
        boxc((q->x0 + q->x1) / 2, H - 0.06f, (q->z0 + q->z1) / 2, 0.7f, 0.06f, 0.7f, rgb(250, 246, 230));
    }
}

/* ------------------------------------------------------------------ furniture */

static void plant(float x, float z, float s)
{
    prism(x, 0, z, 0.2f * s, 0.26f * s, 0.4f * s, 6, rgb(180, 100, 70));
    Col g = rgb(60, 140, 70);
    ball(x, 0.62f * s, z, 0.3f * s, g);
    ball(x + 0.15f * s, 0.85f * s, z - 0.05f * s, 0.22f * s, shade(g, 1.1f));
    ball(x - 0.12f * s, 0.9f * s, z + 0.1f * s, 0.2f * s, shade(g, 0.9f));
    add_collider(x - 0.25f * s, z - 0.25f * s, x + 0.25f * s, z + 0.25f * s, false);
}

static void floor_lamp(float x, float z)
{
    prism(x, 0, z, 0.18f, 0.18f, 0.03f, 8, C_METAL);
    prism(x, 0.03f, z, 0.025f, 0.025f, 1.45f, 6, C_METAL);
    prism(x, 1.4f, z, 0.24f, 0.14f, 0.32f, 8, rgb(240, 226, 190));
    add_collider(x - 0.2f, z - 0.2f, x + 0.2f, z + 0.2f, false);
}

static void chair(float x, float z, float yaw, Col c)
{
    /* a simple dining chair; yaw = the way a sitter faces */
    float bx = -sinf(yaw) * 0.22f, bz = -cosf(yaw) * 0.22f;
    boxc(x, 0.42f, z, 0.44f, 0.05f, 0.44f, c);
    for (int i = 0; i < 4; i++) {
        float lx = x + ((i & 1) ? 0.18f : -0.18f), lz = z + ((i & 2) ? 0.18f : -0.18f);
        boxc(lx, 0, lz, 0.04f, 0.42f, 0.04f, shade(c, 0.8f));
    }
    if (fabsf(bx) > fabsf(bz)) boxc(x + bx, 0.47f, z, 0.04f, 0.5f, 0.44f, c);
    else boxc(x, 0.47f, z + bz, 0.44f, 0.5f, 0.04f, c);
}

static void bunk(int i)
{
    float cx = BUNK_X[i];
    Col frame = rgb(90, 96, 110), mat = rgb(236, 236, 240);
    Col blanket = i % 2 ? rgb(70, 110, 170) : rgb(150, 80, 110);
    float x0 = cx - 0.5f, x1 = cx + 0.5f, z0 = 0.1f + HT, z1 = 2.1f + HT;
    for (int k = 0; k < 4; k++) {
        float px = k & 1 ? x1 - 0.06f : x0, pz = k & 2 ? z1 - 0.06f : z0;
        box(px, 0, pz, px + 0.06f, 1.95f, pz + 0.06f, frame);
    }
    for (int lvl = 0; lvl < 2; lvl++) {
        float y = lvl ? 1.18f : 0.25f;
        box(x0, y, z0, x1, y + 0.1f, z1, frame);
        box(x0 + 0.04f, y + 0.1f, z0 + 0.04f, x1 - 0.04f, y + 0.24f, z1 - 0.04f, mat);
        box(x0 + 0.1f, y + 0.24f, z0 + 0.08f, x1 - 0.1f, y + 0.34f, z0 + 0.42f, rgb(250, 250, 250));
        box(x0 + 0.05f, y + 0.24f, z0 + 0.8f, x1 - 0.05f, y + 0.29f, z1 - 0.05f, lvl ? shade(blanket, 1.15f) : blanket);
    }
    /* safety rail and ladder */
    box(x0, 1.5f, z0 + 0.5f, x0 + 0.04f, 1.56f, z1, frame);
    for (float y = 0.4f; y < 1.3f; y += 0.28f) box(x1 - 0.04f, y, z1 - 0.5f, x1 + 0.02f, y + 0.04f, z1 - 0.06f, frame);
    add_collider(x0, z0, x1, z1, false);
}

static void island(int i)
{
    float cx = ISLAND_X[i], cz = ISLAND_Z;
    Col top = rgb(226, 222, 214), leg = C_METAL;
    box(cx - 0.9f, 0.72f, cz - 0.7f, cx + 0.9f, 0.76f, cz + 0.7f, top);
    for (int k = 0; k < 4; k++) {
        float lx = cx + (k & 1 ? 0.85f : -0.9f), lz = cz + (k & 2 ? 0.65f : -0.7f);
        box(lx, 0, lz, lx + 0.05f, 0.72f, lz + 0.05f, leg);
    }
    box(cx - 0.9f, 0.76f, cz - 0.02f, cx + 0.9f, 1.1f, cz + 0.02f, rgb(120, 140, 160));
    for (int s = 0; s < 4; s++) {
        float sx = cx + (s & 1 ? 0.45f : -0.45f);
        int north = !(s & 2);
        float mz = north ? cz - 0.25f : cz + 0.25f;
        /* monitor back + stand; the screen face is dynamic */
        boxc(sx, 0.76f, mz, 0.08f, 0.2f, 0.06f, C_BLACK);
        box(sx - 0.3f, 0.95f, mz - 0.025f, sx + 0.3f, 1.3f, mz + 0.025f, C_BLACK);
        float kz = north ? cz - 0.5f : cz + 0.5f;
        boxc(sx, 0.76f, kz, 0.4f, 0.02f, 0.13f, rgb(60, 60, 66));
        float chz = north ? cz - 1.05f : cz + 1.05f;
        Col chc = rgb(40, 44, 54);
        boxc(sx, 0.44f, chz, 0.46f, 0.06f, 0.46f, chc);
        boxc(sx, 0, chz, 0.06f, 0.44f, 0.06f, C_METAL);
        boxc(sx, 0, chz, 0.5f, 0.04f, 0.08f, C_METAL);
        boxc(sx, 0, chz, 0.08f, 0.04f, 0.5f, C_METAL);
        boxc(sx, 0.5f, north ? chz - 0.23f : chz + 0.23f, 0.46f, 0.55f, 0.05f, chc);
    }
    add_collider(cx - 0.9f, cz - 0.7f, cx + 0.9f, cz + 0.7f, false);
}

static void living_room(void)
{
    Col couch = item_col(SHOP.lay.couch, rgb(84, 92, 108));
    bool big = lay_has("extra:bigtv");
    /* TV stand + TV */
    box(3.8f, 0, 15.35f, 6.2f, 0.5f, 15.88f, C_DARKWOOD);
    box(3.9f, 0.08f, 15.33f, 6.1f, 0.42f, 15.35f, shade(C_DARKWOOD, 0.7f));
    if (big) {
        box(3.4f, 0.62f, 15.76f, 6.6f, 2.4f, 15.88f, C_BLACK);
        W.tv_center = v3(5, 1.51f, 15.75f); W.tv_w = 3.0f; W.tv_h = 1.69f;
    } else {
        boxc(5, 0.5f, 15.7f, 0.3f, 0.12f, 0.2f, C_BLACK);
        box(4.0f, 0.72f, 15.74f, 6.0f, 1.9f, 15.84f, C_BLACK);
        W.tv_center = v3(5, 1.31f, 15.73f); W.tv_w = 1.86f; W.tv_h = 1.05f;
    }
    add_collider(3.8f, 15.3f, 6.2f, 16, false);
    /* couch facing the TV (south) */
    box(3.4f, 0.1f, 11.6f, 6.6f, 0.42f, 12.5f, couch);
    for (int i = 0; i < 3; i++) box(3.45f + i * 1.04f, 0.42f, 11.65f, 4.47f + i * 1.04f, 0.52f, 12.45f, shade(couch, 1.12f));
    box(3.4f, 0.1f, 11.4f, 6.6f, 1.0f, 11.66f, shade(couch, 0.95f));
    box(3.2f, 0.1f, 11.4f, 3.42f, 0.68f, 12.5f, couch);
    box(6.58f, 0.1f, 11.4f, 6.8f, 0.68f, 12.5f, couch);
    for (int k = 0; k < 4; k++) boxc(k & 1 ? 6.7f : 3.3f, 0, k & 2 ? 12.4f : 11.5f, 0.08f, 0.1f, 0.08f, C_DARKWOOD);
    add_collider(3.2f, 11.4f, 6.8f, 12.5f, false);
    /* coffee table */
    box(4.2f, 0.38f, 13.2f, 5.8f, 0.44f, 13.9f, C_WOOD);
    for (int k = 0; k < 4; k++) boxc(k & 1 ? 5.7f : 4.3f, 0, k & 2 ? 13.8f : 13.3f, 0.06f, 0.38f, 0.06f, C_WOOD);
    boxc(4.6f, 0.44f, 13.5f, 0.12f, 0.1f, 0.12f, rgb(200, 80, 60));   /* a mug */
    boxc(5.3f, 0.44f, 13.6f, 0.3f, 0.02f, 0.2f, rgb(40, 40, 46));    /* the remote */
    add_collider(4.2f, 13.2f, 5.8f, 13.9f, false);
    /* armchair */
    box(8.6f, 0.1f, 12.2f, 9.5f, 0.45f, 13.1f, shade(couch, 0.85f));
    box(9.3f, 0.1f, 12.2f, 9.55f, 1.0f, 13.1f, shade(couch, 0.8f));
    box(8.6f, 0.1f, 12.05f, 9.55f, 0.66f, 12.22f, shade(couch, 0.8f));
    box(8.6f, 0.1f, 13.08f, 9.55f, 0.66f, 13.25f, shade(couch, 0.8f));
    add_collider(8.6f, 12.05f, 9.55f, 13.25f, false);
    /* side table */
    boxc(2.7f, 0, 11.9f, 0.4f, 0.55f, 0.4f, C_WOOD);
    add_collider(2.5f, 11.7f, 2.9f, 12.1f, false);
    if (lay_has("decor:rug")) box(2.9f, 0.003f, 12.6f, 7.1f, 0.012f, 14.8f, rgb(170, 70, 60));
    if (lay_has("decor:lamps")) floor_lamp(2.7f, 12.6f);
    if (lay_has("decor:bookshelf")) {
        box(HT, 0, 11.8f, 0.55f, 2.0f, 13.0f, C_DARKWOOD);
        Col books[5] = {rgb(170, 60, 60), rgb(60, 110, 170), rgb(220, 180, 70), rgb(70, 140, 90), rgb(120, 80, 150)};
        for (int sh = 0; sh < 4; sh++) {
            float y = 0.12f + sh * 0.47f;
            box(0.12f, y - 0.03f, 11.85f, 0.56f, y, 12.95f, shade(C_DARKWOOD, 1.2f));
            float z = 11.9f;
            for (int b = 0; z < 12.88f; b++) {
                float w = 0.05f + ((b * 37 + sh * 11) % 5) * 0.012f, hb = 0.28f + ((b * 13 + sh) % 4) * 0.035f;
                box(0.2f, y, z, 0.52f, y + hb, z + w, books[(b + sh) % 5]);
                z += w + 0.008f;
            }
        }
        add_collider(HT, 11.8f, 0.6f, 13.0f, false);
    }
    if (lay_has("decor:fireplace")) {
        box(HT, 0, 9.4f, 0.7f, 1.2f, 11.4f, rgb(150, 140, 130));
        box(HT, 1.2f, 9.3f, 0.8f, 1.3f, 11.5f, rgb(110, 100, 94));
        box(0.55f, 0.15f, 9.9f, 0.72f, 0.85f, 10.9f, rgb(30, 22, 20));
        add_collider(HT, 9.3f, 0.8f, 11.5f, false);
    }
    if (lay_has("decor:aquarium")) {
        box(HT, 0, 13.4f, 0.7f, 0.7f, 15.1f, C_DARKWOOD);
        box(HT, 1.32f, 13.4f, 0.7f, 1.38f, 15.1f, C_BLACK);
        add_collider(HT, 13.4f, 0.7f, 15.1f, false);
    }
    if (lay_has("decor:arcade")) {
        box(9.05f, 0, 15.1f, 9.8f, 1.85f, 15.85f, rgb(40, 30, 80));
        box(9.0f, 0.95f, 15.0f, 9.85f, 1.05f, 15.25f, rgb(30, 30, 36));
        add_collider(9.0f, 15.0f, 9.85f, 15.9f, false);
    }
    if (lay_has("decor:piano")) {
        box(9.35f, 0, 9.6f, 9.9f, 1.3f, 11.2f, rgb(24, 22, 24));
        box(9.0f, 0.72f, 9.6f, 9.4f, 0.8f, 11.2f, rgb(24, 22, 24));
        box(9.02f, 0.8f, 9.65f, 9.3f, 0.83f, 11.15f, rgb(240, 240, 236));
        for (float z = 9.7f; z < 11.1f; z += 0.09f) box(9.12f, 0.83f, z, 9.3f, 0.86f, z + 0.04f, rgb(20, 20, 20));
        boxc(8.55f, 0, 10.4f, 0.4f, 0.48f, 0.9f, rgb(24, 22, 24));
        add_collider(9.0f, 9.6f, 9.9f, 11.2f, false);
    }
    if (lay_has("decor:plants")) { plant(7.4f, 15.5f, 1.2f); plant(0.6f, 9.6f, 1.0f); }
    add_inter(I_TV, 5, 1.2f, 15.6f, 3.2f, 0, "Choose a video");
    for (int i = 0; i < 3; i++) {
        W.couch_seats[i] = add_spot(SPOT_RELAX, R_LIVING, 4.25f + i * 0.85f, 0.5f, 12.05f, 0, 1, -1);
        approach(W.couch_seats[i], 7.6f, 12.85f);
        add_inter(I_COUCH, 4.25f + i * 0.85f, 0.6f, 12.0f, 1.7f, W.couch_seats[i], "Sit on the couch");
    }
    int s = add_spot(SPOT_RELAX, R_LIVING, 9.05f, 0.47f, 12.65f, atan2f(5 - 9.05f, 15.7f - 12.65f), 1, -1);
    approach(s, 8.0f, 12.0f);
    add_spot(SPOT_STAND, R_LIVING, 8.0f, 0, 14.6f, 0, 0, -1);
    if (lay_has("decor:arcade")) add_inter(I_ARCADE, 9.4f, 1.3f, 15.2f, 1.8f, 0, "Play the arcade");
    if (lay_has("decor:piano")) add_inter(I_PIANO, 9.2f, 0.9f, 10.4f, 1.8f, 0, "Play the piano");
}

static void master_bedroom(void)
{
    Col frame = C_DARKWOOD, sheet = rgb(240, 238, 232), blanket = rgb(70, 90, 140);
    box(0.6f, 0, 0.1f + HT, 2.6f, 1.15f, 0.26f + HT, frame);
    box(0.6f, 0.1f, 0.1f + HT, 2.6f, 0.42f, 2.4f, frame);
    box(0.65f, 0.42f, 0.2f + HT, 2.55f, 0.6f, 2.35f, sheet);
    box(0.8f, 0.6f, 0.32f + HT, 1.55f, 0.72f, 0.7f + HT, rgb(250, 250, 250));
    box(1.65f, 0.6f, 0.32f + HT, 2.4f, 0.72f, 0.7f + HT, rgb(250, 250, 250));
    box(0.62f, 0.55f, 1.0f, 2.58f, 0.66f, 2.38f, blanket);
    add_collider(0.6f, 0.1f, 2.6f, 2.4f, false);
    W.master_bed_lie = v3(1.6f, 0.9f, 1.5f);
    add_inter(I_BED, 1.6f, 0.6f, 1.6f, 2.2f, 0, "Lie down");
    /* nightstand + lamp */
    box(2.75f, 0, 0.15f + HT, 3.25f, 0.55f, 0.6f + HT, C_WOOD);
    prism(3.0f, 0.55f, 0.45f, 0.05f, 0.05f, 0.25f, 6, C_METAL);
    prism(3.0f, 0.75f, 0.45f, 0.14f, 0.09f, 0.18f, 8, rgb(240, 226, 190));
    /* wardrobe */
    box(HT, 0, 3.4f, 0.75f, 2.1f, 5.6f, C_WOOD);
    box(0.75f, 1.0f, 4.45f, 0.78f, 1.2f, 4.55f, C_METAL);
    add_collider(HT, 3.4f, 0.75f, 5.6f, false);
    /* rug */
    box(3.2f, 0.003f, 2.6f, 6.4f, 0.012f, 5.0f, rgb(200, 190, 170));
    /* the PC desk: one monitor, the user's real computer */
    bool gaming = lay_has("decor:gaming");
    Col desk = gaming ? rgb(30, 30, 36) : C_WOOD;
    box(5.0f, 0.72f, HT, 7.6f, 0.78f, 0.85f, desk);
    for (int k = 0; k < 4; k++) boxc(k & 1 ? 7.5f : 5.1f, 0, k & 2 ? 0.78f : 0.2f, 0.06f, 0.72f, 0.06f, shade(desk, 0.8f));
    boxc(6.3f, 0.78f, 0.33f, 0.1f, 0.22f, 0.06f, C_BLACK);
    boxc(6.3f, 0.78f, 0.33f, 0.36f, 0.02f, 0.2f, C_BLACK);
    box(5.75f, 1.0f, 0.28f, 6.85f, 1.62f, 0.35f, C_BLACK);
    W.pc_screen = v3(6.3f, 1.31f, 0.356f);
    box(5.95f, 0.78f, 0.55f, 6.65f, 0.8f, 0.7f, rgb(50, 50, 56));
    box(6.8f, 0.78f, 0.58f, 6.9f, 0.81f, 0.7f, rgb(50, 50, 56));
    box(7.05f, 0, 0.2f, 7.4f, 0.5f, 0.75f, rgb(36, 36, 42));
    add_collider(5.0f, HT, 7.6f, 0.85f, false);
    /* desk chair */
    Col cc = gaming ? rgb(190, 40, 50) : rgb(50, 54, 64);
    boxc(6.3f, 0.44f, 1.3f, 0.5f, 0.07f, 0.5f, cc);
    boxc(6.3f, 0.5f, 1.55f, 0.5f, gaming ? 0.85f : 0.55f, 0.06f, cc);
    boxc(6.3f, 0, 1.3f, 0.06f, 0.44f, 0.06f, C_METAL);
    boxc(6.3f, 0, 1.3f, 0.55f, 0.04f, 0.08f, C_METAL);
    boxc(6.3f, 0, 1.3f, 0.08f, 0.04f, 0.55f, C_METAL);
    W.pc_view_eye = v3(6.3f, 1.31f, 0.98f);
    W.pc_stand = v3(6.3f, 1.62f, 2.1f);
    add_inter(I_PC, 6.3f, 1.2f, 0.6f, 2.0f, 0, "Use the computer");
    if (lay_has("decor:lamps")) floor_lamp(3.5f, 2.9f);
    if (lay_has("decor:plants")) plant(7.5f, 6.4f, 1.0f);
}

static void bathroom(void)
{
    Col white = rgb(244, 244, 246);
    box(8 + HT, 0, HT, 11.5f - HT, 0.55f, 1.0f, white);
    box(8.25f, 0.4f, 0.25f, 11.25f, 0.5f, 0.85f, rgb(110, 170, 210));
    add_collider(8, 0, 11.5f, 1.0f, false);
    /* toilet */
    boxc(11.0f, 0, 3.0f, 0.42f, 0.4f, 0.55f, white);
    box(11.22f, 0.3f, 2.78f, 11.4f, 0.85f, 3.22f, white);
    add_collider(10.75f, 2.7f, 11.4f, 3.3f, false);
    /* sink + mirror */
    box(8 + HT, 0.78f, 3.6f, 8.62f, 0.88f, 4.3f, white);
    box(8 + HT, 0, 3.85f, 8.4f, 0.78f, 4.05f, white);
    box(8.2f, 0.88f, 3.9f, 8.3f, 1.0f, 3.98f, C_METAL);
    add_collider(8, 3.6f, 8.62f, 4.3f, false);
    add_spot(SPOT_BATH, R_BATH, 8.95f, 0, 3.95f, -PI_F / 2, 0, -1);
    /* towel rail */
    box(10.6f, 1.0f, 7 - HT - 0.06f, 11.2f, 1.04f, 7 - HT - 0.02f, C_METAL);
    box(10.65f, 0.6f, 7 - HT - 0.08f, 11.15f, 1.0f, 7 - HT - 0.05f, rgb(90, 160, 150));
    add_inter(I_SINK, 8.6f, 0.9f, 3.95f, 1.6f, 0, "Wash your hands");
}

static void ai_room(int room)
{
    int first = room == R_AI1 ? 0 : 3;
    for (int i = first; i < first + 3; i++) {
        bunk(i);
        float cx = BUNK_X[i];
        int lo = add_spot(SPOT_BED, room, cx, 0.62f, 1.85f + HT, 0, 2, i * 2);
        approach(lo, cx + 0.75f, 2.9f);
        int hi = add_spot(SPOT_BED, room, cx, 1.55f, 1.85f + HT, 0, 2, i * 2 + 1);
        approach(hi, cx + 0.75f, 2.9f);
        /* small locker at the foot */
        box(cx - 0.3f, 0, 2.3f, cx + 0.3f, 0.5f, 2.6f, rgb(110, 120, 140));
        add_collider(cx - 0.3f, 2.3f, cx + 0.3f, 2.6f, false);
    }
    Rect q = ROOM_RECT[room];
    box(q.x0 + 1.0f, 0.003f, 3.4f, q.x1 - 1.0f, 0.012f, 5.6f, rgb(90, 100, 130));
    add_spot(SPOT_STAND, room, (q.x0 + q.x1) / 2 - 1, 0, 4.6f, PI_F, 0, -1);
    add_spot(SPOT_STAND, room, (q.x0 + q.x1) / 2 + 1.2f, 0, 5.4f, -PI_F / 2, 0, -1);
}

static void kitchen(void)
{
    Col cab = rgb(236, 234, 228), top = rgb(70, 72, 78);
    box(10 + HT, 0, 15.25f, 15.5f - HT, 0.86f, 16 - HT, cab);
    box(10 + HT, 0.86f, 15.2f, 15.5f - HT, 0.92f, 16 - HT, top);
    box(10 + HT, 0, 15.2f, 15.5f - HT, 0.1f, 15.3f, shade(cab, 0.5f));
    for (float x = 10.6f; x < 15.2f; x += 0.6f) box(x - 0.01f, 0.15f, 15.23f, x + 0.01f, 0.8f, 15.26f, shade(cab, 0.7f));
    box(10 + HT, 1.5f, 15.55f, 13.3f, 2.2f, 16 - HT, cab);
    /* stove (burners are dynamic) and sink */
    box(12.2f, 0.92f, 15.3f, 12.9f, 0.93f, 15.85f, rgb(20, 20, 22));
    box(13.6f, 0.9f, 15.35f, 14.4f, 0.93f, 15.8f, C_METAL);
    box(13.95f, 0.93f, 15.75f, 14.05f, 1.2f, 15.8f, C_METAL);
    add_collider(10, 15.2f, 15.5f, 16, false);
    /* fridge */
    box(14.65f, 0, 14.3f, 15.5f - HT, 2.0f, 15.2f, rgb(200, 204, 210));
    box(14.6f, 1.1f, 14.4f, 14.65f, 1.6f, 14.45f, C_METAL);
    box(14.62f, 1.25f, 14.3f, 14.65f, 1.27f, 15.2f, shade(C_METAL, 0.7f));
    add_collider(14.65f, 14.3f, 15.5f, 15.2f, false);
    add_inter(I_FRIDGE, 14.6f, 1.2f, 14.75f, 1.6f, 0, "Open the fridge");
    add_inter(I_SINK, 14.0f, 0.95f, 15.5f, 1.6f, 1, "Do the dishes");
    /* dining table + six chairs */
    box(11.8f, 0.72f, 11.8f, 13.4f, 0.77f, 13.0f, C_WOOD);
    for (int k = 0; k < 4; k++) boxc(k & 1 ? 13.3f : 11.9f, 0, k & 2 ? 12.9f : 11.9f, 0.06f, 0.72f, 0.06f, C_WOOD);
    add_collider(11.8f, 11.8f, 13.4f, 13.0f, false);
    struct { float x, z, yaw; } ch[6] = {{12.2f, 11.4f, 0}, {13.0f, 11.4f, 0}, {12.2f, 13.4f, PI_F}, {13.0f, 13.4f, PI_F},
                                         {11.4f, 12.4f, PI_F / 2}, {13.8f, 12.4f, -PI_F / 2}};
    for (int i = 0; i < 6; i++) {
        chair(ch[i].x, ch[i].z, ch[i].yaw, C_WOOD);
        int s = add_spot(SPOT_EAT, R_KITCHEN, ch[i].x, 0.47f, ch[i].z, ch[i].yaw, 1, -1);
        if (i == 2 || i == 3) approach(s, 11.1f, 13.6f);
        if (i == 5) approach(s, 14.2f, 11.2f);
    }
    /* fruit bowl */
    prism(12.6f, 0.77f, 12.4f, 0.12f, 0.18f, 0.08f, 8, rgb(230, 230, 230));
    ball(12.55f, 0.88f, 12.38f, 0.06f, rgb(220, 60, 50));
    ball(12.66f, 0.88f, 12.44f, 0.06f, rgb(240, 200, 60));
    int s = add_spot(SPOT_KITCHEN, R_KITCHEN, 11.2f, 0, 14.7f, 0, 0, -1);
    approach(s, 11.1f, 13.6f);
    s = add_spot(SPOT_KITCHEN, R_KITCHEN, 13.0f, 0, 14.7f, 0, 0, -1);
    approach(s, 11.1f, 13.6f);
    s = add_spot(SPOT_KITCHEN, R_KITCHEN, 14.1f, 0, 14.75f, PI_F / 2, 0, -1);
    approach(s, 14.2f, 11.2f);
    if (lay_has("decor:coffee")) {
        box(10.45f, 0.92f, 15.45f, 10.85f, 1.35f, 15.85f, rgb(40, 40, 44));
        box(10.55f, 0.92f, 15.35f, 10.75f, 0.95f, 15.5f, C_METAL);
    }
}

static void work_room(void)
{
    for (int i = 0; i < 3; i++) island(i);
    for (int i = 0; i < 3; i++)
        for (int s = 0; s < 4; s++) {
            float sx = ISLAND_X[i] + (s & 1 ? 0.45f : -0.45f);
            int north = !(s & 2);
            int spot = add_spot(SPOT_DESK, R_WORK, sx, 0.5f, north ? ISLAND_Z - 1.05f : ISLAND_Z + 1.05f,
                                north ? 0 : PI_F, 1, i * 4 + s);
            if (north) approach(spot, sx, 10.3f);
            else { approach(spot, 16.0f, 13.8f); approach(spot, sx, 13.8f); }
        }
    /* whiteboard */
    box(18.0f, 0.9f, 16 - HT - 0.05f, 22.0f, 2.1f, 16 - HT, rgb(248, 248, 250));
    box(17.95f, 0.85f, 16 - HT - 0.08f, 22.05f, 0.9f, 16 - HT, C_METAL);
    W.whiteboard = v3(21.9f, 2.02f, 16 - HT - 0.055f);
    add_inter(I_WHITEBOARD, 20.0f, 1.5f, 15.6f, 2.4f, 0, "Read the whiteboard");
    int s = add_spot(SPOT_STAND, R_WORK, 19.0f, 0, 14.9f, 0, 0, -1);
    approach(s, 16.0f, 13.8f);
    s = add_spot(SPOT_STAND, R_WORK, 21.0f, 0, 14.9f, 0, 0, -1);
    approach(s, 16.0f, 13.8f);
    /* server rack in the corner */
    box(23.2f, 0, 9 + HT, 24 - HT, 2.0f, 10.1f, rgb(30, 32, 40));
    add_collider(23.2f, 9, 24, 10.1f, false);
    if (lay_has("decor:plants")) plant(23.5f, 15.5f, 1.1f);
}

static void hallway(void)
{
    box(0.6f, 0.003f, 7.45f, 23.4f, 0.012f, 8.55f, rgb(120, 60, 60));
    /* the shop kiosk at the west end */
    box(HT, 0, 7.5f, 0.55f, 1.1f, 8.5f, rgb(40, 44, 60));
    box(HT, 1.1f, 7.6f, 0.4f, 1.9f, 8.4f, rgb(30, 32, 44));
    add_collider(0, 7.5f, 0.55f, 8.5f, false);
    add_inter(I_SHOP, 0.6f, 1.3f, 8.0f, 1.9f, 0, "Open the shop");
    float xs[4] = {4, 11, 16, 21};
    for (int i = 0; i < 4; i++) add_spot(SPOT_STAND, R_HALL, xs[i], 0, i % 2 ? 7.6f : 8.4f, i % 2 ? PI_F : 0, 0, -1);
    if (lay_has("decor:plants")) plant(23.5f, 8.0f, 1.0f);
    if (lay_has("decor:posters")) {
        Col art[4] = {rgb(230, 120, 80), rgb(80, 150, 200), rgb(240, 200, 80), rgb(140, 90, 180)};
        for (int i = 0; i < 4; i++) {
            float x = 3.0f + i * 5.6f;
            box(x, 1.2f, 9 - HT - 0.03f, x + 0.9f, 2.1f, 9 - HT, rgb(30, 30, 30));
            box(x + 0.06f, 1.26f, 9 - HT - 0.035f, x + 0.84f, 2.04f, 9 - HT - 0.03f, art[i]);
            box(x + 0.2f, 1.45f, 9 - HT - 0.04f, x + 0.6f, 1.75f, 9 - HT - 0.035f, shade(art[(i + 1) % 4], 1.1f));
        }
        box(7.9f - 0.03f, 1.2f, 2.0f, 7.9f, 2.0f, 3.2f, rgb(30, 30, 30));
        box(7.9f - 0.035f, 1.26f, 2.06f, 7.9f - 0.03f, 1.94f, 3.14f, rgb(60, 160, 140));
    }
}

/* ------------------------------------------------------------------ build */

void world_build(void)
{
    C_TRIM = rgb(246, 244, 238); C_WOOD = rgb(176, 134, 92); C_DARKWOOD = rgb(96, 66, 44);
    C_METAL = rgb(150, 154, 160); C_WHITE = rgb(246, 246, 246); C_BLACK = rgb(22, 22, 26); C_CEIL = rgb(236, 234, 230);
    W.ncolliders = W.nwalls = W.nspots = W.ninters = 0;
    if (W.list) glDeleteLists(W.list, 1);
    W.list = glGenLists(1);
    glNewList(W.list, GL_COMPILE);
    for (int r = 0; r < NROOMS; r++) floor_room(r);
    ceiling();
    for (int r = 0; r < NROOMS; r++) {
        Col paint = item_col(SHOP.lay.wall[r], rgb(230, 224, 210));
        if (r == R_HALL) {
            /* the hallway's long walls are the rooms' hallway sides */
            room_side(r, 0, paint); room_side(r, 1, paint); room_side(r, 2, paint); room_side(r, 3, paint);
            continue;
        }
        for (int s = 0; s < 4; s++) room_side(r, s, paint);
    }
    door_frames();
    window_frames();
    wall_colliders();
    living_room();
    master_bedroom();
    bathroom();
    ai_room(R_AI1);
    ai_room(R_AI2);
    kitchen();
    work_room();
    hallway();
    glEndList();
    W.spawn = v3(5.0f, 0, 8.0f);
    for (int i = 0; i < W.nspots; i++) W.spots[i].occupant = -1;
}

/* ------------------------------------------------------------------ dynamic */

extern int npc_desk_working(int desk_owner);   /* npc.c */
extern const char *npc_bed_name(int bed_owner);
extern void draw_tv_screen(V3 c, float w, float h);   /* main.c */
extern void draw_pc_screen(V3 c, float w, float h);
extern void draw_whiteboard(V3 o);
extern int g_points_balance;

static void windows(Col sky)
{
    for (int i = 0; i < NWINS; i++) {
        const Win *w = &WINS[i];
        Col top = shade(sky, 0.85f), c = sky;
        switch (w->side) {
        case 0: glow_quad(v3(w->a, w->y1, HT + 0.01f), v3(w->b, w->y1, HT + 0.01f), v3(w->b, w->y0, HT + 0.01f), v3(w->a, w->y0, HT + 0.01f), c); break;
        case 1: glow_quad(v3(w->a, w->y1, 16 - HT - 0.01f), v3(w->b, w->y1, 16 - HT - 0.01f), v3(w->b, w->y0, 16 - HT - 0.01f), v3(w->a, w->y0, 16 - HT - 0.01f), c); break;
        case 2: glow_quad(v3(HT + 0.01f, w->y1, w->a), v3(HT + 0.01f, w->y1, w->b), v3(HT + 0.01f, w->y0, w->b), v3(HT + 0.01f, w->y0, w->a), c); break;
        default: glow_quad(v3(24 - HT - 0.01f, w->y1, w->a), v3(24 - HT - 0.01f, w->y1, w->b), v3(24 - HT - 0.01f, w->y0, w->b), v3(24 - HT - 0.01f, w->y0, w->a), c);
        }
        (void)top;
    }
    if (lay_has("extra:skylight")) {
        float sx[3] = {5, 12.6f, 20};
        for (int i = 0; i < 3; i++)
            glow_quad(v3(sx[i] - 1, H - 0.01f, 12), v3(sx[i] + 1, H - 0.01f, 12), v3(sx[i] + 1, H - 0.01f, 13.4f),
                      v3(sx[i] - 1, H - 0.01f, 13.4f), sky);
    }
}

static void desk_screens(float t)
{
    for (int i = 0; i < 3; i++)
        for (int s = 0; s < 4; s++) {
            float sx = ISLAND_X[i] + (s & 1 ? 0.45f : -0.45f);
            int north = !(s & 2);
            float z = north ? ISLAND_Z - 0.25f - 0.03f : ISLAND_Z - 0.25f + 0.53f;
            int working = npc_desk_working(i * 4 + s);
            Col c = working == 2 ? rgb(30, 60, 40) : working == 1 ? rgb(60, 50, 20) : rgb(16, 18, 24);
            V3 a = v3(sx - 0.28f, 1.28f, z), b = v3(sx + 0.28f, 1.28f, z), cc = v3(sx + 0.28f, 0.97f, z), d = v3(sx - 0.28f, 0.97f, z);
            if (!north) { a.x = sx + 0.28f; b.x = sx - 0.28f; cc.x = sx - 0.28f; d.x = sx + 0.28f; }
            glow_quad(a, b, cc, d, c);
            if (working) {
                /* scrolling "code" lines */
                for (int l = 0; l < 6; l++) {
                    float y = 1.25f - l * 0.045f;
                    float len = 0.12f + 0.3f * fabsf(sinf(l * 1.7f + floorf(t * 3) * 0.9f + i + s));
                    float x0 = north ? sx - 0.25f : sx + 0.25f, x1 = north ? x0 + len : x0 - len;
                    float zz = north ? z + 0.002f : z - 0.002f;
                    Col lc = working == 2 ? rgb(120, 240, 150) : rgb(250, 210, 90);
                    glow_quad(v3(x0, y, zz), v3(x1, y, zz), v3(x1, y - 0.018f, zz), v3(x0, y - 0.018f, zz), lc);
                }
            }
        }
}

static void clock_and_kiosk(void)
{
    time_t now = time(NULL);
    struct tm lt;
    localtime_r(&now, &lt);
    char buf[64];
    strftime(buf, sizeof buf, "%H:%M", &lt);
    /* hallway clock on the south hallway wall, readable from the hall */
    float z = 9 - HT - 0.02f;
    glow_quad(v3(13.4f, 2.25f, z), v3(14.6f, 2.25f, z), v3(14.6f, 1.75f, z), v3(13.4f, 1.75f, z), rgb(20, 22, 30));
    text3d(F_HUGE, v3(14.45f, 2.22f, z - 0.005f), v3(-1, 0, 0), v3(0, -1, 0), 0.4f / text_h(F_HUGE), buf, rgb(120, 220, 255));
    /* kiosk screen faces east */
    float x = 0.41f;
    glow_quad(v3(x, 1.85f, 7.65f), v3(x, 1.85f, 8.35f), v3(x, 1.15f, 8.35f), v3(x, 1.15f, 7.65f), rgb(30, 40, 90));
    text3d(F_TITLE, v3(x + 0.005f, 1.8f, 8.3f), v3(0, 0, -1), v3(0, -1, 0), 0.12f / text_h(F_TITLE), "SHOP", rgb(255, 220, 120));
    snprintf(buf, sizeof buf, "%d pts", g_points_balance);
    text3d(F_TITLE, v3(x + 0.005f, 1.55f, 8.3f), v3(0, 0, -1), v3(0, -1, 0), 0.1f / text_h(F_TITLE), buf, rgb(255, 255, 255));
}

static void bed_plates(void)
{
    for (int i = 0; i < 6; i++)
        for (int lvl = 0; lvl < 2; lvl++) {
            const char *name = npc_bed_name(i * 2 + lvl);
            if (!name) continue;
            float cx = BUNK_X[i], y = lvl ? 1.68f : 0.75f, z = 2.1f + HT + 0.01f;
            glow_quad(v3(cx - 0.3f, y + 0.12f, z), v3(cx + 0.3f, y + 0.12f, z), v3(cx + 0.3f, y, z), v3(cx - 0.3f, y, z), rgb(230, 230, 220));
            float px = 0.1f / text_h(F_BODY);
            float w = text_w(F_BODY, name) * px;
            if (w > 0.56f) { px *= 0.56f / w; w = 0.56f; }
            text3d(F_BODY, v3(cx - w / 2, y + 0.11f, z + 0.004f), v3(1, 0, 0), v3(0, -1, 0), px, name, rgb(30, 30, 40));
        }
}

void world_draw_dynamic(float t, float sky_k)
{
    (void)sky_k;
    Col sky = sky_color(NULL);
    windows(sky);
    draw_tv_screen(W.tv_center, W.tv_w, W.tv_h);
    draw_pc_screen(W.pc_screen, 1.04f, 0.56f);
    draw_whiteboard(W.whiteboard);
    desk_screens(t);
    clock_and_kiosk();
    bed_plates();
    bool night = NOW_HOURS >= 19 || NOW_HOURS < 7;
    /* stove at meal times */
    float h = NOW_HOURS;
    if ((h > 7.3f && h < 8.4f) || (h > 12.1f && h < 13.2f) || (h > 18.3f && h < 19.4f)) {
        for (int b = 0; b < 2; b++) {
            float bx = 12.38f + b * 0.34f;
            Col c = rgb(255, 90 + (int)(40 * sinf(t * 7 + b)), 30);
            glow_quad(v3(bx - 0.1f, 0.935f, 15.45f), v3(bx + 0.1f, 0.935f, 15.45f), v3(bx + 0.1f, 0.935f, 15.65f), v3(bx - 0.1f, 0.935f, 15.65f), c);
        }
    }
    if (lay_has("decor:lamps") && night) {
        glow_quad(v3(2.5f, 1.72f, 12.4f), v3(2.9f, 1.72f, 12.4f), v3(2.9f, 1.72f, 12.8f), v3(2.5f, 1.72f, 12.8f), rgb(255, 230, 160));
    }
    if (lay_has("decor:aquarium")) {
        glow_quad(v3(0.71f, 1.32f, 13.42f), v3(0.71f, 1.32f, 15.08f), v3(0.71f, 0.72f, 15.08f), v3(0.71f, 0.72f, 13.42f), rgba(60, 140, 200, 200));
        for (int f = 0; f < 5; f++) {
            float fz = 14.25f + sinf(t * (0.4f + f * 0.13f) + f * 1.3f) * 0.7f;
            float fy = 0.85f + f * 0.09f + sinf(t * 1.3f + f) * 0.03f;
            Col fc = f % 2 ? rgb(255, 140, 40) : rgb(250, 220, 60);
            glow_quad(v3(0.72f, fy + 0.03f, fz - 0.06f), v3(0.72f, fy + 0.03f, fz + 0.06f), v3(0.72f, fy - 0.03f, fz + 0.06f), v3(0.72f, fy - 0.03f, fz - 0.06f), fc);
        }
    }
    if (lay_has("decor:fireplace")) {
        for (int f = 0; f < 4; f++) {
            float z = 10.0f + f * 0.22f, hgt = 0.3f + 0.2f * fabsf(sinf(t * 5 + f * 2.1f));
            Col c = f % 2 ? rgb(255, 150, 40) : rgb(255, 90, 20);
            glow_quad(v3(0.71f, 0.2f + hgt, z), v3(0.71f, 0.2f + hgt, z + 0.2f), v3(0.71f, 0.2f, z + 0.2f), v3(0.71f, 0.2f, z), c);
        }
    }
    if (lay_has("decor:arcade")) {
        Col c = rgb(40 + (int)(60 * fabsf(sinf(t * 2))), 20, 120 + (int)(100 * fabsf(sinf(t * 1.3f))));
        glow_quad(v3(9.15f, 1.7f, 15.09f), v3(9.7f, 1.7f, 15.09f), v3(9.7f, 1.15f, 15.09f), v3(9.15f, 1.15f, 15.09f), c);
        text3d(F_SMALL, v3(9.67f, 1.82f, 15.08f), v3(-1, 0, 0), v3(0, -1, 0), 0.08f / text_h(F_SMALL), "PIXEL RUN", rgb(255, 240, 120));
    }
    if (lay_has("decor:neon")) {
        float flick = fmodf(t, 7.0f) < 0.12f ? 0.4f : 1.0f;
        text3d(F_HUGE, v3(10 - HT - 0.02f, 2.2f, 11.9f), v3(0, 0, 1), v3(0, -1, 0), 0.45f / text_h(F_HUGE), "LIFE OS",
               rgb((int)(255 * flick), (int)(60 * flick), (int)(200 * flick)));
    }
    if (lay_has("decor:stringlights")) {
        for (int r = 0; r < 2; r++) {
            float x0 = r ? 17.7f : 11.7f, x1 = r ? 23.8f : 17.3f;
            for (float x = x0; x < x1; x += 0.3f) {
                float y = 2.45f - 0.08f * fabsf(sinf((x - x0) * 2.2f));
                int k = (int)(x * 10 + t * 2) % 3;
                Col c = k == 0 ? rgb(255, 210, 120) : k == 1 ? rgb(255, 140, 200) : rgb(140, 220, 255);
                glow_quad(v3(x - 0.03f, y + 0.03f, 0.2f), v3(x + 0.03f, y + 0.03f, 0.2f), v3(x + 0.03f, y - 0.03f, 0.2f), v3(x - 0.03f, y - 0.03f, 0.2f), c);
            }
        }
    }
    if (lay_has("decor:discoball")) {
        glPushMatrix();
        glTranslatef(19.75f, 2.2f, 12.5f);
        glRotatef(t * 40, 0, 1, 0);
        ball(0, 0, 0, 0.22f, rgb(220, 220, 230));
        glPopMatrix();
        box(19.74f, 2.4f, 12.49f, 19.76f, 2.7f, 12.51f, rgb(60, 60, 60));
        for (int s = 0; s < 6; s++) {
            float a = t * 0.7f + s * 1.05f;
            float x = 19.75f + cosf(a) * (1.5f + s * 0.3f), z = 12.5f + sinf(a) * (1.2f + s * 0.25f);
            Col c = s % 3 == 0 ? rgba(255, 80, 80, 140) : s % 3 == 1 ? rgba(80, 255, 120, 140) : rgba(90, 120, 255, 140);
            glow_quad(v3(x - 0.15f, 0.02f, z - 0.15f), v3(x + 0.15f, 0.02f, z - 0.15f), v3(x + 0.15f, 0.02f, z + 0.15f), v3(x - 0.15f, 0.02f, z + 0.15f), c);
        }
    }
    if (lay_has("decor:gaming")) {
        Col c = rgb((int)(127 + 127 * sinf(t)), (int)(127 + 127 * sinf(t + 2.1f)), (int)(127 + 127 * sinf(t + 4.2f)));
        glow_quad(v3(5.0f, 0.72f, 0.86f), v3(7.6f, 0.72f, 0.86f), v3(7.6f, 0.69f, 0.86f), v3(5.0f, 0.69f, 0.86f), c);
    }
    /* the PC tower's power light */
    glow_quad(v3(7.06f, 0.45f, 0.76f), v3(7.12f, 0.45f, 0.76f), v3(7.12f, 0.42f, 0.76f), v3(7.06f, 0.42f, 0.76f), rgb(80, 200, 255));
}
