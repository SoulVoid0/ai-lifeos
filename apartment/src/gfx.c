/* Drawing helpers: camera, shaded low-poly shapes, and text (SDL_ttf rendered once, cached as textures). */
#include "app.h"
#include <SDL2/SDL_ttf.h>
#include <stdlib.h>

static TTF_Font *fonts[F_COUNT];
static int font_px[F_COUNT];
static float MV[16], PR[16];
static int frame_no;

/* ------------------------------------------------------------------ fonts */

static const char *FONT_PATHS[] = {
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Regular.ttf",
    NULL,
};

static char font_file[512];

static void find_font(void)
{
    const char *env = getenv("LIFEOS_APARTMENT_FONT");
    if (env && *env) { snprintf(font_file, sizeof font_file, "%s", env); return; }
    for (int i = 0; FONT_PATHS[i]; i++) {
        FILE *f = fopen(FONT_PATHS[i], "rb");
        if (f) { fclose(f); snprintf(font_file, sizeof font_file, "%s", FONT_PATHS[i]); return; }
    }
    FILE *p = popen("fc-match -f '%{file}' sans:style=Regular 2>/dev/null", "r");
    if (p) {
        if (!fgets(font_file, sizeof font_file, p)) font_file[0] = 0;
        pclose(p);
    }
}

typedef struct {
    uint32_t hash;
    char *s;
    int font, wrap;
    GLuint tex;
    int w, h, tw, th;
    int used;
} TextTex;
#define TEXT_CACHE 384
static TextTex cache[TEXT_CACHE];

static void cache_clear(void)
{
    for (int i = 0; i < TEXT_CACHE; i++) {
        if (cache[i].tex) glDeleteTextures(1, &cache[i].tex);
        free(cache[i].s);
        memset(&cache[i], 0, sizeof cache[i]);
    }
}

float g_ui_scale = 1.0f;

void gfx_resize(int win_h)
{
    int base = (int)((win_h < 480 ? 480 : win_h) * g_ui_scale);
    int sizes[F_COUNT] = {base / 62, base / 46, base / 32, base / 15};
    if (!font_file[0]) find_font();
    cache_clear();
    for (int i = 0; i < F_COUNT; i++) {
        if (fonts[i]) TTF_CloseFont(fonts[i]);
        if (sizes[i] < 11) sizes[i] = 11;
        fonts[i] = font_file[0] ? TTF_OpenFont(font_file, sizes[i]) : NULL;
        font_px[i] = sizes[i];
        if (!fonts[i]) fprintf(stderr, "apartment: no font (%s): %s\n", font_file, TTF_GetError());
    }
}

void gfx_init(int win_h)
{
    if (TTF_Init() < 0) fprintf(stderr, "apartment: TTF_Init: %s\n", TTF_GetError());
    gfx_resize(win_h);
    glDisable(GL_CULL_FACE);
    glEnable(GL_DEPTH_TEST);
    glDepthFunc(GL_LEQUAL);
    glShadeModel(GL_SMOOTH);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glHint(GL_PERSPECTIVE_CORRECTION_HINT, GL_FASTEST);
    /* Every lit color is glColor x the light model ambient: one number dims the whole apartment. */
    glEnable(GL_COLOR_MATERIAL);
    glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE);
    gfx_brightness(1.0f);
}

void gfx_shutdown(void)
{
    cache_clear();
    for (int i = 0; i < F_COUNT; i++) if (fonts[i]) TTF_CloseFont(fonts[i]);
    TTF_Quit();
}

void gfx_frame_begin(void) { frame_no++; }

void gfx_brightness(float k)
{
    GLfloat amb[4] = {k, k, k * 0.98f, 1};
    glLightModelfv(GL_LIGHT_MODEL_AMBIENT, amb);
}

/* ------------------------------------------------------------------ camera */

void gfx_perspective(float fovy, float aspect, float zn, float zf)
{
    glMatrixMode(GL_PROJECTION);
    glLoadIdentity();
    float h = tanf(DEG(fovy) / 2) * zn, w = h * aspect;
    glFrustum(-w, w, -h, h, zn, zf);
    glMatrixMode(GL_MODELVIEW);
}

void gfx_camera(V3 eye, float yaw, float pitch)
{
    glMatrixMode(GL_MODELVIEW);
    glLoadIdentity();
    glRotatef(-pitch * 180 / PI_F, 1, 0, 0);
    glRotatef(-yaw * 180 / PI_F + 180, 0, 1, 0);
    glTranslatef(-eye.x, -eye.y, -eye.z);
    glGetFloatv(GL_MODELVIEW_MATRIX, MV);
    glGetFloatv(GL_PROJECTION_MATRIX, PR);
}

