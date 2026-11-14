/* LifeOS Apartment: main loop, player, interaction, and every on-screen panel.
 *
 * Keys: WASD/arrows walk, Shift run, mouse look, E interact, Esc menu, P shop, C decorate, V videos (couch/TV),
 * F1 help. On the couch: Space pause, Left/Right seek, Up/Down volume. Using the PC (E at the desk) leaves the
 * apartment for the KDE Plasma desktop (exit code 10); Shift+F4 there brings the apartment back.
 */
#include "app.h"
#include <errno.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define EXIT_TO_PC 10

Settings CFG = {1, 1.0f, false, 75, 70, false, true, 0};
float NOW_HOURS;
int WIN_W = 1280, WIN_H = 720;
int g_points_balance;

static SDL_Window *win;
static bool running = true;
static int exit_code;
static float T;                  /* seconds since start */
static bool from_pc, windowed;
static bool quality_saved;

/* ------------------------------------------------------------------ player */
static struct { float x, z, yaw, pitch, eye; } P = {5, 8, 0, 0, 1.62f};

/* ------------------------------------------------------------------ controls (rebindable in Settings) */
enum { K_FWD, K_BACK, K_LEFT, K_RIGHT, K_RUN, K_USE, K_TURNL, K_TURNR, K_LOOKU, K_LOOKD, NKEYS };
static const char *KEY_NAMES[NKEYS] = {"Walk forward", "Walk back", "Step left", "Step right", "Run (hold)",
                                       "Interact / talk", "Turn left", "Turn right", "Look up", "Look down"};
static const char *KEY_IDS[NKEYS] = {"fwd", "back", "left", "right", "run", "use", "turnl", "turnr", "looku", "lookd"};
static const SDL_Scancode KEY_DEF[NKEYS] = {SDL_SCANCODE_W, SDL_SCANCODE_S, SDL_SCANCODE_A, SDL_SCANCODE_D,
                                            SDL_SCANCODE_LSHIFT, SDL_SCANCODE_E, SDL_SCANCODE_LEFT, SDL_SCANCODE_RIGHT,
                                            SDL_SCANCODE_PAGEUP, SDL_SCANCODE_PAGEDOWN};
static SDL_Scancode KEYS[NKEYS] = {SDL_SCANCODE_W, SDL_SCANCODE_S, SDL_SCANCODE_A, SDL_SCANCODE_D,
                                   SDL_SCANCODE_LSHIFT, SDL_SCANCODE_E, SDL_SCANCODE_LEFT, SDL_SCANCODE_RIGHT,
                                   SDL_SCANCODE_PAGEUP, SDL_SCANCODE_PAGEDOWN};
static float walk_speed = 1.0f, turn_speed = 1.0f;
static int fps_limit = 60;        /* 30, 45, 60, or 0 = no limit (vsync only) */
static bool idle_save = true;     /* drop to a few frames per second when idle or in the background */
static Uint32 last_input_ms;
static bool has_focus = true;
static int rebinding = -1;       /* the key row waiting for a key press, or -1 */
/* Some VMs (Hyper-V, VirtualBox with mouse integration) only have an absolute pointer, which gives no motion while
 * the mouse is captured. "Drag" look mode leaves the cursor free and looks around while the right button is held. */
static bool drag_look;

enum { M_WALK, M_CHAT, M_MENU, M_SHOP, M_DECOR, M_VIDEOS, M_SETTINGS, M_SIT, M_LIE, M_PC_IN, M_PC_OUT, M_HELP, M_BOARD, M_QUIT };
static int mode = M_WALK, back_mode = M_WALK;
static float anim_t;
static V3 anim_from; static float anim_yaw0, anim_pitch0;
static int sit_seat = -1;

/* ------------------------------------------------------------------ toasts */
typedef struct { char s[200]; float t; } Toast;
static Toast toasts[5];

void toast(const char *fmt, ...)
{
    memmove(&toasts[1], &toasts[0], sizeof(Toast) * 4);
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(toasts[0].s, sizeof toasts[0].s, fmt, ap);
    va_end(ap);
    toasts[0].t = 6;
}

/* ------------------------------------------------------------------ files */

const char *state_dir(void)
{
    static char dir[512];
    if (dir[0]) return dir;
    const char *x = getenv("XDG_STATE_HOME"), *home = getenv("HOME");
    if (x && *x) snprintf(dir, sizeof dir, "%s/lifeos", x);
    else snprintf(dir, sizeof dir, "%s/.local/state/lifeos", home ? home : "/tmp");
    char tmp[512];
    snprintf(tmp, sizeof tmp, "%s", dir);
    for (char *p = tmp + 1; *p; p++) if (*p == '/') { *p = 0; mkdir(tmp, 0755); *p = '/'; }
    mkdir(tmp, 0755);
    return dir;
}

static char *read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0 || n > (16 << 20)) { fclose(f); return NULL; }
    char *b = malloc((size_t)n + 1);
    size_t got = fread(b, 1, (size_t)n, f);
    b[got] = 0;
    fclose(f);
    return b;
}

static void write_file(const char *path, const char *data)
{
    char tmp[600];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "wb");
    if (!f) return;
    fputs(data, f);
    fclose(f);
    rename(tmp, path);
}

static void save_state(bool at_pc)
{
    char path[600], buf[2048];
    snprintf(path, sizeof path, "%s/apartment.ini", state_dir());
    int n = snprintf(buf, sizeof buf,
             "x=%.3f\nz=%.3f\nyaw=%.3f\npitch=%.3f\nquality=%d\nsens=%.2f\ninvert=%d\nfov=%.0f\nvolume=%d\n"
             "fps=%d\nnames=%d\ndisplay=%d\nat_pc=%d\n",
             P.x, P.z, P.yaw, P.pitch, CFG.quality, CFG.sens, CFG.invert, CFG.fov, CFG.volume, CFG.show_fps,
             CFG.show_names, CFG.display, at_pc);
    n += snprintf(buf + n, sizeof buf - n, "walk=%.2f\nturn=%.2f\ndrag=%d\nfps_limit=%d\nidle_save=%d\n", walk_speed, turn_speed, drag_look, fps_limit, idle_save);
    for (int i = 0; i < NKEYS && n < (int)sizeof buf - 40; i++) n += snprintf(buf + n, sizeof buf - n, "key_%s=%d\n", KEY_IDS[i], (int)KEYS[i]);
    write_file(path, buf);
}

static void load_state(void)
{
    char path[600];
    snprintf(path, sizeof path, "%s/apartment.ini", state_dir());
    char *b = read_file(path);
    if (!b) return;
    for (char *line = strtok(b, "\n"); line; line = strtok(NULL, "\n")) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        const char *k = line, *v = eq + 1;
        if (!strcmp(k, "x")) P.x = (float)atof(v);
        else if (!strcmp(k, "z")) P.z = (float)atof(v);
        else if (!strcmp(k, "yaw")) P.yaw = (float)atof(v);
        else if (!strcmp(k, "pitch")) P.pitch = (float)atof(v);
        else if (!strcmp(k, "quality")) { CFG.quality = atoi(v); quality_saved = true; }
        else if (!strcmp(k, "sens")) CFG.sens = (float)atof(v);
        else if (!strcmp(k, "invert")) CFG.invert = atoi(v);
        else if (!strcmp(k, "fov")) CFG.fov = (float)atof(v);
        else if (!strcmp(k, "volume")) CFG.volume = atoi(v);
        else if (!strcmp(k, "fps")) CFG.show_fps = atoi(v);
        else if (!strcmp(k, "names")) CFG.show_names = atoi(v);
        else if (!strcmp(k, "display")) CFG.display = atoi(v);
        else if (!strcmp(k, "walk")) walk_speed = clampf((float)atof(v), 0.5f, 2.0f);
        else if (!strcmp(k, "turn")) turn_speed = clampf((float)atof(v), 0.3f, 3.0f);
        else if (!strcmp(k, "drag")) drag_look = atoi(v);
        else if (!strcmp(k, "fps_limit")) fps_limit = atoi(v);
        else if (!strcmp(k, "idle_save")) idle_save = atoi(v);
        else if (!strncmp(k, "key_", 4)) {
            for (int i = 0; i < NKEYS; i++)
                if (!strcmp(k + 4, KEY_IDS[i])) { int sc = atoi(v); if (sc > 0 && sc < SDL_NUM_SCANCODES) KEYS[i] = (SDL_Scancode)sc; }
        }
    }
    free(b);
    CFG.quality = CFG.quality < 0 ? 0 : CFG.quality > 2 ? 2 : CFG.quality;
    CFG.sens = clampf(CFG.sens, 0.2f, 4.0f);
    CFG.fov = clampf(CFG.fov, 55, 100);
    if (room_at(P.x, P.z) < 0) { P.x = 5; P.z = 8; }
}

/* ------------------------------------------------------------------ chat */

#define CHAT_MAX 40
typedef struct { char role[12]; char *text; } Msg;
typedef struct { char slug[40]; Msg msgs[CHAT_MAX]; int n; bool waiting; int mission; char mission_status[16]; } Chat;
static Chat chats[MAX_AGENTS];
static int nchats;
static int chat_agent = -1;          /* index into AG */
static char input[2000];
static bool task_mode;
static int chat_scroll;

static Chat *chat_for(const char *slug)
{
    for (int i = 0; i < nchats; i++) if (!strcmp(chats[i].slug, slug)) return &chats[i];
    if (nchats >= MAX_AGENTS) return &chats[0];
    Chat *c = &chats[nchats++];
    memset(c, 0, sizeof *c);
    snprintf(c->slug, sizeof c->slug, "%s", slug);
    return c;
}

static void chat_add(Chat *c, const char *role, const char *text)
{
    if (c->n == CHAT_MAX) { free(c->msgs[0].text); memmove(&c->msgs[0], &c->msgs[1], sizeof(Msg) * (CHAT_MAX - 1)); c->n--; }
    snprintf(c->msgs[c->n].role, sizeof c->msgs[c->n].role, "%s", role);
    c->msgs[c->n].text = strdup(text);
    c->n++;
}

static void chats_save(void)
{
    char path[600];
    snprintf(path, sizeof path, "%s/apartment-chat.txt", state_dir());
    FILE *f = fopen(path, "w");
    if (!f) return;
    for (int i = 0; i < nchats; i++)
        for (int m = 0; m < chats[i].n; m++) {
            fprintf(f, "%s\t%s\t", chats[i].slug, chats[i].msgs[m].role);
            for (const char *s = chats[i].msgs[m].text; *s; s++) {
                if (*s == '\n') fputs("\\n", f);
                else if (*s == '\\') fputs("\\\\", f);
                else if (*s == '\t') fputc(' ', f);
                else fputc(*s, f);
            }
            fputc('\n', f);
        }
    fclose(f);
}

static void chats_load(void)
{
    char path[600];
    snprintf(path, sizeof path, "%s/apartment-chat.txt", state_dir());
    char *b = read_file(path);
    if (!b) return;
    for (char *line = strtok(b, "\n"); line; line = strtok(NULL, "\n")) {
        char *t1 = strchr(line, '\t');
        if (!t1) continue;
        char *t2 = strchr(t1 + 1, '\t');
        if (!t2) continue;
        *t1 = *t2 = 0;
        char *o = t2 + 1, *w = o;
        for (; *o; o++) {
            if (*o == '\\' && o[1] == 'n') { *w++ = '\n'; o++; }
            else if (*o == '\\' && o[1] == '\\') { *w++ = '\\'; o++; }
            else *w++ = *o;
        }
        *w = 0;
        chat_add(chat_for(line), t1 + 1, t2 + 1);
    }
    free(b);
}

typedef struct { char slug[40]; int mission; } ChatCtx;

static Agent *agent_by_slug(const char *slug)
{
    for (int i = 0; i < NAG; i++) if (!strcmp(AG[i].slug, slug)) return &AG[i];
    return NULL;
}

