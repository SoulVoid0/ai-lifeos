/* Separate screens for the tablet and the TV: each runs its own small, invisible (headless) Wayland session with
 * one program in it, apart from the KDE desktop on the PC. No desktop, panel or other windows, and nothing of them
 * ever shows on the real screen.
 *
 *   tablet: LSIW Control (its own instance, with its own session bus)
 *   TV:     Steam Big Picture
 *
 * The session is sway with the headless backend and a config that only starts the program. Pictures come from grim
 * (wlr-screencopy) on a worker thread; the pointer is a virtual pointer (wlr-virtual-pointer) this file creates in
 * the session, and keys come from a virtual keyboard (zwp-virtual-keyboard) whose keymap holds the keys typed so far.
 * One worker thread per screen sends input in order.
 */
#define _GNU_SOURCE
#include "app.h"
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#include <wayland-client.h>
#include "wlr-virtual-pointer.h"
#include "virtual-keyboard.h"
#include <sys/mman.h>
#include <xkbcommon/xkbcommon.h>

extern char **environ;

#define QN 128
enum { EV_TEXT, EV_KEY, EV_MOVE, EV_BUTTON, EV_SCROLL };
typedef struct { int kind, a, b; char s[200]; } Ev;   /* EV_TEXT: UTF-8; EV_KEY: a keysym name, a = modifier mask */
#define MAX_SYMS 240

typedef struct {
    bool used, adopted;   /* adopted: a screen kept running by the apartment before this one (not our child) */
    pid_t sway;
    int w, h;
    Uint32 started;
    char dir[400], conf[512], sock[512], envf[512], wl[64];
    /* pictures */
    pthread_t cap_t, in_t;
    pthread_mutex_t mu;
    pthread_cond_t cv;
    volatile int fps, stop;
    uint8_t *ready;
    int rw, rh;
    bool fresh, content;
    Uint32 shot_at;
    GLuint tex;
    int tw, th;
    float u, v;
    /* input */
    Ev q[QN];
    int qh, qt;
    float last_x, last_y;
    struct wl_display *dpy;
    struct wl_seat *seat;
    struct zwlr_virtual_pointer_manager_v1 *vpm;
    struct zwlr_virtual_pointer_v1 *vp;
    struct zwp_virtual_keyboard_manager_v1 *vkm;
    struct zwp_virtual_keyboard_v1 *vk;
    xkb_keysym_t syms[MAX_SYMS];   /* keycode 9 + i types syms[i] */
    int nsyms;
    int sent;
} Nest;

static Nest N[NEST_N];
static const char *NAMES[NEST_N] = {"tablet", "tv"};

static bool exe(const char *name)
{
    char p[256];
    snprintf(p, sizeof p, "/usr/bin/%s", name);
    return access(p, X_OK) == 0;
}

bool nest_available(void) { return exe("sway") && exe("grim"); }

/* The environment for programs inside screen n (or for sway itself when n < 0 is given with base). */
static char **make_env(Nest *ns, bool for_sway)
{
    int n = 0;
    while (environ[n]) n++;
    char **e = calloc((size_t)n + 12, sizeof *e);
    int k = 0;
    for (int i = 0; i < n; i++) {
        const char *v = environ[i];
        if (!strncmp(v, "WAYLAND_DISPLAY=", 16) || !strncmp(v, "DISPLAY=", 8) || !strncmp(v, "SWAYSOCK=", 9) ||
            !strncmp(v, "WLR_", 4) || !strncmp(v, "XDG_SESSION_TYPE=", 17) || !strncmp(v, "XDG_CURRENT_DESKTOP=", 20) ||
            !strncmp(v, "DESKTOP_SESSION=", 16) || !strncmp(v, "KDE_", 4) || !strncmp(v, "QT_QPA_PLATFORM", 15) ||
            !strncmp(v, "LIFEOS_", 7))
            continue;
        e[k++] = strdup(v);
    }
    char b[700];
    snprintf(b, sizeof b, "SWAYSOCK=%s", ns->sock);
    e[k++] = strdup(b);
    if (for_sway) {
        e[k++] = strdup("WLR_BACKENDS=headless");
        e[k++] = strdup("WLR_LIBINPUT_NO_DEVICES=1");
        e[k++] = strdup("WLR_RENDERER_ALLOW_SOFTWARE=1");
        /* without a GPU render node (virtual machines), draw on the CPU */
        if (access("/dev/dri/renderD128", R_OK | W_OK) != 0) e[k++] = strdup("WLR_RENDERER=pixman");
        e[k++] = strdup("XDG_CURRENT_DESKTOP=sway");
    } else {
        snprintf(b, sizeof b, "WAYLAND_DISPLAY=%s", ns->wl);
        e[k++] = strdup(b);
    }
    e[k] = NULL;
    return e;
}

