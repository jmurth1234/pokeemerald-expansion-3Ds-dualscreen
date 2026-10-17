#ifndef CTR_CLOCK_STORE_H
#define CTR_CLOCK_STORE_H

#include <stdbool.h>
#include <stddef.h>

bool CtrClockStore_Load(const void *save, size_t size);
bool CtrClockStore_Save(const void *save, size_t size);

#endif
