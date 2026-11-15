/* Real programs inside the apartment: the tablet on the bed (LSIW Control), Steam Big Picture on the living room TV,
 * and the letter that comes through the front door when a new LifeOS is out.
 *
 * The tablet and the TV each have their own screen (nest.c): a separate, invisible session with only that program
 * in it, not the KDE desktop of the PC. Their pictures are drawn on the tablet and the TV, and while you use one
 * (holding the tablet, or sitting in front of the TV) your mouse and keys go to it. Setting Steam up and updating
 * LifeOS run in a terminal on the KDE desktop, brought in front of the apartment (KWin scripts, pccap_kwin).
 */
#define _GNU_SOURCE
#include "app.h"
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <spawn.h>
#include <stdlib.h>
#include <strings.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

/* ------------------------------------------------------------------ programs */

#define MAX_KIDS 16
static pid_t kids[MAX_KIDS];

/* Starts a program in its own session (it keeps running when the apartment exits). */
static pid_t spawn(const char *const argv[])
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
    posix_spawnattr_setflags(&at, POSIX_SPAWN_SETSID | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF);
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
    pid_t pid = -1;
    int r = posix_spawnp(&pid, argv[0], &fa, &at, (char *const *)argv, environ);
    posix_spawn_file_actions_destroy(&fa);
    posix_spawnattr_destroy(&at);
    if (r) { fprintf(stderr, "apartment: couldn't start %s: %s\n", argv[0], strerror(r)); return -1; }
    for (int i = 0; i < MAX_KIDS; i++) if (!kids[i]) { kids[i] = pid; break; }
    return pid;
}

static void reap(void)
{
    for (int i = 0; i < MAX_KIDS; i++)
        if (kids[i] && waitpid(kids[i], NULL, WNOHANG) == kids[i]) kids[i] = 0;
}

static bool installed(const char *path) { return access(path, X_OK) == 0; }

/* ------------------------------------------------------------------ KWin scripts */

/* F(re): normal windows and dialogs whose class matches, bottom to top. A(): the apartment. I(id): a window by id.
 * R(): where the held tablet's screen goes (the same fractions as tablet_rect()). */
static const char *JS_LIB =
    "function F(re){var l=workspace.stackingOrder,o=[];for(var i=0;i<l.length;i++){var w=l[i];"
    "if(w.deleted||!(w.normalWindow||w.dialog))continue;if(re.test(String(w.resourceClass||'')))o.push(w);}return o;}\n"
    "function A(){var l=workspace.stackingOrder;for(var i=0;i<l.length;i++)"
    "if(String(l[i].resourceClass||'').indexOf('lifeos-apartment')===0)return l[i];return null;}\n"
    "function I(id){var l=workspace.stackingOrder;for(var i=0;i<l.length;i++)if(l[i].internalId.toString()==id)return l[i];return null;}\n"
    "function R(){var a=A();var g=a?a.frameGeometry:workspace.activeScreen.geometry;"
    "var w=Math.round(g.width*0.62),h=Math.round(w/1.6);if(h>g.height*0.74){h=Math.round(g.height*0.74);w=Math.round(h*1.6);}"
    "return {x:g.x+Math.round((g.width-w)/2),y:g.y+Math.round((g.height-h)/2-g.height*0.03),width:w,height:h};}\n"
    "function front(w){if(!w)return;w.minimized=false;workspace.activeWindow=w;}\n"
    "function home(){var a=A();if(a){a.minimized=false;workspace.activeWindow=a;}}\n";

static void kwin(const char *fmt, ...)
{
    char body[2048], js[4096];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(body, sizeof body, fmt, ap);
    va_end(ap);
    snprintf(js, sizeof js, "%s%s", JS_LIB, body);
    pccap_kwin(js);
}

static bool safe_id(const char *id)
{
    for (const char *p = id; *p; p++) if (!(*p == '{' || *p == '}' || *p == '-' || (*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'f') || (*p >= 'A' && *p <= 'F'))) return false;
    return *id != 0;
}