static void free_env(char **e)
{
    for (int i = 0; e[i]; i++) free(e[i]);
    free(e);
}

static pid_t run(const char *const argv[], char **env, int out_fd, bool own_session)
{
    posix_spawnattr_t at;
    posix_spawnattr_init(&at);
    sigset_t none, dfl;
    sigemptyset(&none);
    sigemptyset(&dfl);
    sigaddset(&dfl, SIGPIPE);
    sigaddset(&dfl, SIGCHLD);
    posix_spawnattr_setsigmask(&at, &none);
    posix_spawnattr_setsigdefault(&at, &dfl);
    posix_spawnattr_setflags(&at, (own_session ? POSIX_SPAWN_SETSID : 0) | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF);
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
    if (out_fd >= 0) posix_spawn_file_actions_adddup2(&fa, out_fd, 1);
    else posix_spawn_file_actions_addopen(&fa, 1, "/dev/null", O_WRONLY, 0);
    pid_t pid = -1;
    int r = posix_spawnp(&pid, argv[0], &fa, &at, (char *const *)argv, env);
    posix_spawn_file_actions_destroy(&fa);
    posix_spawnattr_destroy(&at);
    return r ? -1 : pid;
}

/* ------------------------------------------------------------------ pictures */

static int read_full(int fd, uint8_t *b, size_t n)
{
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, b + got, n - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return -1;
        got += (size_t)r;
    }
    return 0;
}

/* reads "P6\n<w> <h>\n255\n" one byte at a time */
static int ppm_header(int fd, int *w, int *h)
{
    char hd[64];
    int n = 0, fields = 0;
    int vals[3] = {0};
    bool in_num = false;
    char c;
    if (read(fd, hd, 2) != 2 || hd[0] != 'P' || hd[1] != '6') return -1;
    while (fields < 3 && n < 60) {
        if (read(fd, &c, 1) != 1) return -1;
        n++;
        if (c >= '0' && c <= '9') { vals[fields] = vals[fields] * 10 + (c - '0'); in_num = true; }
        else if (in_num) { fields++; in_num = false; }
    }
    if (fields < 3) return -1;
    *w = vals[0]; *h = vals[1];
    return (*w > 0 && *h > 0 && *w <= 8192 && *h <= 8192) ? 0 : -1;
}

static void *capture_main(void *arg)
{
    Nest *ns = arg;
    uint8_t *buf = NULL;
    size_t cap = 0;
    while (!ns->stop) {
        int fps = ns->fps;
        if (fps <= 0 || !ns->wl[0]) { usleep(100000); continue; }
        Uint32 t0 = SDL_GetTicks();
        int p[2];
        if (pipe2(p, O_CLOEXEC) < 0) { usleep(500000); continue; }
        char **env = make_env(ns, false);
        const char *argv[] = {"grim", "-t", "ppm", "-", NULL};
        pid_t pid = run(argv, env, p[1], false);
        free_env(env);
        close(p[1]);
        int w = 0, h = 0;
        bool ok = false;
        if (pid > 0 && ppm_header(p[0], &w, &h) == 0) {
            size_t need = (size_t)w * h * 3;
            if (need > cap) { free(buf); buf = malloc(need); cap = buf ? need : 0; }
            if (buf && read_full(p[0], buf, need) == 0) ok = true;
        }
        close(p[0]);
        if (pid > 0) waitpid(pid, NULL, 0);
        if (ok) {
            pthread_mutex_lock(&ns->mu);
            uint8_t *dst = realloc(ns->ready, (size_t)w * h * 4);
            if (dst) {
                ns->ready = dst;
                const uint8_t *s = buf;
                for (size_t i = 0, n = (size_t)w * h; i < n; i++, s += 3, dst += 4) { dst[0] = s[0]; dst[1] = s[1]; dst[2] = s[2]; dst[3] = 255; }
                ns->rw = w; ns->rh = h;
                ns->fresh = true;
                /* still black while the program starts: the drawn screen stays until something shows */
                if (!ns->content)
                    for (size_t i = 0, n = (size_t)w * h * 3; i < n; i += 3 * 997)
                        if (buf[i] > 8 || buf[i + 1] > 8 || buf[i + 2] > 8) { ns->content = true; break; }
                ns->shot_at = SDL_GetTicks();
            }
            pthread_mutex_unlock(&ns->mu);
        }
        Uint32 spent = SDL_GetTicks() - t0, want = 1000u / (unsigned)fps;
        usleep((useconds_t)((spent < want ? want - spent : 10) * 1000));
        if (!ok) usleep(300000);
    }
    free(buf);
    return NULL;
}