static void on_chat_reply(int status, const char *body, void *user)
{
    ChatCtx *ctx = user;
    Chat *c = chat_for(ctx->slug);
    c->waiting = false;
    J *j = json_parse(body);
    Agent *a = agent_by_slug(ctx->slug);
    if (status == 200 && jstr(j, "reply", NULL)) {
        chat_add(c, "assistant", jstr(j, "reply", ""));
        if (a && chat_agent != (int)(a - AG)) toast("%s replied", a->name);
    } else if (status == 0) {
        chat_add(c, "system", "LifeOS isn't answering. It may still be starting; try again in a moment.");
    } else {
        char m[400];
        snprintf(m, sizeof m, "Couldn't get a reply: %s", jstr(j, "detail", "unknown error"));
        chat_add(c, "system", m);
    }
    json_free(j);
    free(ctx);
    chats_save();
}

static void on_mission_started(int status, const char *body, void *user)
{
    ChatCtx *ctx = user;
    Chat *c = chat_for(ctx->slug);
    c->waiting = false;
    J *j = json_parse(body);
    if (status == 200) {
        c->mission = (int)jnum(j, "id", 0);
        snprintf(c->mission_status, sizeof c->mission_status, "queued");
        char m[200];
        snprintf(m, sizeof m, "Task started (mission #%d). I'm heading to the work room.", c->mission);
        chat_add(c, "assistant", m);
        Agent *a = agent_by_slug(ctx->slug);
        if (a) npc_say(a, "On it!");
    } else {
        char m[400];
        snprintf(m, sizeof m, "Couldn't start the task: %s", status ? jstr(j, "detail", "unknown error") : "LifeOS isn't answering");
        chat_add(c, "system", m);
    }
    json_free(j);
    free(ctx);
    chats_save();
}

static void on_mission_poll(int status, const char *body, void *user)
{
    ChatCtx *ctx = user;
    Chat *c = chat_for(ctx->slug);
    if (status == 200 && c->mission == ctx->mission) {
        J *j = json_parse(body);
        const char *st = jstr(j, "status", "");
        if (strcmp(st, c->mission_status)) {
            snprintf(c->mission_status, sizeof c->mission_status, "%s", st);
            Agent *a = agent_by_slug(ctx->slug);
            if (!strcmp(st, "done") || !strcmp(st, "failed") || !strcmp(st, "cancelled")) {
                const char *res = jstr(j, "result", NULL), *err = jstr(j, "error", NULL);
                char m[1400];
                if (!strcmp(st, "done")) snprintf(m, sizeof m, "Finished mission #%d.\n%.1200s", c->mission, res ? res : "");
                else snprintf(m, sizeof m, "Mission #%d %s. %.1000s", c->mission, st, err ? err : "");
                chat_add(c, "assistant", m);
                toast("%s %s mission #%d", a ? a->name : ctx->slug, !strcmp(st, "done") ? "finished" : st, c->mission);
                c->mission = 0;
                chats_save();
            }
        }
        json_free(j);
    }
    free(ctx);
}

static void chat_send(void)
{
    if (chat_agent < 0 || chat_agent >= NAG || !input[0]) return;
    Agent *a = &AG[chat_agent];
    Chat *c = chat_for(a->slug);
    if (c->waiting) return;
    chat_add(c, "user", input);
    ChatCtx *ctx = calloc(1, sizeof *ctx);
    snprintf(ctx->slug, sizeof ctx->slug, "%s", a->slug);
    static char body[16384];
    char esc[8192];
    json_escape(esc, sizeof esc, input);
    if (task_mode) {
        char slug[100];
        json_escape(slug, sizeof slug, a->slug);
        snprintf(body, sizeof body, "{\"goal\":%s,\"agent\":%s}", esc, slug);
        net_request("POST", "/api/missions", body, 1, on_mission_started, ctx);
    } else {
        int o = snprintf(body, sizeof body, "{\"message\":%s,\"history\":[", esc);
        int first = c->n - 1 - 10 < 0 ? 0 : c->n - 1 - 10;
        bool comma = false;
        for (int i = first; i < c->n - 1 && o < (int)sizeof body - 4200; i++) {
            if (strcmp(c->msgs[i].role, "user") && strcmp(c->msgs[i].role, "assistant")) continue;
            char e[4096];
            json_escape(e, sizeof e, c->msgs[i].text);
            o += snprintf(body + o, sizeof body - (size_t)o, "%s{\"role\":\"%s\",\"content\":%s}", comma ? "," : "", c->msgs[i].role, e);
            comma = true;
        }
        snprintf(body + o, sizeof body - (size_t)o, "]}");
        char path[160];
        snprintf(path, sizeof path, "/api/crew/agents/%s/chat", a->slug);
        net_request("POST", path, body, 1, on_chat_reply, ctx);
    }
    c->waiting = true;
    input[0] = 0;
    chat_scroll = 0;
    chats_save();
}

/* ------------------------------------------------------------------ backend data */

static J *crew_json;           /* last /api/crew/status */
static char board_lines[12][200];
static int nboard;
static bool ever_online;
static float offline_for;
static int last_balance = -1;
static float plus_t; static int plus_n;
static float layout_dirty_t = -1;

static void apply_layout(J *lay)
{
    if (!lay || lay->t != J_OBJ) return;
    J *walls = jget(lay, "walls"), *floors = jget(lay, "floors");
    for (int r = 0; r < NROOMS; r++) {
        int i = shop_find(jstr(walls, ROOM_KEYS[r], ""));
        if (i >= 0) SHOP.lay.wall[r] = i;
        i = shop_find(jstr(floors, ROOM_KEYS[r], ""));
        if (i >= 0) SHOP.lay.floor[r] = i;
    }
    int ci = shop_find(jstr(lay, "couch", ""));
    if (ci >= 0) SHOP.lay.couch = ci;
    memset(SHOP.lay.decor, 0, sizeof SHOP.lay.decor);
    memset(SHOP.lay.extra, 0, sizeof SHOP.lay.extra);
    J *d = jget(lay, "decor"), *e = jget(lay, "extras");
    for (int k = 0; d && k < d->count; k++) { int i = shop_find(jstr(jat(d, k), NULL, "")); if (i >= 0) SHOP.lay.decor[i] = true; }
    for (int k = 0; e && k < e->count; k++) { int i = shop_find(jstr(jat(e, k), NULL, "")); if (i >= 0) SHOP.lay.extra[i] = true; }
}

static void apply_shop(J *root)
{
    J *cat = jget(root, "catalog");
    if (cat && cat->t == J_ARR && cat->count) {
        SHOP.nitems = 0;
        for (int k = 0; k < cat->count && SHOP.nitems < MAX_ITEMS; k++) {
            J *c = jat(cat, k);
            Item *it = &SHOP.items[SHOP.nitems++];
            memset(it, 0, sizeof *it);
            snprintf(it->id, sizeof it->id, "%s", jstr(c, "id", ""));
            snprintf(it->name, sizeof it->name, "%s", jstr(c, "name", it->id));
            snprintf(it->kind, sizeof it->kind, "%s", jstr(c, "kind", ""));
            snprintf(it->desc, sizeof it->desc, "%s", jstr(c, "desc", ""));
            it->cost = (int)jnum(c, "cost", 0);
            it->owned = jbool(c, "owned", it->cost == 0);
            J *rgbj = jget(c, "rgb");
            it->rgb = rgbj && rgbj->count == 3 ? rgb((int)jnum(jat(rgbj, 0), NULL, 0), (int)jnum(jat(rgbj, 1), NULL, 0), (int)jnum(jat(rgbj, 2), NULL, 0))
                                               : rgb(200, 200, 200);
        }
    }
    apply_layout(jget(root, "layout"));
    J *p = jget(root, "points");
    if (p) {
        SHOP.balance = (int)jnum(p, "balance", 0);
        SHOP.earned = (int)jnum(p, "earned", 0);
        SHOP.spent = (int)jnum(p, "spent", 0);
        J *cn = jget(p, "counts");
        const char *keys[5] = {"call", "kilotoken", "message", "task", "mission"};
        for (int i = 0; i < 5; i++) SHOP.counts[i] = (int)jnum(cn, keys[i], 0);
        if (last_balance >= 0 && SHOP.balance > last_balance) { plus_n = SHOP.balance - last_balance; plus_t = 3; }
        last_balance = SHOP.balance;
        g_points_balance = SHOP.balance;
    }
    SHOP.loaded = true;
}

static char last_layout_sig[4096];

static void layout_json(char *out, size_t n)
{
    int o = snprintf(out, n, "{\"walls\":{");
    for (int r = 0; r < NROOMS; r++)
        o += snprintf(out + o, n - (size_t)o, "%s\"%s\":\"%s\"", r ? "," : "", ROOM_KEYS[r],
                      SHOP.lay.wall[r] >= 0 ? SHOP.items[SHOP.lay.wall[r]].id : "");
    o += snprintf(out + o, n - (size_t)o, "},\"floors\":{");
    for (int r = 0; r < NROOMS; r++)
        o += snprintf(out + o, n - (size_t)o, "%s\"%s\":\"%s\"", r ? "," : "", ROOM_KEYS[r],
                      SHOP.lay.floor[r] >= 0 ? SHOP.items[SHOP.lay.floor[r]].id : "");
    o += snprintf(out + o, n - (size_t)o, "},\"couch\":\"%s\",\"decor\":[", SHOP.lay.couch >= 0 ? SHOP.items[SHOP.lay.couch].id : "couch:slate");
    bool c = false;
    for (int i = 0; i < SHOP.nitems; i++) if (SHOP.lay.decor[i]) { o += snprintf(out + o, n - (size_t)o, "%s\"%s\"", c ? "," : "", SHOP.items[i].id); c = true; }
    o += snprintf(out + o, n - (size_t)o, "],\"extras\":[");
    c = false;
    for (int i = 0; i < SHOP.nitems; i++) if (SHOP.lay.extra[i]) { o += snprintf(out + o, n - (size_t)o, "%s\"%s\"", c ? "," : "", SHOP.items[i].id); c = true; }
    snprintf(out + o, n - (size_t)o, "]}");
}

static void rebuild_if_changed(void)
{
    char sig[4096];
    layout_json(sig, sizeof sig);
    if (strcmp(sig, last_layout_sig)) {
        snprintf(last_layout_sig, sizeof last_layout_sig, "%s", sig);
        world_build();
        npc_place_all();
    }
}

static void save_shop_cache(const char *body)
{
    char path[600];
    snprintf(path, sizeof path, "%s/apartment-shop.json", state_dir());
    write_file(path, body);
}

static void on_apartment(int status, const char *body, void *user)
{
    (void)user;
    if (status != 200) return;
    J *j = json_parse(body);
    if (!j) return;
    apply_shop(j);
    json_free(j);
    save_shop_cache(body);
    rebuild_if_changed();
}

static void on_buy(int status, const char *body, void *user)
{
    char *name = user;
    J *j = json_parse(body);
    if (status == 200 && j) {
        apply_shop(j);
        save_shop_cache(body);
        rebuild_if_changed();
        toast("Bought %s!", name);
    } else toast("%s", status ? jstr(j, "detail", "Couldn't buy that") : "LifeOS isn't answering");
    json_free(j);
    free(name);
}

static void on_layout_saved(int status, const char *body, void *user)
{
    (void)user;
    if (status != 200) {
        J *j = json_parse(body);
        toast("Couldn't save the decoration: %s", status ? jstr(j, "detail", "error") : "LifeOS isn't answering");
        json_free(j);
    }
}

static void on_crew(int status, const char *body, void *user)
{
    (void)user;
    if (status != 200) return;
    J *j = json_parse(body);
    if (!j) return;
    ever_online = true;
    npc_set_roster(jget(j, "agents"));
    json_free(crew_json);
    crew_json = j;
    /* the whiteboard: active missions first */
    nboard = 0;
    J *act = jget(j, "active");
    for (int i = 0; act && i < act->count && nboard < 6; i++)
        snprintf(board_lines[nboard++], sizeof board_lines[0], "> %s", jstr(jat(act, i), "goal", ""));
}