bool gfx_project(V3 p, int vw, int vh, float *sx, float *sy)
{
    float in[4] = {p.x, p.y, p.z, 1}, e[4], c[4];
    for (int i = 0; i < 4; i++) e[i] = MV[i] * in[0] + MV[4 + i] * in[1] + MV[8 + i] * in[2] + MV[12 + i] * in[3];
    for (int i = 0; i < 4; i++) c[i] = PR[i] * e[0] + PR[4 + i] * e[1] + PR[8 + i] * e[2] + PR[12 + i] * e[3];
    if (c[3] <= 0.05f) return false;
    *sx = (c[0] / c[3] * 0.5f + 0.5f) * vw;
    *sy = (1 - (c[1] / c[3] * 0.5f + 0.5f)) * vh;
    return true;
}

void gfx_ortho(int w, int h)
{
    glMatrixMode(GL_PROJECTION);
    glLoadIdentity();
    glOrtho(0, w, h, 0, -1, 1);
    glMatrixMode(GL_MODELVIEW);
    glLoadIdentity();
}

/* ------------------------------------------------------------------ shapes */

static inline void col(Col c, float k)
{
    float r = c.r * k / 255.f, g = c.g * k / 255.f, b = c.b * k / 255.f;
    glColor4f(r > 1 ? 1 : r, g > 1 ? 1 : g, b > 1 ? 1 : b, c.a / 255.f);
}

/* Fixed "sun from above-front-left" shading per face, so boxes read as 3D without any lights. */
#define K_TOP 1.0f
#define K_BOTTOM 0.55f
#define K_X 0.80f
#define K_Z 0.90f

void box(float x0, float y0, float z0, float x1, float y1, float z1, Col c)
{
    glBegin(GL_QUADS);
    col(c, K_TOP);    glVertex3f(x0, y1, z0); glVertex3f(x1, y1, z0); glVertex3f(x1, y1, z1); glVertex3f(x0, y1, z1);
    col(c, K_BOTTOM); glVertex3f(x0, y0, z0); glVertex3f(x0, y0, z1); glVertex3f(x1, y0, z1); glVertex3f(x1, y0, z0);
    col(c, K_Z);      glVertex3f(x0, y0, z1); glVertex3f(x0, y1, z1); glVertex3f(x1, y1, z1); glVertex3f(x1, y0, z1);
    col(c, K_Z * 0.92f); glVertex3f(x0, y0, z0); glVertex3f(x1, y0, z0); glVertex3f(x1, y1, z0); glVertex3f(x0, y1, z0);
    col(c, K_X);      glVertex3f(x1, y0, z0); glVertex3f(x1, y0, z1); glVertex3f(x1, y1, z1); glVertex3f(x1, y1, z0);
    col(c, K_X * 0.9f); glVertex3f(x0, y0, z0); glVertex3f(x0, y1, z0); glVertex3f(x0, y1, z1); glVertex3f(x0, y0, z1);
    glEnd();
}

void boxc(float cx, float y0, float cz, float sx, float sy, float sz, Col c)
{
    box(cx - sx / 2, y0, cz - sz / 2, cx + sx / 2, y0 + sy, cz + sz / 2, c);
}

void prism(float cx, float y0, float cz, float r0, float r1, float h, int sides, Col c)
{
    if (sides < 3) sides = 3;
    glBegin(GL_QUADS);
    for (int i = 0; i < sides; i++) {
        float a0 = 2 * PI_F * i / sides, a1 = 2 * PI_F * (i + 1) / sides;
        float k = 0.72f + 0.28f * (0.5f + 0.5f * cosf((a0 + a1) / 2 - 0.6f));
        col(c, k);
        glVertex3f(cx + cosf(a0) * r0, y0, cz + sinf(a0) * r0);
        glVertex3f(cx + cosf(a0) * r1, y0 + h, cz + sinf(a0) * r1);
        glVertex3f(cx + cosf(a1) * r1, y0 + h, cz + sinf(a1) * r1);
        glVertex3f(cx + cosf(a1) * r0, y0, cz + sinf(a1) * r0);
    }
    glEnd();
    if (r1 > 0.001f) {
        glBegin(GL_TRIANGLE_FAN);
        col(c, K_TOP);
        glVertex3f(cx, y0 + h, cz);
        for (int i = 0; i <= sides; i++) {
            float a = -2 * PI_F * i / sides;
            glVertex3f(cx + cosf(a) * r1, y0 + h, cz + sinf(a) * r1);
        }
        glEnd();
    }
}