/* ------------------------------------------------------------------ input */

static void reg_global(void *data, struct wl_registry *r, uint32_t name, const char *iface, uint32_t ver)
{
    Nest *ns = data;
    if (!strcmp(iface, wl_seat_interface.name) && !ns->seat) ns->seat = wl_registry_bind(r, name, &wl_seat_interface, 1);
    else if (!strcmp(iface, zwlr_virtual_pointer_manager_v1_interface.name) && !ns->vpm)
        ns->vpm = wl_registry_bind(r, name, &zwlr_virtual_pointer_manager_v1_interface, ver < 2 ? ver : 2);
    else if (!strcmp(iface, zwp_virtual_keyboard_manager_v1_interface.name) && !ns->vkm)
        ns->vkm = wl_registry_bind(r, name, &zwp_virtual_keyboard_manager_v1_interface, 1);
}
static void reg_remove(void *data, struct wl_registry *r, uint32_t name) { (void)data; (void)r; (void)name; }
static const struct wl_registry_listener REG = {reg_global, reg_remove};

static void vp_close(Nest *ns)
{
    if (ns->dpy) wl_display_disconnect(ns->dpy);
    ns->dpy = NULL; ns->seat = NULL; ns->vpm = NULL; ns->vp = NULL; ns->vkm = NULL; ns->vk = NULL; ns->nsyms = 0;
}

/* the virtual pointer in the screen's session (made once its display name is known) */
static bool vp_ready(Nest *ns)
{
    if (ns->vp) {
        if (wl_display_get_error(ns->dpy) == 0) return true;
        vp_close(ns);
    }
    if (!ns->wl[0]) return false;
    ns->dpy = wl_display_connect(ns->wl);
    if (!ns->dpy) return false;
    struct wl_registry *reg = wl_display_get_registry(ns->dpy);
    wl_registry_add_listener(reg, &REG, ns);
    wl_display_roundtrip(ns->dpy);
    if (!ns->seat || !ns->vpm || !ns->vkm) { vp_close(ns); return false; }
    ns->vp = zwlr_virtual_pointer_manager_v1_create_virtual_pointer(ns->vpm, ns->seat);
    ns->vk = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(ns->vkm, ns->seat);
    wl_display_roundtrip(ns->dpy);
    return true;
}

static void pointer_event(Nest *ns, const Ev *e)
{
    if (!vp_ready(ns)) return;
    uint32_t t = SDL_GetTicks();
    if (e->kind == EV_MOVE) {
        zwlr_virtual_pointer_v1_motion_absolute(ns->vp, t, (uint32_t)e->a, (uint32_t)e->b, (uint32_t)ns->w, (uint32_t)ns->h);
    } else if (e->kind == EV_BUTTON) {
        uint32_t code = e->a == 3 ? 0x111 : e->a == 2 ? 0x112 : 0x110;   /* BTN_RIGHT, BTN_MIDDLE, BTN_LEFT */
        zwlr_virtual_pointer_v1_button(ns->vp, t, code, e->b ? WL_POINTER_BUTTON_STATE_PRESSED : WL_POINTER_BUTTON_STATE_RELEASED);
    } else {
        /* wheel up (SDL y > 0) scrolls up: a negative vertical axis value */
        zwlr_virtual_pointer_v1_axis_source(ns->vp, WL_POINTER_AXIS_SOURCE_WHEEL);
        zwlr_virtual_pointer_v1_axis_discrete(ns->vp, t, WL_POINTER_AXIS_VERTICAL_SCROLL, wl_fixed_from_int(-15 * e->a), -e->a);
    }
    zwlr_virtual_pointer_v1_frame(ns->vp);
    if (++ns->sent % 64 == 0) wl_display_roundtrip(ns->dpy);   /* read what the session sent meanwhile */
    else wl_display_flush(ns->dpy);
}

