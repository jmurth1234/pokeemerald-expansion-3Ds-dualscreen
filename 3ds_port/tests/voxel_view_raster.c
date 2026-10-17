/* The game's camera over a whole scene, fast: the rasteriser of
 * scripts/voxel_view_scan.py (the scene is the console's own, from
 * tests/voxel_scene_dump.c; this only decides which triangle each pixel
 * shows, alpha-tested as the GPU does).
 *
 *     voxel_view_raster SCENE CAMERAS OUT [OWNERS]
 *
 * SCENE: u32 texture count, then per texture u16 width, u16 height,
 * width*height bytes of alpha and width*height bytes of mark (row major); u32 triangle count, then per
 * triangle 3 x (float x, y, z, texel u, texel v), i32 texture (-1: opaque),
 * u8 class, u8 pad[3], i32 cell.
 * CAMERAS: u32 count, then per camera float target x, y, z, pitch, yaw,
 * distance, fov and u32 width, height (voxel_building.Camera). A fov under
 * 0 is the drawing's own projection instead, -fov pixels a cell: a point at
 * height y drawn y rows up from where it stands (x across, z - y down, the
 * target at the centre), what is higher and further south in front.
 * OUT: per camera, u32 count of (cell, class) pairs seen, then that many
 * (i32 cell, u32 class, u32 pixels).
 * OWNERS (optional): per camera, width*height i32: the triangle each pixel
 * shows (-1: nothing), then width*height x 2 floats: the texel it shows.
 *
 * A class is the caller's: this counts, per camera, the pixels of each
 * class each cell shows. Class 0 is not counted; class 1 (HOLE_CLASS) is the
 * floor laid under the map, counted for the cell of the first surface below
 * it on the screen (the one in front of the gap it shows through); class 4
 * (FLAT_CLASS) only where its texture marks the texel. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HOLE_CLASS 1   /* scripts/voxel_view_scan.py HOLE: the floor under the map */
#define FLAT_CLASS 4   /* FLAT: counted only on the texels the texture marks */

typedef struct
{
    float p[3][5];
    int32_t tex;
    uint8_t cls;
    int32_t cell;
} Tri;

typedef struct
{
    uint16_t w, h;
    uint8_t *alpha, *mark;
} Tex;

static void *Read(FILE *f, size_t n)
{
    void *p = malloc(n ? n : 1);
    if (!p || fread(p, 1, n, f) != n)
    {
        fprintf(stderr, "short read\n");
        exit(1);
    }
    return p;
}

static uint32_t ReadU32(FILE *f)
{
    uint32_t v;
    if (fread(&v, 4, 1, f) != 1)
    {
        fprintf(stderr, "short read\n");
        exit(1);
    }
    return v;
}

typedef struct
{
    float eye[3], s[3], u[3], f[3], t, aspect;
    float drawn, target[3];    /* drawn > 0: the drawing's projection, px a cell */
    uint32_t w, h;
} Cam;

static void MakeCam(Cam *c, const float *in, uint32_t w, uint32_t h)
{
    const float d2r = 3.14159265358979f / 180.0f;
    float tx = in[0], ty = in[1], tz = in[2];
    float p = in[3] * d2r, yw = in[4] * d2r, dist = in[5], fov = in[6];
    float fl, sl;
    c->drawn = fov < 0.0f ? -fov : 0.0f;
    c->target[0] = tx;
    c->target[1] = ty;
    c->target[2] = tz;
    c->eye[0] = tx + sinf(yw) * dist;
    c->eye[1] = ty + tanf(p) * dist;
    c->eye[2] = tz + cosf(yw) * dist;
    c->f[0] = tx - c->eye[0];
    c->f[1] = ty - c->eye[1];
    c->f[2] = tz - c->eye[2];
    fl = sqrtf(c->f[0] * c->f[0] + c->f[1] * c->f[1] + c->f[2] * c->f[2]);
    for (int i = 0; i < 3; ++i)
        c->f[i] /= fl;
    /* s = f x up, up = (0, 1, 0) */
    c->s[0] = -c->f[2];
    c->s[1] = 0.0f;
    c->s[2] = c->f[0];
    sl = sqrtf(c->s[0] * c->s[0] + c->s[2] * c->s[2]);
    c->s[0] /= sl;
    c->s[2] /= sl;
    c->u[0] = c->s[1] * c->f[2] - c->s[2] * c->f[1];
    c->u[1] = c->s[2] * c->f[0] - c->s[0] * c->f[2];
    c->u[2] = c->s[0] * c->f[1] - c->s[1] * c->f[0];
    c->t = c->drawn > 0.0f ? 1.0f : 1.0f / tanf(fov * d2r / 2.0f);
    c->w = w;
    c->h = h;
    c->aspect = (float)w / (float)h;
}

/* screen x, y, the texel weight (1/z) and the depth (larger: nearer); 0
 * when behind the near plane */
