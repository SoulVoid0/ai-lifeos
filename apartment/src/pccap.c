/* The live KDE desktop on the PC monitor in the master bedroom.
 *
 * The apartment covers the whole screen, so a plain screenshot would only show the apartment. Instead, a worker
 * thread asks KWin for the window list (a tiny KWin script that calls back over D-Bus with the stacking order and
 * geometry of every window), captures the desktop, the panels and each open window separately through KWin's
 * ScreenShot2 D-Bus interface, and paints them into a small picture of the screen. The main thread uploads that
 * picture as a texture a couple of times a second, only while someone is in the master bedroom.
 *
 * The same thread also keeps a list of the open windows for the main thread, captures single windows for the
 * tablet (LSIW Control) and the TV (Steam), and runs small KWin scripts that move those windows onto the tablet's
 * screen or bring them in front of the apartment.
 *
 * ScreenShot2 is restricted: KWin only answers programs whose .desktop file lists it in
 * X-KDE-DBUS-Restricted-Interfaces (see /usr/share/applications/lifeos-apartment-bin.desktop). Anything that goes
 * wrong leaves the old drawn picture on the monitor.
 */
#define _GNU_SOURCE
#include "app.h"
#include <strings.h>
#include <dbus/dbus.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdlib.h>
#include <unistd.h>
#include <time.h>

#define CAN_W 768
#define CAN_H_MAX 512
#define MAX_WINS 12
#define MAX_LIST 48

typedef WinInfo Win;
#define FEED_W 1024

static pthread_t thread;
static bool started;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static volatile int want, want_list, want_feed[NFEEDS], stop_flag, slow;
static volatile int state;            /* captures: 0 trying, 1 working, -1 unavailable */
static volatile int list_state;       /* the window list: 0 trying, 1 working, -1 unavailable */
static char feed_cls[NFEEDS][64], feed_cap[NFEEDS][64];
static char hide_id[64];              /* a window left out of the PC picture (the parked tablet window) */
typedef struct { uint8_t *ready; int w, h; bool fresh; GLuint tex; int tex_w, tex_h; Uint32 at; } Feed;
static Feed feeds[NFEEDS];
static Win shared_wins[MAX_LIST];
static int shared_n = -1;
static Uint32 shared_at;
#define MAX_CMDS 8
static char *cmds[MAX_CMDS];
static int ncmds;
static int cmd_seq;
static uint8_t *ready;                /* RGBA, published picture */
static int ready_w, ready_h;
static bool fresh;
static GLuint tex;
static int tex_w, tex_h;
static char script_path[600];

/* filled by the KWin script's call back */
static char *wins_json;
static bool wins_fresh;

static DBusHandlerResult on_message(DBusConnection *c, DBusMessage *m, void *user)
{
    (void)user;
    if (!dbus_message_is_method_call(m, "org.lifeos.Apartment", "Windows")) return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    const char *s = NULL;
    if (dbus_message_get_args(m, NULL, DBUS_TYPE_STRING, &s, DBUS_TYPE_INVALID) && s) {
        free(wins_json);
        wins_json = strdup(s);
        wins_fresh = true;
    }
    DBusMessage *r = dbus_message_new_method_return(m);
    if (r) { dbus_connection_send(c, r, NULL); dbus_message_unref(r); }
    return DBUS_HANDLER_RESULT_HANDLED;
}

