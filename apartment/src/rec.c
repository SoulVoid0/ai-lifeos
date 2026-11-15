/* The camera in the hallway: records everything on the screen to a video in ~/Videos/LifeOS recordings, the
 * apartment, the PC (KDE desktop), the tablet, Steam, all of it, with the computer's sound.
 *
 * The recorder is this same program started as "lifeos-apartment-bin --record" in its own session, so it keeps
 * recording when the apartment closes (going to the PC). KWin only hands screen pictures to programs listed in
 * /usr/share/applications/lifeos-apartment-bin.desktop, which is this program. It asks KWin's ScreenShot2 interface
 * for the screen 15 times a second and pipes the frames to ffmpeg. "--record-toggle" (the camera, and Shift+F5 on
 * the desktop) starts a recorder or stops the running one; the running one is in $XDG_RUNTIME_DIR/lifeos-recording.
 */
#define _GNU_SOURCE
#include "app.h"
#include <dbus/dbus.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

extern char **environ;

#define FPS 15
#define MAX_W 1920

static volatile sig_atomic_t stop_rec;
static void on_signal(int s) { (void)s; stop_rec = 1; }

static void pid_path(char *out, size_t n)
{
    const char *run = getenv("XDG_RUNTIME_DIR");
    snprintf(out, n, "%s/lifeos-recording", run && *run ? run : "/tmp");
}

/* The running recorder: its pid (0 if none), when it started and the file it writes. */
pid_t rec_running(time_t *since, char *file, size_t n)
{
    char p[600];
    pid_path(p, sizeof p);
    FILE *f = fopen(p, "r");
    if (!f) return 0;
    long pid = 0, t = 0;
    char line[700] = "";
    if (fscanf(f, "%ld %ld\n", &pid, &t) != 2) pid = 0;
    if (!fgets(line, sizeof line, f)) line[0] = 0;
    fclose(f);
    line[strcspn(line, "\n")] = 0;
    if (pid <= 0 || kill((pid_t)pid, 0) != 0) return 0;
    if (since) *since = (time_t)t;
    if (file) snprintf(file, n, "%s", line);
    return (pid_t)pid;
}

static void notify(const char *title, const char *body)
{
    fprintf(stderr, "recorder: %s: %s\n", title, body);
    const char *argv[] = {"notify-send", "-a", "LifeOS", "-i", "camera-video", title, body, NULL};
    pid_t pid;
    if (posix_spawnp(&pid, "notify-send", NULL, NULL, (char *const *)argv, environ) == 0) waitpid(pid, NULL, 0);
}

static void videos_dir(char *out, size_t n)
{
    out[0] = 0;
    FILE *p = popen("xdg-user-dir VIDEOS 2>/dev/null", "r");
    if (p) {
        if (fgets(out, (int)n, p)) out[strcspn(out, "\n")] = 0;
        pclose(p);
    }
    const char *home = getenv("HOME");
    if (!out[0] || (home && !strcmp(out, home))) snprintf(out, n, "%s/Videos", home ? home : "/tmp");
    size_t l = strlen(out);
    snprintf(out + l, n - l, "/LifeOS recordings");
}

static void mkdirs(const char *path)
{
    char tmp[700];
    snprintf(tmp, sizeof tmp, "%s", path);
    for (char *p = tmp + 1; *p; p++) if (*p == '/') { *p = 0; mkdir(tmp, 0755); *p = '/'; }
    mkdir(tmp, 0755);
}