static int Project(const Cam *c, const float *p, float *out)
{
    if (c->drawn > 0.0f)
    {
        out[0] = (p[0] - c->target[0]) * c->drawn + 0.5f * (float)c->w;
        out[1] = ((p[2] - c->target[2]) - (p[1] - c->target[1])) * c->drawn + 0.5f * (float)c->h;
        out[2] = 1.0f;
        out[3] = p[1] + p[2];
        return 1;
    }
    float d[3] = {p[0] - c->eye[0], p[1] - c->eye[1], p[2] - c->eye[2]};
    float x = d[0] * c->s[0] + d[1] * c->s[1] + d[2] * c->s[2];
    float y = d[0] * c->u[0] + d[1] * c->u[1] + d[2] * c->u[2];
    float z = d[0] * c->f[0] + d[1] * c->f[1] + d[2] * c->f[2];
    if (z < 0.05f)
        return 0;
    out[0] = (x * c->t / c->aspect / z * 0.5f + 0.5f) * (float)c->w;
    out[1] = (0.5f - y * c->t / z * 0.5f) * (float)c->h;
    out[2] = out[3] = 1.0f / z;
    return 1;
}

int main(int argc, char **argv)
{
    FILE *f, *out, *owners = NULL;
    uint32_t ntex, ntri, ncam;
    Tex *texs;
    Tri *tris;
    float *depth = NULL, *texel = NULL;
    int32_t *own = NULL;
    size_t cap = 0;
    int32_t maxCell = 0;
    uint32_t *count;
    int32_t *touched;

    if (argc < 4)
    {
        fprintf(stderr, "usage: voxel_view_raster SCENE CAMERAS OUT [OWNERS]\n");
        return 2;
    }
    f = fopen(argv[1], "rb");
    if (!f)
        return 1;
    ntex = ReadU32(f);
    texs = calloc(ntex ? ntex : 1, sizeof(Tex));
    for (uint32_t i = 0; i < ntex; ++i)
    {
        uint16_t wh[2];
        if (fread(wh, 2, 2, f) != 2)
            return 1;
        texs[i].w = wh[0];
        texs[i].h = wh[1];
        texs[i].alpha = Read(f, (size_t)wh[0] * wh[1]);
        texs[i].mark = Read(f, (size_t)wh[0] * wh[1]);
    }
    ntri = ReadU32(f);
    tris = malloc(sizeof(Tri) * (ntri ? ntri : 1));
    for (uint32_t i = 0; i < ntri; ++i)
    {
        uint8_t pad[4];
        if (fread(tris[i].p, sizeof(float), 15, f) != 15 || fread(&tris[i].tex, 4, 1, f) != 1
            || fread(pad, 1, 4, f) != 4 || fread(&tris[i].cell, 4, 1, f) != 1)
            return 1;
        tris[i].cls = pad[0];
        if (tris[i].cell > maxCell)
            maxCell = tris[i].cell;
    }
    fclose(f);

    f = fopen(argv[2], "rb");
    if (!f)
        return 1;
    ncam = ReadU32(f);
    out = fopen(argv[3], "wb");
    if (argc > 4)
        owners = fopen(argv[4], "wb");
    count = calloc((size_t)(maxCell + 1) * 256, sizeof(uint32_t));
    touched = malloc(sizeof(int32_t) * (size_t)(maxCell + 1) * 256);
    for (uint32_t k = 0; k < ncam; ++k)
    {
        float in[7];
        uint32_t wh[2];
        Cam cam;
        size_t npx;
        uint32_t ntouched = 0;
        if (fread(in, 4, 7, f) != 7 || fread(wh, 4, 2, f) != 2)
            return 1;
        MakeCam(&cam, in, wh[0], wh[1]);
        npx = (size_t)wh[0] * wh[1];
        if (npx > cap)
        {
            cap = npx;
            depth = realloc(depth, sizeof(float) * cap);
            own = realloc(own, sizeof(int32_t) * cap);
            texel = realloc(texel, sizeof(float) * 2 * cap);
        }
        for (size_t i = 0; i < npx; ++i)
        {
            depth[i] = -1e30f;
            own[i] = -1;
            texel[2 * i] = texel[2 * i + 1] = 0.0f;
        }
        for (uint32_t n = 0; n < ntri; ++n)
        {
            const Tri *t = &tris[n];
            float v[3][4];
            float area, inv;
            int minx, maxx, miny, maxy;
            const Tex *tx = t->tex >= 0 ? &texs[t->tex] : NULL;
            if (!Project(&cam, t->p[0], v[0]) || !Project(&cam, t->p[1], v[1])
                || !Project(&cam, t->p[2], v[2]))
                continue;
            area = (v[1][0] - v[0][0]) * (v[2][1] - v[0][1]) - (v[2][0] - v[0][0]) * (v[1][1] - v[0][1]);
            if (fabsf(area) < 1e-9f)
                continue;
            minx = (int)floorf(fminf(v[0][0], fminf(v[1][0], v[2][0])));
            maxx = (int)ceilf(fmaxf(v[0][0], fmaxf(v[1][0], v[2][0])));
            miny = (int)floorf(fminf(v[0][1], fminf(v[1][1], v[2][1])));
            maxy = (int)ceilf(fmaxf(v[0][1], fmaxf(v[1][1], v[2][1])));
            if (minx < 0) minx = 0;
            if (miny < 0) miny = 0;
            if (maxx > (int)wh[0] - 1) maxx = (int)wh[0] - 1;
            if (maxy > (int)wh[1] - 1) maxy = (int)wh[1] - 1;
            if (minx > maxx || miny > maxy)
                continue;
            inv = 1.0f / area;
            for (int py = miny; py <= maxy; ++py)
            {
                float cy = py + 0.5f;
                for (int px = minx; px <= maxx; ++px)
                {
                    float cx = px + 0.5f;
                    float a = ((v[1][0] - cx) * (v[2][1] - cy) - (v[2][0] - cx) * (v[1][1] - cy)) * inv;
                    float b = ((v[2][0] - cx) * (v[0][1] - cy) - (v[0][0] - cx) * (v[2][1] - cy)) * inv;
                    float c = 1.0f - a - b;
                    float d, q, u = 0.0f, w = 0.0f;
                    size_t idx;
                    if (a < -1e-7f || b < -1e-7f || c < -1e-7f)
                        continue;
                    d = a * v[0][3] + b * v[1][3] + c * v[2][3];
                    idx = (size_t)py * wh[0] + (size_t)px;
                    if (d <= depth[idx])
                        continue;
                    /* perspective-correct texel, as the GPU samples it */
                    q = a * v[0][2] + b * v[1][2] + c * v[2][2];
                    u = (a * t->p[0][3] * v[0][2] + b * t->p[1][3] * v[1][2]
                         + c * t->p[2][3] * v[2][2]) / q;
                    w = (a * t->p[0][4] * v[0][2] + b * t->p[1][4] * v[1][2]
                         + c * t->p[2][4] * v[2][2]) / q;
                    if (tx)
                    {
                        int tu = (int)floorf(u), tv = (int)floorf(w);
                        if (tu < 0 || tv < 0 || tu >= tx->w || tv >= tx->h)
                            continue;
                        if (tx->alpha[(size_t)tv * tx->w + (size_t)tu] < 128)
                            continue;
                    }
                    depth[idx] = d;
                    own[idx] = (int32_t)n;
                    texel[2 * idx] = u;
                    texel[2 * idx + 1] = w;
                }
            }
        }
        for (size_t i = 0; i < npx; ++i)
        {
            const Tri *t;
            size_t key;
            int32_t cell;
            if (own[i] < 0)
                continue;
            t = &tris[own[i]];
            if (!t->cls || t->cell < 0)
                continue;
            if (t->cls == FLAT_CLASS)
            {
                const Tex *tx = t->tex >= 0 ? &texs[t->tex] : NULL;
                int tu = (int)floorf(texel[2 * i]), tv = (int)floorf(texel[2 * i + 1]);
                if (!tx || tu < 0 || tv < 0 || tu >= tx->w || tv >= tx->h
                    || !tx->mark[(size_t)tv * tx->w + (size_t)tu])
                    continue;
            }
            cell = t->cell;
            if (t->cls == HOLE_CLASS)
            {
                /* the floor seen through a gap lies under whatever is
                 * behind the gap; the gap is the fault of what stands in
                 * front of it: the first surface below it on the screen */
                for (size_t j = i + wh[0]; j < npx; j += wh[0])
                    if (own[j] >= 0 && tris[own[j]].cls != HOLE_CLASS)
                    {
                        if (tris[own[j]].cell >= 0)
                            cell = tris[own[j]].cell;
                        break;
                    }
            }
            key = (size_t)cell * 256 + t->cls;
            if (!count[key]++)
                touched[ntouched++] = (int32_t)key;
        }
        fwrite(&ntouched, 4, 1, out);
        for (uint32_t i = 0; i < ntouched; ++i)
        {
            int32_t cell = touched[i] / 256;
            uint32_t cls = (uint32_t)(touched[i] % 256), n = count[touched[i]];
            fwrite(&cell, 4, 1, out);
            fwrite(&cls, 4, 1, out);
            fwrite(&n, 4, 1, out);
            count[touched[i]] = 0;
        }
        if (owners)
        {
            fwrite(own, sizeof(int32_t), npx, owners);
            fwrite(texel, sizeof(float), 2 * npx, owners);
        }
    }
    fclose(f);
    fclose(out);
    if (owners)
        fclose(owners);
    return 0;
}