static const char *SCRIPT =
    "(function () {\n"
    "  var list = workspace.stackingOrder, scr = null, out = [], act = workspace.activeWindow;\n"
    "  for (var i = 0; i < list.length; i++) {\n"
    "    var c = String(list[i].resourceClass || '');\n"
    "    if (c.indexOf('lifeos-apartment') === 0 && list[i].output) scr = list[i].output.geometry;\n"
    "  }\n"
    "  if (!scr) scr = workspace.screens.length ? workspace.screens[0].geometry : {x: 0, y: 0, width: 1280, height: 720};\n"
    "  var cur = workspace.currentDesktop;\n"
    "  for (var i = 0; i < list.length; i++) {\n"
    "    var w = list[i];\n"
    "    if (w.deleted) continue;\n"
    "    var kind = w.desktopWindow ? 1 : w.dock ? 2 : (w.normalWindow || w.dialog) ? 0 : -1;\n"
    "    if (kind < 0) continue;\n"
    "    var other = 0;\n"
    "    if (kind == 0 && !w.onAllDesktops && cur) {\n"
    "      other = 1;\n"
    "      for (var d = 0; d < w.desktops.length; d++) if (w.desktops[d].id == cur.id) other = 0;\n"
    "    }\n"
    "    var g = w.frameGeometry;\n"
    "    out.push({id: w.internalId.toString(), c: String(w.resourceClass || ''), t: String(w.caption || '').substring(0, 120),\n"
    "              k: kind, x: g.x, y: g.y, w: g.width, h: g.height, m: w.minimized ? 1 : 0, a: w === act ? 1 : 0,\n"
    "              b: w.keepBelow ? 1 : 0, o: other});\n"
    "  }\n"
    "  callDBus('org.lifeos.Apartment', '/org/lifeos/Apartment', 'org.lifeos.Apartment', 'Windows',\n"
    "          JSON.stringify({sx: scr.x, sy: scr.y, sw: scr.width, sh: scr.height, wins: out}));\n"
    "})();\n";

static DBusMessage *call(DBusConnection *c, DBusMessage *m, int timeout_ms, DBusError *err)
{
    DBusMessage *r = dbus_connection_send_with_reply_and_block(c, m, timeout_ms, err);
    dbus_message_unref(m);
    return r;
}

/* Loads and runs a KWin script file, then unloads it. Every run gets a new plugin name: KWin unloads scripts later
 * (not during the call), and loading a name that is still there hands back the old script, which doesn't run again. */
static bool run_script(DBusConnection *c, const char *path, const char *base)
{
    static unsigned seq;
    char name[96];
    snprintf(name, sizeof name, "%s-%d-%u", base, (int)getpid(), seq++);
    const char *plugin = name;
    DBusError err;
    dbus_error_init(&err);
    DBusMessage *m = dbus_message_new_method_call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript");
    dbus_message_append_args(m, DBUS_TYPE_STRING, &path, DBUS_TYPE_STRING, &plugin, DBUS_TYPE_INVALID);
    DBusMessage *r = call(c, m, 3000, &err);
    if (!r) { dbus_error_free(&err); return false; }
    dbus_int32_t id = -1;
    dbus_message_get_args(r, NULL, DBUS_TYPE_INT32, &id, DBUS_TYPE_INVALID);
    dbus_message_unref(r);
    if (id < 0) return false;
    char obj[64];
    snprintf(obj, sizeof obj, "/Scripting/Script%d", (int)id);
    m = dbus_message_new_method_call("org.kde.KWin", obj, "org.kde.kwin.Script", "run");
    r = call(c, m, 3000, &err);
    if (r) dbus_message_unref(r);
    dbus_error_free(&err);
    /* KWin reads and runs the file a moment later, so a script is unloaded only after a few newer ones ran */
    static char ring[8][96];
    static int ri;
    if (ring[ri][0]) {
        const char *old = ring[ri];
        m = dbus_message_new_method_call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript");
        dbus_message_append_args(m, DBUS_TYPE_STRING, &old, DBUS_TYPE_INVALID);
        r = call(c, m, 3000, &err);
        if (r) dbus_message_unref(r);
        dbus_error_free(&err);
    }
    snprintf(ring[ri], sizeof ring[ri], "%s", name);
    ri = (ri + 1) % 8;
    return true;
}

/* Runs the window-list script once; the answer arrives as a "Windows" call. */
static bool ask_window_list(DBusConnection *c) { return run_script(c, script_path, "lifeos-pc-windows"); }

