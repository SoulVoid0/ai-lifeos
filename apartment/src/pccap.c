/* The live KDE desktop on the PC monitor in the master bedroom.
 *
 * The apartment covers the whole screen, so a plain screenshot would only show the apartment. Instead, a worker
 * thread asks KWin for the window list (a tiny KWin script that calls back over D-Bus with the stacking order and
 * geometry of every window), captures the desktop, the panels and each open window separately through KWin's
 * ScreenShot2 D-Bus interface, and paints them into a small picture of the screen. The main thread uploads that
 * picture as a texture a couple of times a second, only while someone is in the master bedroom.
 *
 * ScreenShot2 is restricted: KWin only answers programs whose .desktop file lists it in
 * X-KDE-DBUS-Restricted-Interfaces (see /usr/share/applications/lifeos-apartment-bin.desktop). Anything that goes
 * wrong leaves the old drawn picture on the monitor.
 */
#include "app.h"
#include <dbus/dbus.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdlib.h>
#include <unistd.h>

#define CAN_W 768
#define CAN_H_MAX 512
#define MAX_WINS 12

typedef struct { char id[64]; int x, y, w, h, kind; } Win;   /* kind: 0 window, 1 desktop, 2 panel */

static pthread_t thread;
static bool started;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static volatile int want, stop_flag, slow;
static volatile int state;            /* 0 trying, 1 working, -1 unavailable */
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
    "  var list = workspace.stackingOrder, scr = null, out = [];\n"
    "  for (var i = 0; i < list.length; i++) {\n"
    "    var c = String(list[i].resourceClass || '');\n"
    "    if (c.indexOf('lifeos-apartment') === 0 && list[i].output) scr = list[i].output.geometry;\n"
    "  }\n"
    "  if (!scr) scr = workspace.screens.length ? workspace.screens[0].geometry : {x: 0, y: 0, width: 1280, height: 720};\n"
    "  var cur = workspace.currentDesktop;\n"
    "  for (var i = 0; i < list.length; i++) {\n"
    "    var w = list[i], cls = String(w.resourceClass || '');\n"
    "    if (cls.indexOf('lifeos-apartment') === 0 || w.minimized || w.deleted) continue;\n"
    "    var kind = w.desktopWindow ? 1 : w.dock ? 2 : (w.normalWindow || w.dialog) ? 0 : -1;\n"
    "    if (kind < 0) continue;\n"
    "    if (kind == 0 && !w.onAllDesktops && cur) {\n"
    "      var here = false;\n"
    "      for (var d = 0; d < w.desktops.length; d++) if (w.desktops[d].id == cur.id) here = true;\n"
    "      if (!here) continue;\n"
    "    }\n"
    "    var g = w.frameGeometry;\n"
    "    out.push({id: w.internalId.toString(), k: kind, x: g.x, y: g.y, w: g.width, h: g.height});\n"
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