/* The keyboard's keymap: one key per keysym typed so far (like wtype), sent again when a new one is needed. */
static void send_keymap(Nest *ns)
{
    size_t cap = 4096 + (size_t)ns->nsyms * 96, l = 0;
    char *km = malloc(cap);
    if (!km) return;
    l += (size_t)snprintf(km + l, cap - l, "xkb_keymap {\nxkb_keycodes \"(unnamed)\" {\nminimum = 8;\nmaximum = %d;\n", 9 + ns->nsyms);
    for (int i = 0; i < ns->nsyms; i++) l += (size_t)snprintf(km + l, cap - l, "<K%d> = %d;\n", i + 1, i + 9);
    l += (size_t)snprintf(km + l, cap - l, "};\nxkb_types \"(unnamed)\" { include \"complete\" };\n"
                                            "xkb_compatibility \"(unnamed)\" { include \"complete\" };\nxkb_symbols \"(unnamed)\" {\n");
    for (int i = 0; i < ns->nsyms; i++) {
        char name[64];
        if (xkb_keysym_get_name(ns->syms[i], name, sizeof name) < 0) snprintf(name, sizeof name, "space");
        l += (size_t)snprintf(km + l, cap - l, "key <K%d> {[ %s ]};\n", i + 1, name);
    }
    l += (size_t)snprintf(km + l, cap - l, "};\n};\n");
    int fd = memfd_create("lifeos-keymap", MFD_CLOEXEC);
    if (fd >= 0) {
        if (write(fd, km, l + 1) == (ssize_t)(l + 1))
            zwp_virtual_keyboard_v1_keymap(ns->vk, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fd, (uint32_t)(l + 1));
        close(fd);
    }
    free(km);
}

static int key_for(Nest *ns, xkb_keysym_t sym)
{
    for (int i = 0; i < ns->nsyms; i++) if (ns->syms[i] == sym) return i;
    if (ns->nsyms >= MAX_SYMS) ns->nsyms = 0;
    ns->syms[ns->nsyms++] = sym;
    send_keymap(ns);
    return ns->nsyms - 1;
}

static void tap(Nest *ns, xkb_keysym_t sym, uint32_t mods)
{
    int k = key_for(ns, sym);
    uint32_t t = SDL_GetTicks();
    if (mods) zwp_virtual_keyboard_v1_modifiers(ns->vk, mods, 0, 0, 0);
    zwp_virtual_keyboard_v1_key(ns->vk, t, (uint32_t)k + 1, WL_KEYBOARD_KEY_STATE_PRESSED);   /* evdev code = xkb - 8 */
    zwp_virtual_keyboard_v1_key(ns->vk, t + 1, (uint32_t)k + 1, WL_KEYBOARD_KEY_STATE_RELEASED);
    if (mods) zwp_virtual_keyboard_v1_modifiers(ns->vk, 0, 0, 0, 0);
}