/* The queued window-management scripts from the main thread. */
static void run_commands(DBusConnection *c)
{
    for (;;) {
        pthread_mutex_lock(&mu);
        char *js = NULL;
        if (ncmds) { js = cmds[0]; memmove(cmds, cmds + 1, sizeof(char *) * (size_t)(--ncmds)); }
        pthread_mutex_unlock(&mu);
        if (!js) return;
        const char *run = getenv("XDG_RUNTIME_DIR");
        char path[600];
        snprintf(path, sizeof path, "%s/lifeos-apartment-cmd-%d.js", run && *run ? run : "/tmp", cmd_seq % 8);
        char plugin[48];
        snprintf(plugin, sizeof plugin, "lifeos-apartment-cmd");
        cmd_seq++;
        FILE *f = fopen(path, "w");
        if (f) {
            fprintf(f, "(function () {\n%s\n})();\n", js);
            fclose(f);
            run_script(c, path, plugin);
        }
        free(js);
    }
}

static bool cls_match(const char *cls, const char *want)
{
    if (!want[0]) return false;
    if (want[0] == '=') return !strcasecmp(cls, want + 1);
    return strcasestr(cls, want) != NULL;
}

/* The window shown by a feed: the topmost window of its class, preferring one whose caption has feed_cap. */
static const Win *feed_window(const Win *w, int n, int f)
{
    const Win *best = NULL;
    for (int i = n - 1; i >= 0; i--) {
        if (w[i].kind != 0 || !cls_match(w[i].cls, feed_cls[f])) continue;
        if (feed_cap[f][0] && strcasestr(w[i].cap, feed_cap[f])) return &w[i];
        if (!best && !w[i].minimized) best = &w[i];
    }
    return best;
}

static void dict_bool(DBusMessageIter *dict, const char *key, dbus_bool_t v)
{
    DBusMessageIter e, var;
    dbus_message_iter_open_container(dict, DBUS_TYPE_DICT_ENTRY, NULL, &e);
    dbus_message_iter_append_basic(&e, DBUS_TYPE_STRING, &key);
    dbus_message_iter_open_container(&e, DBUS_TYPE_VARIANT, "b", &var);
    dbus_message_iter_append_basic(&var, DBUS_TYPE_BOOLEAN, &v);
    dbus_message_iter_close_container(&e, &var);
    dbus_message_iter_close_container(dict, &e);
}

typedef struct { uint8_t *px; int w, h, stride; bool bgra; } Img;

