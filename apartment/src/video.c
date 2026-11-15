/* The living room TV: plays the user's own video files. ffmpeg decodes the picture into a small raw RGB
 * stream (read on a thread, uploaded to a texture), ffplay (or mpv) plays the sound. Low quality keeps the
 * decode tiny, so even an old CPU can watch while the apartment renders. */
#include "app.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static pid_t vpid, apid;
static int vfd = -1;
static SDL_Thread *reader;
static SDL_mutex *lock;
static unsigned char *frame, *back;
static volatile int fresh, finished, stop_reader;
static int FW = 320, FH = 180, TW = 512, TH = 256;
static GLuint tex;
static char cur_path[512], cur_title[128];
static double start_pos, paused_pos;
static Uint32 started_ms;
static bool playing, paused;
static int volume = 70;

static const char *EXTS[] = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv", ".ogv", ".ts", NULL};

static bool is_video(const char *n)
{
    const char *dot = strrchr(n, '.');
    if (!dot) return false;
    for (int i = 0; EXTS[i]; i++) if (!strcasecmp(dot, EXTS[i])) return true;
    return false;
}

static void scan_dir(const char *dir, int depth, VideoFile *out, int *n, int max)
{
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) && *n < max) {
        if (de->d_name[0] == '.') continue;
        char p[512];
        snprintf(p, sizeof p, "%s/%s", dir, de->d_name);
        struct stat st;
        if (stat(p, &st) < 0) continue;
        if (S_ISDIR(st.st_mode)) { if (depth > 0) scan_dir(p, depth - 1, out, n, max); continue; }
        if (!is_video(de->d_name)) continue;
        snprintf(out[*n].path, sizeof out[*n].path, "%s", p);
        snprintf(out[*n].name, sizeof out[*n].name, "%s", de->d_name);
        (*n)++;
    }
    closedir(d);
}

static int cmp_video(const void *a, const void *b) { return strcasecmp(((const VideoFile *)a)->name, ((const VideoFile *)b)->name); }

int video_scan(VideoFile *out, int max)
{
    int n = 0;
    const char *home = getenv("HOME");
    char dir[512];
    /* the XDG videos folder, then the usual places */
    FILE *p = popen("xdg-user-dir VIDEOS 2>/dev/null", "r");
    if (p) {
        if (fgets(dir, sizeof dir, p)) {
            dir[strcspn(dir, "\n")] = 0;
            if (*dir && (!home || strcmp(dir, home))) scan_dir(dir, 3, out, &n, max);
        }
        pclose(p);
    }
    if (home) {
        const char *subs[] = {"Videos", "Movies", "Downloads", "Desktop", NULL};
        for (int i = 0; subs[i]; i++) {
            snprintf(dir, sizeof dir, "%s/%s", home, subs[i]);
            bool dup = false;
            for (int k = 0; k < n; k++) if (!strncmp(out[k].path, dir, strlen(dir))) { dup = true; break; }
            if (!dup) scan_dir(dir, i == 0 ? 3 : 1, out, &n, max);
        }
    }
    scan_dir("/run/media", 3, out, &n, max);
    qsort(out, (size_t)n, sizeof *out, cmp_video);
    return n;
}

static int read_full(int fd, unsigned char *buf, int len)
{
    int got = 0;
    while (got < len) {
        ssize_t r = read(fd, buf + got, (size_t)(len - got));
        if (r <= 0) { if (r < 0 && errno == EINTR) continue; return got; }
        got += (int)r;
    }
    return got;
}

static int reader_main(void *arg)
{
    (void)arg;
    int len = FW * FH * 3;
    while (!stop_reader) {
        if (read_full(vfd, back, len) < len) { finished = 1; break; }
        SDL_LockMutex(lock);
        unsigned char *t = frame; frame = back; back = t;
        fresh = 1;
        SDL_UnlockMutex(lock);
    }
    return 0;
}

static pid_t spawn(char *const argv[], int out_fd)
{
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
    if (out_fd >= 0) posix_spawn_file_actions_adddup2(&fa, out_fd, 1);
    else posix_spawn_file_actions_addopen(&fa, 1, "/dev/null", O_WRONLY, 0);
    posix_spawn_file_actions_addopen(&fa, 2, "/dev/null", O_WRONLY, 0);
    pid_t pid = 0;
    if (posix_spawnp(&pid, argv[0], &fa, NULL, argv, environ) != 0) pid = 0;
    posix_spawn_file_actions_destroy(&fa);
    return pid;
}

static void kill_wait(pid_t *pid)
{
    if (*pid > 0) {
        kill(*pid, SIGTERM);
        for (int i = 0; i < 50; i++) { if (waitpid(*pid, NULL, WNOHANG) != 0) { *pid = 0; return; } SDL_Delay(10); }
        kill(*pid, SIGKILL);
        waitpid(*pid, NULL, 0);
    }
    *pid = 0;
}