static char task_lines[8][200];
static int ntasks;

static void on_tasks(int status, const char *body, void *user)
{
    (void)user;
    if (status != 200) return;
    J *j = json_parse(body);
    ntasks = 0;
    for (int i = 0; j && j->t == J_ARR && i < j->count && ntasks < 6; i++)
        snprintf(task_lines[ntasks++], sizeof task_lines[0], "[ ] %s", jstr(jat(j, i), "title", ""));
    json_free(j);
}

static void poll_backend(float dt)
{
    static float crew_t, shop_t = 0.5f, tasks_t = 1, mission_t = 5;
    crew_t -= dt; shop_t -= dt; tasks_t -= dt; mission_t -= dt;
    if (crew_t <= 0) { crew_t = 3; net_request("GET", "/api/crew/status", NULL, 0, on_crew, NULL); }
    if (shop_t <= 0) { shop_t = 20; net_request("GET", "/api/apartment", NULL, 0, on_apartment, NULL); }
    if (tasks_t <= 0) { tasks_t = 15; net_request("GET", "/api/tasks?status=open", NULL, 0, on_tasks, NULL); }
    if (mission_t <= 0) {
        mission_t = 5;
        for (int i = 0; i < nchats; i++) {
            if (!chats[i].mission) continue;
            ChatCtx *ctx = calloc(1, sizeof *ctx);
            snprintf(ctx->slug, sizeof ctx->slug, "%s", chats[i].slug);
            ctx->mission = chats[i].mission;
            char path[64];
            snprintf(path, sizeof path, "/api/missions/%d", chats[i].mission);
            net_request("GET", path, NULL, 0, on_mission_poll, ctx);
        }
    }
    if (layout_dirty_t >= 0) {
        layout_dirty_t -= dt;
        if (layout_dirty_t < 0) {
            char body[4096];
            layout_json(body, sizeof body);
            net_request("PATCH", "/api/apartment/layout", body, 0, on_layout_saved, NULL);
        }
    }
    offline_for = net_online() ? 0 : offline_for + dt;
}

/* ------------------------------------------------------------------ screens drawn in the world */

void draw_tv_screen(V3 c, float w, float h)
{
    float u, v;
    GLuint tex = video_texture(&u, &v);
    /* the screen faces north (-z); seen from the couch, its left edge is at +x */
    V3 tl = v3(c.x + w / 2, c.y + h / 2, c.z), tr = v3(c.x - w / 2, c.y + h / 2, c.z);
    V3 br = v3(c.x - w / 2, c.y - h / 2, c.z), bl = v3(c.x + w / 2, c.y - h / 2, c.z);
    if (tex && !video_paused()) { tex_quad(tex, u, v, tl, tr, br, bl); return; }
    glow_quad(tl, tr, br, bl, video_paused() ? rgb(10, 10, 14) : rgb(14, 18, 30));
    float px = h * 0.12f / text_h(F_TITLE);
    const char *t1 = video_paused() ? "Paused" : "LifeOS TV";
    text3d(F_TITLE, v3(c.x + text_w(F_TITLE, t1) * px / 2, c.y + 0.1f * h, c.z - 0.003f), v3(-1, 0, 0), v3(0, -1, 0), px, t1, rgb(200, 220, 255));
    const char *t2 = video_paused() ? video_title() : "Press E to choose a video";
    float px2 = h * 0.06f / text_h(F_BODY);
    text3d(F_BODY, v3(c.x + text_w(F_BODY, t2) * px2 / 2, c.y - 0.08f * h, c.z - 0.003f), v3(-1, 0, 0), v3(0, -1, 0), px2, t2, rgb(150, 160, 190));
}

void draw_pc_screen(V3 c, float w, float h)
{
    /* faces south (+z); seen from the chair, left is -x */
    float x0 = c.x - w / 2, x1 = c.x + w / 2, y0 = c.y - h / 2, y1 = c.y + h / 2, z = c.z + 0.002f;
    glow_quad(v3(x0, y1, z), v3(x1, y1, z), v3(x1, y0, z), v3(x0, y0, z), rgb(30, 60, 120));
    glow_quad(v3(x0, y1, z + 0.001f), v3(x1, y1, z + 0.001f), v3(x1, (y0 + y1) / 2, z + 0.001f), v3(x0, (y0 + y1) / 2, z + 0.001f), rgb(50, 90, 160));
    /* taskbar */
    glow_quad(v3(x0, y0 + 0.05f, z + 0.002f), v3(x1, y0 + 0.05f, z + 0.002f), v3(x1, y0, z + 0.002f), v3(x0, y0, z + 0.002f), rgb(24, 26, 32));
    glow_quad(v3(x0 + 0.01f, y0 + 0.04f, z + 0.003f), v3(x0 + 0.04f, y0 + 0.04f, z + 0.003f), v3(x0 + 0.04f, y0 + 0.01f, z + 0.003f), v3(x0 + 0.01f, y0 + 0.01f, z + 0.003f), rgb(60, 170, 240));
    time_t now = time(NULL);
    struct tm lt;
    localtime_r(&now, &lt);
    char buf[16];
    strftime(buf, sizeof buf, "%H:%M", &lt);
    float px = 0.03f / text_h(F_BODY);
    text3d(F_BODY, v3(x1 - 0.01f - text_w(F_BODY, buf) * px, y0 + 0.042f, z + 0.004f), v3(1, 0, 0), v3(0, -1, 0), px, buf, rgb(230, 230, 240));
    float px2 = 0.045f / text_h(F_TITLE);
    text3d(F_TITLE, v3(x0 + 0.05f, y1 - 0.06f, z + 0.004f), v3(1, 0, 0), v3(0, -1, 0), px2, "KDE Plasma", rgb(240, 240, 250));
    float px3 = 0.028f / text_h(F_BODY);
    text3d(F_BODY, v3(x0 + 0.05f, y1 - 0.13f, z + 0.004f), v3(1, 0, 0), v3(0, -1, 0), px3, "Press E to use the computer", rgb(210, 220, 240));
}

void draw_whiteboard(V3 o)
{
    /* origin is the board's top-left as seen from the room (looking south, so right is -x) */
    float px = 0.085f / text_h(F_TITLE);
    text3d(F_TITLE, o, v3(-1, 0, 0), v3(0, -1, 0), px, "Missions", rgb(30, 60, 160));
    float y = o.y - 0.13f;
    float pxb = 0.06f / text_h(F_BODY);
    if (!nboard) { text3d(F_BODY, v3(o.x, y, o.z), v3(-1, 0, 0), v3(0, -1, 0), pxb, "(nothing running)", rgb(90, 90, 100)); y -= 0.08f; }
    for (int i = 0; i < nboard; i++, y -= 0.075f) {
        char s[64];
        snprintf(s, sizeof s, "%.52s", board_lines[i]);
        text3d(F_BODY, v3(o.x, y, o.z), v3(-1, 0, 0), v3(0, -1, 0), pxb, s, rgb(20, 20, 30));
    }
    y -= 0.05f;
    text3d(F_TITLE, v3(o.x, y, o.z), v3(-1, 0, 0), v3(0, -1, 0), px, "Your tasks", rgb(160, 40, 40));
    y -= 0.13f;
    for (int i = 0; i < ntasks && y > 1.0f; i++, y -= 0.075f) {
        char s[64];
        snprintf(s, sizeof s, "%.52s", task_lines[i]);
        text3d(F_BODY, v3(o.x, y, o.z), v3(-1, 0, 0), v3(0, -1, 0), pxb, s, rgb(20, 20, 30));
    }
}

/* ------------------------------------------------------------------ interaction */

typedef struct { int kind; int ref; const char *label; char text[120]; } Target;
static Target target;

static void find_target(void)
{
    target.kind = I_NONE;
    if (mode != M_WALK) return;
    float fx = sinf(P.yaw), fz = cosf(P.yaw);
    float best = 1e9f;
    for (int i = 0; i < NAG; i++) {
        Agent *a = &AG[i];
        float d = dist2(P.x, P.z, a->x, a->z);
        if (d > 2.4f) continue;
        float dx = (a->x - P.x) / (d + 1e-4f), dz = (a->z - P.z) / (d + 1e-4f);
        float dot = dx * fx + dz * fz;
        if (dot < 0.82f && d > 0.6f) continue;
        float score = d * (2 - dot);
        if (score < best && sight_clear(P.x, P.z, a->x, a->z)) {
            best = score;
            target.kind = I_AGENT; target.ref = i;
            snprintf(target.text, sizeof target.text, "Talk to %s", a->name);
        }
    }
    for (int i = 0; i < W.ninters; i++) {
        Inter *in = &W.inters[i];
        float d = dist2(P.x, P.z, in->pos.x, in->pos.z);
        if (d > in->reach) continue;
        float dx = (in->pos.x - P.x) / (d + 1e-4f), dz = (in->pos.z - P.z) / (d + 1e-4f);
        float dot = dx * fx + dz * fz;
        if (dot < 0.75f) continue;
        float score = d * (2 - dot) + 0.3f;
        if (score < best) {
            best = score;
            target.kind = in->kind; target.ref = in->ref;
            snprintf(target.text, sizeof target.text, "%s", in->label);
        }
    }
}

/* Some VMs (Hyper-V, VirtualBox with mouse integration) only have an absolute pointer, which gives no motion while
 * the mouse is captured. Right-drag looks around with plain cursor positions instead; once used, the apartment stops
 * capturing the mouse so the cursor stays visible. Arrow keys turn too. */
static bool dragging;
static int drag_x, drag_y;
static void set_relative(bool on) { SDL_SetRelativeMouseMode(on && !drag_look ? SDL_TRUE : SDL_FALSE); }

static void open_mode(int m)
{
    back_mode = (mode == M_SIT || mode == M_LIE) ? mode : M_WALK;
    mode = m;
    set_relative(false);
    if (m == M_CHAT) SDL_StartTextInput(); else SDL_StopTextInput();
}

static void close_panel(void)
{
    if (mode == M_CHAT && chat_agent >= 0 && chat_agent < NAG) AG[chat_agent].chatting = false;
    SDL_StopTextInput();
    mode = back_mode;
    back_mode = M_WALK;
    set_relative(true);
}

static int menu_sel;
static VideoFile videos[MAX_VIDEOS];
static int nvideos;

static void enter_pc(void)
{
    mode = M_PC_IN;
    anim_t = 0;
    anim_from = v3(P.x, P.eye, P.z);
    anim_yaw0 = P.yaw; anim_pitch0 = P.pitch;
    set_relative(false);
}

static void interact(void)
{
    switch (target.kind) {
    case I_AGENT: {
        chat_agent = target.ref;
        Agent *a = &AG[chat_agent];
        a->chatting = true;
        task_mode = false;
        input[0] = 0;
        chat_scroll = 0;
        Chat *c = chat_for(a->slug);
        if (!c->n) {
            char hello[300];
            snprintf(hello, sizeof hello, "Hi! I'm %s, the %s. Ask me anything, or press Tab to give me a task.", a->name, a->role);
            chat_add(c, "assistant", hello);
        }
        open_mode(M_CHAT);
        break;
    }
    case I_COUCH: {
        Spot *s = &W.spots[target.ref];
        if (s->occupant >= 0) { toast("%s is sitting there", AG[s->occupant].name); break; }
        sit_seat = target.ref;
        s->occupant = 99;
        mode = M_SIT;
        P.x = s->pos.x; P.z = s->pos.z; P.yaw = 0; P.pitch = -0.05f;
        toast("On the couch: V videos, Space pause, Left/Right seek, Up/Down volume, E stand up");
        break;
    }
    case I_TV:
        nvideos = video_scan(videos, MAX_VIDEOS);
        menu_sel = 0;
        open_mode(M_VIDEOS);
        break;
    case I_PC: enter_pc(); break;
    case I_BED:
        mode = M_LIE;
        P.x = W.master_bed_lie.x; P.z = W.master_bed_lie.z; P.yaw = PI_F; P.pitch = 1.1f;
        toast("Resting. Press E to get up.");
        break;
    case I_SHOP: menu_sel = 0; open_mode(M_SHOP); break;
    case I_FRIDGE: toast("%s", NOW_HOURS < 11 ? "Milk, eggs and leftover pizza." : "You grab a cold drink."); break;
    case I_SINK: toast("%s", target.ref ? "The dishes are done. The crew owes you one." : "Fresh and clean."); break;
    case I_WHITEBOARD: menu_sel = 0; open_mode(M_BOARD); break;
    case I_PIANO: toast("You play a few notes. The crew pretends not to notice."); break;
    case I_ARCADE: toast("PIXEL RUN: new high score! (it's always a high score)"); break;
    default: break;
    }
}

