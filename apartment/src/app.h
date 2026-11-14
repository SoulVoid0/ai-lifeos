/* LifeOS Apartment: the 3D desktop of the LifeOS operating system.
 *
 * C + SDL2 + OpenGL 1.x fixed function, so it runs on almost anything, including software rendering
 * (llvmpipe) in a virtual machine. Everything is built from low-poly boxes and prisms made in code: no
 * asset files besides a system font.
 */
#pragma once
#include <SDL2/SDL.h>
#include <SDL2/SDL_opengl.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define PI_F 3.14159265f
#define DEG(x) ((x) * PI_F / 180.0f)

typedef struct { float x, y, z; } V3;
typedef struct { uint8_t r, g, b, a; } Col;

static inline V3 v3(float x, float y, float z) { V3 v = {x, y, z}; return v; }
static inline Col rgb(int r, int g, int b) { Col c = {(uint8_t)r, (uint8_t)g, (uint8_t)b, 255}; return c; }
static inline Col rgba(int r, int g, int b, int a) { Col c = {(uint8_t)r, (uint8_t)g, (uint8_t)b, (uint8_t)a}; return c; }
static inline Col shade(Col c, float k) {
    float r = c.r * k, g = c.g * k, b = c.b * k;
    return rgba(r > 255 ? 255 : (int)r, g > 255 ? 255 : (int)g, b > 255 ? 255 : (int)b, c.a);
}
static inline Col mix(Col a, Col b, float t) {
    return rgba((int)(a.r + (b.r - a.r) * t), (int)(a.g + (b.g - a.g) * t), (int)(a.b + (b.b - a.b) * t),
                (int)(a.a + (b.a - a.a) * t));
}
static inline float dist2(float ax, float az, float bx, float bz) { float dx = ax - bx, dz = az - bz; return sqrtf(dx * dx + dz * dz); }
static inline float clampf(float v, float a, float b) { return v < a ? a : v > b ? b : v; }
static inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }
static inline float wrap_angle(float a) { while (a > PI_F) a -= 2 * PI_F; while (a < -PI_F) a += 2 * PI_F; return a; }
static inline uint32_t hash_str(const char *s) { uint32_t h = 2166136261u; while (*s) { h ^= (uint8_t)*s++; h *= 16777619u; } return h; }

/* ------------------------------------------------------------------ gfx.c */
enum { F_SMALL, F_BODY, F_TITLE, F_HUGE, F_COUNT };
void gfx_init(int win_h);
extern float g_ui_scale;   /* text and menu size, 0.75..2 */
void gfx_resize(int win_h);
void gfx_shutdown(void);
void gfx_frame_begin(void);                 /* text cache bookkeeping */
void gfx_perspective(float fovy, float aspect, float zn, float zf);
void gfx_camera(V3 eye, float yaw, float pitch);
bool gfx_project(V3 p, int vw, int vh, float *sx, float *sy);  /* world -> window pixels; false if behind */
void gfx_ortho(int w, int h);
void gfx_brightness(float k);               /* multiplies every lit color (night, power cuts...) */

/* World geometry (immediate mode; also used while compiling display lists). */
void box(float x0, float y0, float z0, float x1, float y1, float z1, Col c);
void boxc(float cx, float y0, float cz, float sx, float sy, float sz, Col c);  /* centered on x/z, from y0 up */
void prism(float cx, float y0, float cz, float r0, float r1, float h, int sides, Col c);
void ball(float cx, float cy, float cz, float r, Col c);
void quad(V3 a, V3 b, V3 c, V3 d, Col col);
void glow_quad(V3 a, V3 b, V3 c, V3 d, Col col);   /* unlit (full bright) */

/* Text and 2D. */
int text_w(int font, const char *s);
int text_h(int font);
void text(int font, float x, float y, const char *s, Col c);
void text_center(int font, float cx, float y, const char *s, Col c);
int text_wrap(int font, float x, float y, int wrap, const char *s, Col c);   /* returns height */
int text_wrap_h(int font, int wrap, const char *s);
void text3d(int font, V3 origin, V3 right, V3 down, float px_size, const char *s, Col c);  /* text on a surface */
void rect(float x, float y, float w, float h, Col c);
void rect_line(float x, float y, float w, float h, Col c);
void tex_quad(GLuint tex, float u1, float v1, V3 a, V3 b, V3 c, V3 d);  /* a=top-left, clockwise */

/* ------------------------------------------------------------------ json.c */
typedef enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ } JType;
typedef struct J {
    JType t;
    double n;
    char *s;
    int count;
    struct J *items;
    char **keys;
} J;
J *json_parse(const char *text);
void json_free(J *j);
J *jget(J *obj, const char *key);
J *jat(J *arr, int i);
const char *jstr(J *obj, const char *key, const char *def);
double jnum(J *obj, const char *key, double def);
bool jbool(J *obj, const char *key, bool def);
void json_escape(char *out, size_t n, const char *s);

/* ------------------------------------------------------------------ net.c */
typedef void (*NetDone)(int status, const char *body, void *user);
void net_init(void);
void net_shutdown(void);
void net_request(const char *method, const char *path, const char *json_body, int slow, NetDone done, void *user);
void net_pump(void);          /* runs finished callbacks on the main thread */
bool net_online(void);

/* ------------------------------------------------------------------ world.c */
enum { R_LIVING, R_MASTER, R_KITCHEN, R_BATH, R_AI1, R_AI2, R_WORK, R_HALL, NROOMS };
extern const char *ROOM_KEYS[NROOMS];
extern const char *ROOM_NAMES[NROOMS];

typedef struct { float x0, z0, x1, z1; } Rect;

