/* The crew as residents: simple low-poly bodies with a daily routine driven by the real clock and by what
 * LifeOS says they are doing. Given a task, an agent walks to its desk in the work room; at night it sleeps
 * in its bunk; at meal times it eats in the kitchen; otherwise it relaxes, wanders, or hangs out in its room. */
#include "app.h"
#include <stdlib.h>
#include <time.h>

Agent AG[MAX_AGENTS];
int NAG;

extern int spot_approach(int spot, float *xs, float *zs);   /* world.c */

/* a cat, if the user bought one */
static struct { float x, z, yaw, tx, tz, wait, phase; bool init; } CAT;

static const struct { const char *name; Col c; } COLORS[] = {
    {"violet", {139, 92, 246, 255}}, {"amber", {245, 158, 11, 255}}, {"cyan", {6, 182, 212, 255}},
    {"emerald", {16, 185, 129, 255}}, {"rose", {244, 63, 94, 255}}, {"blue", {59, 130, 246, 255}},
    {"orange", {249, 115, 22, 255}}, {"pink", {236, 72, 153, 255}}, {"lime", {132, 204, 22, 255}},
    {"teal", {20, 184, 166, 255}}, {"slate", {100, 116, 139, 255}}, {"red", {239, 68, 68, 255}},
    {"green", {34, 197, 94, 255}}, {"yellow", {234, 179, 8, 255}}, {"indigo", {99, 102, 241, 255}},
    {"purple", {168, 85, 247, 255}}, {"sky", {14, 165, 233, 255}}, {"fuchsia", {217, 70, 239, 255}},
    {"gray", {107, 114, 128, 255}}, {"zinc", {113, 113, 122, 255}}, {"stone", {120, 113, 108, 255}},
};

static Col color_named(const char *n, const char *slug)
{
    for (size_t i = 0; i < sizeof COLORS / sizeof COLORS[0]; i++) if (n && !strcmp(n, COLORS[i].name)) return COLORS[i].c;
    uint32_t h = hash_str(slug);
    return rgb(80 + (int)(h % 160), 80 + (int)((h >> 8) % 160), 80 + (int)((h >> 16) % 160));
}

Col crew_color(const char *name) { return color_named(name, name ? name : ""); }

static float rnd(void) { return (float)rand() / (float)RAND_MAX; }

/* ------------------------------------------------------------------ roster */

static Agent *find(const char *slug)
{
    for (int i = 0; i < NAG; i++) if (!strcmp(AG[i].slug, slug)) return &AG[i];
    return NULL;
}

static void release_spot(Agent *a)
{
    if (a->spot >= 0 && a->spot < W.nspots && W.spots[a->spot].occupant == (int)(a - AG)) W.spots[a->spot].occupant = -1;
}

static void add_agent(const char *slug, const char *name, const char *role, const char *color, bool enabled)
{
    if (NAG >= MAX_AGENTS) return;
    Agent *a = &AG[NAG++];
    memset(a, 0, sizeof *a);
    snprintf(a->slug, sizeof a->slug, "%s", slug);
    snprintf(a->name, sizeof a->name, "%s", name);
    snprintf(a->role, sizeof a->role, "%s", role);
    snprintf(a->state, sizeof a->state, "%s", enabled ? "idle" : "offline");
    a->color = color_named(color, slug);
    a->enabled = enabled;
    a->spot = -1;
    a->act = -1;
    a->x = 12; a->z = 8;
    a->leisure_timer = 0;
    a->present = true;
}

void npc_default_roster(void)
{
    static const char *d[][4] = {
        {"overseer", "Atlas", "Overseer", "violet"}, {"planner", "Chrono", "Planner", "amber"},
        {"files", "Archivist", "File manager", "cyan"}, {"research", "Scout", "Researcher", "emerald"},
        {"coder", "Forge", "Coder", "rose"}, {"memory", "Mnemo", "Memory keeper", "blue"},
        {"comms", "Herald", "Communications", "orange"}, {"creative", "Muse", "Creative director", "pink"},
        {"photo", "Lumen", "Photographer", "lime"}, {"video", "Reel", "Video maker", "teal"},
        {"quick", "Spark", "Quick helper", "slate"},
    };
    NAG = 0;
    for (size_t i = 0; i < sizeof d / sizeof d[0]; i++) add_agent(d[i][0], d[i][1], d[i][2], d[i][3], true);
}

