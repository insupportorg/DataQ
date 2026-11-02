"""Regression: python tests/test_vod_drop.py (see native_harness.py for compiler setup)."""
import re
from native_harness import APP, function, run

source = (APP / "VOD.c").read_text(encoding="utf-8-sig")
# Exercise the production cache and callback; stub only the SDK/transport interfaces.
types = source[source.index("typedef struct {"):source.index("static vod_internal_ctx_t g_ctx")]
limits = "\n".join(re.search(rf"^static [^;\n]*\b{name}\b[^;\n]*;", source, re.M).group(0) for name in (
    "MAX_MISSING_FRAMES", "MAX_UNACCEPTED_SECONDS", "SCENE_GAP_SECONDS", "last_scene_time",
))
functions = "\n".join(function(source, signature) for signature in (
    "static void free_tracked_object(",
    "static void clear_object_map(",
    "static tracked_object_t *find_tracked(",
    "static tracked_object_t *create_tracked(",
    "static void remove_inactive_objects_from_cache(",
    "static void transform_bbox(",
    "static void detection_callback(",
))
run(r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "VOD.h"
#include "video_object_detection.pb-c.h"
#define CHECK(condition) do { if (!(condition)) { fprintf(stderr, "Failed: %s at %d\n", #condition, __LINE__); exit(1); } } while (0)
#define LOG_WARN(...) ((void)0)
#define LOG_TRACE(...) ((void)0)
#ifdef _MSC_VER
#define strdup _strdup
#endif
typedef struct sdk_class video_object_detection_subscriber_class_t;
static int vod_mutex;
static int vod_predictions;
static double last_detection_time;
static int consecutive_timeouts;
static void pthread_mutex_lock(int* mutex) {}
static void pthread_mutex_unlock(int* mutex) {}
static double g_get_real_time(void) { return 100000000; }
static int64_t now_us = 1000000000;
static int64_t g_get_monotonic_time(void) { return now_us; }
static const char* get_class_name(int id) { return "Human"; }
static const char* get_attr_type_name(uint32_t id) { return "Color"; }
static const char* get_attr_class_name(uint32_t type, uint32_t id) { return "Blue"; }
static VOD__Scene input_scene;
VOD__Scene* vod__scene__unpack(ProtobufCAllocator* allocator, size_t size, const uint8_t* data) {
    return &input_scene;
}
void vod__scene__free_unpacked(VOD__Scene* scene, ProtobufCAllocator* allocator) {}
''' + limits + "\n" + types + "\nstatic vod_internal_ctx_t g_ctx = {0};\n" + functions + r'''
static vod_object_t observed[4];
static size_t observed_count;
static void receive(const vod_object_t* objects, size_t count, void* data) {
    CHECK(count <= 4);
    observed_count = count;
    memcpy(observed, objects, count * sizeof(*objects));
}
static void frame(VOD__Detection** detections, size_t count, VOD__Event** events, size_t event_count) {
    input_scene.n_detections = count;
    input_scene.detections = detections;
    input_scene.n_events = event_count;
    input_scene.events = events;
    observed_count = 0;
    now_us += 100000;
    detection_callback(NULL, 0, NULL);
}
static void expect(const char* id, bool active) {
    for (size_t i = 0; i < observed_count; ++i) {
        if (strcmp(observed[i].id, id) == 0) {
            CHECK(observed[i].active == active);
            CHECK(observed[i].confidence == 90);
            CHECK(strcmp(observed[i].class_name, "Human") == 0);
            return;
        }
    }
    CHECK(!"Expected object missing from callback");
}
static void empty_frames(int count) {
    for (int i = 0; i < count; ++i) frame(NULL, 0, NULL, 0);
}
static void reset(void) {
    clear_object_map();
    memset(&g_ctx, 0, sizeof(g_ctx));
    g_ctx.cb = receive;
    g_ctx.num_classes = 1;
    vod_predictions = 0;
    last_scene_time = 0;
}
int main(void) {
    VOD__Detection first = {0}, second = {0};
    first.id = 1;
    first.score = 90;
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_CONFIDENT;
    first.left = -0.5f; first.top = 0.5f; first.right = 0.5f; first.bottom = -0.5f;
    second = first;
    second.id = 2;
    VOD__Detection* detections[] = {&first, &second};

    // No delete event: retain the four-frame grace period, then emit one terminal object.
    reset();
    frame(detections, 1, NULL, 0);
    expect("1", true);
    for (int i = 1; i <= 4; ++i) {
        empty_frames(1);
        CHECK(observed_count == 1);
        expect("1", true);
    }
    empty_frames(1);
    CHECK(observed_count == 1);
    expect("1", false);
    CHECK(g_ctx.object_map == NULL);
    empty_frames(100);
    CHECK(observed_count == 0);

    // A new accepted observation restarts the grace period.
    reset();
    frame(detections, 1, NULL, 0);
    empty_frames(4);
    frame(detections, 1, NULL, 0);
    empty_frames(4);
    expect("1", true);
    empty_frames(1);
    expect("1", false);

    // A stationary object is still alive when fresh detections keep arriving.
    reset();
    for (int i = 0; i < 100; ++i) {
        frame(detections, 1, NULL, 0);
        expect("1", true);
    }

    // Explicit deletes remain immediate.
    VOD__Event deletion = {0};
    deletion.action = VOD__EVENT_ACTION__EVENT_DELETE;
    deletion.object_id = 1;
    VOD__Event* events[] = {&deletion};
    frame(NULL, 0, events, 1);
    expect("1", false);
    CHECK(g_ctx.object_map == NULL);

    // A delete for an id no longer cached (e.g. already expired) creates nothing.
    frame(NULL, 0, events, 1);
    CHECK(observed_count == 0 && g_ctx.object_map == NULL);

    // A connect ends the merged sources at once; the target continues.
    reset();
    frame(detections, 2, NULL, 0);
    int32_t sources[] = {1, 2};
    VOD__Event connection = {0};
    connection.action = VOD__EVENT_ACTION__EVENT_CONNECT;
    connection.object_id = 2;
    connection.n_object_ids = 2;
    connection.object_ids = sources;
    VOD__Event* connections[] = {&connection};
    frame(detections + 1, 1, connections, 1);
    expect("1", false);
    expect("2", true);
    frame(detections + 1, 1, NULL, 0);
    CHECK(observed_count == 1);
    expect("2", true);

    // One object's detections must not keep another missing object alive.
    reset();
    frame(detections, 2, NULL, 0);
    for (int i = 0; i < 5; ++i) frame(detections, 1, NULL, 0);
    expect("1", true);
    expect("2", false);
    frame(detections, 1, NULL, 0);
    CHECK(observed_count == 1);
    expect("1", true);

    // Rejected predictions and zero-score detections keep a listed track alive
    // without updating it; it expires once VOD stops listing it.
    reset();
    frame(detections, 1, NULL, 0);
    int x = observed[0].x;
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_UNCONFIDENT;
    first.left = 0.0f;
    for (int i = 0; i < 10; ++i) frame(detections, 1, NULL, 0);
    expect("1", true);
    CHECK(observed[0].x == x);
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_CONFIDENT;
    first.score = 0;
    for (int i = 0; i < 10; ++i) frame(detections, 1, NULL, 0);
    expect("1", true);
    CHECK(observed[0].x == x);
    first.score = 90;
    first.left = -0.5f;
    empty_frames(4);
    expect("1", true);
    empty_frames(1);
    expect("1", false);
    CHECK(g_ctx.object_map == NULL);

    // A listed track whose detections are all rejected ends after MAX_UNACCEPTED_SECONDS.
    reset();
    frame(detections, 1, NULL, 0);
    int64_t accepted_us = now_us;
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_UNCONFIDENT;
    while (now_us - accepted_us < (int64_t)(MAX_UNACCEPTED_SECONDS * 1000000) - 100000) {
        frame(detections, 1, NULL, 0);
        expect("1", true);
    }
    now_us += 50000;  // Past the limit, clear of rounding at exactly the limit
    frame(detections, 1, NULL, 0);
    expect("1", false);
    CHECK(g_ctx.object_map == NULL);
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_CONFIDENT;

    // A rejected detection does not create a track.
    reset();
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_UNCONFIDENT;
    frame(detections, 1, NULL, 0);
    CHECK(observed_count == 0 && g_ctx.object_map == NULL);
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_CONFIDENT;

    // Untracked detections carry no track id: they neither keep a track alive
    // nor create one, even with predictions enabled.
    reset();
    frame(detections, 1, NULL, 0);
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__UNTRACKED;
    for (int i = 1; i <= 4; ++i) {
        frame(detections, 1, NULL, 0);
        expect("1", true);
    }
    frame(detections, 1, NULL, 0);
    expect("1", false);
    reset();
    vod_predictions = 1;
    frame(detections, 1, NULL, 0);
    CHECK(observed_count == 0 && g_ctx.object_map == NULL);
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_CONFIDENT;

    // Predictions remain eligible when that option is enabled.
    reset();
    vod_predictions = 1;
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_UNCONFIDENT;
    for (int i = 0; i < 10; ++i) frame(detections, 1, NULL, 0);
    expect("1", true);
    first.detection_status = VOD__DETECTION__DETECTION_STATUS__TRACKED_CONFIDENT;

    // After a gap in scenes, ObjectDetection has already ended the cached objects,
    // so they are dropped rather than re-emitted. A shorter gap keeps them.
    reset();
    frame(detections, 1, NULL, 0);
    now_us += (int64_t)(SCENE_GAP_SECONDS * 1000000) - 200000;  // frame() adds 100 ms
    empty_frames(1);
    expect("1", true);
    now_us += (int64_t)(SCENE_GAP_SECONDS * 1000000);
    empty_frames(1);
    CHECK(observed_count == 0 && g_ctx.object_map == NULL);

    clear_object_map();
    puts("PASS: missing-frame expiry, recovery, stationary detections, deletes, connects, per-object expiry, "
         "unaccepted cap, untracked and prediction filtering, and scene gaps.");
    return 0;
}
''')