/* CaptureWindow: the image comes through a pipe after the reply. Returns false (and sets *denied) on errors. */
static bool capture(DBusConnection *c, const char *id, Img *out, bool *denied, bool decoration)
{
    int fds[2];
    if (pipe2(fds, O_CLOEXEC) < 0) return false;
    DBusMessage *m = dbus_message_new_method_call("org.kde.KWin.ScreenShot2", "/org/kde/KWin/ScreenShot2", "org.kde.KWin.ScreenShot2", "CaptureWindow");
    DBusMessageIter it, dict;
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_STRING, &id);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &dict);
    dict_bool(&dict, "include-decoration", decoration ? TRUE : FALSE);
    dict_bool(&dict, "include-shadow", FALSE);
    dict_bool(&dict, "include-cursor", FALSE);
    dict_bool(&dict, "native-resolution", FALSE);
    dbus_message_iter_close_container(&it, &dict);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_UNIX_FD, &fds[1]);
    DBusError err;
    dbus_error_init(&err);
    DBusMessage *r = dbus_connection_send_with_reply_and_block(c, m, 3000, &err);
    dbus_message_unref(m);
    close(fds[1]);
    if (!r) {
        if (err.name && (strstr(err.name, "AccessDenied") || strstr(err.name, "UnknownMethod") || strstr(err.name, "ServiceUnknown") || strstr(err.name, "UnknownObject")))
            *denied = true;
        if (err.message) fprintf(stderr, "apartment: PC screen capture: %s\n", err.message);
        dbus_error_free(&err);
        close(fds[0]);
        return false;
    }
    unsigned w = 0, h = 0, stride = 0, format = 0;
    DBusMessageIter ri, arr;
    if (dbus_message_iter_init(r, &ri) && dbus_message_iter_get_arg_type(&ri) == DBUS_TYPE_ARRAY) {
        dbus_message_iter_recurse(&ri, &arr);
        while (dbus_message_iter_get_arg_type(&arr) == DBUS_TYPE_DICT_ENTRY) {
            DBusMessageIter e, v;
            const char *k = NULL;
            dbus_message_iter_recurse(&arr, &e);
            dbus_message_iter_get_basic(&e, &k);
            dbus_message_iter_next(&e);
            dbus_message_iter_recurse(&e, &v);
            if (dbus_message_iter_get_arg_type(&v) == DBUS_TYPE_UINT32 && k) {
                dbus_uint32_t u;
                dbus_message_iter_get_basic(&v, &u);
                if (!strcmp(k, "width")) w = u;
                else if (!strcmp(k, "height")) h = u;
                else if (!strcmp(k, "stride")) stride = u;
                else if (!strcmp(k, "format")) format = u;
            }
            dbus_message_iter_next(&arr);
        }
    }
    dbus_message_unref(r);
    /* QImage formats: 4 RGB32, 5 ARGB32, 6 ARGB32 premultiplied (bytes B,G,R,A); 16 RGBX8888, 17/18 RGBA8888 (R,G,B,A) */
    bool bgra = format == 4 || format == 5 || format == 6, rgbaf = format == 16 || format == 17 || format == 18;
    if (!w || !h || w > 16384 || h > 16384 || stride < w * 4 || (!bgra && !rgbaf)) { close(fds[0]); return false; }
    size_t need = (size_t)stride * h, got = 0;
    uint8_t *px = malloc(need);
    while (px && got < need) {
        struct pollfd p = {fds[0], POLLIN, 0};
        if (poll(&p, 1, 3000) <= 0) break;
        ssize_t n = read(fds[0], px + got, need - got);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        got += (size_t)n;
    }
    close(fds[0]);
    if (!px || got < need) { free(px); return false; }
    if (format == 4 || format == 16) for (size_t i = 3; i < need; i += 4) px[i] = 255;
    out->px = px; out->w = (int)w; out->h = (int)h; out->stride = (int)stride; out->bgra = bgra;
    return true;
}

static Img desk;   /* the last picture of the desktop (wallpaper) that wasn't black */

static bool dark(const Img *im)
{
    long sum = 0, n = 0;
    for (int y = 0; y < im->h; y += im->h / 16 + 1)
        for (int x = 0; x < im->w; x += im->w / 16 + 1) {
            const uint8_t *p = im->px + (size_t)y * im->stride + (size_t)x * 4;
            sum += p[0] + p[1] + p[2];
            n++;
        }
    return n && sum / n < 6;
}

/* Paints an image into the canvas rectangle (premultiplied "over", 2x2 samples per pixel). */
static void paint(uint8_t *can, int cw, int ch, const Img *im, float dx, float dy, float dw, float dh)
{
    int x0 = (int)floorf(dx), y0 = (int)floorf(dy), x1 = (int)ceilf(dx + dw), y1 = (int)ceilf(dy + dh);
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > cw) x1 = cw;
    if (y1 > ch) y1 = ch;
    int ri = im->bgra ? 2 : 0, bi = im->bgra ? 0 : 2;
    for (int y = y0; y < y1; y++) {
        for (int x = x0; x < x1; x++) {
            int acc[4] = {0, 0, 0, 0};
            for (int s = 0; s < 4; s++) {
                float fx = ((x + 0.25f + 0.5f * (s & 1)) - dx) / dw, fy = ((y + 0.25f + 0.5f * (s >> 1)) - dy) / dh;
                int sx = (int)(fx * im->w), sy = (int)(fy * im->h);
                if (sx < 0) sx = 0;
                if (sy < 0) sy = 0;
                if (sx >= im->w) sx = im->w - 1;
                if (sy >= im->h) sy = im->h - 1;
                const uint8_t *p = im->px + (size_t)sy * im->stride + (size_t)sx * 4;
                acc[0] += p[ri]; acc[1] += p[1]; acc[2] += p[bi]; acc[3] += p[3];
            }
            int a = acc[3] / 4;
            if (!a) continue;
            uint8_t *d = can + ((size_t)y * cw + x) * 4;
            for (int k = 0; k < 3; k++) {
                int v = acc[k] / 4 + d[k] * (255 - a) / 255;
                d[k] = (uint8_t)(v > 255 ? 255 : v);
            }
            d[3] = 255;
        }
    }
}