static WinInfo wins[64];
static int nwins = -1;
static Uint32 wins_age = 0xffffffffu;

/* ------------------------------------------------------------------ the tablet (LSIW Control) */

static struct { int starts; Uint32 started_at; bool held; } tab;

/* LSIW Control in its own screen, restarted when it's closed; its own session bus makes it a separate instance
 * from one opened on the PC. */
#define TAB_CMD "sh -c 'while [ -S $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY ]; do GDK_DEBUG=no-portals GTK_A11Y=none GTK_THEME=Breeze " \
                "dbus-run-session -- lsiw-control; sleep 1; done'"

bool tablet_live(void) { return installed("/usr/bin/lsiw-control") && nest_available(); }
bool tablet_ready(void) { return nest_ready(NEST_TAB); }

void tablet_rect(float *x, float *y, float *w, float *h)
{
    float ww = WIN_W * 0.62f, hh = ww / 1.6f;
    if (hh > WIN_H * 0.74f) { hh = WIN_H * 0.74f; ww = hh * 1.6f; }
    *w = ww; *h = hh;
    *x = (WIN_W - ww) / 2;
    *y = (WIN_H - hh) / 2 - WIN_H * 0.03f;
}

static void tablet_start(void)
{
    if (nest_running(NEST_TAB) || !tablet_live() || tab.starts >= 4) return;
    Uint32 now = SDL_GetTicks();
    if (tab.started_at && now - tab.started_at < 10000) return;
    tab.starts++;
    tab.started_at = now;
    nest_start(NEST_TAB, TAB_CMD, 1280, 800);
}

bool tablet_hold(void)
{
    tablet_start();
    tab.held = true;
    return nest_running(NEST_TAB);
}

void tablet_put_down(void) { tab.held = false; }

static void tablet_update(bool near)
{
    if (near || tab.held) tablet_start();
    nest_set_fps(NEST_TAB, tab.held ? 20 : near ? 3 : 0);
}

GLuint tablet_texture(float *u, float *v) { return nest_ready(NEST_TAB) ? nest_texture(NEST_TAB, u, v) : 0; }

/* ------------------------------------------------------------------ Steam on the TV */

static struct { bool using_; Uint32 started_at; } tvs;

/* Steam Big Picture in the TV's own screen; when Steam quits, the screen closes */
#define TV_CMD "sh -c 'steam -gamepadui; swaymsg exit'"

bool steam_tv_start(void)
{
    if (nest_running(NEST_TV)) return true;
    if (!nest_available()) return false;
    tvs.started_at = SDL_GetTicks();
    return nest_start(NEST_TV, TV_CMD, 1280, 720);
}

void steam_tv_use(bool on) { tvs.using_ = on; }

/* ------------------------------------------------------------------ windows brought in front (Steam, the updater) */

static struct {
    int kind;                 /* JOB_* */
    int phase;                /* 0 waiting for a window list, 1 started */
    Uint32 started;
    char known[64][64];       /* windows that were open before */
    int nknown;
    char fronted[8][64];
    int nfronted;
    bool lost_focus;
} job;

int job_kind(void) { return job.kind; }

static bool was_known(const char *id)
{
    for (int i = 0; i < job.nknown; i++) if (!strcmp(job.known[i], id)) return true;
    return false;
}

static bool was_fronted(const char *id)
{
    for (int i = 0; i < job.nfronted; i++) if (!strcmp(job.fronted[i], id)) return true;
    return false;
}

static void front_once(const WinInfo *w)
{
    if (was_fronted(w->id) || !safe_id(w->id)) return;
    if (job.nfronted < 8) snprintf(job.fronted[job.nfronted++], 64, "%s", w->id);
    kwin("front(I('%s'));", w->id);
}

void job_start(int kind)
{
    memset(&job, 0, sizeof job);
    job.kind = kind;
    job.started = SDL_GetTicks();
    pccap_watch(true);
}

void job_end(void) { job.kind = JOB_NONE; }