static void keyboard_event(Nest *ns, const Ev *e)
{
    if (!vp_ready(ns)) return;
    if (e->kind == EV_KEY) {
        xkb_keysym_t sym = xkb_keysym_from_name(e->s, XKB_KEYSYM_NO_FLAGS);
        if (sym != XKB_KEY_NoSymbol) tap(ns, sym, (uint32_t)e->a);
    } else {
        for (const unsigned char *p = (const unsigned char *)e->s; *p;) {
            uint32_t cp;
            int n = *p < 0x80 ? 1 : (*p >> 5) == 6 ? 2 : (*p >> 4) == 14 ? 3 : (*p >> 3) == 30 ? 4 : 1;
            cp = n == 1 ? *p : n == 2 ? (*p & 0x1f) : n == 3 ? (*p & 0x0f) : (*p & 0x07);
            for (int i = 1; i < n; i++) cp = (cp << 6) | (p[i] & 0x3f);
            p += n;
            xkb_keysym_t sym = cp == '\n' ? XKB_KEY_Return : cp == '\t' ? XKB_KEY_Tab : xkb_utf32_to_keysym(cp);
            if (sym != XKB_KEY_NoSymbol) tap(ns, sym, 0);
        }
    }
    wl_display_flush(ns->dpy);
}

static void *input_main(void *arg)
{
    Nest *ns = arg;
    pthread_mutex_lock(&ns->mu);
    while (!ns->stop) {
        if (ns->qh == ns->qt) {
            /* connect the virtual pointer and keyboard ahead of the first click */
            if (!ns->vp && ns->wl[0]) {
                pthread_mutex_unlock(&ns->mu);
                vp_ready(ns);
                pthread_mutex_lock(&ns->mu);
                if (ns->qh != ns->qt) continue;
            }
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            ts.tv_nsec += 500000000;
            if (ts.tv_nsec >= 1000000000) { ts.tv_sec++; ts.tv_nsec -= 1000000000; }
            pthread_cond_timedwait(&ns->cv, &ns->mu, &ts);
            continue;
        }
        Ev e = ns->q[ns->qt];
        ns->qt = (ns->qt + 1) % QN;
        pthread_mutex_unlock(&ns->mu);
        if (e.kind == EV_TEXT || e.kind == EV_KEY) keyboard_event(ns, &e);
        else pointer_event(ns, &e);
        pthread_mutex_lock(&ns->mu);
    }
    pthread_mutex_unlock(&ns->mu);
    return NULL;
}

static void push(Nest *ns, int kind, int a, int b, const char *s)
{
    if (!ns->used || !ns->wl[0]) return;
    pthread_mutex_lock(&ns->mu);
    int nh = (ns->qh + 1) % QN;
    if (nh != ns->qt) {
        ns->q[ns->qh].kind = kind;
        ns->q[ns->qh].a = a;
        ns->q[ns->qh].b = b;
        snprintf(ns->q[ns->qh].s, sizeof ns->q[ns->qh].s, "%s", s ? s : "");
        ns->qh = nh;
        pthread_cond_signal(&ns->cv);
    }
    pthread_mutex_unlock(&ns->mu);
}

void nest_pointer(int n, float fx, float fy)
{
    Nest *ns = &N[n];
    float x = clampf(fx, 0, 1) * (ns->w - 1), y = clampf(fy, 0, 1) * (ns->h - 1);
    if (fabsf(x - ns->last_x) < 0.5f && fabsf(y - ns->last_y) < 0.5f) return;
    ns->last_x = x; ns->last_y = y;
    push(ns, EV_MOVE, (int)x, (int)y, NULL);
}

void nest_button(int n, int button, bool down)
{
    push(&N[n], EV_BUTTON, button, down, NULL);
}

void nest_scroll(int n, int dy)
{
    if (dy) push(&N[n], EV_SCROLL, dy > 5 ? 5 : dy < -5 ? -5 : dy, 0, NULL);
}

void nest_text(int n, const char *utf8)
{
    if (!*utf8) return;
    push(&N[n], EV_TEXT, 0, 0, utf8);
}

