#ifndef CTR_FIELD_VIEW_H
#define CTR_FIELD_VIEW_H

/*
 * The part of the overworld the top screen shows, in metatiles from the
 * player's own: which map objects have to exist (event_object_movement.c).
 *
 * The game has room for 16 of them, a size the save keeps. The original
 * spawns those within a fixed window around the camera; the 3DS screen is
 * wider than the GBA's, and the voxel camera looks north across many more
 * rows than it does south, as far as its pitch and zoom take it. A window
 * large enough for that holds more people than there is room for, and the
 * ones that came first kept the room: people standing on screen never came
 * into existence while others out of sight did. The view says how far the
 * window reaches and which of the people in it come first.
 *
 * The 2D field is a rectangle. The voxel view is a trapezoid over the ground,
 * narrow at the player's feet and wide at its far (northern) edge.
 */
typedef struct
{
    float north;         /* rows seen up the screen from the player's */
    float south;         /* rows seen down it */
    float farHalfWidth;  /* columns either side at the northern edge */
    float nearHalfWidth; /* columns either side at the southern edge */
} CtrFieldView;

/* The 2D field: the whole top screen around the player. */
void CtrFieldView_Flat(CtrFieldView *view);
/* The voxel camera at this pitch (degrees) and zoom (percent). */
void CtrFieldView_Voxel(CtrFieldView *view, float pitchDegrees, float zoomPercent);
/* Tiles by which the tile (dx, dy) from the player's (east and south
 * positive) lies outside the view; 0 within it. */
float CtrFieldView_Outside(const CtrFieldView *view, int dx, int dy);
/* Which of the people at these tiles come first: smaller first. Tiles out of
 * sight count double, then the distance from the player. */
float CtrFieldView_Score(const CtrFieldView *view, int dx, int dy);
/* The rectangle around the view, whole metatiles, each side `margin` more. */
void CtrFieldView_Window(const CtrFieldView *view, int margin, int *sideways, int *north, int *south);

/* What the overworld shows with the player's current options, and a number
 * that changes whenever that does (device only: it reads the settings). */
const CtrFieldView *CtrFieldView_Current(void);
unsigned CtrFieldView_Generation(void);

#endif