void npc_set_roster(J *agents)
{
    if (!agents || agents->t != J_ARR) return;
    bool seen[MAX_AGENTS] = {0};
    bool changed = false;
    for (int i = 0; i < agents->count; i++) {
        J *j = jat(agents, i);
        const char *slug = jstr(j, "slug", NULL);
        if (!slug) continue;
        Agent *a = find(slug);
        if (!a) {
            add_agent(slug, jstr(j, "name", slug), jstr(j, "role", ""), jstr(j, "color", ""), jbool(j, "enabled", true));
            a = find(slug);
            if (!a) continue;
            changed = true;
        }
        seen[a - AG] = true;
        snprintf(a->name, sizeof a->name, "%s", jstr(j, "name", a->name));
        snprintf(a->role, sizeof a->role, "%s", jstr(j, "role", a->role));
        a->color = color_named(jstr(j, "color", ""), slug);
        a->enabled = jbool(j, "enabled", true);
        const char *st = jstr(j, "state", "idle");
        if (strcmp(a->state, st)) {
            if (!strcmp(st, "working") && strcmp(a->state, "working")) npc_say(a, "On it!");
            if (!strcmp(a->state, "working") && !strcmp(st, "idle")) npc_say(a, "Done!");
            snprintf(a->state, sizeof a->state, "%s", st);
        }
        J *cur = jget(j, "current");
        snprintf(a->task, sizeof a->task, "%s", cur && cur->t == J_OBJ ? jstr(cur, "title", "") : "");
    }
    /* agents that were removed in LifeOS leave the apartment */
    for (int i = NAG - 1; i >= 0; i--) {
        if (seen[i]) continue;
        release_spot(&AG[i]);
        memmove(&AG[i], &AG[i + 1], sizeof(Agent) * (size_t)(NAG - i - 1));
        NAG--;
        changed = true;
    }
    if (changed) {
        for (int i = 0; i < W.nspots; i++) W.spots[i].occupant = -1;
        for (int i = 0; i < NAG; i++) AG[i].spot = -1, AG[i].act = -1;
        npc_place_all();
    }
}

/* ------------------------------------------------------------------ schedule */

static int busy_state(const Agent *a)
{
    if (!strcmp(a->state, "working")) return 2;
    if (!strcmp(a->state, "waiting") || !strcmp(a->state, "asking")) return 1;
    return 0;
}

static int decide(Agent *a, int idx)
{
    if (!a->enabled) return ACT_SLEEP;
    if (busy_state(a)) return ACT_WORK;
    uint32_t h = hash_str(a->slug);
    float j = (float)(h % 75) / 60.0f;                 /* up to 1h15 of personal jitter */
    float now = NOW_HOURS;
    float bed = 22.5f + j, wake = 6.25f + j;
    bool asleep = bed >= 24 ? (now >= bed - 24 && now < wake) : (now >= bed || now < wake);
    if (asleep) return ACT_SLEEP;
    float m = j / 3;
    if ((now >= 7.5f + m && now < 8.2f + m) || (now >= 12.25f + m && now < 12.95f + m) || (now >= 18.5f + m && now < 19.25f + m))
        return ACT_EAT;
    (void)idx;
    return -2;   /* free time: pick a leisure activity */
}

static int pick_leisure(void)
{
    float r = rnd();
    if (r < 0.30f) return ACT_RELAX;
    if (r < 0.52f) return ACT_WANDER;
    if (r < 0.70f) return ACT_ROOM;
    if (r < 0.82f) return ACT_SNACK;
    if (r < 0.90f) return ACT_BATH;
    return ACT_BOARD;
}

static int free_spot(int kind, int room_or_minus1, int idx)
{
    int cand[MAX_SPOTS], n = 0;
    for (int i = 0; i < W.nspots; i++) {
        Spot *s = &W.spots[i];
        if (s->kind != kind) continue;
        if (room_or_minus1 >= 0 && s->room != room_or_minus1) continue;
        if (room_or_minus1 == -1 && kind == SPOT_STAND && (s->room == R_AI1 || s->room == R_AI2)) continue;
        if (s->occupant >= 0 && s->occupant != idx) continue;
        cand[n++] = i;
    }
    return n ? cand[rand() % n] : -1;
}

static int owned_spot(int kind, int owner)
{
    for (int i = 0; i < W.nspots; i++) if (W.spots[i].kind == kind && W.spots[i].owner == owner) return i;
    return -1;
}