/* Runs the window-list script once; the answer arrives as a "Windows" call. */
static bool ask_window_list(DBusConnection *c)
{
    DBusError err;
    dbus_error_init(&err);
    const char *plugin = "lifeos-pc-windows";
    DBusMessage *m = dbus_message_new_method_call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript");
    dbus_message_append_args(m, DBUS_TYPE_STRING, &plugin, DBUS_TYPE_INVALID);
    DBusMessage *r = call(c, m, 2000, &err);
    if (r) dbus_message_unref(r);
    dbus_error_free(&err);
    const char *path = script_path;
    m = dbus_message_new_method_call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript");
    dbus_message_append_args(m, DBUS_TYPE_STRING, &path, DBUS_TYPE_STRING, &plugin, DBUS_TYPE_INVALID);
    r = call(c, m, 2000, &err);
    if (!r) { dbus_error_free(&err); return false; }
    dbus_int32_t id = -1;
    dbus_message_get_args(r, NULL, DBUS_TYPE_INT32, &id, DBUS_TYPE_INVALID);
    dbus_message_unref(r);
    if (id < 0) return false;
    char obj[64];
    snprintf(obj, sizeof obj, "/Scripting/Script%d", (int)id);
    m = dbus_message_new_method_call("org.kde.KWin", obj, "org.kde.kwin.Script", "run");
    r = call(c, m, 2000, &err);
    if (r) dbus_message_unref(r);
    dbus_error_free(&err);
    return true;
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
static bool capture(DBusConnection *c, const char *id, Img *out, bool *denied)
{
    int fds[2];
    if (pipe2(fds, O_CLOEXEC) < 0) return false;
    DBusMessage *m = dbus_message_new_method_call("org.kde.KWin.ScreenShot2", "/org/kde/KWin/ScreenShot2", "org.kde.KWin.ScreenShot2", "CaptureWindow");
    DBusMessageIter it, dict;
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_STRING, &id);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &dict);
    dict_bool(&dict, "include-decoration", TRUE);
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
    /* QImage formats: 4 RGB32, 5 ARGB32, 6 ARGB32 premultiplied (bytes B,G,R,A); 17/18/19 RGBX/RGBA8888 (R,G,B,A) */
    bool bgra = format == 4 || format == 5 || format == 6, rgbaf = format == 17 || format == 18 || format == 19;
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
    if (format == 4 || format == 17) for (size_t i = 3; i < need; i += 4) px[i] = 255;
    out->px = px; out->w = (int)w; out->h = (int)h; out->stride = (int)stride; out->bgra = bgra;
    return true;
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

static void *worker(void *arg)
{
    (void)arg;
    DBusError err;
    dbus_error_init(&err);
    DBusConnection *c = dbus_bus_get_private(DBUS_BUS_SESSION, &err);
    if (!c) { dbus_error_free(&err); state = -1; return NULL; }
    dbus_connection_set_exit_on_disconnect(c, FALSE);
    if (dbus_bus_request_name(c, "org.lifeos.Apartment", DBUS_NAME_FLAG_DO_NOT_QUEUE, &err) != DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER) {
        dbus_error_free(&err);
        dbus_connection_close(c); dbus_connection_unref(c);
        state = -1;
        return NULL;
    }
    dbus_connection_add_filter(c, on_message, NULL, NULL);
    int failures = 0;
    while (!stop_flag) {
        pthread_mutex_lock(&mu);
        while (!want && !stop_flag) pthread_cond_wait(&cv, &mu);
        pthread_mutex_unlock(&mu);
        if (stop_flag) break;
        Uint32 t0 = SDL_GetTicks();
        wins_fresh = false;
        if (!ask_window_list(c)) { if (++failures > 5) { state = -1; break; } SDL_Delay(2000); continue; }
        while (!wins_fresh && SDL_GetTicks() - t0 < 2000) dbus_connection_read_write_dispatch(c, 100);
        if (!wins_fresh) { if (++failures > 5) { state = -1; break; } continue; }
        J *j = json_parse(wins_json);
        int sx = (int)jnum(j, "sx", 0), sy = (int)jnum(j, "sy", 0), sw = (int)jnum(j, "sw", 0), sh = (int)jnum(j, "sh", 0);
        J *list = jget(j, "wins");
        if (sw <= 0 || sh <= 0 || !list || list->t != J_ARR) { json_free(j); failures++; continue; }
        Win wins[MAX_WINS];
        int n = 0;
        /* keep the topmost MAX_WINS: walk from the top of the stack, then paint bottom-up */
        for (int i = list->count - 1; i >= 0 && n < MAX_WINS; i--) {
            J *o = jat(list, i);
            Win *w = &wins[n];
            snprintf(w->id, sizeof w->id, "%s", jstr(o, "id", ""));
            w->x = (int)jnum(o, "x", 0); w->y = (int)jnum(o, "y", 0); w->w = (int)jnum(o, "w", 0); w->h = (int)jnum(o, "h", 0);
            w->kind = (int)jnum(o, "k", 0);
            if (!w->id[0] || w->w <= 0 || w->h <= 0) continue;
            if (w->x >= sx + sw || w->y >= sy + sh || w->x + w->w <= sx || w->y + w->h <= sy) continue;
            n++;
        }
        json_free(j);
        int cw = CAN_W, ch = (int)((float)CAN_W * sh / sw);
        if (ch > CAN_H_MAX) ch = CAN_H_MAX;
        if (ch < 64) ch = 64;
        uint8_t *can = calloc((size_t)cw * ch, 4);
        for (int i = 0; can && i < cw * ch; i++) { can[i * 4] = 28; can[i * 4 + 1] = 36; can[i * 4 + 2] = 56; can[i * 4 + 3] = 255; }
        bool denied = false;
        int painted = 0;
        for (int i = n - 1; i >= 0 && can && !stop_flag; i--) {
            Img im;
            if (!capture(c, wins[i].id, &im, &denied)) { if (denied) break; continue; }
            float k = (float)cw / sw, kh = (float)ch / sh;
            paint(can, cw, ch, &im, (wins[i].x - sx) * k, (wins[i].y - sy) * kh, wins[i].w * k, wins[i].h * kh);
            free(im.px);
            painted++;
        }
        if (denied) { free(can); state = -1; fprintf(stderr, "apartment: KWin doesn't allow screen captures; the PC shows a picture\n"); break; }
        if (painted || !n) {
            failures = 0;
            state = 1;
            pthread_mutex_lock(&mu);
            free(ready);
            ready = can; ready_w = cw; ready_h = ch; fresh = true;
            pthread_mutex_unlock(&mu);
        } else { free(can); if (++failures > 8) { state = -1; break; } }
        Uint32 spent = SDL_GetTicks() - t0, gap = slow ? 1500 : 600;
        if (spent < gap) SDL_Delay(gap - spent);
    }
    dbus_connection_close(c);
    dbus_connection_unref(c);
    return NULL;
}