/* Keys that don't come as text. Returns false for keys left alone. */
bool nest_key(int n, SDL_Keycode k, Uint16 mod)
{
    const char *sym = NULL;
    static char letter[2];
    switch (k) {
    case SDLK_RETURN: case SDLK_KP_ENTER: sym = "Return"; break;
    case SDLK_BACKSPACE: sym = "BackSpace"; break;
    case SDLK_TAB: sym = "Tab"; break;
    case SDLK_DELETE: sym = "Delete"; break;
    case SDLK_LEFT: sym = "Left"; break;
    case SDLK_RIGHT: sym = "Right"; break;
    case SDLK_UP: sym = "Up"; break;
    case SDLK_DOWN: sym = "Down"; break;
    case SDLK_HOME: sym = "Home"; break;
    case SDLK_END: sym = "End"; break;
    case SDLK_PAGEUP: sym = "Prior"; break;
    case SDLK_PAGEDOWN: sym = "Next"; break;
    case SDLK_ESCAPE: sym = "Escape"; break;
    case SDLK_SPACE: if (mod & (KMOD_CTRL | KMOD_ALT)) sym = "space"; break;
    case SDLK_F1: case SDLK_F2: case SDLK_F3: case SDLK_F4: case SDLK_F5: case SDLK_F6:
    case SDLK_F7: case SDLK_F8: case SDLK_F9: case SDLK_F10: case SDLK_F11: {
        static char f[4];
        snprintf(f, sizeof f, "F%d", (int)(k - SDLK_F1) + 1);
        sym = f;
        break;
    }
    default:
        /* Ctrl/Alt + a letter or digit (Ctrl+C, Ctrl+A...); plain ones come as text */
        if ((mod & (KMOD_CTRL | KMOD_ALT)) && ((k >= SDLK_a && k <= SDLK_z) || (k >= SDLK_0 && k <= SDLK_9))) {
            letter[0] = (char)k; letter[1] = 0;
            sym = letter;
        }
    }
    if (!sym) return false;
    /* real modifier bits of the "complete" keymap: Shift 1, Control 4, Mod1 (Alt) 8 */
    int mods = ((mod & KMOD_CTRL) ? 4 : 0) | ((mod & KMOD_ALT) ? 8 : 0) | ((mod & KMOD_SHIFT) ? 1 : 0);
    push(&N[n], EV_KEY, mods, 0, sym);
    return true;
}

/* ------------------------------------------------------------------ the sessions */

/* Is pid a running sway with this config (not a zombie, not a reused pid)? */
static bool sway_alive(pid_t pid, const char *conf)
{
    if (pid <= 1) return false;
    char cl[64], cmdline[700] = "";
    snprintf(cl, sizeof cl, "/proc/%d/cmdline", (int)pid);
    FILE *c = fopen(cl, "r");
    if (!c) return false;
    size_t k = fread(cmdline, 1, sizeof cmdline - 1, c);
    fclose(c);
    for (size_t i = 0; i < k; i++) if (!cmdline[i]) cmdline[i] = ' ';
    return strstr(cmdline, "sway") && strstr(cmdline, conf);
}

static void set_paths(Nest *ns, int n, const char *run_dir)
{
    snprintf(ns->dir, sizeof ns->dir, "%s/lifeos-%s", run_dir, NAMES[n]);
    snprintf(ns->conf, sizeof ns->conf, "%s.sway", ns->dir);
    snprintf(ns->sock, sizeof ns->sock, "%s.sock", ns->dir);
    snprintf(ns->envf, sizeof ns->envf, "%s.env", ns->dir);
}

static pid_t read_pidfile(const Nest *ns)
{
    char pidf[600], line[64];
    snprintf(pidf, sizeof pidf, "%s.pid", ns->dir);
    FILE *f = fopen(pidf, "r");
    pid_t pid = 0;
    if (f) {
        if (fgets(line, sizeof line, f)) pid = (pid_t)atoi(line);
        fclose(f);
    }
    return pid;
}

static void start_threads(Nest *ns)
{
    ns->used = true;
    ns->started = SDL_GetTicks();
    pthread_mutex_init(&ns->mu, NULL);
    pthread_cond_init(&ns->cv, NULL);
    pthread_create(&ns->cap_t, NULL, capture_main, ns);
    pthread_create(&ns->in_t, NULL, input_main, ns);
}

/* Ends our threads but leaves the screen and its program running, for the next apartment (back from the PC). */
static void detach_one(Nest *ns)
{
    if (!ns->used) return;
    ns->stop = 1;
    pthread_mutex_lock(&ns->mu);
    pthread_cond_broadcast(&ns->cv);
    pthread_mutex_unlock(&ns->mu);
    pthread_join(ns->cap_t, NULL);
    pthread_join(ns->in_t, NULL);
    vp_close(ns);
    free(ns->ready);
    GLuint tex = ns->tex;
    pthread_mutex_destroy(&ns->mu);
    pthread_cond_destroy(&ns->cv);
    memset(ns, 0, sizeof *ns);
    ns->tex = tex;
}