static void job_launch(void)
{
    if (job.kind == JOB_STEAM) {
        const char *argv[] = {"konsole", "--hide-menubar", "--hide-tabbar", "-e", "/usr/bin/lifeos-steam", "--setup", NULL};
        if (spawn(argv) < 0) { toast("Couldn't open the Steam setup"); job_end(); return; }
        toast("Setting Steam up in a terminal window...");
    } else if (job.kind == JOB_UPDATE) {
        const char *argv[] = {"konsole", "--hide-menubar", "--hide-tabbar", "-e", "/usr/bin/lifeos-update", "--install", NULL};
        if (spawn(argv) < 0) { toast("Couldn't open the updater"); job_end(); return; }
        toast("Updating LifeOS in a terminal window...");
    }
}

static void job_update(bool focused)
{
    if (job.kind == JOB_NONE) return;
    Uint32 now = SDL_GetTicks();
    if (job.phase == 0) {
        /* remember which windows were already open, so the new ones can be brought in front */
        if (nwins >= 0 && wins_age < 1500) {
            for (int i = 0; i < nwins && job.nknown < 64; i++) snprintf(job.known[job.nknown++], 64, "%s", wins[i].id);
        } else if (now - job.started < 2500 && pccap_kwin_ok()) return;
        job.phase = 1;
        job.started = now;
        job_launch();
        return;
    }
    if (!focused) job.lost_focus = true;
    else if (job.lost_focus) { job_end(); return; }          /* back in the apartment */
    if (now - job.started > 600000) { job_end(); return; }
    if (nwins < 0 || wins_age > 2000) return;
    const WinInfo *fresh = NULL;
    for (int i = nwins - 1; i >= 0; i--) {
        const WinInfo *w = &wins[i];
        if (w->kind != 0 || !strncmp(w->cls, "lifeos-apartment", 16) || was_known(w->id)) continue;
        if (strcasestr(w->cls, "konsole") && !fresh) fresh = w;
    }
    if (fresh) front_once(fresh);
}

/* ------------------------------------------------------------------ the TV */

static int tv_mode;   /* TV_VIDEO or TV_STEAM */
int tv_get_mode(void) { return tv_mode; }
void tv_set_mode(int m) { tv_mode = m == TV_STEAM ? TV_STEAM : TV_VIDEO; }
bool steam_installed(void) { return installed("/usr/bin/steam"); }

/* ------------------------------------------------------------------ the letter (a new LifeOS is out) */

static pthread_mutex_t upd_mu = PTHREAD_MUTEX_INITIALIZER;
static char upd_ver[32];
static volatile int upd_new;          /* 1: a version newer than this one is out */
static float letter_t = -1;           /* seconds since the letter came through the door; -1 none */
static bool updated;

static void *update_checker(void *arg)
{
    (void)arg;
    sleep(20);
    for (;;) {
        FILE *p = popen("/usr/bin/lifeos-update --check 2>/dev/null", "r");
        if (p) {
            char line[128] = "";
            if (fgets(line, sizeof line, p) && !strncmp(line, "available ", 10)) {
                line[strcspn(line, "\r\n")] = 0;
                pthread_mutex_lock(&upd_mu);
                snprintf(upd_ver, sizeof upd_ver, "%s", line + 10);
                pthread_mutex_unlock(&upd_mu);
                upd_new = 1;
            }
            pclose(p);
        }
        sleep(6 * 3600);
    }
    return NULL;
}

bool letter_here(void) { return letter_t >= 0; }
float letter_age(void) { return letter_t; }

const char *letter_version(void)
{
    static char v[32];
    pthread_mutex_lock(&upd_mu);
    snprintf(v, sizeof v, "%s", upd_ver);
    pthread_mutex_unlock(&upd_mu);
    return v;
}

void update_start(void) { if (job.kind == JOB_NONE) job_start(JOB_UPDATE); }

bool update_done(void) { return updated; }

/* ------------------------------------------------------------------ every frame */

