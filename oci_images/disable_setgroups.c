#define _GNU_SOURCE
#include <sys/types.h>
#include <errno.h>

int setgroups(size_t size, const gid_t *list) {
    return 0;
}
