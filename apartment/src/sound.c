/* Sounds, all made in code (no sound files): footsteps on wood, tiles, snow, grass and asphalt, the front door,
 * clicks and menu blips, the phone, teleporting, and ambience that follows where you are: the apartment's quiet
 * room tone and the kitchen fridge, wind (strong at the South Pole), traffic near big roads, birds among trees in
 * daytime, water, and the hum of tunnels. Volumes: master, effects, ambience (media is the TV, in video.c). */
#include "app.h"
#include <stdlib.h>

#define RATE 44100
#define MAXV 24

int g_vol_master = 80, g_vol_effects = 80, g_vol_ambient = 60;
bool g_mute;

typedef struct { float *s; int n; } Clip;
static Clip clips[NSND];
typedef struct { const Clip *c; int pos; float vol; float pan; } Voice;
static Voice voices[MAXV];
static SDL_AudioDeviceID dev;
static float amb_target[NAMB], amb_level[NAMB];

static uint32_t seed = 12345;
static float noise(void) { seed = seed * 1664525u + 1013904223u; return ((seed >> 9) & 0x7fff) / 16384.0f - 1.0f; }

static Clip make(float secs) { Clip c; c.n = (int)(secs * RATE); c.s = calloc((size_t)c.n, sizeof(float)); return c; }

/* a footstep: a filtered noise burst; lp is the low-pass amount (0 bright .. 0.98 dull), crunch adds grit */
static Clip step(float secs, float lp, float crunch, float thump_hz, float gain)
{
    Clip c = make(secs);
    if (!c.s) return c;
    float y = 0, ph = 0;
    for (int i = 0; i < c.n; i++) {
        float t = (float)i / RATE, env = expf(-t * (8.0f / secs)) * fminf(1, t * 400);
        float x = noise();
        if (crunch > 0 && (noise() > 1 - crunch * 0.1f)) x *= 4;
        y = y * lp + x * (1 - lp);
        ph += 2 * PI_F * thump_hz / RATE;
        c.s[i] = (y * 0.9f + sinf(ph) * 0.5f * expf(-t * 40)) * env * gain;
    }
    return c;
}

static Clip tone(float secs, float f0, float f1, float decay, float gain, bool square)
{
    Clip c = make(secs);
    if (!c.s) return c;
    float ph = 0;
    for (int i = 0; i < c.n; i++) {
        float t = (float)i / RATE, f = f0 + (f1 - f0) * t / secs;
        ph += 2 * PI_F * f / RATE;
        float v = square ? (sinf(ph) > 0 ? 0.6f : -0.6f) : sinf(ph);
        c.s[i] = v * expf(-t * decay) * fminf(1, t * 300) * gain;
    }
    return c;
}

static Clip door(void)
{
    Clip c = make(0.9f);
    if (!c.s) return c;
    float ph = 0, y = 0;
    for (int i = 0; i < c.n; i++) {
        float t = (float)i / RATE;
        /* creak: a wobbling squeak, then the thump of the door shutting */
        float f = 380 + 120 * sinf(t * 23) + 60 * sinf(t * 71);
        ph += 2 * PI_F * f / RATE;
        float creak = t < 0.55f ? sinf(ph) * 0.18f * sinf(t / 0.55f * PI_F) * (0.6f + 0.4f * noise()) : 0;
        y = y * 0.97f + noise() * 0.03f;
        float thump = t > 0.6f ? (y * 6 + sinf(2 * PI_F * 70 * (t - 0.6f)) * 0.8f) * expf(-(t - 0.6f) * 18) : 0;
        c.s[i] = creak + thump * 0.7f;
    }
    return c;
}

static Clip whoosh(float secs)
{
    Clip c = make(secs);
    if (!c.s) return c;
    float y = 0;
    for (int i = 0; i < c.n; i++) {
        float t = (float)i / RATE, k = t / secs;
        float lp = 0.995f - 0.3f * sinf(k * PI_F);
        y = y * lp + noise() * (1 - lp);
        c.s[i] = y * sinf(k * PI_F) * 1.6f + sinf(2 * PI_F * (200 + 900 * k) * t) * 0.08f * sinf(k * PI_F);
    }
    return c;
}