enum { SPOT_BED, SPOT_DESK, SPOT_EAT, SPOT_RELAX, SPOT_STAND, SPOT_BATH, SPOT_KITCHEN };
typedef struct {
    int kind, room;
    V3 pos;          /* where the body stands/sits/lies (y = seat or mattress height) */
    float yaw;       /* facing; 0 = +z (south) */
    int owner;       /* agent index for beds/desks, -1 for shared spots */
    int occupant;    /* agent index using it now, -1 if free */
    int pose;        /* 0 stand, 1 sit, 2 lie */
} Spot;

enum { I_NONE, I_AGENT, I_COUCH, I_TV, I_PC, I_BED, I_SHOP, I_FRIDGE, I_SINK, I_WHITEBOARD, I_PIANO, I_ARCADE, I_LIGHT, I_TABLET };
typedef struct { int kind; V3 pos; float reach; int ref; const char *label; } Inter;

#define MAX_COLLIDERS 256
#define MAX_SPOTS 96
#define MAX_INTERS 48
typedef struct {
    Rect colliders[MAX_COLLIDERS]; int ncolliders;
    Rect walls[MAX_COLLIDERS]; int nwalls;          /* subset: walls only (sight lines) */
    Spot spots[MAX_SPOTS]; int nspots;
    Inter inters[MAX_INTERS]; int ninters;
    GLuint list;                                   /* static geometry display list */
    /* fixed points other code needs */
    V3 tv_center; float tv_w, tv_h;
    V3 pc_screen; V3 pc_view_eye; V3 pc_stand;
    V3 whiteboard;
    V3 master_bed_lie; V3 spawn;
    V3 tablet; V3 tablet_eye;                      /* the tablet on the master bed and where you look at it from */
    int couch_seats[3];
} World;
extern World W;

/* What the user bought and chose (from the backend, cached locally). */
#define MAX_ITEMS 64
typedef struct { char id[40]; char name[48]; char kind[8]; int cost; bool owned; Col rgb; char desc[80]; } Item;
typedef struct {
    int wall[NROOMS], floor[NROOMS];   /* item indexes */
    int couch;
    bool decor[MAX_ITEMS];             /* by item index */
    bool extra[MAX_ITEMS];
} Layout;
typedef struct {
    Item items[MAX_ITEMS]; int nitems;
    Layout lay;
    int balance, earned, spent;
    int counts[5];
    bool loaded;
} Shop;
extern Shop SHOP;
int shop_find(const char *id);
bool lay_has(const char *id);            /* decor or extra switched on */
void shop_defaults(void);

void world_build(void);                  /* (re)compiles the static geometry from SHOP.lay */
int room_at(float x, float z);
bool collide_circle(float *x, float *z, float r);
bool sight_clear(float ax, float az, float bx, float bz);
void world_draw_static(void);             /* the compiled rooms, skipping ones outside the view */
void world_draw_dynamic(float t, float sky_k);   /* windows, screens, aquarium fish, fire... */
Col sky_color(float *k_out);             /* from the real clock */
void door_points(int room, V3 *inside, V3 *outside);
V3 room_hub(int room);

/* ------------------------------------------------------------------ npc.c */
#define MAX_AGENTS 16
enum { ACT_SLEEP, ACT_WORK, ACT_EAT, ACT_RELAX, ACT_WANDER, ACT_ROOM, ACT_BATH, ACT_SNACK, ACT_BOARD };
typedef struct {
    char slug[40], name[48], role[64], state[16], task[200];
    Col color;
    bool enabled, present;
    /* simulation */
    float x, z, y, yaw;
    int act, spot;
    V3 path[12]; int npath, pathi;
    float walk_phase, leisure_timer, idle_t;
    bool moving, chatting;
    int pose;            /* 0 stand, 1 sit, 2 lie */
    float bubble_t; char bubble[120];
} Agent;
extern Agent AG[MAX_AGENTS];
extern int NAG;
void npc_set_roster(J *agents);          /* /api/crew/status "agents" */
void npc_default_roster(void);
void npc_place_all(void);                /* teleport everyone to where they should be now */
void npc_update(float dt, float px, float pz);
void npc_draw(float t);
const char *npc_status_text(const Agent *a);
void npc_face(Agent *a, float x, float z);
void npc_say(Agent *a, const char *s);
Col crew_color(const char *name);          /* a crew color name (violet, amber...) as RGB */

/* ------------------------------------------------------------------ video.c */
#define MAX_VIDEOS 200
typedef struct { char path[512]; char name[128]; } VideoFile;
int video_scan(VideoFile *out, int max);
bool video_play(const char *path, double start);
void video_stop(void);
void video_pause_toggle(void);
void video_seek(double delta);
bool video_active(void);
bool video_paused(void);
double video_position(void);
const char *video_title(void);
void video_set_volume(int v);
void video_set_quality(int q);
GLuint video_texture(float *u, float *v);  /* uploads the newest frame; 0 if nothing to show */
void video_poll(void);                     /* notices the end of a file */

/* ------------------------------------------------------------------ pccap.c */
void pccap_set(bool on, bool low_power);   /* capture the live KDE desktop for the PC monitor (worker thread) */
GLuint pccap_texture(float *u, float *v);  /* the newest picture; 0 while there is none */
bool pccap_failed(void);                   /* KWin refused or isn't there: show the drawn picture */
void pccap_shutdown(void);

/* ------------------------------------------------------------------ main.c (shared state) */
typedef struct {
    int quality;        /* 0 low, 1 medium, 2 high */
    float sens;
    bool invert;
    float fov;
    int volume;
    bool show_fps, show_names;
    int display;
} Settings;
extern Settings CFG;
extern float NOW_HOURS;   /* local time of day, 0..24 */
extern int WIN_W, WIN_H;
const char *state_dir(void);
void toast(const char *fmt, ...);
