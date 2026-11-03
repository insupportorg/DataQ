"""Final path duration runs through the exit. python tests/test_path_exit.py"""
from native_harness import APP, function, run

source = (APP / "main.c").read_text(encoding="utf-8-sig")
helpers = "\n".join(function(source, signature) for signature in (
    "static void Path_Add_Box_From(",
    "static void Path_Add_Box(",
    "cJSON* ProcessPaths(",
))
run(r'''
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "cJSON.h"
#define CHECK(condition) do { if (!(condition)) { fprintf(stderr, "Failed: %s at %d\n", #condition, __LINE__); exit(1); } } while (0)
int GeoSpace_transform(int x, int y, double *lat, double *lon) { (void)x; (void)y; (void)lat; (void)lon; return 0; }
double ACAP_DEVICE_Timestamp(void) { return 5000; }
cJSON* PathCache;
''' + helpers + r'''
static cJSON* tracker(double timestamp, double previous, int active, int cx) {
    cJSON* obj = cJSON_CreateObject();
    cJSON_AddStringToObject(obj, "id", "7");
    cJSON_AddStringToObject(obj, "class", "Human");
    cJSON_AddNumberToObject(obj, "confidence", 90);
    cJSON_AddNumberToObject(obj, "age", 2);
    cJSON_AddNumberToObject(obj, "distance", 2);
    cJSON_AddNumberToObject(obj, "timestamp", timestamp);
    cJSON_AddNumberToObject(obj, "previousTimestamp", previous);
    cJSON_AddNumberToObject(obj, "birth", 1000);
    cJSON_AddNumberToObject(obj, "cx", cx);
    cJSON_AddNumberToObject(obj, "cy", 10);
    cJSON_AddNumberToObject(obj, "bx", 0);
    cJSON_AddNumberToObject(obj, "by", 0);
    cJSON_AddNumberToObject(obj, "x", cx);
    cJSON_AddNumberToObject(obj, "y", 0);
    cJSON_AddNumberToObject(obj, "w", 10);
    cJSON_AddNumberToObject(obj, "h", 10);
    cJSON_AddNumberToObject(obj, "dx", cx);
    cJSON_AddNumberToObject(obj, "dy", 10);
    cJSON_AddBoolToObject(obj, "active", active);
    return obj;
}
static void step(double timestamp, double previous, int active, int cx) {
    cJSON* obj = tracker(timestamp, previous, active, cx);
    cJSON* path = ProcessPaths(obj);
    CHECK(path == NULL);
    cJSON_Delete(obj);
}
int main(void) {
    step(1000, 1000, 1, 10);
    step(2000, 1000, 1, 30);

    cJSON* exit_tracker = tracker(2000, 1000, 0, 30);
    cJSON* path = ProcessPaths(exit_tracker);
    CHECK(cJSON_GetObjectItem(exit_tracker, "timestamp")->valuedouble == 2000);
    CHECK(path);
    cJSON* points = cJSON_GetObjectItem(path, "path");
    CHECK(cJSON_GetArraySize(points) == 3);
    cJSON* last = cJSON_GetArrayItem(points, 2);
    CHECK(cJSON_GetObjectItem(last, "d")->valuedouble == 4);
    CHECK(cJSON_GetObjectItem(path, "dwell")->valuedouble == 4);
    cJSON_Delete(path);
    cJSON_Delete(exit_tracker);
    cJSON_Delete(PathCache);

    puts("PASS: final path point covers the time until exit.");
    return 0;
}
''', sources=("cJSON.c",))