static int spot_for(int act, int idx)
{
    int slot = idx % 12;
    switch (act) {
    case ACT_SLEEP: return owned_spot(SPOT_BED, slot);
    case ACT_WORK: return owned_spot(SPOT_DESK, slot);
    case ACT_EAT: { int s = free_spot(SPOT_EAT, -1, idx); return s >= 0 ? s : free_spot(SPOT_KITCHEN, -1, idx); }
    case ACT_RELAX: { int s = free_spot(SPOT_RELAX, -1, idx); return s >= 0 ? s : free_spot(SPOT_STAND, -1, idx); }
    case ACT_WANDER: return free_spot(SPOT_STAND, -1, idx);
    case ACT_ROOM: return free_spot(SPOT_STAND, slot < 6 ? R_AI1 : R_AI2, idx);
    case ACT_SNACK: return free_spot(SPOT_KITCHEN, -1, idx);
    case ACT_BATH: { int s = free_spot(SPOT_BATH, -1, idx); return s >= 0 ? s : free_spot(SPOT_STAND, -1, idx); }
    case ACT_BOARD: return free_spot(SPOT_STAND, R_WORK, idx);
    }
    return -1;
}

/* ------------------------------------------------------------------ paths */

static void push(Agent *a, float x, float z) { if (a->npath < 12) a->path[a->npath++] = v3(x, 0, z); }

static void route(Agent *a, int from_spot, int to_spot)
{
    a->npath = a->pathi = 0;
    float ax[3], az[3];
    /* step out the way we came in */
    int n = spot_approach(from_spot, ax, az);
    for (int i = n - 1; i >= 0; i--) push(a, ax[i], az[i]);
    float sx = n ? ax[0] : a->x, sz = n ? az[0] : a->z;
    int rf = room_at(sx, sz), rt = to_spot >= 0 ? W.spots[to_spot].room : R_HALL;
    if (rf != rt) {
        V3 in, out;
        if (rf >= 0 && rf != R_HALL) {
            V3 hub = room_hub(rf);
            push(a, hub.x, hub.z);
            door_points(rf, &in, &out);
            push(a, in.x, in.z); push(a, out.x, out.z);
        }
        if (rt != R_HALL) {
            door_points(rt, &in, &out);
            push(a, out.x, out.z); push(a, in.x, in.z);
            V3 hub = room_hub(rt);
            push(a, hub.x, hub.z);
        }
    } else if (rt != R_HALL && rf == rt && from_spot != to_spot) {
        V3 hub = room_hub(rt);
        push(a, hub.x, hub.z);
    }
    if (to_spot >= 0) {
        n = spot_approach(to_spot, ax, az);
        for (int i = 0; i < n; i++) push(a, ax[i], az[i]);
        push(a, W.spots[to_spot].pos.x, W.spots[to_spot].pos.z);
    }
}

static void set_activity(Agent *a, int idx, int act, bool teleport)
{
    int old = a->spot;
    int spot = spot_for(act, idx);
    release_spot(a);
    a->act = act;
    a->spot = spot;
    if (spot >= 0) W.spots[spot].occupant = idx;
    a->leisure_timer = 40 + rnd() * 80;
    if (teleport || spot < 0) {
        if (spot >= 0) {
            Spot *s = &W.spots[spot];
            a->x = s->pos.x; a->z = s->pos.z; a->yaw = s->yaw; a->pose = s->pose;
            a->y = s->pose ? s->pos.y : 0;
        }
        a->npath = a->pathi = 0;
        a->moving = false;
        return;
    }
    a->pose = 0;
    a->y = 0;
    route(a, old, spot);
    a->moving = a->npath > 0;
    switch (act) {
    case ACT_SLEEP: if (rnd() < 0.5f) npc_say(a, "Time for bed. Good night!"); break;
    case ACT_EAT: if (rnd() < 0.5f) npc_say(a, NOW_HOURS < 10 ? "Breakfast!" : NOW_HOURS < 15 ? "Lunch time." : "Dinner time!"); break;
    case ACT_SNACK: if (rnd() < 0.3f) npc_say(a, "Snack break."); break;
    default: break;
    }
}

void npc_place_all(void)
{
    for (int i = 0; i < NAG; i++) {
        Agent *a = &AG[i];
        int act = decide(a, i);
        if (act == -2) act = pick_leisure();
        set_activity(a, i, act, true);
    }
}

/* ------------------------------------------------------------------ update */

void npc_say(Agent *a, const char *s)
{
    snprintf(a->bubble, sizeof a->bubble, "%s", s);
    a->bubble_t = 4.0f;
}

void npc_face(Agent *a, float x, float z) { a->yaw = atan2f(x - a->x, z - a->z); }