static void stop_one(Nest *ns)
{
    if (!ns->used) return;
    if (ns->sway > 0) {
        kill(-ns->sway, SIGTERM);   /* sway and everything started in it */
        kill(ns->sway, SIGTERM);
        for (int i = 0; i < 30 && (ns->adopted ? sway_alive(ns->sway, ns->conf) : waitpid(ns->sway, NULL, WNOHANG) == 0); i++)
            usleep(50000);
        kill(-ns->sway, SIGKILL);
        if (!ns->adopted) waitpid(ns->sway, NULL, WNOHANG);
        char pidf[600];
        snprintf(pidf, sizeof pidf, "%s.pid", ns->dir);
        unlink(pidf);
    }
    ns->stop = 1;
    pthread_mutex_lock(&ns->mu);
    pthread_cond_broadcast(&ns->cv);
    pthread_mutex_unlock(&ns->mu);
    pthread_join(ns->cap_t, NULL);
    pthread_join(ns->in_t, NULL);
    vp_close(ns);
    free(ns->ready);
    unlink(ns->conf);
    unlink(ns->envf);
    GLuint tex = ns->tex;
    pthread_mutex_destroy(&ns->mu);
    pthread_cond_destroy(&ns->cv);
    memset(ns, 0, sizeof *ns);
    ns->tex = tex;   /* reused */
}

/* Takes over screen n left running by the apartment before this one (going to the PC and back). */
bool nest_adopt(int n, int w, int h)
{
    Nest *ns = &N[n];
    if (ns->used) return true;
    const char *run_dir = getenv("XDG_RUNTIME_DIR");
    if (!run_dir || !*run_dir || !nest_available()) return false;
    GLuint tex = ns->tex;
    memset(ns, 0, sizeof *ns);
    ns->tex = tex;
    set_paths(ns, n, run_dir);
    pid_t old = read_pidfile(ns);
    if (!sway_alive(old, ns->conf) || access(ns->sock, F_OK) != 0 || access(ns->envf, R_OK) != 0) return false;
    ns->w = w; ns->h = h;
    ns->sway = old;
    ns->adopted = true;
    FILE *f = fopen(ns->envf, "r");
    if (f) {
        if (fgets(ns->wl, sizeof ns->wl, f)) ns->wl[strcspn(ns->wl, "\r\n")] = 0;
        fclose(f);
    }
    start_threads(ns);
    fprintf(stderr, "apartment: took over the %s's screen (sway %d)\n", NAMES[n], (int)old);
    return true;
}

bool nest_start(int n, const char *cmd, int w, int h)
{
    Nest *ns = &N[n];
    if (ns->used) return true;
    if (nest_adopt(n, w, h)) return true;
    if (!nest_available()) return false;
    const char *run_dir = getenv("XDG_RUNTIME_DIR");
    if (!run_dir || !*run_dir) return false;
    GLuint tex = ns->tex;
    memset(ns, 0, sizeof *ns);
    ns->tex = tex;
    ns->w = w; ns->h = h;
    set_paths(ns, n, run_dir);
    /* a session left behind (an apartment that crashed) but not usable: end it first */
    pid_t old = read_pidfile(ns);
    if (sway_alive(old, ns->conf)) { kill(-old, SIGTERM); kill(old, SIGTERM); }
    unlink(ns->sock);
    unlink(ns->envf);
    char pidf[600];
    snprintf(pidf, sizeof pidf, "%s.pid", ns->dir);
    FILE *f;
    f = fopen(ns->conf, "w");
    if (!f) return false;
    fprintf(f,
            "# the %s's own screen (written by the LifeOS apartment)\n"
            "output HEADLESS-1 resolution %dx%d position 0 0\n"
            "default_border none\n"
            "default_floating_border normal\n"
            "focus_follows_mouse yes\n"
            "seat seat0 hide_cursor 0\n"
            "exec sh -c 'printf \"%%s\\n\" \"$WAYLAND_DISPLAY\" > \"%s.tmp\" && mv \"%s.tmp\" \"%s\"'\n"
            "exec %s\n",
            NAMES[n], w, h, ns->envf, ns->envf, ns->envf, cmd);
    fclose(f);
    char **env = make_env(ns, true);
    const char *argv[] = {"sway", "-c", ns->conf, NULL};
    ns->sway = run(argv, env, -1, true);
    free_env(env);
    if (ns->sway <= 0) return false;
    f = fopen(pidf, "w");
    if (f) { fprintf(f, "%d\n", (int)ns->sway); fclose(f); }
    start_threads(ns);
    fprintf(stderr, "apartment: started the %s's own screen (sway %d)\n", NAMES[n], (int)ns->sway);
    return true;
}