static void stand_up(void)
{
    if (mode == M_SIT) {
        if (sit_seat >= 0 && W.spots[sit_seat].occupant == 99) W.spots[sit_seat].occupant = -1;
        P.z = 12.9f;
        sit_seat = -1;
    } else if (mode == M_LIE) {
        P.x = 3.2f; P.z = 1.9f; P.yaw = PI_F / 2; P.pitch = 0;
    }
    mode = M_WALK;
}

/* ------------------------------------------------------------------ panels */

typedef struct { float x, y, w, h; } UIRow;
static UIRow rows[80];
static int nrows;
static int mouse_x, mouse_y;
static Col C_PANEL = {14, 16, 24, 236}, C_ACCENT = {120, 190, 255, 255}, C_TXT = {235, 238, 245, 255}, C_DIM = {150, 156, 172, 255};

static void panel(float x, float y, float w, float h, const char *title)
{
    rect(x, y, w, h, C_PANEL);
    rect_line(x, y, w, h, rgba(255, 255, 255, 40));
    if (title) text(F_TITLE, x + 20, y + 14, title, C_TXT);
}

static int row_at(int mx, int my)
{
    for (int i = 0; i < nrows; i++) if (mx >= rows[i].x && mx < rows[i].x + rows[i].w && my >= rows[i].y && my < rows[i].y + rows[i].h) return i;
    return -1;
}

/* A scrolling list: draws rows [first, first+visible) and records their rectangles. */
static void list_rows(int n, int *sel, float x, float y, float w, float h, void (*draw_row)(int i, float x, float y, float w, float rh, bool on))
{
    float rh = text_h(F_BODY) * 1.7f;
    int vis = (int)(h / rh);
    if (vis < 1) vis = 1;
    if (*sel >= n) *sel = n - 1;
    if (*sel < 0) *sel = 0;
    int first = *sel - vis / 2;
    if (first > n - vis) first = n - vis;
    if (first < 0) first = 0;
    nrows = 0;
    for (int i = first; i < n && i < first + vis; i++) {
        float ry = y + (i - first) * rh;
        bool on = i == *sel;
        if (on) rect(x, ry, w, rh, rgba(120, 190, 255, 50));
        draw_row(i, x + 12, ry + (rh - text_h(F_BODY)) / 2, w - 24, rh, on);
        if (nrows < 80) { rows[nrows].x = x; rows[nrows].y = ry; rows[nrows].w = w; rows[nrows].h = rh; nrows++; }
    }
    if (n > vis) {
        float bar = h * vis / n;
        rect(x + w - 4, y + (h - bar) * first / (float)(n - vis), 4, bar, rgba(255, 255, 255, 60));
    }
    /* map row index back to item: store first in a static for the click handler */
    static int dummy; (void)dummy;
    rows[79].x = (float)first;   /* the click handler adds this offset */
}

static int row_first(void) { return (int)rows[79].x; }

/* --- the pause menu */
static const char *MENU[] = {"Return to the PC", "Settings", "Quit"};
#define NMENU 3
static const char *QUIT[] = {"Log out", "Sleep", "Hibernate", "Restart", "Shut down", "Back"};
#define NQUIT 6
static bool from_menu;   /* Settings / Quit were opened from the Esc menu: Esc goes back to it */

static void menu_row(int i, float x, float y, float w, float rh, bool on)
{
    (void)w; (void)rh;
    text(F_BODY, x, y, MENU[i], on ? C_ACCENT : C_TXT);
}

static void quit_row(int i, float x, float y, float w, float rh, bool on)
{
    (void)w; (void)rh;
    text(F_BODY, x, y, QUIT[i], on ? C_ACCENT : C_TXT);
}

static void power(const char *what)
{
    save_state(false);
    chats_save();
    char cmd[256];
    if (!strcmp(what, "logout"))
        snprintf(cmd, sizeof cmd, "(dbus-send --session --type=method_call --dest=org.kde.Shutdown /Shutdown org.kde.Shutdown.logout "
                                  "|| loginctl terminate-session \"$XDG_SESSION_ID\") >/dev/null 2>&1 &");
    else snprintf(cmd, sizeof cmd, "systemctl %s >/dev/null 2>&1 &", what);
    if (system(cmd) != 0) toast("Couldn't %s", what);
    if (strcmp(what, "suspend") && strcmp(what, "hibernate")) running = false;
    else close_panel();
}

static void menu_activate(int i)
{
    switch (i) {
    case 0: mode = back_mode = M_WALK; P.x = W.pc_stand.x; P.z = W.pc_stand.z; enter_pc(); break;
    case 1: menu_sel = 0; from_menu = true; mode = M_SETTINGS; break;
    case 2: menu_sel = 0; from_menu = true; mode = M_QUIT; break;
    }
}

static void quit_activate(int i)
{
    switch (i) {
    case 0: power("logout"); break;
    case 1: power("suspend"); break;
    case 2: power("hibernate"); break;
    case 3: power("reboot"); break;
    case 4: power("poweroff"); break;
    default: menu_sel = 2; mode = M_MENU; from_menu = false; break;
    }
}

/* --- the shop */
static int shop_order[MAX_ITEMS], nshop;

static void shop_sort(void)
{
    static const char *kinds[] = {"decor", "extra", "paint", "floor", "couch"};
    nshop = 0;
    for (int k = 0; k < 5; k++)
        for (int i = 0; i < SHOP.nitems; i++)
            if (!strcmp(SHOP.items[i].kind, kinds[k]) && SHOP.items[i].cost > 0) shop_order[nshop++] = i;
}

static void shop_row(int r, float x, float y, float w, float rh, bool on)
{
    (void)rh;
    Item *it = &SHOP.items[shop_order[r]];
    if (strcmp(it->kind, "decor") && strcmp(it->kind, "extra")) {
        float s = text_h(F_BODY) * 0.8f;
        rect(x, y + 2, s, s, it->rgb);
        x += s + 10;
    }
    text(F_BODY, x, y, it->name, on ? C_ACCENT : C_TXT);
    char c[32];
    if (it->owned) snprintf(c, sizeof c, "Owned");
    else snprintf(c, sizeof c, "%d pts", it->cost);
    Col cc = it->owned ? rgb(120, 220, 140) : it->cost <= SHOP.balance ? rgb(255, 214, 110) : C_DIM;
    text(F_BODY, x + w - text_w(F_BODY, c) - 30, y, c, cc);
}

static void shop_buy(int r)
{
    if (r < 0 || r >= nshop) return;
    Item *it = &SHOP.items[shop_order[r]];
    if (it->owned) { toast("You own %s. Use Decorate (C) to place it.", it->name); return; }
    if (!net_online()) { toast("LifeOS isn't running, so the shop is closed"); return; }
    char body[128];
    snprintf(body, sizeof body, "{\"item\":\"%s\"}", it->id);
    net_request("POST", "/api/apartment/buy", body, 0, on_buy, strdup(it->name));
}

/* --- decorate */
static int decor_room;
enum { D_ROOM, D_WALL, D_FLOOR, D_COUCH, D_FIXED };
static int decor_items[MAX_ITEMS], ndecor;

static void decor_collect(void)
{
    ndecor = 0;
    for (int i = 0; i < SHOP.nitems; i++)
        if (SHOP.items[i].owned && (!strcmp(SHOP.items[i].kind, "decor") || !strcmp(SHOP.items[i].kind, "extra"))) decor_items[ndecor++] = i;
}

static void cycle(int *slot, const char *kind, int dir)
{
    int n = SHOP.nitems, i = *slot;
    for (int k = 0; k < n; k++) {
        i = (i + dir + n) % n;
        if (SHOP.items[i].owned && !strcmp(SHOP.items[i].kind, kind)) { *slot = i; return; }
    }
}

static void decor_change(int row, int dir)
{
    switch (row) {
    case D_ROOM: decor_room = (decor_room + dir + NROOMS) % NROOMS; return;
    case D_WALL: cycle(&SHOP.lay.wall[decor_room], "paint", dir); break;
    case D_FLOOR: cycle(&SHOP.lay.floor[decor_room], "floor", dir); break;
    case D_COUCH: cycle(&SHOP.lay.couch, "couch", dir); break;
    default: {
        int i = decor_items[row - D_FIXED];
        if (!strcmp(SHOP.items[i].kind, "decor")) SHOP.lay.decor[i] = !SHOP.lay.decor[i];
        else SHOP.lay.extra[i] = !SHOP.lay.extra[i];
    }
    }
    rebuild_if_changed();
    layout_dirty_t = 0.8f;
}

static void decor_row(int r, float x, float y, float w, float rh, bool on)
{
    (void)rh;
    char v[120] = "";
    const char *label = "";
    Col sw = {0, 0, 0, 0};
    switch (r) {
    case D_ROOM: label = "Room"; snprintf(v, sizeof v, "<  %s  >", ROOM_NAMES[decor_room]); break;
    case D_WALL: label = "Walls"; snprintf(v, sizeof v, "<  %s  >", SHOP.items[SHOP.lay.wall[decor_room]].name); sw = SHOP.items[SHOP.lay.wall[decor_room]].rgb; break;
    case D_FLOOR: label = "Floor"; snprintf(v, sizeof v, "<  %s  >", SHOP.items[SHOP.lay.floor[decor_room]].name); sw = SHOP.items[SHOP.lay.floor[decor_room]].rgb; break;
    case D_COUCH: label = "Couch"; snprintf(v, sizeof v, "<  %s  >", SHOP.items[SHOP.lay.couch].name); sw = SHOP.items[SHOP.lay.couch].rgb; break;
    default: {
        int i = decor_items[r - D_FIXED];
        label = SHOP.items[i].name;
        bool onn = SHOP.lay.decor[i] || SHOP.lay.extra[i];
        snprintf(v, sizeof v, "%s", onn ? "[ shown ]" : "[ hidden ]");
    }
    }
    text(F_BODY, x, y, label, on ? C_ACCENT : C_TXT);
    float vx = x + w * 0.42f;
    if (sw.a) { float s = text_h(F_BODY) * 0.8f; rect(vx, y + 2, s, s, sw); vx += s + 10; }
    text(F_BODY, vx, y, v, C_TXT);
}

/* --- videos */
static void video_row(int r, float x, float y, float w, float rh, bool on)
{
    (void)w; (void)rh;
    if (video_active()) {
        if (r == 0) { text(F_BODY, x, y, "Stop the TV", on ? C_ACCENT : rgb(255, 150, 140)); return; }
        r--;
    }
    text(F_BODY, x, y, videos[r].name, on ? C_ACCENT : C_TXT);
}

static void video_pick(int r)
{
    if (video_active()) {
        if (r == 0) { video_stop(); close_panel(); return; }
        r--;
    }
    if (r < 0 || r >= nvideos) return;
    video_set_volume(CFG.volume);
    video_set_quality(CFG.quality);
    if (video_play(videos[r].path, 0)) toast("Now playing: %s", videos[r].name);
    close_panel();
}

/* --- settings */
#define NSET (14 + NKEYS + 1)
static void apply_quality(void);