static void update_cat(float dt)
{
    if (!lay_has("decor:cat")) { CAT.init = false; return; }
    if (!CAT.init) { CAT.x = 6; CAT.z = 14.5f; CAT.tx = CAT.x; CAT.tz = CAT.z; CAT.init = true; }
    CAT.phase += dt;
    if (CAT.wait > 0) { CAT.wait -= dt; return; }
    float d = dist2(CAT.x, CAT.z, CAT.tx, CAT.tz);
    if (d < 0.1f) {
        /* next stop: somewhere in the living room, kitchen or hallway */
        static const float P[][2] = {{6, 14.5f}, {8.2f, 10.5f}, {2, 14}, {7.6f, 11}, {4.75f, 9.8f}, {4.75f, 8},
                                     {9, 8}, {12.2f, 9.8f}, {11, 11}, {14, 11.2f}};
        int i = rand() % (int)(sizeof P / sizeof P[0]);
        /* only to places reachable in a straight line (no walls in between) */
        if (sight_clear(CAT.x, CAT.z, P[i][0], P[i][1])) { CAT.tx = P[i][0]; CAT.tz = P[i][1]; }
        CAT.wait = 2 + rnd() * 8;
        return;
    }
    float sp = 0.6f * dt;
    CAT.yaw = atan2f(CAT.tx - CAT.x, CAT.tz - CAT.z);
    CAT.x += (CAT.tx - CAT.x) / d * fminf(sp, d);
    CAT.z += (CAT.tz - CAT.z) / d * fminf(sp, d);
}

void npc_update(float dt, float px, float pz)
{
    static float decide_t;
    decide_t -= dt;
    bool redecide = decide_t <= 0;
    if (redecide) decide_t = 1.0f;
    update_cat(dt);
    for (int i = 0; i < NAG; i++) {
        Agent *a = &AG[i];
        if (a->bubble_t > 0) a->bubble_t -= dt;
        if (redecide) {
            int act = decide(a, i);
            bool again = false;
            if (act == -2) {
                a->leisure_timer -= 1.0f;
                bool leisure_now = a->act == ACT_RELAX || a->act == ACT_WANDER || a->act == ACT_ROOM ||
                                   a->act == ACT_SNACK || a->act == ACT_BATH || a->act == ACT_BOARD;
                if (!leisure_now || (a->leisure_timer <= 0 && !a->moving && !a->chatting)) { act = pick_leisure(); again = leisure_now; }
                else act = a->act;
            }
            if ((act != a->act || again) && !a->chatting) set_activity(a, i, act, false);
        }
        if (a->chatting) {
            if (a->pose == 0) { float want = atan2f(px - a->x, pz - a->z); a->yaw += wrap_angle(want - a->yaw) * fminf(1, dt * 6); }
            continue;
        }
        if (!a->moving) { a->idle_t += dt; continue; }
        if (a->pathi >= a->npath) {
            a->moving = false;
            if (a->spot >= 0) {
                Spot *s = &W.spots[a->spot];
                a->x = s->pos.x; a->z = s->pos.z; a->yaw = s->yaw; a->pose = s->pose;
            }
            continue;
        }
        /* don't walk through the player: wait a moment */
        V3 tgt = a->path[a->pathi];
        float d = dist2(a->x, a->z, tgt.x, tgt.z);
        float speed = (a->act == ACT_WORK ? 1.7f : 1.25f) * dt;
        float nx = a->x + (d > 0.001f ? (tgt.x - a->x) / d * fminf(speed, d) : 0);
        float nz = a->z + (d > 0.001f ? (tgt.z - a->z) / d * fminf(speed, d) : 0);
        if (dist2(nx, nz, px, pz) < 0.55f && dist2(a->x, a->z, px, pz) >= dist2(nx, nz, px, pz)) {
            a->idle_t += dt;
            if (a->idle_t > 2.5f) { a->idle_t = 0; npc_say(a, "Excuse me!"); }
            continue;
        }
        if (d > 0.01f) {
            float want = atan2f(tgt.x - a->x, tgt.z - a->z);
            a->yaw += wrap_angle(want - a->yaw) * fminf(1, dt * 10);
        }
        a->x = nx; a->z = nz;
        a->walk_phase += dt * 9;
        if (d <= speed + 0.001f) a->pathi++;
        /* climb into the top bunk / onto a seat on the last stretch */
        if (a->spot >= 0 && a->pathi >= a->npath - 1) {
            Spot *s = &W.spots[a->spot];
            float left = dist2(a->x, a->z, s->pos.x, s->pos.z);
            if (s->pose && left < 1.0f) a->y = lerpf(s->pos.y, a->y, left / 1.0f);
        } else a->y = 0;
    }
}