bool nest_running(int n) { return N[n].used; }
bool nest_ready(int n) { return N[n].used && N[n].content; }
Uint32 nest_age(int n) { return N[n].used ? SDL_GetTicks() - N[n].started : 0; }
void nest_set_fps(int n, int fps) { N[n].fps = fps; }
void nest_stop(int n) { stop_one(&N[n]); }

GLuint nest_texture(int n, float *u, float *v)
{
    Nest *ns = &N[n];
    if (!ns->used) return 0;
    pthread_mutex_lock(&ns->mu);
    if (ns->fresh && ns->ready) {
        int pw = 1, ph = 1;
        while (pw < ns->rw) pw <<= 1;
        while (ph < ns->rh) ph <<= 1;
        if (!ns->tex || pw != ns->tw || ph != ns->th) {
            if (!ns->tex) glGenTextures(1, &ns->tex);
            glBindTexture(GL_TEXTURE_2D, ns->tex);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP);
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, pw, ph, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
            ns->tw = pw; ns->th = ph;
        }
        glBindTexture(GL_TEXTURE_2D, ns->tex);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, ns->rw, ns->rh, GL_RGBA, GL_UNSIGNED_BYTE, ns->ready);
        ns->u = (float)ns->rw / ns->tw;
        ns->v = (float)ns->rh / ns->th;
        ns->fresh = false;
    }
    pthread_mutex_unlock(&ns->mu);
    if (!ns->tw) return 0;
    *u = ns->u; *v = ns->v;
    return ns->tex;
}

/* Every frame: notices a session that ended, and learns its Wayland display name. Returns a bit per screen that
 * ended since the last call. */
int nest_update(void)
{
    int ended = 0;
    for (int n = 0; n < NEST_N; n++) {
        Nest *ns = &N[n];
        if (!ns->used) continue;
        if (!ns->wl[0]) {
            FILE *f = fopen(ns->envf, "r");
            if (f) {
                if (fgets(ns->wl, sizeof ns->wl, f)) ns->wl[strcspn(ns->wl, "\r\n")] = 0;
                fclose(f);
            }
        }
        if (ns->adopted ? !sway_alive(ns->sway, ns->conf) : waitpid(ns->sway, NULL, WNOHANG) == ns->sway) {
            fprintf(stderr, "apartment: the %s's screen ended\n", NAMES[n]);
            ns->sway = 0;
            stop_one(ns);
            ended |= 1 << n;
        }
    }
    return ended;
}

void nest_shutdown(bool keep_running)
{
    for (int n = 0; n < NEST_N; n++) {
        if (keep_running) detach_one(&N[n]);
        else stop_one(&N[n]);
    }
}

/* A new size for screen n (the TV going fullscreen and back); the program in it follows. */
void nest_resize(int n, int w, int h)
{
    Nest *ns = &N[n];
    if (!ns->used || !ns->wl[0] || (ns->w == w && ns->h == h)) return;
    ns->w = w; ns->h = h;
    char res[32];
    snprintf(res, sizeof res, "%dx%d", w, h);
    char **env = make_env(ns, false);
    const char *argv[] = {"swaymsg", "output", "HEADLESS-1", "resolution", res, NULL};
    pid_t pid = run(argv, env, -1, false);
    free_env(env);
    if (pid > 0) waitpid(pid, NULL, 0);
}
