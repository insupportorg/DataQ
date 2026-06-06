# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

DataQ is an **Axis ACAP** (camera-embedded) application written in C. It subscribes to a camera's on-device object detection/analytics, post-processes the data (filtering, tracking, path building, occupancy counting, anomaly detection, homography-to-GPS), and publishes the results over **MQTT**. A bundled web UI (FastCGI + static HTML/JS) configures it and visualizes the live MQTT stream. The same source builds two flavors: **DataQ** (cameras) and **DataQ_Radar** (Axis radar D2110/D2210).

The canonical reference for MQTT topics and payload shapes is [MQTT_topics.md](MQTT_topics.md). User-facing feature docs and the full changelog live in [README.md](README.md).

## Build

There is no local toolchain — the ACAP is cross-compiled inside the Axis SDK Docker image. The `.eap` package is the build artifact.

```sh
# Build both architectures (produces .eap files in repo root)
./build.sh
```

[build.sh](build.sh) builds aarch64 then armv7hf by invoking the [Dockerfile](Dockerfile), which runs `acap-build` against the SDK. To build a single arch manually:

```sh
docker build --build-arg ARCH=aarch64 --tag dataq .     # or ARCH=armv7hf
docker cp $(docker create dataq):/opt/app ./build       # extract the .eap
```

[app/Makefile](app/Makefile) is invoked *inside* the container by `acap-build`; the `PKGS` line lists the Axis SDK libraries linked (axevent, axparameter, vdo, video-object-detection-subscriber, fcgi, libcurl, glib). Adding a new `.c` file means adding it to `OBJS1` there. There is no test suite.

## Architecture

The whole app is a single binary running one GLib main loop ([app/main.c](app/main.c) `main()`). Everything is event/callback driven off that loop — there is no request thread per detection.

**Data flow (the spine of the app):**

```
Axis VOD engine ──► VOD.c ──► ObjectDetection.c ──► main.c callbacks ──► MQTT.c ──► broker
   (protobuf            (raw         (scene filters,        (paths, occupancy,
    over D-Bus           objects)     trackers, idle/age)    anomaly, geospace)
    socket)
```

- **[VOD.c](app/VOD.c)** — lowest layer. Subscribes to the Axis `VideoObjectDetection1` scene over D-Bus (declared in [manifest.json](app/manifest.json) `dbus.requiredMethods`), decodes protobuf (`video_object_detection.pb-c.c`, `protobuf-c.c`), and emits `vod_object_t` structs. Coordinates are normalized `[0..1000]`, top-left origin. Also exposes the label list.
- **[ObjectDetection.c](app/ObjectDetection.c)** — turns raw VOD objects into the app's data model. Applies the **scene** config (area-of-interest, min/max width/height, max idle, confidence, ignored classes, COG mode, perspective cutoff), maintains tracker state, and computes derived properties (age, idle, speed, direction, distance). Invokes two callbacks registered by main: a **detections** callback (full current list, every frame) and a **tracker** callback (per-object movement updates).
- **[main.c](app/main.c)** — the orchestrator. It owns all the "what to publish" logic and the `publish*` global flags (toggled by the `publish` settings service). Key responsibilities:
  - `Detections_Data()` → publishes `detections/<serial>` and runs `ProcessOccupancy()`.
  - `Tracker_Data()` → runs anomaly checks, builds/updates paths (`ProcessPaths`), runs geospace transform, publishes `tracker/<serial>` and `geospace/<serial>`.
  - **Paths**: `ProcessPaths()` accumulates per-object position samples in `PathCache`; on object exit the finalized path is handed to `Stitch_Path()` and ultimately `Publish_Path()` → `path/<serial>`.
  - **Occupancy** (multi-area): `g_areas[]` holds up to `MAX_AREAS` polygon zones loaded by `Occupancy_Load_Areas()`. Two modes — *on_change* (immediate publish with `apply_hold_down()` debounce on decreases) and *periodic* (GLib timer averages accumulated samples). Topic is `occupancy/<serial>/<area>`, or `occupancy/<serial>` for the whole-frame fallback when no areas are defined.
