"""Listed repeats keep a track alive. python tests/test_track_liveness.py"""
import re
from native_harness import APP, function, run

source = (APP / "ObjectDetection.c").read_text(encoding="utf-8-sig")
vod = function(source, "static void VOD_Data(")
reaper = function(source, "gboolean update_trackers(")
if "note_listed_repeat" not in vod or "tracker_is_stale" not in reaper:
    raise SystemExit("liveness helpers are not wired into VOD_Data and the reaper")
if "now - entry->timestamp" in reaper:
    raise SystemExit("reaper still keys off the observation timestamp")

end = source.index("} detection_cache_entry_t;") + len("} detection_cache_entry_t;")
struct = source[source.rfind("typedef struct {", 0, end):end]
attr_end = source.index("} od_attribute_t;") + len("} od_attribute_t;")
attr = source[source.rfind("typedef struct {", 0, attr_end):attr_end]
struct = attr + "\n" + struct
limit = re.search(r"^#define STALE_TRACKER_TIMEOUT_MS\s+\d+", source, re.M).group(0)
helpers = "\n".join(function(source, signature) for signature in (
    "static void note_listed_repeat(",
    "static int tracker_is_stale(",
))
run(r'''
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(condition) do { if (!(condition)) { fprintf(stderr, "Failed: %s at %d\n", #condition, __LINE__); exit(1); } } while (0)
''' + limit + "\n" + struct + "\n" + helpers + r'''
int main(void) {
    detection_cache_entry_t entry;
    memset(&entry, 0, sizeof(entry));
    entry.birthTime = 1000;
    entry.idle_start_time = 1000;
    entry.timestamp = 1000;
    entry.last_listed = 1000;

    note_listed_repeat(&entry, 3500);
    CHECK(entry.timestamp == 1000);
    CHECK(entry.last_listed == 3500);
    CHECK(entry.age == 2.5);
    CHECK(entry.idle);
    CHECK(entry.idle_start_time == 1000);
    CHECK(entry.idle_duration == 2.5);
    CHECK(entry.max_idle_duration == 2.5);
    CHECK(!tracker_is_stale(&entry, 3500));
    CHECK(!tracker_is_stale(&entry, 3500 + STALE_TRACKER_TIMEOUT_MS));
    CHECK(tracker_is_stale(&entry, 3500 + STALE_TRACKER_TIMEOUT_MS + 1));

    note_listed_repeat(&entry, 4500);
    CHECK(entry.timestamp == 1000);
    CHECK(entry.idle_start_time == 1000);
    CHECK(entry.idle_duration == 3.5);
    CHECK(entry.last_listed == 4500);

    puts("PASS: listed repeats keep timestamp and refresh liveness, age, and idle.");
    return 0;
}
''')