/* ------------------------------------------------------------------ drawing */

static void limb(float x, float y, float sx, float sy, float sz, float angle, Col c)
{
    /* a box hanging down from (x, y), rotated about x by angle (degrees) */
    glPushMatrix();
    glTranslatef(x, y, 0);
    glRotatef(angle, 1, 0, 0);
    box(-sx / 2, -sy, -sz / 2, sx / 2, 0, sz / 2, c);
    glPopMatrix();
}

static void leg(float x, float thigh, float shin, Col c)
{
    glPushMatrix();
    glTranslatef(x, 0.72f, 0);
    glRotatef(thigh, 1, 0, 0);
    box(-0.065f, -0.36f, -0.07f, 0.065f, 0, 0.07f, c);
    glTranslatef(0, -0.36f, 0);
    glRotatef(shin, 1, 0, 0);
    box(-0.06f, -0.36f, -0.065f, 0.06f, 0, 0.065f, shade(c, 0.9f));
    box(-0.065f, -0.36f, -0.02f, 0.065f, -0.31f, 0.13f, rgb(40, 40, 46));
    glPopMatrix();
}

static void body(const Agent *a, float t, bool hats)
{
    Col c = a->color, skin = rgb(222, 226, 232), dark = shade(c, 0.45f);
    float swing = a->moving ? sinf(a->walk_phase) * 32 : 0;
    float th_l = swing, th_r = -swing, sh_l = a->moving ? fmaxf(0, -swing) * 0.8f : 0, sh_r = a->moving ? fmaxf(0, swing) * 0.8f : 0;
    float arm_l = -swing * 0.8f, arm_r = swing * 0.8f;
    float bob = a->moving ? fabsf(sinf(a->walk_phase)) * 0.03f : sinf(t * 1.6f + a->x) * 0.005f;
    if (a->pose == 1) {
        th_l = th_r = -90; sh_l = sh_r = 90;
        arm_l = arm_r = -20;
        if (a->act == ACT_WORK && busy_state(a) == 2) {
            arm_l = -65 + sinf(t * 18) * 6; arm_r = -65 + sinf(t * 18 + 1.4f) * 6;
        } else if (a->act == ACT_EAT) {
            arm_r = -55 + sinf(t * 3) * 15;
        }
        bob = 0;
    }
    if (a->chatting && a->pose == 0) arm_r = -30 + sinf(t * 4) * 12;
    glPushMatrix();
    glTranslatef(0, bob, 0);
    leg(-0.1f, th_l, sh_l, dark);
    leg(0.1f, th_r, sh_r, dark);
    box(-0.21f, 0.7f, -0.13f, 0.21f, 1.26f, 0.13f, c);            /* torso */
    box(-0.22f, 0.68f, -0.14f, 0.22f, 0.76f, 0.14f, shade(c, 0.6f));  /* belt */
    box(-0.08f, 0.9f, 0.13f, 0.08f, 1.12f, 0.135f, shade(c, 1.25f));  /* chest light */
    limb(-0.28f, 1.23f, 0.11f, 0.5f, 0.11f, arm_l, c);
    limb(0.28f, 1.23f, 0.11f, 0.5f, 0.11f, arm_r, c);
    box(-0.05f, 1.26f, -0.05f, 0.05f, 1.31f, 0.05f, rgb(120, 124, 130));  /* neck */
    box(-0.17f, 1.3f, -0.16f, 0.17f, 1.62f, 0.16f, skin);                /* head */
    /* visor: the agent's color, brighter while working */
    float glow = a->act == ACT_SLEEP && a->pose == 2 ? 0.35f : busy_state(a) == 2 ? 1.25f : 1.0f;
    glow_quad(v3(-0.14f, 1.52f, 0.162f), v3(0.14f, 1.52f, 0.162f), v3(0.14f, 1.42f, 0.162f), v3(-0.14f, 1.42f, 0.162f), shade(c, glow));
    box(-0.015f, 1.62f, -0.015f, 0.015f, 1.72f, 0.015f, rgb(120, 124, 130));
    ball(0, 1.74f, 0, 0.03f, shade(c, 1.2f));
    if (hats) {
        uint32_t h = hash_str(a->slug) % 3;
        Col hc = h == 0 ? rgb(30, 30, 34) : h == 1 ? rgb(200, 40, 40) : rgb(240, 200, 60);
        if (h == 0) { box(-0.2f, 1.62f, -0.2f, 0.2f, 1.64f, 0.2f, hc); box(-0.13f, 1.64f, -0.13f, 0.13f, 1.84f, 0.13f, hc); }
        else if (h == 1) { prism(0, 1.62f, 0, 0.17f, 0.17f, 0.08f, 8, hc); box(-0.17f, 1.62f, 0.05f, 0.17f, 1.64f, 0.3f, hc); }
        else prism(0, 1.62f, 0, 0.16f, 0.0f, 0.3f, 6, hc);
    }
    glPopMatrix();
}