static void setting_row(int r, float x, float y, float w, float rh, bool on)
{
    (void)rh;
    static const char *names[NSET] = {"Graphics quality", "Mouse sensitivity", "Invert mouse", "Field of view", "TV volume",
                                      "Show names over the crew", "Show FPS", "Screen (display number)"};
    char v[64];
    static const char *q[3] = {"Low (fastest)", "Medium", "High"};
    static const char *more[6] = {"Mouse look", "Walking speed", "Turn speed (keys)", "Frame rate limit", "Save power when idle", "Reset keys to default"};
    if (r >= 8) {
        const char *name;
        if (r >= 13 && r < 13 + NKEYS) {
            int k = r - 13;
            name = KEY_NAMES[k];
            if (rebinding == k) snprintf(v, sizeof v, "Press a key (Esc cancels)");
            else snprintf(v, sizeof v, "%s", SDL_GetScancodeName(KEYS[k]));
        } else {
            int m = r < 13 ? r - 8 : 5;
            name = more[m];
            if (m == 0) snprintf(v, sizeof v, "%s", drag_look ? "Hold right button + drag" : "Mouse captured");
            else if (m == 1) snprintf(v, sizeof v, "%.0f%%", walk_speed * 100);
            else if (m == 2) snprintf(v, sizeof v, "%.0f%%", turn_speed * 100);
            else if (m == 3) { if (fps_limit) snprintf(v, sizeof v, "%d fps", fps_limit); else snprintf(v, sizeof v, "No limit"); }
            else if (m == 4) snprintf(v, sizeof v, "%s", idle_save ? "On" : "Off");
            else v[0] = 0;
        }
        text(F_BODY, x, y, name, on ? C_ACCENT : C_TXT);
        char b[96];
        if (r >= 13 && r < 13 + NKEYS) snprintf(b, sizeof b, "[ %s ]", v);
        else if (v[0]) snprintf(b, sizeof b, "<  %s  >", v);
        else snprintf(b, sizeof b, "Enter");
        text(F_BODY, x + w * 0.55f, y, b, (rebinding >= 0 && rebinding == r - 13) ? C_ACCENT : C_TXT);
        return;
    }
    switch (r) {
    case 0: snprintf(v, sizeof v, "%s", q[CFG.quality]); break;
    case 1: snprintf(v, sizeof v, "%.1f", CFG.sens); break;
    case 2: snprintf(v, sizeof v, "%s", CFG.invert ? "On" : "Off"); break;
    case 3: snprintf(v, sizeof v, "%.0f", CFG.fov); break;
    case 4: snprintf(v, sizeof v, "%d%%", CFG.volume); break;
    case 5: snprintf(v, sizeof v, "%s", CFG.show_names ? "On" : "Off"); break;
    case 6: snprintf(v, sizeof v, "%s", CFG.show_fps ? "On" : "Off"); break;
    default: snprintf(v, sizeof v, "%d of %d", CFG.display + 1, SDL_GetNumVideoDisplays()); break;
    }
    text(F_BODY, x, y, names[r], on ? C_ACCENT : C_TXT);
    char b[80];
    snprintf(b, sizeof b, "<  %s  >", v);
    text(F_BODY, x + w * 0.55f, y, b, C_TXT);
}

static void move_to_display(void)
{
    int n = SDL_GetNumVideoDisplays();
    if (CFG.display >= n) CFG.display = 0;
    if (windowed) return;
    SDL_SetWindowFullscreen(win, 0);
    SDL_SetWindowPosition(win, SDL_WINDOWPOS_CENTERED_DISPLAY(CFG.display), SDL_WINDOWPOS_CENTERED_DISPLAY(CFG.display));
    SDL_SetWindowFullscreen(win, SDL_WINDOW_FULLSCREEN_DESKTOP);
}

static void setting_change(int r, int dir)
{
    switch (r) {
    case 0: CFG.quality = (CFG.quality + dir + 3) % 3; apply_quality(); break;
    case 1: CFG.sens = clampf(CFG.sens + dir * 0.1f, 0.2f, 4.0f); break;
    case 2: CFG.invert = !CFG.invert; break;
    case 3: CFG.fov = clampf(CFG.fov + dir * 5, 55, 100); break;
    case 4: CFG.volume = (int)clampf((float)(CFG.volume + dir * 10), 0, 100); video_set_volume(CFG.volume); break;
    case 5: CFG.show_names = !CFG.show_names; break;
    case 6: CFG.show_fps = !CFG.show_fps; break;
    case 7: { int n = SDL_GetNumVideoDisplays(); CFG.display = (CFG.display + dir + n) % (n ? n : 1); move_to_display(); break; }
    case 8: drag_look = !drag_look; break;
    case 9: walk_speed = clampf(walk_speed + dir * 0.1f, 0.5f, 2.0f); break;
    case 10: turn_speed = clampf(turn_speed + dir * 0.1f, 0.3f, 3.0f); break;
    case 11: { static const int L[4] = {30, 45, 60, 0}; int i = 0; while (i < 4 && L[i] != fps_limit) i++;
        fps_limit = L[((i < 4 ? i : 2) + dir + 4) % 4]; break; }
    case 12: idle_save = !idle_save; break;
    default:
        if (r >= 13 && r < 13 + NKEYS) { rebinding = r - 13; return; }
        memcpy(KEYS, KEY_DEF, sizeof KEYS); toast("Keys reset"); break;
    }
    save_state(false);
}

/* ------------------------------------------------------------------ render scale (low quality) */

static GLuint scale_tex;
static int scale_tw, scale_th;

static float render_scale(void) { return CFG.quality == 0 ? 0.5f : CFG.quality == 1 ? 0.75f : 1.0f; }

static void apply_quality(void)
{
    if (scale_tex) { glDeleteTextures(1, &scale_tex); scale_tex = 0; }
    video_set_quality(CFG.quality);
}

/* ------------------------------------------------------------------ HUD */

static void draw_labels(int vw, int vh, float sx, float sy)
{
    if (!CFG.show_names) return;
    for (int i = 0; i < NAG; i++) {
        Agent *a = &AG[i];
        float d = dist2(P.x, P.z, a->x, a->z);
        if (d > 11 || !sight_clear(P.x, P.z, a->x, a->z)) continue;
        float head = a->pose == 2 ? a->y + 0.5f : a->pose == 1 ? a->y + 1.05f : a->y + 1.95f;
        float x, y;
        if (!gfx_project(v3(a->x, head, a->z), vw, vh, &x, &y)) continue;
        x *= sx; y *= sy;
        float alpha = clampf(1.4f - d / 11, 0.2f, 1);
        const char *st = npc_status_text(a);
        int w = text_w(F_BODY, a->name), w2 = text_w(F_SMALL, st);
        int bw = (w > w2 ? w : w2) + 18, bh = text_h(F_BODY) + text_h(F_SMALL) + 8;
        rect(x - bw / 2.0f, y - bh, (float)bw, (float)bh, rgba(10, 12, 18, (int)(170 * alpha)));
        rect(x - bw / 2.0f, y - bh, 3, (float)bh, rgba(a->color.r, a->color.g, a->color.b, (int)(255 * alpha)));
        text_center(F_BODY, x, y - bh + 3, a->name, rgba(255, 255, 255, (int)(255 * alpha)));
        text_center(F_SMALL, x, y - text_h(F_SMALL) - 3, st, rgba(190, 200, 220, (int)(255 * alpha)));
        if (a->act == ACT_SLEEP && !a->moving && a->pose == 2) {
            for (int k = 0; k < 3; k++) {
                float ph = fmodf(T * 0.6f + k / 3.0f, 1.0f);
                text(F_BODY, x + 20 + ph * 30, y - bh - 10 - ph * 40, "z", rgba(200, 220, 255, (int)(255 * (1 - ph) * alpha)));
            }
        }
        if (a->bubble_t > 0) {
            int tw = text_w(F_BODY, a->bubble) + 20;
            float by = y - bh - text_h(F_BODY) - 16;
            rect(x - tw / 2.0f, by, (float)tw, text_h(F_BODY) + 8.0f, rgba(250, 250, 250, (int)(230 * alpha)));
            text_center(F_BODY, x, by + 4, a->bubble, rgba(20, 20, 30, (int)(255 * alpha)));
        }
        if (!strcmp(a->state, "asking") || !strcmp(a->state, "waiting"))
            text_center(F_TITLE, x, y - bh - text_h(F_TITLE) - 4, "!", rgb(255, 200, 60));
    }
}

static void draw_chat(void)
{
    if (chat_agent < 0 || chat_agent >= NAG) { mode = M_WALK; return; }
    Agent *a = &AG[chat_agent];
    Chat *c = chat_for(a->slug);
    float w = fminf(WIN_W * 0.42f, 760), x = WIN_W - w - 24, y = 24, h = WIN_H - 48;
    panel(x, y, w, h, NULL);
    rect(x, y, w, 6, a->color);
    char title[160];
    snprintf(title, sizeof title, "%s", a->name);
    text(F_TITLE, x + 20, y + 16, title, C_TXT);
    char sub[300];
    snprintf(sub, sizeof sub, "%s  ·  %s", a->role, npc_status_text(a));
    text(F_SMALL, x + 20, y + 20 + text_h(F_TITLE), sub, C_DIM);
    float top = y + 30 + text_h(F_TITLE) + text_h(F_SMALL);
    float in_h = text_h(F_BODY) * 2.6f, foot = text_h(F_SMALL) + 16;
    float bottom = y + h - in_h - foot - 16;
    /* messages, newest at the bottom */
    int wrap = (int)(w - 80);
    float cy = bottom;
    if (c->waiting) {
        char dots[64];
        snprintf(dots, sizeof dots, "%s is %s%.*s", a->name, task_mode ? "starting the task" : "thinking", (int)(T * 2) % 4, "...");
        cy -= text_h(F_BODY) + 8;
        text(F_BODY, x + 24, cy, dots, C_DIM);
    }
    int skip = chat_scroll;
    for (int i = c->n - 1; i >= 0 && cy > top; i--) {
        if (skip > 0) { skip--; continue; }
        Msg *m = &c->msgs[i];
        bool me = !strcmp(m->role, "user"), sys = !strcmp(m->role, "system");
        int th = text_wrap_h(F_BODY, wrap - 24, m->text);
        float bh = th + 16.0f;
        cy -= bh + 10;
        if (cy < top) break;
        float bx = me ? x + 50 : x + 20;
        Col bg = me ? rgba(60, 110, 200, 220) : sys ? rgba(120, 60, 50, 200) : rgba(40, 44, 58, 230);
        rect(bx, cy, (float)wrap - 10, bh, bg);
        if (!me && !sys) rect(bx, cy, 3, bh, a->color);
        text_wrap(F_BODY, bx + 12, cy + 8, wrap - 24, m->text, C_TXT);
    }
    /* input */
    float iy = y + h - in_h - foot - 6;
    rect(x + 20, iy, w - 40, in_h, rgba(255, 255, 255, 18));
    rect_line(x + 20, iy, w - 40, in_h, task_mode ? rgba(255, 190, 90, 200) : rgba(120, 190, 255, 160));
    const char *mode_lbl = task_mode ? "TASK" : "CHAT";
    text(F_SMALL, x + 30, iy + 4, mode_lbl, task_mode ? rgb(255, 190, 90) : C_ACCENT);
    char shown[2100];
    snprintf(shown, sizeof shown, "%s%s", input, fmodf(T, 1) < 0.5f ? "|" : " ");
    int tw = text_w(F_BODY, shown);
    float maxw = w - 80;
    const char *vis = shown;
    while (tw > maxw && *vis) { vis++; while ((*vis & 0xC0) == 0x80) vis++; tw = text_w(F_BODY, vis); }
    if (!input[0]) text(F_BODY, x + 30, iy + in_h - text_h(F_BODY) - 6, task_mode ? "Describe the task..." : "Say something...", C_DIM);
    else text(F_BODY, x + 30, iy + in_h - text_h(F_BODY) - 6, vis, C_TXT);
    text(F_SMALL, x + 20, y + h - foot, "Enter send  ·  Tab switch Chat/Task  ·  Wheel scroll  ·  Esc close", C_DIM);
}