void apps_init(void)
{
    /* Programs started from here must not keep the wrapper's lock (or anything else) open. */
    for (int fd = 3; fd < 256; fd++) {
        int fl = fcntl(fd, F_GETFD);
        if (fl >= 0) fcntl(fd, F_SETFD, fl | FD_CLOEXEC);
    }
    if (installed("/usr/bin/lifeos-update") && !getenv("LIFEOS_NO_UPDATE_CHECK")) {
        pthread_t t;
        if (pthread_create(&t, NULL, update_checker, NULL) == 0) pthread_detach(t);
    }
    const char *run = getenv("XDG_RUNTIME_DIR");
    char path[600];
    snprintf(path, sizeof path, "%s/lifeos-updated", run && *run ? run : "/tmp");
    unlink(path);
}

void apps_update(float dt, int room, bool focused)
{
    reap();
    bool near = room == R_MASTER;
    bool tv = tv_mode == TV_STEAM && room == R_LIVING;
    int ended = nest_update();
    if (ended & (1 << NEST_TV)) {
        tvs.using_ = false;
        if (SDL_GetTicks() - tvs.started_at < 15000)
            toast("Steam closed right away. If it's open on the PC, quit it there first (Steam runs once).");
    }
    nest_set_fps(NEST_TV, tvs.using_ ? 24 : tv ? 4 : 0);
    pccap_watch(job.kind != JOB_NONE);
    nwins = pccap_windows(wins, 64, &wins_age);
    tablet_update(near);
    job_update(focused);
    if (upd_new && letter_t < 0) {
        letter_t = 0;
        toast("Something came through the front door (in the hallway)");
    }
    if (letter_t >= 0) letter_t += dt;
    static float marker_t;
    if ((marker_t += dt) > 1.0f && !updated) {
        marker_t = 0;
        const char *run = getenv("XDG_RUNTIME_DIR");
        char path[600];
        snprintf(path, sizeof path, "%s/lifeos-updated", run && *run ? run : "/tmp");
        if (access(path, F_OK) == 0) { unlink(path); updated = true; }
    }
}

void apps_shutdown(void)
{
    nest_shutdown();
    pccap_shutdown();
}

/* ------------------------------------------------------------------ drawing */

static void ellipse(float cx, float cy, float rx, float ry, Col c)
{
    glColor4ub(c.r, c.g, c.b, c.a);
    glBegin(GL_TRIANGLE_FAN);
    glVertex2f(cx, cy);
    for (int i = 0; i <= 32; i++) {
        float a = i * 2 * PI_F / 32;
        glVertex2f(cx + cosf(a) * rx, cy + sinf(a) * ry);
    }
    glEnd();
}

static void rounded(float x, float y, float w, float h, float r, Col c)
{
    rect(x + r, y, w - 2 * r, h, c);
    rect(x, y + r, r, h - 2 * r, c);
    rect(x + w - r, y + r, r, h - 2 * r, c);
    ellipse(x + r, y + r, r, r, c);
    ellipse(x + w - r, y + r, r, r, c);
    ellipse(x + r, y + h - r, r, r, c);
    ellipse(x + w - r, y + h - r, r, r, c);
}

static void hand(float x, float y, float s, int side)
{
    /* side -1 left hand, +1 right hand: the palm below the tablet's lower corner, the thumb over the bezel */
    Col skin = rgb(224, 172, 140), dark = rgb(196, 142, 112);
    ellipse(x + side * s * 0.15f, y + s * 0.75f, s * 0.55f, s * 0.7f, dark);
    ellipse(x + side * s * 0.1f, y + s * 0.7f, s * 0.5f, s * 0.65f, skin);
    /* thumb */
    ellipse(x - side * s * 0.05f, y + s * 0.02f, s * 0.16f, s * 0.36f, dark);
    ellipse(x - side * s * 0.04f, y, s * 0.14f, s * 0.33f, skin);
    ellipse(x - side * s * 0.04f, y - s * 0.2f, s * 0.1f, s * 0.08f, rgb(240, 200, 180));
}