static void stop_processes(void)
{
    stop_reader = 1;
    kill_wait(&vpid);
    kill_wait(&apid);
    if (vfd >= 0) { close(vfd); vfd = -1; }
    if (reader) { SDL_WaitThread(reader, NULL); reader = NULL; }
    stop_reader = 0;
}

void video_set_quality(int q)
{
    int w = q <= 0 ? 256 : q == 1 ? 384 : 640, h = w * 9 / 16;
    if (w == FW) return;
    bool was = playing && !paused;
    double pos = video_position();
    if (was) stop_processes();
    FW = w; FH = h;
    TW = 1; while (TW < FW) TW <<= 1;
    TH = 1; while (TH < FH) TH <<= 1;
    if (tex) { glDeleteTextures(1, &tex); tex = 0; }
    free(frame); free(back); frame = back = NULL;
    if (was) video_play(cur_path, pos);
}

void video_set_volume(int v) { volume = v < 0 ? 0 : v > 100 ? 100 : v; }

bool video_play(const char *path, double start)
{
    if (!lock) lock = SDL_CreateMutex();
    stop_processes();
    if (!frame) { frame = calloc(1, (size_t)FW * FH * 3); back = calloc(1, (size_t)FW * FH * 3); }
    int fds[2];
    if (pipe(fds) < 0) return false;
    char ss[32], vf[160], vol[16];
    snprintf(ss, sizeof ss, "%.2f", start < 0 ? 0 : start);
    snprintf(vf, sizeof vf, "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,fps=25", FW, FH, FW, FH);
    char *vargs[] = {"ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re", "-ss", ss, "-i", (char *)path,
                     "-an", "-sn", "-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1", NULL};
    vpid = spawn(vargs, fds[1]);
    close(fds[1]);
    if (!vpid) { close(fds[0]); toast("Can't play videos: ffmpeg is missing"); return false; }
    vfd = fds[0];
    snprintf(vol, sizeof vol, "%d", volume);
    char *aargs[] = {"ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", vol, "-ss", ss, (char *)path, NULL};
    apid = spawn(aargs, -1);
    if (!apid) {
        char start_opt[48], vol_opt[32];
        snprintf(start_opt, sizeof start_opt, "--start=%s", ss);
        snprintf(vol_opt, sizeof vol_opt, "--volume=%d", volume);
        char *margs[] = {"mpv", "--no-video", "--really-quiet", start_opt, vol_opt, (char *)path, NULL};
        apid = spawn(margs, -1);
    }
    finished = 0;
    fresh = 0;
    reader = SDL_CreateThread(reader_main, "tv", NULL);
    if (path != cur_path) snprintf(cur_path, sizeof cur_path, "%s", path);
    const char *slash = strrchr(cur_path, '/');
    snprintf(cur_title, sizeof cur_title, "%s", slash ? slash + 1 : cur_path);
    start_pos = start < 0 ? 0 : start;
    started_ms = SDL_GetTicks();
    playing = true;
    paused = false;
    return true;
}

void video_stop(void)
{
    stop_processes();
    playing = paused = false;
    cur_title[0] = 0;
}

void video_pause_toggle(void)
{
    if (!playing) return;
    if (!paused) {
        paused_pos = video_position();
        stop_processes();
        paused = true;
    } else {
        video_play(cur_path, paused_pos);
    }
}

void video_seek(double delta)
{
    if (!playing) return;
    double p = video_position() + delta;
    if (p < 0) p = 0;
    if (paused) { paused_pos = p; return; }
    video_play(cur_path, p);
}

bool video_active(void) { return playing; }
bool video_paused(void) { return paused; }
const char *video_title(void) { return cur_title; }

double video_position(void)
{
    if (!playing) return 0;
    if (paused) return paused_pos;
    return start_pos + (SDL_GetTicks() - started_ms) / 1000.0;
}

void video_poll(void)
{
    if (playing && !paused && finished && !fresh) {
        stop_processes();
        playing = false;
        toast("Finished: %s", cur_title);
        cur_title[0] = 0;
    }
}

GLuint video_texture(float *u, float *v)
{
    if (!playing || !frame) return 0;
    if (!tex) {
        glGenTextures(1, &tex);
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, TW, TH, 0, GL_RGB, GL_UNSIGNED_BYTE, NULL);
    }
    if (fresh) {
        SDL_LockMutex(lock);
        glBindTexture(GL_TEXTURE_2D, tex);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, FW, FH, GL_RGB, GL_UNSIGNED_BYTE, frame);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        fresh = 0;
        SDL_UnlockMutex(lock);
    }
    *u = (float)FW / TW;
    *v = (float)FH / TH;
    return tex;
}