static void draw_hud(float fps)
{
    time_t now = time(NULL);
    struct tm lt;
    localtime_r(&now, &lt);
    char tbuf[32], dbuf[64];
    strftime(tbuf, sizeof tbuf, "%H:%M", &lt);
    strftime(dbuf, sizeof dbuf, "%A %d %B", &lt);
    int cw = text_w(F_BODY, dbuf) > text_w(F_HUGE, tbuf) ? text_w(F_BODY, dbuf) : text_w(F_HUGE, tbuf);
    rect(14, 10, cw + 30.0f, text_h(F_HUGE) + text_h(F_BODY) + text_h(F_SMALL) + 16.0f, rgba(10, 12, 18, 150));
    text(F_HUGE, 24, 14, tbuf, rgba(255, 255, 255, 230));
    text(F_BODY, 28, 14 + text_h(F_HUGE), dbuf, rgba(220, 225, 235, 220));
    int room = room_at(P.x, P.z);
    if (room >= 0) text(F_SMALL, 28, 18 + text_h(F_HUGE) + text_h(F_BODY), ROOM_NAMES[room], rgba(180, 190, 210, 220));
    char pts[48];
    snprintf(pts, sizeof pts, "%d pts", SHOP.balance);
    int pw = text_w(F_TITLE, pts);
    rect(WIN_W - pw - 50.0f, 16, pw + 32.0f, text_h(F_TITLE) + 10.0f, rgba(10, 12, 18, 160));
    text(F_TITLE, WIN_W - pw - 34.0f, 21, pts, rgb(255, 214, 110));
    if (plus_t > 0) {
        char pl[32];
        snprintf(pl, sizeof pl, "+%d", plus_n);
        text(F_TITLE, WIN_W - pw - 34.0f, 30 + text_h(F_TITLE) + (3 - plus_t) * 10, pl, rgba(140, 255, 160, (int)(255 * plus_t / 3)));
    }
    if (video_active() && mode != M_SIT) {
        char np[200];
        snprintf(np, sizeof np, "TV: %s", video_title());
        text(F_SMALL, WIN_W - text_w(F_SMALL, np) - 24.0f, 30.0f + text_h(F_TITLE) * 2, np, C_DIM);
    }
    if (mode == M_WALK) {
        rect(WIN_W / 2.0f - 2, WIN_H / 2.0f - 2, 4, 4, rgba(255, 255, 255, 200));
        if (target.kind != I_NONE) {
            char s[160];
            snprintf(s, sizeof s, "[%s]  %s", SDL_GetScancodeName(KEYS[K_USE]), target.text);
            int w = text_w(F_BODY, s) + 30;
            rect(WIN_W / 2.0f - w / 2.0f, WIN_H * 0.62f, (float)w, text_h(F_BODY) + 12.0f, rgba(10, 12, 18, 180));
            text_center(F_BODY, WIN_W / 2.0f, WIN_H * 0.62f + 6, s, C_TXT);
        }
    }
    if (mode == M_SIT) {
        char s[300];
        if (video_active()) {
            int p = (int)video_position();
            snprintf(s, sizeof s, "%s  %d:%02d  ·  Space %s  ·  Left/Right seek  ·  Up/Down volume %d%%  ·  V videos  ·  E stand up",
                     video_title(), p / 60, p % 60, video_paused() ? "play" : "pause", CFG.volume);
        } else snprintf(s, sizeof s, "V choose a video  ·  E stand up");
        text_center(F_SMALL, WIN_W / 2.0f, WIN_H - text_h(F_SMALL) - 20.0f, s, C_TXT);
    }
    if (mode == M_LIE) text_center(F_BODY, WIN_W / 2.0f, WIN_H - text_h(F_BODY) - 24.0f, "E  get up", C_TXT);
    /* toasts */
    float ty = WIN_H - 40.0f - (mode == M_SIT ? text_h(F_SMALL) + 20 : 0);
    for (int i = 0; i < 5; i++) {
        if (toasts[i].t <= 0) continue;
        int a = (int)(255 * clampf(toasts[i].t, 0, 1));
        int w = text_w(F_BODY, toasts[i].s) + 28;
        ty -= text_h(F_BODY) + 16;
        rect(24, ty, (float)w, text_h(F_BODY) + 10.0f, rgba(10, 12, 18, a * 200 / 255));
        text(F_BODY, 38, ty + 5, toasts[i].s, rgba(240, 240, 245, a));
    }
    if (ever_online ? offline_for > 15 : offline_for > 6) {
        const char *s = ever_online ? "LifeOS stopped answering: the crew can't talk right now" : "Waiting for LifeOS to start...";
        int w = text_w(F_SMALL, s) + 24;
        rect(WIN_W / 2.0f - w / 2.0f, 14, (float)w, text_h(F_SMALL) + 8.0f, rgba(140, 60, 40, 200));
        text_center(F_SMALL, WIN_W / 2.0f, 18, s, C_TXT);
    }
    if (CFG.show_fps) {
        char f[32];
        snprintf(f, sizeof f, "%.0f fps", fps);
        text(F_SMALL, WIN_W - 80.0f, WIN_H - 30.0f, f, C_DIM);
    }
}

static void draw_panels(void)
{
    float w = fminf(WIN_W * 0.6f, 900), h = fminf(WIN_H * 0.8f, 760), x = (WIN_W - w) / 2, y = (WIN_H - h) / 2;
    float lx = x + 20, ly = y + 30 + text_h(F_TITLE), lw = w - 40, lh = h - (ly - y) - 20 - text_h(F_SMALL) - 10;
    const char *foot = "Up/Down choose  ·  Enter select  ·  Esc back";
    switch (mode) {
    case M_MENU:
        h = text_h(F_BODY) * 1.7f * NMENU + text_h(F_TITLE) + 80; w = fminf(WIN_W * 0.4f, 520);
        x = (WIN_W - w) / 2; y = (WIN_H - h) / 2;
        panel(x, y, w, h, "LifeOS");
        list_rows(NMENU, &menu_sel, x + 20, y + 30 + text_h(F_TITLE), w - 40, h, menu_row);
        foot = "Esc resume  ·  P shop  ·  C decorate  ·  F1 help";
        break;
    case M_QUIT:
        h = text_h(F_BODY) * 1.7f * NQUIT + text_h(F_TITLE) + 80; w = fminf(WIN_W * 0.4f, 520);
        x = (WIN_W - w) / 2; y = (WIN_H - h) / 2;
        panel(x, y, w, h, "Quit");
        list_rows(NQUIT, &menu_sel, x + 20, y + 30 + text_h(F_TITLE), w - 40, h, quit_row);
        foot = "Enter select  ·  Esc back";
        break;
    case M_SHOP: {
        shop_sort();
        panel(x, y, w, h, "Shop");
        char b[200];
        snprintf(b, sizeof b, "%d points to spend", SHOP.balance);
        text(F_TITLE, x + w - text_w(F_TITLE, b) - 20, y + 14, b, rgb(255, 214, 110));
        char e[300];
        snprintf(e, sizeof e, "Earn points by using the crew: 1 per AI call and per 1000 tokens, 2 per message, 10 per task done, 20 per mission. "
                              "So far: %d calls, %dk tokens, %d messages, %d tasks, %d missions.",
                 SHOP.counts[0], SHOP.counts[1], SHOP.counts[2], SHOP.counts[3], SHOP.counts[4]);
        int eh = text_wrap(F_SMALL, lx, ly, (int)lw, e, C_DIM);
        if (!nshop) text(F_BODY, lx, ly + eh + 20, net_online() ? "Loading the catalog..." : "The shop opens when LifeOS is running.", C_DIM);
        list_rows(nshop, &menu_sel, lx, ly + eh + 12, lw, lh - eh - 12, shop_row);
        if (menu_sel >= 0 && menu_sel < nshop && SHOP.items[shop_order[menu_sel]].desc[0])
            text(F_SMALL, lx, y + h - text_h(F_SMALL) * 2 - 18, SHOP.items[shop_order[menu_sel]].desc, C_ACCENT);
        foot = "Enter buy  ·  Esc back  ·  C decorate";
        break;
    }
    case M_DECOR:
        decor_collect();
        panel(x, y, w, h, "Decorate");
        list_rows(D_FIXED + ndecor, &menu_sel, lx, ly, lw, lh, decor_row);
        foot = "Left/Right change  ·  Enter show/hide  ·  Buy more in the shop (P)  ·  Esc back";
        break;
    case M_VIDEOS: {
        panel(x, y, w, h, "Videos");
        int n = nvideos + (video_active() ? 1 : 0);
        if (!n) text_wrap(F_BODY, lx, ly, (int)lw, "No videos found. Put video files in your Videos folder (or on a USB stick) and open this again.", C_DIM);
        list_rows(n, &menu_sel, lx, ly, lw, lh, video_row);
        break;
    }
    case M_SETTINGS:
        panel(x, y, w, h, "Settings");
        list_rows(NSET, &menu_sel, lx, ly, lw, lh, setting_row);
        foot = "Left/Right change  ·  Esc back";
        break;
    case M_BOARD: {
        panel(x, y, w, h, "Whiteboard");
        float yy = ly;
        text(F_TITLE, lx, yy, "Missions running", C_ACCENT);
        yy += text_h(F_TITLE) + 6;
        if (!nboard) { text(F_BODY, lx, yy, "Nothing right now. Talk to an agent and press Tab to give it a task.", C_DIM); yy += text_h(F_BODY) + 4; }
        for (int i = 0; i < nboard; i++) yy += text_wrap(F_BODY, lx, yy, (int)lw, board_lines[i], C_TXT) + 6;
        yy += 16;
        text(F_TITLE, lx, yy, "Your open tasks", rgb(255, 170, 150));
        yy += text_h(F_TITLE) + 6;
        if (!ntasks) text(F_BODY, lx, yy, "No open tasks.", C_DIM);
        for (int i = 0; i < ntasks; i++) yy += text_wrap(F_BODY, lx, yy, (int)lw, task_lines[i], C_TXT) + 6;
        nrows = 0;
        foot = "Esc close";
        break;
    }
    case M_HELP: {
        panel(x, y, w, h, "How the apartment works");
        const char *help =
            "WASD or Up/Down: walk  ·  Shift: run  ·  Mouse, Left/Right arrows or right-drag: look  ·  PgUp/PgDn: look up/down\n"
            "(Change keys, mouse speed and look mode in Esc > Settings)\n"
            "E: talk to a crew member, sit on the couch, use the TV, lie in bed, use the PC\n"
            "Esc: menu (PC, settings, quit)  ·  P: shop  ·  C: decorate  ·  F1: this help  ·  F2: graphics quality\n\n"
            "The crew lives here. They follow the real clock: they sleep in the AI bedrooms at night, eat in the kitchen "
            "at meal times, and walk to the work room when you give them a task (talk to one, press Tab, describe the task).\n\n"
            "Using LifeOS earns points. Spend them in the shop (the kiosk in the hallway, or P) on paint, floors, furniture "
            "and extras, then place them with Decorate (C).\n\n"
            "The computer in the master bedroom is your real desktop (KDE Plasma). Press E at the desk to use it and "
            "Shift+F4 to come back here.\n\n"
            "Got a second monitor? It shows your tasks, the crew and the time; arrange those widgets like any Plasma widget.";
        text_wrap(F_BODY, lx, ly, (int)lw, help, C_TXT);
        nrows = 0;
        foot = "Esc close";
        break;
    }
    default: return;
    }
    text(F_SMALL, x + 20, y + h - text_h(F_SMALL) - 12, foot, C_DIM);
}

/* ------------------------------------------------------------------ input */

static int list_len(void)
{
    switch (mode) {
    case M_MENU: return NMENU;
    case M_QUIT: return NQUIT;
    case M_SHOP: return nshop;
    case M_DECOR: return D_FIXED + ndecor;
    case M_VIDEOS: return nvideos + (video_active() ? 1 : 0);
    case M_SETTINGS: return NSET;
    }
    return 0;
}