/* LIFEOS_PCCAP_DEBUG=1: log what is captured and keep the latest pictures in $XDG_RUNTIME_DIR/pccap-*.ppm */
static bool debug_on(void)
{
    static int on = -1;
    if (on < 0) {
        const char *run = getenv("XDG_RUNTIME_DIR");
        char flag[600];
        snprintf(flag, sizeof flag, "%s/pccap-debug", run && *run ? run : "/tmp");
        on = getenv("LIFEOS_PCCAP_DEBUG") != NULL || access(flag, F_OK) == 0;
    }
    return on;
}

static void debug_dump(const char *name, const uint8_t *rgba, int w, int h)
{
    if (!debug_on()) return;
    const char *run = getenv("XDG_RUNTIME_DIR");
    char path[600];
    snprintf(path, sizeof path, "%s/pccap-%s.ppm", run && *run ? run : "/tmp", name);
    FILE *f = fopen(path, "wb");
    if (!f) return;
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    for (int i = 0; i < w * h; i++) fwrite(rgba + i * 4, 1, 3, f);
    fclose(f);
}

/* Scales a window picture into a feed buffer (at most FEED_W wide) and publishes it. */
static void publish_feed(int f, const Img *im)
{
    int cw = im->w > FEED_W ? FEED_W : im->w, ch = (int)((float)cw * im->h / im->w);
    if (ch < 1) ch = 1;
    uint8_t *can = calloc((size_t)cw * ch, 4);
    if (!can) return;
    paint(can, cw, ch, im, 0, 0, (float)cw, (float)ch);
    debug_dump(f == FEED_TABLET ? "tablet" : "tv", can, cw, ch);
    pthread_mutex_lock(&mu);
    free(feeds[f].ready);
    feeds[f].ready = can; feeds[f].w = cw; feeds[f].h = ch; feeds[f].fresh = true; feeds[f].at = SDL_GetTicks();
    pthread_mutex_unlock(&mu);
}

static bool any_feed(void)
{
    for (int f = 0; f < NFEEDS; f++) if (want_feed[f]) return true;
    return false;
}