static void draw_cat(float t)
{
    if (!CAT.init) return;
    glPushMatrix();
    glTranslatef(CAT.x, 0, CAT.z);
    glRotatef(CAT.yaw * 180 / PI_F, 0, 1, 0);
    Col c = rgb(230, 150, 70);
    float step = CAT.wait > 0 ? 0 : sinf(CAT.phase * 10) * 0.03f;
    box(-0.09f, 0.12f, -0.22f, 0.09f, 0.28f, 0.2f, c);
    box(-0.08f, 0.2f, 0.17f, 0.08f, 0.36f, 0.33f, c);
    box(-0.07f, 0.36f, 0.2f, -0.03f, 0.42f, 0.24f, c);
    box(0.03f, 0.36f, 0.2f, 0.07f, 0.42f, 0.24f, c);
    box(-0.05f, 0.26f, 0.33f, -0.02f, 0.29f, 0.335f, rgb(30, 30, 30));
    box(0.02f, 0.26f, 0.33f, 0.05f, 0.29f, 0.335f, rgb(30, 30, 30));
    for (int k = 0; k < 4; k++) {
        float lx = k & 1 ? 0.05f : -0.08f, lz = k & 2 ? 0.12f : -0.16f;
        box(lx, 0, lz + (k % 3 ? step : -step), lx + 0.03f, 0.12f, lz + 0.03f + (k % 3 ? step : -step), c);
    }
    glPushMatrix();
    glTranslatef(0, 0.25f, -0.22f);
    glRotatef(30 + sinf(t * 2) * 15, 1, 0, 0);
    box(-0.02f, 0, -0.25f, 0.02f, 0.04f, 0, c);
    glPopMatrix();
    glPopMatrix();
}

void npc_draw(float t)
{
    bool hats = lay_has("extra:hats");
    for (int i = 0; i < NAG; i++) {
        Agent *a = &AG[i];
        glPushMatrix();
        glTranslatef(a->x, a->pose == 1 ? a->y - 0.72f : a->y, a->z);
        glRotatef(a->yaw * 180 / PI_F, 0, 1, 0);
        if (a->pose == 2) glRotatef(-90, 1, 0, 0);
        body(a, t, hats);
        glPopMatrix();
    }
    draw_cat(t);
}

const char *npc_status_text(const Agent *a)
{
    static char buf[260];
    int b = busy_state(a);
    if (!a->enabled) return "Off duty (disabled)";
    if (b == 2) { snprintf(buf, sizeof buf, "Working: %s", a->task[0] ? a->task : "a task"); return buf; }
    if (b == 1) return !strcmp(a->state, "asking") ? "Has a question for you" : "Waiting for your approval";
    if (a->moving) {
        switch (a->act) {
        case ACT_SLEEP: return "Heading to bed";
        case ACT_EAT: return "Going to eat";
        default: return "Walking";
        }
    }
    switch (a->act) {
    case ACT_SLEEP: return "Sleeping";
    case ACT_EAT: return "Eating";
    case ACT_RELAX: return "Relaxing";
    case ACT_WANDER: return "Hanging around";
    case ACT_ROOM: return "Chilling in their room";
    case ACT_SNACK: return "Getting a snack";
    case ACT_BATH: return "In the bathroom";
    case ACT_BOARD: return "Reading the whiteboard";
    }
    return "Idle";
}

/* ------------------------------------------------------------------ used by world.c */

int npc_desk_working(int owner)
{
    for (int i = 0; i < NAG; i++) {
        if (i % 12 != owner) continue;
        Agent *a = &AG[i];
        if (a->act == ACT_WORK && !a->moving) return busy_state(a);
    }
    return 0;
}

const char *npc_bed_name(int owner)
{
    for (int i = 0; i < NAG; i++) if (i % 12 == owner) return AG[i].name;
    return NULL;
}
