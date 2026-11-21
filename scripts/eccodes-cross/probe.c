#include <eccodes.h>
#include <stdio.h>

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: probe <file.grib>\n"); return 2; }
    FILE* f = fopen(argv[1], "rb");
    if (!f) { perror("fopen"); return 1; }
    int err = 0;
    grib_handle* h = codes_handle_new_from_file(NULL, f, PRODUCT_GRIB, &err);
    if (!h) { printf("no handle err=%d\n", err); return 1; }
    char buf[64]; size_t len = sizeof(buf);
    err = codes_get_string(h, "shortName", buf, &len);
    printf("shortName err=%d val=%s\n", err, err ? "" : buf);
    long v = 0;
    err = codes_get_long(h, "latitudeOfFirstGridPointInDegrees", &v);
    printf("lat1 err=%d val=%ld\n", err, v);
    size_t n = 0;
    codes_get_size(h, "values", &n);
    printf("points=%zu\n", n);
    codes_handle_delete(h);
    fclose(f);
    return 0;
}