/* --record-toggle: stop the running recorder, or start one (detached). */
int rec_toggle(const char *self)
{
    pid_t pid = rec_running(NULL, NULL, 0);
    if (pid) {
        kill(pid, SIGINT);
        for (int i = 0; i < 100 && kill(pid, 0) == 0; i++) usleep(100000);
        return 0;
    }
    const char *argv[] = {self, "--record", NULL};
    posix_spawnattr_t at;
    posix_spawnattr_init(&at);
    posix_spawnattr_setflags(&at, POSIX_SPAWN_SETSID);
    int r = posix_spawn(&pid, self, NULL, &at, (char *const *)argv, environ);
    posix_spawnattr_destroy(&at);
    if (r) return 1;
    /* wait until it's really recording (or gave up), so the camera's light is right */
    for (int i = 0; i < 40; i++) {
        usleep(100000);
        if (rec_running(NULL, NULL, 0)) return 0;
        if (waitpid(pid, NULL, WNOHANG) == pid) return 1;
    }
    return 0;
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

typedef struct { uint8_t *px; int w, h, stride; bool bgra; } Shot;

/* One picture of the screen the user is on. */
static bool grab(DBusConnection *c, Shot *out, char *err_out, size_t en)
{
    int fds[2];
    if (pipe2(fds, O_CLOEXEC) < 0) return false;
    DBusMessage *m = dbus_message_new_method_call("org.kde.KWin.ScreenShot2", "/org/kde/KWin/ScreenShot2", "org.kde.KWin.ScreenShot2", "CaptureActiveScreen");
    DBusMessageIter it, dict;
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &dict);
    dict_bool(&dict, "include-cursor", TRUE);
    dict_bool(&dict, "native-resolution", FALSE);
    dbus_message_iter_close_container(&it, &dict);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_UNIX_FD, &fds[1]);
    DBusError err;
    dbus_error_init(&err);
    DBusMessage *r = dbus_connection_send_with_reply_and_block(c, m, 3000, &err);
    dbus_message_unref(m);
    close(fds[1]);
    if (!r) {
        if (err_out) snprintf(err_out, en, "%s", err.message ? err.message : "no answer");
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
            if (getenv("LIFEOS_REC_DEBUG") && k) fprintf(stderr, "recorder: key %s type %c\n", k, dbus_message_iter_get_arg_type(&v));
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
    bool bgra = format == 4 || format == 5 || format == 6, rgbaf = format == 16 || format == 17 || format == 18;
    if (!w || !h || w > 16384 || h > 16384 || stride < w * 4 || (!bgra && !rgbaf)) {
        if (err_out) snprintf(err_out, en, "unexpected picture format %u", format);
        close(fds[0]);
        return false;
    }
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
    out->px = px; out->w = (int)w; out->h = (int)h; out->stride = (int)stride; out->bgra = bgra;
    return true;
}

/* Scales a picture into the fixed BGRA video frame (nearest pixel; the frame keeps the first picture's size). */
static void to_frame(const Shot *s, uint8_t *fr, int fw, int fh)
{
    for (int y = 0; y < fh; y++) {
        int sy = (int)((long)y * s->h / fh);
        const uint8_t *row = s->px + (size_t)sy * s->stride;
        uint8_t *d = fr + (size_t)y * fw * 4;
        for (int x = 0; x < fw; x++) {
            const uint8_t *p = row + (size_t)((long)x * s->w / fw) * 4;
            if (s->bgra) { d[0] = p[0]; d[1] = p[1]; d[2] = p[2]; }
            else { d[0] = p[2]; d[1] = p[1]; d[2] = p[0]; }
            d[3] = 255;
            d += 4;
        }
    }
}

static pid_t start_ffmpeg(int fw, int fh, const char *file, bool audio, int *in_fd)
{
    int p[2];
    if (pipe2(p, O_CLOEXEC) < 0) return -1;
    char size[32], rate[8];
    snprintf(size, sizeof size, "%dx%d", fw, fh);
    snprintf(rate, sizeof rate, "%d", FPS);
    const char *argv[40];
    int n = 0;
    const char *head[] = {"ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", size,
                          "-r", rate, "-thread_queue_size", "64", "-i", "pipe:0"};
    for (size_t i = 0; i < sizeof head / sizeof *head; i++) argv[n++] = head[i];
    if (audio) {
        const char *a[] = {"-f", "pulse", "-thread_queue_size", "1024", "-i", "@DEFAULT_MONITOR@"};
        for (size_t i = 0; i < sizeof a / sizeof *a; i++) argv[n++] = a[i];
    }
    const char *v[] = {"-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart"};
    for (size_t i = 0; i < sizeof v / sizeof *v; i++) argv[n++] = v[i];
    if (audio) { argv[n++] = "-c:a"; argv[n++] = "aac"; argv[n++] = "-b:a"; argv[n++] = "160k"; }
    argv[n++] = file;
    argv[n] = NULL;
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, p[0], 0);
    pid_t pid = -1;
    /* its own process group: a Ctrl+C meant for the recorder must not cut the file short */
    posix_spawnattr_t at;
    posix_spawnattr_init(&at);
    posix_spawnattr_setflags(&at, POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&at, 0);
    int r = posix_spawnp(&pid, "ffmpeg", &fa, &at, (char *const *)argv, environ);
    posix_spawnattr_destroy(&at);
    posix_spawn_file_actions_destroy(&fa);
    close(p[0]);
    if (r) { close(p[1]); return -1; }
    *in_fd = p[1];
    return pid;
}