static void *worker(void *arg)
{
    (void)arg;
    DBusError err;
    dbus_error_init(&err);
    DBusConnection *c = dbus_bus_get_private(DBUS_BUS_SESSION, &err);
    if (!c) { dbus_error_free(&err); state = -1; list_state = -1; return NULL; }
    dbus_connection_set_exit_on_disconnect(c, FALSE);
    if (dbus_bus_request_name(c, "org.lifeos.Apartment", DBUS_NAME_FLAG_DO_NOT_QUEUE, &err) != DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER) {
        fprintf(stderr, "apartment: D-Bus name org.lifeos.Apartment is taken: %s\n", err.message ? err.message : "");
        dbus_error_free(&err);
        dbus_connection_close(c); dbus_connection_unref(c);
        state = -1; list_state = -1;
        return NULL;
    }
    dbus_connection_add_filter(c, on_message, NULL, NULL);
    int failures = 0, cap_failures = 0;
    for (;;) {
        pthread_mutex_lock(&mu);
        while (!want && !want_list && !any_feed() && !ncmds && !stop_flag) pthread_cond_wait(&cv, &mu);
        pthread_mutex_unlock(&mu);
        run_commands(c);
        if (stop_flag) break;
        if (!want && !want_list && !any_feed()) continue;
        Uint32 t0 = SDL_GetTicks();
        /* An answer that came in late (a busy machine) is still used: wins_fresh is only cleared once used. */
        dbus_connection_read_write_dispatch(c, 0);
        bool ok = wins_fresh || ask_window_list(c);
        Uint32 asked = SDL_GetTicks();
        while (ok && !wins_fresh && SDL_GetTicks() - asked < 5000) dbus_connection_read_write_dispatch(c, 100);
        if (!ok || !wins_fresh) {
            fprintf(stderr, "apartment: no window list from KWin (%s, try %d)\n", ok ? "no answer" : "script not run", failures + 1);
            /* keep trying, slowly: the tablet and the TV use their drawn pictures meanwhile */
            if (++failures > 4) list_state = -1;
            for (int k = 0; k < (failures > 4 ? 80 : 10) && !stop_flag; k++) SDL_Delay(100);
            continue;
        }
        failures = 0;
        if (list_state != 1) fprintf(stderr, "apartment: KWin window list works\n");
        list_state = 1;
        wins_fresh = false;
        J *j = json_parse(wins_json);
        int sx = (int)jnum(j, "sx", 0), sy = (int)jnum(j, "sy", 0), sw = (int)jnum(j, "sw", 0), sh = (int)jnum(j, "sh", 0);
        J *list = jget(j, "wins");
        static Win all[MAX_LIST];
        int na = 0;
        for (int i = 0; list && list->t == J_ARR && i < list->count && na < MAX_LIST; i++) {
            J *o = jat(list, i);
            Win *w = &all[na];
            memset(w, 0, sizeof *w);
            snprintf(w->id, sizeof w->id, "%s", jstr(o, "id", ""));
            snprintf(w->cls, sizeof w->cls, "%s", jstr(o, "c", ""));
            snprintf(w->cap, sizeof w->cap, "%s", jstr(o, "t", ""));
            w->x = (int)jnum(o, "x", 0); w->y = (int)jnum(o, "y", 0); w->w = (int)jnum(o, "w", 0); w->h = (int)jnum(o, "h", 0);
            w->kind = (int)jnum(o, "k", 0);
            w->minimized = jnum(o, "m", 0) != 0; w->active = jnum(o, "a", 0) != 0; w->below = jnum(o, "b", 0) != 0;
            w->other_desktop = jnum(o, "o", 0) != 0;
            if (w->id[0]) na++;
        }
        json_free(j);
        pthread_mutex_lock(&mu);
        memcpy(shared_wins, all, sizeof(Win) * (size_t)na);
        shared_n = na; shared_at = SDL_GetTicks();
        pthread_mutex_unlock(&mu);
        bool denied = false;

        /* the PC monitor: the desktop as it would look without the apartment */
        if (want && state >= 0 && sw > 0 && sh > 0) {
            Win wins[MAX_WINS];
            int n = 0;
            for (int i = na - 1; i >= 0 && n < MAX_WINS; i--) {   /* the topmost MAX_WINS, painted bottom-up */
                const Win *w = &all[i];
                if (!strncmp(w->cls, "lifeos-apartment", 16) || w->minimized || w->other_desktop) continue;
                if (hide_id[0] && !strcmp(w->id, hide_id)) continue;
                if (w->w <= 0 || w->h <= 0) continue;
                if (w->x >= sx + sw || w->y >= sy + sh || w->x + w->w <= sx || w->y + w->h <= sy) continue;
                wins[n++] = *w;
            }
            int cw = CAN_W, ch = (int)((float)CAN_W * sh / sw);
            if (ch > CAN_H_MAX) ch = CAN_H_MAX;
            if (ch < 64) ch = 64;
            uint8_t *can = calloc((size_t)cw * ch, 4);
            for (int i = 0; can && i < cw * ch; i++) { can[i * 4] = 28; can[i * 4 + 1] = 36; can[i * 4 + 2] = 56; can[i * 4 + 3] = 255; }
            int painted = 0;
            for (int i = n - 1; i >= 0 && can && !stop_flag; i--) {
                Img im;
                if (!capture(c, wins[i].id, &im, &denied, true)) { if (denied) break; continue; }
                if (wins[i].kind == 1) {
                    /* Plasma stops drawing the wallpaper while the apartment covers it, and KWin then hands back a
                     * black picture: keep the last good one. */
                    if (dark(&im)) {
                        if (desk.px && desk.w == im.w && desk.h == im.h) { free(im.px); im = desk; im.px = NULL; }
                        else { free(im.px); continue; }
                    } else {
                        free(desk.px);
                        desk = im;
                        desk.px = malloc((size_t)im.stride * im.h);
                        if (desk.px) memcpy(desk.px, im.px, (size_t)im.stride * im.h);
                    }
                }
                float k = (float)cw / sw, kh = (float)ch / sh;
                const Img *use = im.px ? &im : &desk;
                paint(can, cw, ch, use, (wins[i].x - sx) * k, (wins[i].y - sy) * kh, wins[i].w * k, wins[i].h * kh);
                free(im.px);
                painted++;
            }
            if (debug_on()) {
                fprintf(stderr, "pccap: %d windows listed, %d on the PC, %d painted\n", na, n, painted);
                for (int i = 0; i < n; i++) fprintf(stderr, "pccap:   %s %s k%d %dx%d+%d+%d '%s'\n", wins[i].id, wins[i].cls, wins[i].kind, wins[i].w, wins[i].h, wins[i].x, wins[i].y, wins[i].cap);
                if (can) debug_dump("pc", can, cw, ch);
            }
            if (denied) { free(can); state = -1; fprintf(stderr, "apartment: KWin doesn't allow screen captures; the PC shows a picture\n"); }
            else if (painted || !n) {
                cap_failures = 0;
                state = 1;
                pthread_mutex_lock(&mu);
                free(ready);
                ready = can; ready_w = cw; ready_h = ch; fresh = true;
                pthread_mutex_unlock(&mu);
            } else { free(can); if (++cap_failures > 8) state = -1; }
        }
        /* single windows: the tablet and the TV */
        for (int f = 0; f < NFEEDS && state >= 0 && !denied; f++) {
            if (!want_feed[f]) continue;
            const Win *w = feed_window(all, na, f);
            if (!w) continue;
            Img im;
            if (capture(c, w->id, &im, &denied, false)) { publish_feed(f, &im); free(im.px); state = state ? state : 1; }
            else if (denied) state = -1;
        }
        Uint32 spent = SDL_GetTicks() - t0, gap = slow ? 1500 : any_feed() ? 250 : 600;
        if (spent < gap) {
            pthread_mutex_lock(&mu);
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            long ns = ts.tv_nsec + (long)(gap - spent) * 1000000L;
            ts.tv_sec += ns / 1000000000L; ts.tv_nsec = ns % 1000000000L;
            if (!ncmds && !stop_flag) pthread_cond_timedwait(&cv, &mu, &ts);
            pthread_mutex_unlock(&mu);
        }
    }
    dbus_connection_close(c);
    dbus_connection_unref(c);
    return NULL;
}