void ball(float cx, float cy, float cz, float r, Col c)
{
    /* A low-poly ball: 6 sides, 4 rings. */
    const int S = 6, R = 4;
    glBegin(GL_QUADS);
    for (int j = 0; j < R; j++) {
        float p0 = -PI_F / 2 + PI_F * j / R, p1 = -PI_F / 2 + PI_F * (j + 1) / R;
        for (int i = 0; i < S; i++) {
            float a0 = 2 * PI_F * i / S, a1 = 2 * PI_F * (i + 1) / S;
            col(c, 0.62f + 0.38f * (j + 1) / R);
            glVertex3f(cx + cosf(a0) * cosf(p0) * r, cy + sinf(p0) * r, cz + sinf(a0) * cosf(p0) * r);
            glVertex3f(cx + cosf(a0) * cosf(p1) * r, cy + sinf(p1) * r, cz + sinf(a0) * cosf(p1) * r);
            glVertex3f(cx + cosf(a1) * cosf(p1) * r, cy + sinf(p1) * r, cz + sinf(a1) * cosf(p1) * r);
            glVertex3f(cx + cosf(a1) * cosf(p0) * r, cy + sinf(p0) * r, cz + sinf(a1) * cosf(p0) * r);
        }
    }
    glEnd();
}

void quad(V3 a, V3 b, V3 c, V3 d, Col cl)
{
    glBegin(GL_QUADS);
    col(cl, 1);
    glVertex3f(a.x, a.y, a.z); glVertex3f(b.x, b.y, b.z); glVertex3f(c.x, c.y, c.z); glVertex3f(d.x, d.y, d.z);
    glEnd();
}

void glow_quad(V3 a, V3 b, V3 c, V3 d, Col cl)
{
    GLboolean lit = glIsEnabled(GL_LIGHTING);
    glDisable(GL_LIGHTING);
    if (cl.a < 255) glEnable(GL_BLEND);
    quad(a, b, c, d, cl);
    if (cl.a < 255) glDisable(GL_BLEND);
    if (lit) glEnable(GL_LIGHTING);
}

void tex_quad(GLuint tex, float u1, float v1, V3 a, V3 b, V3 c, V3 d)
{
    GLboolean lit = glIsEnabled(GL_LIGHTING);
    glDisable(GL_LIGHTING);
    glEnable(GL_TEXTURE_2D);
    glBindTexture(GL_TEXTURE_2D, tex);
    glColor4f(1, 1, 1, 1);
    glBegin(GL_QUADS);
    glTexCoord2f(0, 0); glVertex3f(a.x, a.y, a.z);
    glTexCoord2f(u1, 0); glVertex3f(b.x, b.y, b.z);
    glTexCoord2f(u1, v1); glVertex3f(c.x, c.y, c.z);
    glTexCoord2f(0, v1); glVertex3f(d.x, d.y, d.z);
    glEnd();
    glDisable(GL_TEXTURE_2D);
    if (lit) glEnable(GL_LIGHTING);
}

/* ------------------------------------------------------------------ text */

static int pot(int v) { int p = 1; while (p < v) p <<= 1; return p; }

static TextTex *text_get(int font, int wrap, const char *s)
{
    if (!s || !*s || font < 0 || font >= F_COUNT || !fonts[font]) return NULL;
    uint32_t h = hash_str(s) ^ (uint32_t)(font * 7919 + wrap * 104729);
    int free_i = -1, lru = 0;
    for (int i = 0; i < TEXT_CACHE; i++) {
        TextTex *t = &cache[i];
        if (t->s && t->hash == h && t->font == font && t->wrap == wrap && !strcmp(t->s, s)) {
            t->used = frame_no;
            return t;
        }
        if (!t->s && free_i < 0) free_i = i;
        if (cache[i].used < cache[lru].used) lru = i;
    }
    int i = free_i >= 0 ? free_i : lru;
    TextTex *t = &cache[i];
    if (t->tex) glDeleteTextures(1, &t->tex);
    free(t->s);
    memset(t, 0, sizeof *t);
    SDL_Color white = {255, 255, 255, 255};
    SDL_Surface *surf = wrap > 0 ? TTF_RenderUTF8_Blended_Wrapped(fonts[font], s, white, (Uint32)wrap)
                                 : TTF_RenderUTF8_Blended(fonts[font], s, white);
    if (!surf) return NULL;
    SDL_Surface *conv = SDL_ConvertSurfaceFormat(surf, SDL_PIXELFORMAT_ABGR8888, 0);
    SDL_FreeSurface(surf);
    if (!conv) return NULL;
    t->w = conv->w; t->h = conv->h; t->tw = pot(conv->w); t->th = pot(conv->h);
    glGenTextures(1, &t->tex);
    glBindTexture(GL_TEXTURE_2D, t->tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, t->tw, t->th, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glPixelStorei(GL_UNPACK_ROW_LENGTH, conv->pitch / 4);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, conv->w, conv->h, GL_RGBA, GL_UNSIGNED_BYTE, conv->pixels);
    glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
    SDL_FreeSurface(conv);
    t->s = strdup(s);
    t->hash = h; t->font = font; t->wrap = wrap; t->used = frame_no;
    return t;
}