/* The tablet held in both hands, over a dimmed room, with LSIW Control's own screen on it. */
void tablet_draw_held(float t, const char *hint)
{
    float x, y, w, h;
    tablet_rect(&x, &y, &w, &h);
    rect(0, 0, (float)WIN_W, (float)WIN_H, rgba(0, 0, 0, 150));
    float b = h * 0.055f;
    glEnable(GL_BLEND);
    rounded(x - b - 3, y - b - 3, w + 2 * b + 6, h + 2 * b + 6, b * 1.3f, rgb(12, 12, 14));
    rounded(x - b, y - b, w + 2 * b, h + 2 * b, b * 1.1f, rgb(34, 36, 42));
    ellipse(x + w / 2, y - b / 2, b * 0.12f, b * 0.12f, rgb(10, 10, 12));   /* camera */
    float u, v;
    GLuint tex = tablet_texture(&u, &v);
    if (tex) {
        glEnable(GL_TEXTURE_2D);
        glBindTexture(GL_TEXTURE_2D, tex);
        glColor4f(1, 1, 1, 1);
        glBegin(GL_QUADS);
        glTexCoord2f(0, 0); glVertex2f(x, y);
        glTexCoord2f(u, 0); glVertex2f(x + w, y);
        glTexCoord2f(u, v); glVertex2f(x + w, y + h);
        glTexCoord2f(0, v); glVertex2f(x, y + h);
        glEnd();
        glDisable(GL_TEXTURE_2D);
    } else {
        rect(x, y, w, h, rgb(22, 34, 62));
        text_center(F_TITLE, x + w / 2, y + h / 2 - text_h(F_TITLE), "LSIW Control", rgb(240, 240, 250));
        const char *s = fmodf(t, 1.2f) < 0.6f ? "Starting..." : "Starting.. ";
        text_center(F_BODY, x + w / 2, y + h / 2 + 4, s, rgb(170, 190, 230));
    }
    float s = h * 0.32f;
    hand(x - b * 0.4f, y + h * 0.78f, s, -1);
    hand(x + w + b * 0.4f, y + h * 0.78f, s, 1);
    if (hint && *hint) {
        int tw = text_w(F_SMALL, hint) + 24;
        float ty = y + h + b + 14;
        if (ty + text_h(F_SMALL) + 8 > WIN_H) ty = WIN_H - text_h(F_SMALL) - 12.0f;
        rect(WIN_W / 2.0f - tw / 2.0f, ty, (float)tw, text_h(F_SMALL) + 8.0f, rgba(10, 12, 18, 200));
        text_center(F_SMALL, WIN_W / 2.0f, ty + 4, hint, rgb(235, 238, 245));
    }
}

