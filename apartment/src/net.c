/* Talking to the LifeOS backend (http://127.0.0.1:8765) without ever blocking a frame.
 * Two worker threads: one for quick calls (status, shop) and one for slow ones (chatting with an agent can take
 * a minute on a small GPU). Callbacks run on the main thread from net_pump(). */
#include "app.h"
#include <curl/curl.h>
#include <stdlib.h>

typedef struct Job {
    char method[8];
    char url[512];
    char *body;
    int slow;
    NetDone done;
    void *user;
    long status;
    char *resp;
    size_t resp_n;
    struct Job *next;
} Job;

typedef struct { Job *head, *tail; SDL_mutex *m; SDL_cond *c; } Queue;

static Queue q_fast, q_slow, q_done;
static SDL_Thread *workers[2];
static volatile int quitting;
static char base_url[256] = "http://127.0.0.1:8765";
static volatile int online;

static void q_init(Queue *q) { q->head = q->tail = NULL; q->m = SDL_CreateMutex(); q->c = SDL_CreateCond(); }

static void q_push(Queue *q, Job *j)
{
    SDL_LockMutex(q->m);
    j->next = NULL;
    if (q->tail) q->tail->next = j; else q->head = j;
    q->tail = j;
    SDL_CondSignal(q->c);
    SDL_UnlockMutex(q->m);
}

static Job *q_pop(Queue *q, int wait)
{
    SDL_LockMutex(q->m);
    while (wait && !q->head && !quitting) SDL_CondWaitTimeout(q->c, q->m, 500);
    Job *j = q->head;
    if (j) { q->head = j->next; if (!q->head) q->tail = NULL; }
    SDL_UnlockMutex(q->m);
    return j;
}

static size_t on_data(char *ptr, size_t size, size_t nmemb, void *ud)
{
    Job *j = ud;
    size_t n = size * nmemb;
    if (j->resp_n + n > 8u << 20) return 0;  /* 8 MB is plenty for anything we ask for */
    j->resp = realloc(j->resp, j->resp_n + n + 1);
    memcpy(j->resp + j->resp_n, ptr, n);
    j->resp_n += n;
    j->resp[j->resp_n] = 0;
    return n;
}

static int worker(void *arg)
{
    Queue *q = arg;
    CURL *c = curl_easy_init();
    while (!quitting) {
        Job *j = q_pop(q, 1);
        if (!j) continue;
        struct curl_slist *h = NULL;
        h = curl_slist_append(h, "X-LifeOS-Client: 1");
        h = curl_slist_append(h, "Content-Type: application/json");
        curl_easy_reset(c);
        curl_easy_setopt(c, CURLOPT_URL, j->url);
        curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
        curl_easy_setopt(c, CURLOPT_CUSTOMREQUEST, j->method);
        curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
        curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 3L);
        curl_easy_setopt(c, CURLOPT_TIMEOUT, j->slow ? 600L : 15L);
        curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, on_data);
        curl_easy_setopt(c, CURLOPT_WRITEDATA, j);
        curl_easy_setopt(c, CURLOPT_NOPROXY, "*");
        if (j->body) curl_easy_setopt(c, CURLOPT_POSTFIELDS, j->body);
        CURLcode rc = curl_easy_perform(c);
        if (rc == CURLE_OK) curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &j->status);
        else j->status = 0;
        if (!j->slow) online = j->status > 0;
        curl_slist_free_all(h);
        q_push(&q_done, j);
    }
    curl_easy_cleanup(c);
    return 0;
}

void net_init(void)
{
    const char *env = getenv("LIFEOS_API");
    if (env && *env) snprintf(base_url, sizeof base_url, "%s", env);
    curl_global_init(CURL_GLOBAL_DEFAULT);
    q_init(&q_fast); q_init(&q_slow); q_init(&q_done);
    workers[0] = SDL_CreateThread(worker, "net-fast", &q_fast);
    workers[1] = SDL_CreateThread(worker, "net-slow", &q_slow);
}

void net_shutdown(void)
{
    quitting = 1;
    SDL_CondBroadcast(q_fast.c);
    SDL_CondBroadcast(q_slow.c);
    /* A slow chat may still be waiting on the model: don't hold up quitting for it. */
    SDL_DetachThread(workers[1]);
    SDL_WaitThread(workers[0], NULL);
}

void net_request(const char *method, const char *path, const char *body, int slow, NetDone done, void *user)
{
    Job *j = calloc(1, sizeof *j);
    snprintf(j->method, sizeof j->method, "%s", method);
    snprintf(j->url, sizeof j->url, "%s%s", base_url, path);
    j->body = body ? strdup(body) : NULL;
    j->slow = slow;
    j->done = done;
    j->user = user;
    q_push(slow ? &q_slow : &q_fast, j);
}

void net_pump(void)
{
    Job *j;
    while ((j = q_pop(&q_done, 0))) {
        if (j->done) j->done((int)j->status, j->resp ? j->resp : "", j->user);
        free(j->resp);
        free(j->body);
        free(j);
    }
}

bool net_online(void) { return online; }