void pccap_set(bool on, bool low_power)
{
    slow = low_power;
    if (on && !started && state >= 0) {
        if (!getenv("WAYLAND_DISPLAY") || !getenv("DBUS_SESSION_BUS_ADDRESS")) { state = -1; return; }
        const char *run = getenv("XDG_RUNTIME_DIR");
        snprintf(script_path, sizeof script_path, "%s/lifeos-pc-windows.js", run && *run ? run : "/tmp");
        FILE *f = fopen(script_path, "w");
        if (!f) { state = -1; return; }
        fputs(SCRIPT, f);
        fclose(f);
        started = pthread_create(&thread, NULL, worker, NULL) == 0;
        if (!started) { state = -1; return; }
    }
    pthread_mutex_lock(&mu);
    want = on;
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
}

bool pccap_failed(void) { return state < 0; }

GLuint pccap_texture(float *u, float *v)
{
    if (state < 0) return 0;
    pthread_mutex_lock(&mu);
    if (fresh && ready) {
        int pw = 1, ph = 1;
        while (pw < ready_w) pw <<= 1;
        while (ph < ready_h) ph <<= 1;
        if (!tex || pw != tex_w || ph != tex_h) {
            if (!tex) glGenTextures(1, &tex);
            glBindTexture(GL_TEXTURE_2D, tex);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP);
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, pw, ph, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
            tex_w = pw; tex_h = ph;
        }
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, ready_w, ready_h, GL_RGBA, GL_UNSIGNED_BYTE, ready);
        fresh = false;
    }
    float uu = tex_w ? (float)ready_w / tex_w : 0, vv = tex_h ? (float)ready_h / tex_h : 0;
    pthread_mutex_unlock(&mu);
    if (!tex || state != 1) return 0;
    *u = uu; *v = vv;
    return tex;
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