- **[MQTT.c](app/MQTT.c)** — async Paho MQTT client (`MQTTAsync`). `MQTT_Publish_JSON()` is the universal publish path; it auto-injects `serial`, `name`, `location`, and timestamps into every payload, so callers only build the domain fields. Handles reconnect, LWT, and retained connect/disconnect announcements. TLS client certs are managed by **[CERTS.c](app/CERTS.c)**.
- **[GeoSpace.c](app/GeoSpace.c)** — homography. `GeoSpace_transform(x, y, &lat, &lon)` maps image coordinates to GPS using a 3×3 matrix computed from calibration markers. Linear algebra (double precision) comes from the vendored **[linmatrix/](app/linmatrix/)** library. The matrix is persisted in `settings.json` under `markers`/`matrix`.
- **[Stitch.c](app/Stitch.c)** — merges path segments of the same physical object across brief occlusions (the temporary `t` epoch field on path points exists only for stitch matching and is stripped before publish).
- **[ACAP.c](app/ACAP.c) / [ACAP.h](app/ACAP.h)** — the SDK wrapper underpinning everything: HTTP endpoint registration, the settings/config store (`ACAP_Get_Config`/`ACAP_Set_Config`), the runtime status store (`ACAP_STATUS_*`, surfaced at the `/status` endpoint), event declare/fire/subscribe, device properties, VAPIX calls, and file I/O. **Read the memory-ownership header comment in ACAP.h before touching this** — getters return internally-managed pointers you must NOT free; `ACAP_FILE_Read`, `ACAP_VAPIX_*`, and `ACAP_HTTP_Request_Param` return memory you MUST free.

**Configuration & web UI:** Settings are JSON "services" (`publish`, `scene`, `occupancy`, `stitch`, `matrix`, …) defined in [app/settings/settings.json](app/settings/settings.json) and loaded into the config store at startup. A single `Settings_Updated_Callback()` in main.c dispatches POSTed changes by service name to the relevant subsystem. The UI in [app/html/](app/html/) is plain HTML + jQuery; each page POSTs to the FastCGI endpoints declared in [manifest.json](app/manifest.json) (`app`, `settings`, `status`, `mqtt`, `certs`, `objectdetections`, `geospace`) and renders live data by subscribing to MQTT over WebSockets (paho-mqtt) directly in the browser — i.e. all video overlays are reconstructed from the same MQTT payloads consumers receive.

## Conventions specific to this codebase

- **cJSON everywhere.** All inter-module data is `cJSON*`. The detection/tracker callbacks transfer ownership: the receiver must `cJSON_Delete()` the object (see the note in ObjectDetection.h). Mind the ACAP ownership rules above; mismatches here are the most common source of leaks/double-frees.
- **Coordinates are `[0..1000]`, top-left origin**, aspect-ratio independent. Convert only at the edges (e.g. polygon hit-testing, geospace).
- **Adding a published data type** typically means: a `publish.<name>` flag in settings.json → a global in main.c set by `Settings_Updated_Callback` → a publish call building a payload and calling `MQTT_Publish_JSON(topic, payload, qos, retained)`. Keep `MQTT_topics.md` in sync.
- **`manifest.json` version** is the released app version (currently 3.2.0) and must be bumped for releases; `ACAP_VERSION` in ACAP.h is the unrelated SDK-wrapper version. New D-Bus methods or HTTP endpoints must be declared in manifest.json or they will be denied at runtime.
- **Vendored third-party code** (`cJSON.*`, `protobuf-c.*`, `video_object_detection.pb-c.*`, `linmatrix/`, the `MQTT*.h` Paho headers) is not ours — avoid reformatting or "improving" it.