static void mix_cb(void *u, Uint8 *stream, int len)
{
    (void)u;
    float *out = (float *)stream;
    int frames = len / (int)(2 * sizeof(float));
    float master = g_mute ? 0 : g_vol_master / 100.0f;
    float fx = g_vol_effects / 100.0f * master, amb = g_vol_ambient / 100.0f * master;
    static float lp_wind, lp_traffic, lp_room, lfo, bird_ph, bird_f, bird_env, drip_env, hum_ph, water_lp;
    static int bird_left;
    for (int i = 0; i < frames; i++) {
        float l = 0, r = 0;
        for (int v = 0; v < MAXV; v++) {
            Voice *vo = &voices[v];
            if (!vo->c) continue;
            float s = vo->c->s[vo->pos++] * vo->vol * fx;
            l += s * (1 - vo->pan); r += s * (1 + vo->pan);
            if (vo->pos >= vo->c->n) vo->c = NULL;
        }
        for (int a = 0; a < NAMB; a++) amb_level[a] += (amb_target[a] - amb_level[a]) * 0.00005f;
        float n = noise();
        lfo += 0.4f / RATE;
        /* wind: low-passed noise with slow gusts */
        lp_wind = lp_wind * 0.985f + n * 0.015f;
        float gust = 0.55f + 0.45f * sinf(lfo * 2.1f) * sinf(lfo * 0.7f + 1);
        float wind = lp_wind * 3.2f * gust * amb_level[A_WIND];
        /* traffic: a deeper rumble */
        lp_traffic = lp_traffic * 0.996f + noise() * 0.004f;
        float traffic = lp_traffic * 9 * (0.7f + 0.3f * sinf(lfo * 0.33f)) * amb_level[A_TRAFFIC];
        /* room tone */
        lp_room = lp_room * 0.998f + noise() * 0.002f;
        float room = lp_room * 7 * amb_level[A_ROOM];
        /* fridge / machine hum */
        hum_ph += 2 * PI_F * 50 / RATE;
        float hum = (sinf(hum_ph) * 0.5f + sinf(hum_ph * 2) * 0.3f) * 0.05f * amb_level[A_HUM];
        /* birds: short chirps now and then */
        float bird = 0;
        if (amb_level[A_BIRDS] > 0.02f) {
            if (bird_left <= 0 && noise() > 0.99993f) { bird_left = (int)(RATE * (0.06f + 0.1f * (noise() + 1))); bird_f = 2500 + 1500 * (noise() + 1); bird_env = 1; }
            if (bird_left > 0) {
                bird_left--;
                bird_ph += 2 * PI_F * (bird_f + 600 * sinf(bird_left * 0.004f)) / RATE;
                bird = sinf(bird_ph) * 0.12f * bird_env * amb_level[A_BIRDS];
                bird_env *= 0.99995f;
            }
        }
        /* water: brighter noise */
        water_lp = water_lp * 0.8f + noise() * 0.2f;
        float water = water_lp * 0.25f * amb_level[A_WATER] * (0.8f + 0.2f * sinf(lfo * 5));
        /* tunnel: drone and drips */
        float drip = 0;
        if (amb_level[A_TUNNEL] > 0.02f) {
            if (drip_env < 0.001f && noise() > 0.99997f) drip_env = 1;
            drip = sinf(2 * PI_F * 1300 * drip_env) * drip_env * 0.1f;
            drip_env *= 0.9993f;
        }
        float tunnel = (lp_traffic * 6 + drip) * amb_level[A_TUNNEL];
        float ambient = (wind + traffic + room + hum + water + tunnel) * amb;
        l += ambient + bird * amb * 0.6f;
        r += ambient * 0.95f + bird * amb * 1.4f;
        out[2 * i] = clampf(l, -1, 1);
        out[2 * i + 1] = clampf(r, -1, 1);
    }
}

void snd_init(void)
{
    if (SDL_InitSubSystem(SDL_INIT_AUDIO) < 0) { fprintf(stderr, "apartment: no sound: %s\n", SDL_GetError()); return; }
    clips[S_STEP_WOOD] = step(0.12f, 0.82f, 0, 110, 0.55f);
    clips[S_STEP_TILE] = step(0.08f, 0.45f, 0, 0, 0.35f);
    clips[S_STEP_SNOW] = step(0.22f, 0.6f, 1.0f, 0, 0.5f);
    clips[S_STEP_GRASS] = step(0.16f, 0.7f, 0.4f, 0, 0.35f);
    clips[S_STEP_ROAD] = step(0.09f, 0.55f, 0.2f, 60, 0.4f);
    clips[S_STEP_CONCRETE] = step(0.1f, 0.5f, 0, 90, 0.45f);
    clips[S_DOOR] = door();
    clips[S_CLICK] = tone(0.05f, 1800, 1200, 70, 0.25f, false);
    clips[S_BLIP] = tone(0.06f, 900, 1100, 45, 0.15f, false);
    clips[S_OPEN] = tone(0.14f, 600, 1000, 18, 0.18f, false);
    clips[S_PHONE] = tone(0.22f, 1300, 1700, 12, 0.18f, true);
    clips[S_TELEPORT] = whoosh(1.1f);
    clips[S_ERROR] = tone(0.18f, 300, 220, 10, 0.2f, true);
    SDL_AudioSpec want = {0}, have;
    want.freq = RATE; want.format = AUDIO_F32SYS; want.channels = 2; want.samples = 1024; want.callback = mix_cb;
    dev = SDL_OpenAudioDevice(NULL, 0, &want, &have, 0);
    if (!dev) { fprintf(stderr, "apartment: no sound device: %s\n", SDL_GetError()); return; }
    SDL_PauseAudioDevice(dev, 0);
}

void snd_play(int id, float vol)
{
    if (!dev || id < 0 || id >= NSND || !clips[id].s) return;
    SDL_LockAudioDevice(dev);
    int slot = 0;
    for (int i = 0; i < MAXV; i++) if (!voices[i].c) { slot = i; break; }
    voices[slot].c = &clips[id];
    voices[slot].pos = 0;
    voices[slot].vol = vol * (0.9f + 0.2f * (rand() / (float)RAND_MAX));
    voices[slot].pan = 0;
    SDL_UnlockAudioDevice(dev);
}

void snd_ambience(int which, float level) { if (which >= 0 && which < NAMB) amb_target[which] = clampf(level, 0, 1); }

void snd_shutdown(void)
{
    if (dev) { SDL_CloseAudioDevice(dev); dev = 0; }
    for (int i = 0; i < NSND; i++) free(clips[i].s);
}