static void list_activate(int i)
{
    switch (mode) {
    case M_MENU: menu_activate(i); break;
    case M_QUIT: quit_activate(i); break;
    case M_SHOP: shop_buy(i); break;
    case M_DECOR: if (i >= D_FIXED) decor_change(i, 1); else decor_change(i, 1); break;
    case M_VIDEOS: video_pick(i); break;
    case M_SETTINGS: setting_change(i, 1); break;
    }
}

static void list_side(int dir)
{
    if (mode == M_DECOR) decor_change(menu_sel, dir);
    else if (mode == M_SETTINGS) setting_change(menu_sel, dir);
}

static void utf8_pop(char *s)
{
    size_t n = strlen(s);
    while (n > 0) { n--; if ((s[n] & 0xC0) != 0x80) break; }
    s[n] = 0;
}

static bool is_panel(int m) { return m == M_QUIT || m == M_MENU || m == M_SHOP || m == M_DECOR || m == M_VIDEOS || m == M_SETTINGS || m == M_HELP || m == M_BOARD; }

static void handle_key(SDL_Keysym k)
{
    SDL_Keycode key = k.sym;
    if (mode == M_PC_IN || mode == M_PC_OUT) return;
    if (rebinding >= 0) {
        if (key != SDLK_ESCAPE) {
            for (int i = 0; i < NKEYS; i++) if (i != rebinding && KEYS[i] == k.scancode) KEYS[i] = KEYS[rebinding];  /* swap */
            KEYS[rebinding] = k.scancode;
            save_state(false);
        }
        rebinding = -1;
        return;
    }
    if (mode == M_CHAT) {
        if (key == SDLK_ESCAPE) close_panel();
        else if (key == SDLK_RETURN || key == SDLK_KP_ENTER) chat_send();
        else if (key == SDLK_BACKSPACE) utf8_pop(input);
        else if (key == SDLK_TAB) task_mode = !task_mode;
        else if (key == SDLK_PAGEUP) chat_scroll++;
        else if (key == SDLK_PAGEDOWN && chat_scroll > 0) chat_scroll--;
        else if (key == SDLK_v && (k.mod & KMOD_CTRL)) {
            char *clip = SDL_GetClipboardText();
            if (clip) { strncat(input, clip, sizeof input - strlen(input) - 1); SDL_free(clip); }
        }
        return;
    }
    if (is_panel(mode)) {
        int n = list_len();
        if (key == SDLK_ESCAPE) {
            if (from_menu && (mode == M_SETTINGS || mode == M_QUIT)) { menu_sel = mode == M_SETTINGS ? 1 : 2; mode = M_MENU; from_menu = false; }
            else { from_menu = false; close_panel(); }
        }
        else if (key == SDLK_UP || key == SDLK_w) { if (n) menu_sel = (menu_sel - 1 + n) % n; }
        else if (key == SDLK_DOWN || key == SDLK_s) { if (n) menu_sel = (menu_sel + 1) % n; }
        else if (key == SDLK_LEFT || key == SDLK_a) list_side(-1);
        else if (key == SDLK_RIGHT || key == SDLK_d) list_side(1);
        else if (key == SDLK_RETURN || key == SDLK_KP_ENTER || key == SDLK_SPACE) { if (n) list_activate(menu_sel); }
        else if (key == SDLK_c && mode == M_SHOP) { menu_sel = 0; mode = M_DECOR; }
        else if (key == SDLK_p && mode == M_DECOR) { menu_sel = 0; mode = M_SHOP; }
        return;
    }
    /* walking, sitting, lying */
    if (k.scancode == KEYS[K_USE]) {
        if (mode == M_SIT || mode == M_LIE) stand_up();
        else if (target.kind != I_NONE) interact();
        return;
    }
    switch (key) {
    case SDLK_ESCAPE: menu_sel = 0; from_menu = false; open_mode(M_MENU); break;
    case SDLK_p: menu_sel = 0; open_mode(M_SHOP); break;
    case SDLK_c: menu_sel = 0; open_mode(M_DECOR); break;
    case SDLK_F1: open_mode(M_HELP); break;
    case SDLK_F2: CFG.quality = (CFG.quality + 1) % 3; apply_quality(); save_state(false);
        toast("Graphics: %s", CFG.quality == 0 ? "Low" : CFG.quality == 1 ? "Medium" : "High"); break;
    case SDLK_v: if (mode == M_SIT || dist2(P.x, P.z, 5, 13) < 5) { nvideos = video_scan(videos, MAX_VIDEOS); menu_sel = 0; open_mode(M_VIDEOS); } break;
    case SDLK_SPACE: if (mode == M_SIT) video_pause_toggle(); break;
    case SDLK_LEFT: if (mode == M_SIT) video_seek(-10); break;
    case SDLK_RIGHT: if (mode == M_SIT) video_seek(10); break;
    case SDLK_UP: if (mode == M_SIT) { CFG.volume = CFG.volume + 10 > 100 ? 100 : CFG.volume + 10; video_set_volume(CFG.volume); video_seek(0); } break;
    case SDLK_DOWN: if (mode == M_SIT) { CFG.volume = CFG.volume - 10 < 0 ? 0 : CFG.volume - 10; video_set_volume(CFG.volume); video_seek(0); } break;
    default: break;
    }
}

static void handle_click(int mx, int my)
{
    if (!is_panel(mode)) return;
    int r = row_at(mx, my);
    if (r < 0) return;
    int i = row_first() + r;
    menu_sel = i;
    list_activate(i);
}

/* ------------------------------------------------------------------ movement and camera */

static void update_player(float dt)
{
    if (mode == M_WALK) {
        const Uint8 *ks = SDL_GetKeyboardState(NULL);
        float f = 0, s = 0;
        if (ks[KEYS[K_FWD]] || ks[SDL_SCANCODE_UP]) f += 1;
        if (ks[KEYS[K_BACK]] || ks[SDL_SCANCODE_DOWN]) f -= 1;
        if (ks[KEYS[K_RIGHT]]) s += 1;
        if (ks[KEYS[K_LEFT]]) s -= 1;
        if (ks[KEYS[K_TURNL]]) P.yaw += 2.2f * turn_speed * dt;
        if (ks[KEYS[K_TURNR]]) P.yaw -= 2.2f * turn_speed * dt;
        if (ks[KEYS[K_LOOKU]]) P.pitch = clampf(P.pitch + 1.4f * turn_speed * dt, -1.45f, 1.45f);
        if (ks[KEYS[K_LOOKD]]) P.pitch = clampf(P.pitch - 1.4f * turn_speed * dt, -1.45f, 1.45f);
        float sp = (ks[KEYS[K_RUN]] || (KEYS[K_RUN] == SDL_SCANCODE_LSHIFT && ks[SDL_SCANCODE_RSHIFT]) ? 4.2f : 2.4f) * walk_speed;
        float len = sqrtf(f * f + s * s);
        if (len > 0) {
            f /= len; s /= len;
            float fx = sinf(P.yaw), fz = cosf(P.yaw), rx = -cosf(P.yaw), rz = sinf(P.yaw);
            float dx = (fx * f + rx * s) * sp * dt, dz = (fz * f + rz * s) * sp * dt;
            /* small steps so fast movement never tunnels through a wall */
            int steps = (int)(sqrtf(dx * dx + dz * dz) / 0.1f) + 1;
            for (int i = 0; i < steps; i++) {
                P.x += dx / steps; P.z += dz / steps;
                collide_circle(&P.x, &P.z, 0.28f);
                for (int a = 0; a < NAG; a++) {
                    float d = dist2(P.x, P.z, AG[a].x, AG[a].z);
                    if (d < 0.5f && d > 0.001f && AG[a].pose != 2 && AG[a].y < 0.3f) {
                        P.x = AG[a].x + (P.x - AG[a].x) / d * 0.5f;
                        P.z = AG[a].z + (P.z - AG[a].z) / d * 0.5f;
                    }
                }
            }
        }
        P.eye = lerpf(P.eye, 1.62f, fminf(1, dt * 8));
    } else if (mode == M_SIT || (back_mode == M_SIT && is_panel(mode))) {
        P.eye = lerpf(P.eye, 1.12f, fminf(1, dt * 8));
    } else if (mode == M_LIE || (back_mode == M_LIE && is_panel(mode))) {
        P.eye = lerpf(P.eye, W.master_bed_lie.y, fminf(1, dt * 8));
    }
}

static void look(int dx, int dy)
{
    if (!(mode == M_WALK || mode == M_SIT || mode == M_LIE)) return;
    float k = 0.0025f * CFG.sens;
    P.yaw -= dx * k;
    P.pitch += (CFG.invert ? dy : -dy) * k;
    P.pitch = clampf(P.pitch, -1.45f, 1.45f);
    if (mode == M_SIT) P.yaw = clampf(wrap_angle(P.yaw), -1.6f, 1.6f);
}

static float smooth(float t) { t = clampf(t, 0, 1); return t * t * (3 - 2 * t); }

static void camera_for_frame(V3 *eye, float *yaw, float *pitch, float *fade, float dt)
{
    *eye = v3(P.x, P.eye, P.z); *yaw = P.yaw; *pitch = P.pitch; *fade = 0;
    if (mode == M_PC_IN) {
        anim_t += dt;
        float t = smooth(anim_t / 0.8f);
        *eye = v3(lerpf(anim_from.x, W.pc_view_eye.x, t), lerpf(anim_from.y, W.pc_view_eye.y, t), lerpf(anim_from.z, W.pc_view_eye.z, t));
        *yaw = anim_yaw0 + wrap_angle(PI_F - anim_yaw0) * t;
        *pitch = lerpf(anim_pitch0, 0, t);
        *fade = clampf((anim_t - 0.7f) / 0.3f, 0, 1);
        if (anim_t > 1.05f) {
            P.x = W.pc_stand.x; P.z = W.pc_stand.z; P.yaw = PI_F; P.pitch = 0;
            save_state(true);
            chats_save();
            exit_code = EXIT_TO_PC;
            running = false;
        }
    } else if (mode == M_PC_OUT) {
        anim_t += dt;
        float t = smooth((anim_t - 0.2f) / 0.9f);
        *eye = v3(lerpf(W.pc_view_eye.x, W.pc_stand.x, t), lerpf(W.pc_view_eye.y, 1.62f, t), lerpf(W.pc_view_eye.z, W.pc_stand.z, t));
        *yaw = PI_F; *pitch = 0;
        *fade = 1 - clampf(anim_t / 0.4f, 0, 1);
        if (anim_t > 1.1f) {
            mode = M_WALK; P.x = W.pc_stand.x; P.z = W.pc_stand.z; P.yaw = PI_F; P.pitch = 0; P.eye = 1.62f;
            set_relative(true);
        }
    }
}

/* ------------------------------------------------------------------ main */

static void now_hours(void)
{
    const char *fake = getenv("LIFEOS_APARTMENT_HOUR");   /* for testing schedules */
    if (fake && *fake) { NOW_HOURS = (float)atof(fake); return; }
    time_t now = time(NULL);
    struct tm lt;
    localtime_r(&now, &lt);
    NOW_HOURS = lt.tm_hour + lt.tm_min / 60.0f + lt.tm_sec / 3600.0f;
}

static void load_shop_cache(void)
{
    char path[600];
    snprintf(path, sizeof path, "%s/apartment-shop.json", state_dir());
    char *b = read_file(path);
    if (!b) return;
    J *j = json_parse(b);
    if (j) { apply_shop(j); json_free(j); }
    free(b);
}