static bool start_worker(void)
{
    if (started) return true;
    if (list_state < 0) return false;
    if (!getenv("WAYLAND_DISPLAY") || !getenv("DBUS_SESSION_BUS_ADDRESS")) { state = -1; list_state = -1; return false; }
    const char *run = getenv("XDG_RUNTIME_DIR");
    snprintf(script_path, sizeof script_path, "%s/lifeos-pc-windows.js", run && *run ? run : "/tmp");
    FILE *f = fopen(script_path, "w");
    if (!f) { state = -1; list_state = -1; return false; }
    fputs(SCRIPT, f);
    fclose(f);
    started = pthread_create(&thread, NULL, worker, NULL) == 0;
    if (!started) { state = -1; list_state = -1; }
    return started;
}

static void wake(void)
{
    pthread_mutex_lock(&mu);
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
}

void pccap_set(bool on, bool low_power)
{
    slow = low_power;
    if (on && state >= 0) start_worker();
    pthread_mutex_lock(&mu);
    want = on && state >= 0;
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
}

void pccap_watch(bool on)
{
    if (on) start_worker();
    if (want_list != on) { want_list = on; wake(); }
}

void pccap_feed(int f, const char *cls, const char *caption_pref, bool on)
{
    if (f < 0 || f >= NFEEDS) return;
    pthread_mutex_lock(&mu);
    snprintf(feed_cls[f], sizeof feed_cls[f], "%s", cls ? cls : "");
    snprintf(feed_cap[f], sizeof feed_cap[f], "%s", caption_pref ? caption_pref : "");
    pthread_mutex_unlock(&mu);
    if (on) start_worker();
    if (want_feed[f] != on) { want_feed[f] = on; wake(); }
}