int text_w(int font, const char *s)
{
    int w = 0, h = 0;
    if (!s || !*s || !fonts[font]) return 0;
    TTF_SizeUTF8(fonts[font], s, &w, &h);
    return w;
}

int text_h(int font) { return fonts[font] ? TTF_FontLineSkip(fonts[font]) : font_px[font]; }

static void draw_tex2d(TextTex *t, float x, float y, Col c)
{
    glEnable(GL_TEXTURE_2D);
    glEnable(GL_BLEND);
    glBindTexture(GL_TEXTURE_2D, t->tex);
    glColor4ub(c.r, c.g, c.b, c.a);
    float u = (float)t->w / t->tw, v = (float)t->h / t->th;
    x = floorf(x); y = floorf(y);
    glBegin(GL_QUADS);
    glTexCoord2f(0, 0); glVertex2f(x, y);
    glTexCoord2f(u, 0); glVertex2f(x + t->w, y);
    glTexCoord2f(u, v); glVertex2f(x + t->w, y + t->h);
    glTexCoord2f(0, v); glVertex2f(x, y + t->h);
    glEnd();
    glDisable(GL_TEXTURE_2D);
}

void text(int font, float x, float y, const char *s, Col c)
{
    TextTex *t = text_get(font, 0, s);
    if (t) draw_tex2d(t, x, y, c);
}

void text_center(int font, float cx, float y, const char *s, Col c)
{
    TextTex *t = text_get(font, 0, s);
    if (t) draw_tex2d(t, cx - t->w / 2.0f, y, c);
}

int text_wrap(int font, float x, float y, int wrap, const char *s, Col c)
{
    TextTex *t = text_get(font, wrap, s);
    if (!t) return 0;
    draw_tex2d(t, x, y, c);
    return t->h;
}

int text_wrap_h(int font, int wrap, const char *s)
{
    TextTex *t = text_get(font, wrap, s);
    return t ? t->h : 0;
}

void text3d(int font, V3 o, V3 r, V3 d, float px, const char *s, Col c)
{
    TextTex *t = text_get(font, 0, s);
    if (!t) return;
    float w = t->w * px, h = t->h * px;
    V3 b = v3(o.x + r.x * w, o.y + r.y * w, o.z + r.z * w);
    V3 dd = v3(o.x + d.x * h, o.y + d.y * h, o.z + d.z * h);
    V3 cc = v3(b.x + d.x * h, b.y + d.y * h, b.z + d.z * h);
    GLboolean lit = glIsEnabled(GL_LIGHTING);
    glDisable(GL_LIGHTING);
    glEnable(GL_TEXTURE_2D);
    glEnable(GL_BLEND);
    glDepthMask(GL_FALSE);
    glBindTexture(GL_TEXTURE_2D, t->tex);
    glColor4ub(c.r, c.g, c.b, c.a);
    float u = (float)t->w / t->tw, v = (float)t->h / t->th;
    glBegin(GL_QUADS);
    glTexCoord2f(0, 0); glVertex3f(o.x, o.y, o.z);
    glTexCoord2f(u, 0); glVertex3f(b.x, b.y, b.z);
    glTexCoord2f(u, v); glVertex3f(cc.x, cc.y, cc.z);
    glTexCoord2f(0, v); glVertex3f(dd.x, dd.y, dd.z);
    glEnd();
    glDepthMask(GL_TRUE);
    glDisable(GL_BLEND);
    glDisable(GL_TEXTURE_2D);
    if (lit) glEnable(GL_LIGHTING);
}

void rect(float x, float y, float w, float h, Col c)
{
    if (c.a < 255) glEnable(GL_BLEND);
    glColor4ub(c.r, c.g, c.b, c.a);
    glBegin(GL_QUADS);
    glVertex2f(x, y); glVertex2f(x + w, y); glVertex2f(x + w, y + h); glVertex2f(x, y + h);
    glEnd();
}

void rect_line(float x, float y, float w, float h, Col c)
{
    glEnable(GL_BLEND);
    glColor4ub(c.r, c.g, c.b, c.a);
    glBegin(GL_LINE_LOOP);
    glVertex2f(x + 0.5f, y + 0.5f); glVertex2f(x + w - 0.5f, y + 0.5f);
    glVertex2f(x + w - 0.5f, y + h - 0.5f); glVertex2f(x + 0.5f, y + h - 0.5f);
    glEnd();
}