static bool write_all(int fd, const uint8_t *b, size_t n)
{
    while (n) {
        ssize_t w = write(fd, b, n);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return false;
        b += w; n -= (size_t)w;
    }
    return true;
}

static double now_s(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void finish_ffmpeg(pid_t pid, int fd)
{
    close(fd);
    for (int i = 0; i < 100; i++) {
        if (waitpid(pid, NULL, WNOHANG) == pid) return;
        /* the sound input never ends by itself: once the video is in, ffmpeg is told to finish the file */
        if (i == 15) kill(pid, SIGINT);
        usleep(100000);
    }
    kill(pid, SIGKILL);
    waitpid(pid, NULL, 0);
}

/* --record: runs until SIGINT/SIGTERM. */
int rec_main(void)
{
    if (rec_running(NULL, NULL, 0)) return 0;
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGHUP, on_signal);
    signal(SIGPIPE, SIG_IGN);
    DBusError err;
    dbus_error_init(&err);
    DBusConnection *c = dbus_bus_get_private(DBUS_BUS_SESSION, &err);
    if (!c) { notify("Recording didn't start", "No session bus."); return 1; }
    dbus_connection_set_exit_on_disconnect(c, FALSE);
    Shot s;
    char why[300] = "";
    if (!grab(c, &s, why, sizeof why)) {
        char b[400];
        snprintf(b, sizeof b, "KWin didn't hand over the screen: %s", why);
        notify("Recording didn't start", b);
        return 1;
    }
    int fw = s.w, fh = s.h;
    while (fw > MAX_W) { fw /= 2; fh /= 2; }
    fw &= ~1; fh &= ~1;
    char dir[600], file[700];
    videos_dir(dir, sizeof dir);
    mkdirs(dir);
    time_t t0 = time(NULL);
    struct tm lt;
    localtime_r(&t0, &lt);
    char stamp[64];
    strftime(stamp, sizeof stamp, "%Y-%m-%d %H-%M-%S", &lt);
    snprintf(file, sizeof file, "%s/Recording %s.mp4", dir, stamp);
    uint8_t *frame = malloc((size_t)fw * fh * 4);
    if (!frame) return 1;
    to_frame(&s, frame, fw, fh);
    free(s.px);
    int fd = -1;
    bool audio = !getenv("LIFEOS_REC_NO_SOUND");
    pid_t ff = start_ffmpeg(fw, fh, file, audio, &fd);
    if (ff < 0) { notify("Recording didn't start", "ffmpeg isn't installed."); return 1; }
    /* ffmpeg exits right away when the sound can't be recorded: then record without sound */
    usleep(400000);
    if (waitpid(ff, NULL, WNOHANG) == ff) {
        close(fd);
        audio = false;
        ff = start_ffmpeg(fw, fh, file, audio, &fd);
        if (ff < 0) { notify("Recording didn't start", "ffmpeg didn't run."); return 1; }
    }
    char pp[600];
    pid_path(pp, sizeof pp);
    FILE *pf = fopen(pp, "w");
    if (pf) { fprintf(pf, "%ld %ld\n%s\n", (long)getpid(), (long)t0, file); fclose(pf); }
    fprintf(stderr, "recorder: %dx%d at %d fps%s -> %s\n", fw, fh, FPS, audio ? " with sound" : "", file);
    double start = now_s();
    long written = 0;
    int failures = 0;
    while (!stop_rec) {
        long due = (long)((now_s() - start) * FPS) + 1;
        /* keep the video's clock right: repeat the last frame when a capture took longer than a frame */
        bool ok = true;
        while (written < due && ok) { ok = write_all(fd, frame, (size_t)fw * fh * 4); written++; }
        if (!ok) break;
        double next = start + (double)written / FPS;
        double wait = next - now_s();
        if (wait > 0) usleep((useconds_t)(wait * 1e6));
        if (stop_rec) break;
        if (grab(c, &s, NULL, 0)) {
            failures = 0;
            to_frame(&s, frame, fw, fh);
            free(s.px);
        } else if (++failures > 50) break;
    }
    finish_ffmpeg(ff, fd);
    unlink(pp);
    dbus_connection_close(c);
    dbus_connection_unref(c);
    free(frame);
    long secs = written / FPS;
    char b[900];
    snprintf(b, sizeof b, "%ld:%02ld saved to %s (watch it on the TV: Videos)", secs / 60, secs % 60, file);
    notify("Recording saved", b);
    return 0;
}