void pccap_hide_from_pc(const char *id)
{
    pthread_mutex_lock(&mu);
    snprintf(hide_id, sizeof hide_id, "%s", id ? id : "");
    pthread_mutex_unlock(&mu);
}

void pccap_kwin(const char *js)
{
    if (!start_worker()) return;
    pthread_mutex_lock(&mu);
    if (ncmds == MAX_CMDS) { free(cmds[0]); memmove(cmds, cmds + 1, sizeof(char *) * (MAX_CMDS - 1)); ncmds--; }
    cmds[ncmds++] = strdup(js);
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
}

bool pccap_kwin_ok(void) { return list_state >= 0 && getenv("WAYLAND_DISPLAY"); }

int pccap_windows(WinInfo *out, int max, Uint32 *age_ms)
{
    pthread_mutex_lock(&mu);
    int n = shared_n;
    if (n > max) n = max;
    if (n > 0) memcpy(out, shared_wins, sizeof(WinInfo) * (size_t)n);
    if (age_ms) *age_ms = shared_n >= 0 ? SDL_GetTicks() - shared_at : 0xffffffffu;
    pthread_mutex_unlock(&mu);
    return n;
}

bool pccap_failed(void) { return state < 0; }

static void upload(GLuint *tex, int *tex_w, int *tex_h, uint8_t *px, int w, int h)
{
    int pw = 1, ph = 1;
    while (pw < w) pw <<= 1;
    while (ph < h) ph <<= 1;
    if (!*tex || pw != *tex_w || ph != *tex_h) {
        if (!*tex) glGenTextures(1, tex);
        glBindTexture(GL_TEXTURE_2D, *tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, pw, ph, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
        *tex_w = pw; *tex_h = ph;
    }
    glBindTexture(GL_TEXTURE_2D, *tex);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, px);
}

GLuint pccap_texture(float *u, float *v)
{
    if (state < 0) return 0;
    pthread_mutex_lock(&mu);
    if (fresh && ready) { upload(&tex, &tex_w, &tex_h, ready, ready_w, ready_h); fresh = false; }
    float uu = tex_w ? (float)ready_w / tex_w : 0, vv = tex_h ? (float)ready_h / tex_h : 0;
    pthread_mutex_unlock(&mu);
    if (!tex || state != 1) return 0;
    *u = uu; *v = vv;
    return tex;
}

GLuint pccap_feed_texture(int f, float *u, float *v, Uint32 *age_ms)
{
    if (f < 0 || f >= NFEEDS) return 0;
    Feed *d = &feeds[f];
    pthread_mutex_lock(&mu);
    if (d->fresh && d->ready) { upload(&d->tex, &d->tex_w, &d->tex_h, d->ready, d->w, d->h); d->fresh = false; }
    float uu = d->tex_w ? (float)d->w / d->tex_w : 0, vv = d->tex_h ? (float)d->h / d->tex_h : 0;
    Uint32 at = d->at;
    pthread_mutex_unlock(&mu);
    if (!d->tex) return 0;
    *u = uu; *v = vv;
    if (age_ms) *age_ms = SDL_GetTicks() - at;
    return d->tex;
}

void pccap_shutdown(void)
{
    if (!started) return;
    pthread_mutex_lock(&mu);
    stop_flag = 1;
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
    pthread_join(thread, NULL);
    started = false;
}