int main(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--from-pc")) from_pc = true;
        else if (!strcmp(argv[i], "--windowed")) windowed = true;
        else if (!strcmp(argv[i], "--help")) {
            puts("lifeos-apartment [--from-pc] [--windowed]\nThe LifeOS 3D apartment desktop. Exit code 10 = the user went to the PC (KDE desktop).");
            return 0;
        }
    }
    if (getenv("LIFEOS_APARTMENT_WINDOWED")) windowed = true;
    srand((unsigned)time(NULL));
    SDL_SetHint(SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR, "0");
    SDL_SetHint(SDL_HINT_MOUSE_RELATIVE_MODE_WARP, "0");
    SDL_SetHint(SDL_HINT_APP_NAME, "LifeOS Apartment");
    SDL_SetHint(SDL_HINT_GRAB_KEYBOARD, "1");
    SDL_SetHint("SDL_VIDEO_WAYLAND_WMCLASS", "lifeos-apartment");
    SDL_SetHint("SDL_VIDEO_X11_WMCLASS", "lifeos-apartment");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) < 0) { fprintf(stderr, "apartment: SDL_Init: %s\n", SDL_GetError()); return 1; }
    load_state();
    SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
    SDL_GL_SetAttribute(SDL_GL_DEPTH_SIZE, 24);
    Uint32 flags = SDL_WINDOW_OPENGL | SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI;
    if (!windowed) flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;
    int disp = CFG.display < SDL_GetNumVideoDisplays() ? CFG.display : 0;
    win = SDL_CreateWindow("LifeOS Apartment", SDL_WINDOWPOS_CENTERED_DISPLAY(disp), SDL_WINDOWPOS_CENTERED_DISPLAY(disp), 1280, 720, flags);
    if (!win) {
        SDL_GL_SetAttribute(SDL_GL_DEPTH_SIZE, 16);
        win = SDL_CreateWindow("LifeOS Apartment", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 1280, 720, flags);
    }
    if (!win) { fprintf(stderr, "apartment: window: %s\n", SDL_GetError()); return 1; }
    /* The apartment is the desktop: keep the Meta key, Alt+Tab and other desktop shortcuts away from KDE while it
     * runs (KDE is only reached through the PC). Uses the Wayland keyboard-shortcuts-inhibit protocol. */
    if (!windowed) SDL_SetWindowKeyboardGrab(win, SDL_TRUE);
    SDL_GLContext gl = SDL_GL_CreateContext(win);
    if (!gl) { fprintf(stderr, "apartment: OpenGL: %s\n", SDL_GetError()); return 1; }
    SDL_GL_SetSwapInterval(1);
    SDL_GL_GetDrawableSize(win, &WIN_W, &WIN_H);
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    fprintf(stderr, "apartment: OpenGL %s on %s\n", glGetString(GL_VERSION), renderer ? renderer : "?");
    bool software = renderer && (strstr(renderer, "llvmpipe") || strstr(renderer, "softpipe") || strstr(renderer, "Software") ||
                                 strstr(renderer, "SVGA3D") || strstr(renderer, "VirtualBox"));
    if (!quality_saved) CFG.quality = software ? 0 : 2;
    gfx_init(WIN_H);
    net_init();
    shop_defaults();
    load_shop_cache();
    world_build();
    layout_json(last_layout_sig, sizeof last_layout_sig);
    now_hours();
    npc_default_roster();
    npc_place_all();
    chats_load();
    video_set_volume(CFG.volume);
    video_set_quality(CFG.quality);
    SDL_StopTextInput();
    if (from_pc) { mode = M_PC_OUT; anim_t = 0; P.x = W.pc_stand.x; P.z = W.pc_stand.z; }
    else set_relative(true);
    if (!from_pc && room_at(P.x, P.z) < 0) { P.x = W.spawn.x; P.z = W.spawn.z; }
    toast("Welcome home. Walk up to a crew member and press E to talk. F1 for help.");

    Uint64 freq = SDL_GetPerformanceFrequency(), last = SDL_GetPerformanceCounter();
    float fps = 60, fps_acc = 0; int fps_n = 0;
    while (running) {
        Uint64 nowc = SDL_GetPerformanceCounter();
        float dt = (float)(nowc - last) / (float)freq;
        last = nowc;
        if (dt > 0.1f) dt = 0.1f;
        T += dt;
        fps_acc += dt; fps_n++;
        if (fps_acc > 0.5f) { fps = fps_n / fps_acc; fps_acc = 0; fps_n = 0; }
        now_hours();
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_KEYDOWN || ev.type == SDL_MOUSEMOTION || ev.type == SDL_MOUSEBUTTONDOWN || ev.type == SDL_MOUSEWHEEL)
                last_input_ms = SDL_GetTicks();
            switch (ev.type) {
            case SDL_QUIT: running = false; break;
            case SDL_KEYDOWN: handle_key(ev.key.keysym); break;
            case SDL_TEXTINPUT:
                if (mode == M_CHAT && strlen(input) + strlen(ev.text.text) < sizeof input - 1) strcat(input, ev.text.text);
                break;
            case SDL_MOUSEMOTION:
                mouse_x = ev.motion.x; mouse_y = ev.motion.y;
                if (SDL_GetRelativeMouseMode()) look(ev.motion.xrel, ev.motion.yrel);
                else if (dragging) { look((ev.motion.x - drag_x) * 2, (ev.motion.y - drag_y) * 2); drag_x = ev.motion.x; drag_y = ev.motion.y; }
                else if (is_panel(mode)) {
                    int ww, wh; SDL_GetWindowSize(win, &ww, &wh);
                    int r = row_at(mouse_x * WIN_W / (ww ? ww : 1), mouse_y * WIN_H / (wh ? wh : 1));
                    if (r >= 0) menu_sel = row_first() + r;
                }
                break;
            case SDL_MOUSEBUTTONUP:
                if (ev.button.button == SDL_BUTTON_RIGHT) dragging = false;
                break;
            case SDL_MOUSEBUTTONDOWN:
                if (ev.button.button == SDL_BUTTON_RIGHT && (mode == M_WALK || mode == M_SIT || mode == M_LIE)) {
                    drag_look = dragging = true;
                    set_relative(false);
                    drag_x = ev.button.x; drag_y = ev.button.y;
                } else if (ev.button.button == SDL_BUTTON_LEFT) {
                    if (is_panel(mode)) {
                        int ww, wh; SDL_GetWindowSize(win, &ww, &wh);
                        handle_click(ev.button.x * WIN_W / (ww ? ww : 1), ev.button.y * WIN_H / (wh ? wh : 1));
                    } else if (mode == M_WALK) {
                        if (!SDL_GetRelativeMouseMode() && !drag_look) set_relative(true);
                        else if (target.kind != I_NONE) interact();
                    }
                }
                break;
            case SDL_MOUSEWHEEL:
                if (mode == M_CHAT) { chat_scroll += ev.wheel.y; if (chat_scroll < 0) chat_scroll = 0; }
                else if (is_panel(mode)) { int n = list_len(); if (n) menu_sel = (int)clampf((float)(menu_sel - ev.wheel.y), 0, (float)(n - 1)); }
                break;
            case SDL_WINDOWEVENT:
                if (ev.window.event == SDL_WINDOWEVENT_SIZE_CHANGED) {
                    SDL_GL_GetDrawableSize(win, &WIN_W, &WIN_H);
                    gfx_resize(WIN_H);
                    apply_quality();
                } else if (ev.window.event == SDL_WINDOWEVENT_FOCUS_LOST) { has_focus = false; if (mode == M_WALK) set_relative(false); }
                else if (ev.window.event == SDL_WINDOWEVENT_FOCUS_GAINED) { has_focus = true; last_input_ms = SDL_GetTicks(); if (mode == M_WALK || mode == M_SIT || mode == M_LIE) set_relative(true); }
                break;
            }
        }
        net_pump();
        poll_backend(dt);
        video_poll();
        update_player(dt);
        npc_update(dt, P.x, P.z);
        find_target();
        for (int i = 0; i < 5; i++) if (toasts[i].t > 0) toasts[i].t -= dt;
        if (plus_t > 0) plus_t -= dt;
        gfx_frame_begin();

        /* ---- 3D */
        V3 eye; float yaw, pitch, fade;
        camera_for_frame(&eye, &yaw, &pitch, &fade, dt);
        float sc = render_scale();
        int vw = (int)(WIN_W * sc), vh = (int)(WIN_H * sc);
        if (vw < 160) vw = 160;
        if (vh < 90) vh = 90;
        float skyk;
        Col sky = sky_color(&skyk);
        glViewport(0, 0, vw, vh);
        glClearColor(sky.r / 255.f, sky.g / 255.f, sky.b / 255.f, 1);
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        glEnable(GL_DEPTH_TEST);
        gfx_perspective(CFG.fov, (float)WIN_W / WIN_H, 0.05f, 60);
        gfx_camera(eye, yaw, pitch);
        glEnable(GL_LIGHTING);
        gfx_brightness(mode == M_LIE && NOW_HOURS > 21 ? 0.7f : skyk);
        world_draw_static();
        npc_draw(T);
        world_draw_dynamic(T, skyk);
        glDisable(GL_LIGHTING);

        /* ---- scale the low-res 3D image up to the window */
        if (sc < 0.99f) {
            if (!scale_tex) {
                scale_tw = 1; while (scale_tw < vw) scale_tw <<= 1;
                scale_th = 1; while (scale_th < vh) scale_th <<= 1;
                glGenTextures(1, &scale_tex);
                glBindTexture(GL_TEXTURE_2D, scale_tex);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, scale_tw, scale_th, 0, GL_RGB, GL_UNSIGNED_BYTE, NULL);
            }
            glBindTexture(GL_TEXTURE_2D, scale_tex);
            glCopyTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 0, 0, vw, vh);
            glViewport(0, 0, WIN_W, WIN_H);
            glDisable(GL_DEPTH_TEST);
            gfx_ortho(WIN_W, WIN_H);
            glEnable(GL_TEXTURE_2D);
            glColor4f(1, 1, 1, 1);
            float u = (float)vw / scale_tw, v = (float)vh / scale_th;
            glBegin(GL_QUADS);
            glTexCoord2f(0, v); glVertex2f(0, 0);
            glTexCoord2f(u, v); glVertex2f((float)WIN_W, 0);
            glTexCoord2f(u, 0); glVertex2f((float)WIN_W, (float)WIN_H);
            glTexCoord2f(0, 0); glVertex2f(0, (float)WIN_H);
            glEnd();
            glDisable(GL_TEXTURE_2D);
        } else {
            glViewport(0, 0, WIN_W, WIN_H);
        }

        /* ---- 2D */
        glDisable(GL_DEPTH_TEST);
        gfx_ortho(WIN_W, WIN_H);
        glEnable(GL_BLEND);
        if (mode != M_PC_IN && mode != M_PC_OUT) {
            draw_labels(vw, vh, (float)WIN_W / vw, (float)WIN_H / vh);
            draw_hud(fps);
            if (mode == M_CHAT) draw_chat();
            if (is_panel(mode)) { rect(0, 0, (float)WIN_W, (float)WIN_H, rgba(0, 0, 0, 90)); draw_panels(); }
        }
        if (fade > 0) rect(0, 0, (float)WIN_W, (float)WIN_H, rgba(0, 0, 0, (int)(255 * fade)));
        glDisable(GL_BLEND);
        SDL_GL_SwapWindow(win);
        {
            /* Frame pacing keeps the GPU (and CPU) mostly idle: the chosen limit (Low quality: at most 30),
             * and with "Save power when idle" a few frames a second in the background or after a minute
             * without input (unless the TV is playing). */
            int cap = fps_limit;
            if (CFG.quality == 0 && (cap == 0 || cap > 30)) cap = 30;
            if (idle_save) {
                bool tv = video_active() && !video_paused();
                if (!has_focus) cap = 5;
                else if (!tv && SDL_GetTicks() - last_input_ms > 60000) cap = 10;
            }
            if (cap > 0) {
                float ms = 1000.0f * (SDL_GetPerformanceCounter() - nowc) / freq, want = 1000.0f / cap;
                if (ms < want) SDL_Delay((Uint32)(want - ms));
            }
        }
    }
    if (exit_code != EXIT_TO_PC) save_state(false);
    chats_save();
    video_stop();
    net_shutdown();
    gfx_shutdown();
    SDL_GL_DeleteContext(gl);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return exit_code;
}
