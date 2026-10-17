#ifndef CTR_LOCALE_H
#define CTR_LOCALE_H

#include "constants/global.h"

/* Native UI literals are converted from UTF-8 into the game font encoding. */
#if GAME_LANGUAGE == LANGUAGE_SPANISH
#define CTR_TEXT(english, spanish, french, italian) (spanish)
#elif GAME_LANGUAGE == LANGUAGE_FRENCH
#define CTR_TEXT(english, spanish, french, italian) (french)
#elif GAME_LANGUAGE == LANGUAGE_ITALIAN
#define CTR_TEXT(english, spanish, french, italian) (italian)
#else
#define CTR_TEXT(english, spanish, french, italian) (english)
#endif

#endif