/* Steam mode on the TV: its own screen's live picture when Steam runs there, else a home screen. Corners as draw_tv_screen. */
void steam_tv_draw(V3 tl, V3 tr, V3 br, V3 bl, float h, float t)
{
    float u, v;
    GLuint tex = nest_ready(NEST_TV) ? nest_texture(NEST_TV, &u, &v) : 0;
    if (tex) { tex_quad(tex, u, v, tl, tr, br, bl); return; }
    glow_quad(tl, tr, br, bl, rgb(16, 22, 34));
    /* the screen faces -z; right on screen is -x */
    V3 right = v3(-1, 0, 0), down = v3(0, -1, 0);
    float w = tl.x - tr.x, z = tl.z - 0.003f;
    float x0 = tl.x, y0 = tl.y;
    /* top bar */
    glow_quad(v3(x0, y0, z + 0.001f), v3(x0 - w, y0, z + 0.001f), v3(x0 - w, y0 - h * 0.1f, z + 0.001f), v3(x0, y0 - h * 0.1f, z + 0.001f), rgb(24, 32, 48));
    float px = h * 0.055f / text_h(F_TITLE);
    text3d(F_TITLE, v3(x0 - w * 0.04f, y0 - h * 0.022f, z), right, down, px, "STEAM BIG PICTURE", rgb(220, 230, 245));
    time_t now = time(NULL);
    struct tm lt;
    localtime_r(&now, &lt);
    char clk[16];
    strftime(clk, sizeof clk, "%H:%M", &lt);
    text3d(F_TITLE, v3(x0 - w * 0.96f + text_w(F_TITLE, clk) * px, y0 - h * 0.022f, z), right, down, px, clk, rgb(200, 210, 225));
    /* a row of game tiles, the first one highlighted */
    Col tiles[5] = {rgb(70, 110, 190), rgb(170, 70, 60), rgb(60, 140, 100), rgb(160, 120, 50), rgb(110, 70, 150)};
    float tw = w * 0.16f, th = h * 0.42f, gap = w * 0.025f, ty = y0 - h * 0.22f;
    for (int i = 0; i < 5; i++) {
        float tx = x0 - w * 0.05f - i * (tw + gap);
        float grow = i == 0 ? 0.02f * h * (1 + sinf(t * 2)) : 0;
        Col c = i == 0 ? shade(tiles[i], 1.2f) : tiles[i];
        glow_quad(v3(tx + grow, ty + grow, z + 0.001f), v3(tx - tw - grow, ty + grow, z + 0.001f),
                  v3(tx - tw - grow, ty - th - grow, z + 0.001f), v3(tx + grow, ty - th - grow, z + 0.001f), c);
        if (i == 0)
            glow_quad(v3(tx + grow + 0.008f, ty + grow + 0.008f, z + 0.0015f), v3(tx - tw - grow - 0.008f, ty + grow + 0.008f, z + 0.0015f),
                      v3(tx - tw - grow - 0.008f, ty - th - grow - 0.008f, z + 0.0015f), v3(tx + grow + 0.008f, ty - th - grow - 0.008f, z + 0.0015f),
                      rgb(230, 240, 255));
    }
    const char *msg = job.kind == JOB_STEAM ? "Setting Steam up..." : nest_running(NEST_TV) ? "Starting Steam..." : steam_installed() ? "Press E to play  (remote: switch to videos)"
                                                                                     : "Press E to set up Steam  (remote: switch to videos)";
    float px2 = h * 0.05f / text_h(F_BODY);
    text3d(F_BODY, v3(x0 - w * 0.05f, y0 - h * 0.74f, z), right, down, px2, "Library", rgb(160, 175, 200));
    text3d(F_BODY, v3(x0 - w * 0.05f, y0 - h * 0.84f, z), right, down, px2, msg, rgb(235, 240, 250));
}

/* The envelope: drops through the mail slot, then lies on the doormat. */
void letter_draw(void)
{
    if (letter_t < 0) return;
    float k = clampf(letter_t / 0.8f, 0, 1);
    float x = lerpf(23.84f, 23.42f, k), y = 0.95f * (1 - k * k) + 0.022f, rot = 25 * k;
    float tilt = (1 - k) * 80;
    glPushMatrix();
    glTranslatef(x, y, 8.0f);
    glRotatef(rot, 0, 1, 0);
    glRotatef(tilt, 0, 0, 1);
    box(-0.08f, 0, -0.12f, 0.08f, 0.006f, 0.12f, rgb(245, 240, 228));
    glow_quad(v3(-0.03f, 0.0065f, 0.06f), v3(-0.03f, 0.0065f, 0.1f), v3(0.0f, 0.0065f, 0.1f), v3(0.0f, 0.0065f, 0.06f), rgb(200, 50, 50));
    glPopMatrix();
    if (k >= 1) {
        float g = 0.5f + 0.5f * sinf(letter_t * 3);
        glEnable(GL_BLEND);
        glow_quad(v3(23.42f - 0.12f, 0.02f, 7.85f), v3(23.42f + 0.12f, 0.02f, 7.85f), v3(23.42f + 0.12f, 0.02f, 8.15f), v3(23.42f - 0.12f, 0.02f, 8.15f),
                  rgba(255, 230, 140, (int)(60 * g)));
        glDisable(GL_BLEND);
    }
}
