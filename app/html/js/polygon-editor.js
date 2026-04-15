/**
 * polygon-editor.js
 * Reusable polygon draw/edit overlay for a <canvas> sitting on top of video.
 *
 * Canvas coordinate space: 0–1000 × 0–1000 (matches detection pipeline).
 *
 * Usage:
 *   const editor = new PolygonEditor(canvasEl, {
 *       colors:    ['#e74c3c', '#3498db', ...],   // optional, defaults provided
 *       hintEl:    document.getElementById('hint'), // optional hint text element
 *       onChanged: (polygons) => { ... }            // called after every edit
 *   });
 *
 *   editor.setPolygons([
 *       { id: 1, name: 'Zone A', active: true, polygon: [{x,y}, ...] },
 *       ...
 *   ]);
 *   editor.startDraw();    // enter draw mode
 *   editor.cancelDraw();   // cancel draw / deselect
 *   editor.redraw(extraDrawFn); // re-render; optional extraDrawFn(ctx) runs first
 *
 * The host page is responsible for:
 *   - Sizing + positioning the canvas absolutely over the video div
 *   - Calling editor.redraw() whenever it wants to repaint its own overlays
 *     (pass a function that draws those overlays onto ctx before polygons)
 */

'use strict';

const PolygonEditor = (function () {

    /* ── Constants ─────────────────────────────────────────────────── */
    const DEFAULT_COLORS = [
        '#e74c3c', '#3498db', '#2ecc71', '#f39c12',
        '#9b59b6', '#1abc9c', '#e67e22', '#34495e'
    ];
    const VERTEX_R   = 15;   /* hit-test + draw radius in canvas units */
    const EDGE_HIT_R = 10;

    /* ── Geometry helpers ───────────────────────────────────────────── */
    function hexToRgba(hex, alpha) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    function ptSegDist(px, py, ax, ay, bx, by) {
        const dx = bx - ax, dy = by - ay;
        const lenSq = dx * dx + dy * dy;
        if (lenSq === 0) return Math.hypot(px - ax, py - ay);
        const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
        return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
    }

    function hitVertex(poly, px, py) {
        for (let i = 0; i < poly.length; i++)
            if (Math.hypot(poly[i].x - px, poly[i].y - py) <= VERTEX_R) return i;
        return -1;
    }

    function hitEdge(poly, px, py) {
        for (let i = 0; i < poly.length; i++) {
            const a = poly[i], b = poly[(i + 1) % poly.length];
            if (ptSegDist(px, py, a.x, a.y, b.x, b.y) <= EDGE_HIT_R) return i;
        }
        return -1;
    }

    function pointInPoly(poly, px, py) {
        let inside = false;
        for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
            const xi = poly[i].x, yi = poly[i].y, xj = poly[j].x, yj = poly[j].y;
            if (((yi > py) !== (yj > py)) &&
                (px < (xj - xi) * (py - yi) / (yj - yi) + xi))
                inside = !inside;
        }
        return inside;
    }

    function polygonCentroid(pts) {
        let sx = 0, sy = 0;
        for (const p of pts) { sx += p.x; sy += p.y; }
        return { x: sx / pts.length, y: sy / pts.length };
    }

    function canvasCoords(canvas, evt) {
        const rect = canvas.getBoundingClientRect();
        return {
            x: Math.round((evt.clientX - rect.left) * 1000 / rect.width),
            y: Math.round((evt.clientY - rect.top)  * 1000 / rect.height)
        };
    }

    /* ── Class ──────────────────────────────────────────────────────── */
    class PolygonEditor {
        /**
         * @param {HTMLCanvasElement} canvas
         * @param {object} opts
         * @param {string[]}  [opts.colors]
         * @param {HTMLElement} [opts.hintEl]
         * @param {function}  [opts.onChanged]  called with (polygons[]) after each edit
         * @param {function}  [opts.onCommit]   called with (newPolygon) after draw commit
         *                                       host should add it to its array then
         *                                       call setPolygons() + redraw()
         */
        constructor(canvas, opts = {}) {
            this._canvas     = canvas;
            this._ctx        = canvas.getContext('2d');
            this._colors     = opts.colors    || DEFAULT_COLORS;
            this._hintEl     = opts.hintEl    || null;
            this._onChanged  = opts.onChanged || null;
            this._onCommit   = opts.onCommit  || null;

            /* Polygon list — shallow copies of host objects */
            this._polygons   = [];  /* [{id, name, active, polygon:[{x,y}]}] */

            /* Draw-mode state */
            this._drawMode   = false;
            this._drawPoints = [];

            /* Edit state */
            this._editIdx    = -1;  /* index in _polygons currently selected */
            this._dragVertex = -1;
            this._dragPoly   = false;
            this._dragLastX  = 0;
            this._dragLastY  = 0;
            this._isDragging = false;

            /* Mouse tracking */
            this._mx         = 0;
            this._my         = 0;
            this._shiftKey   = false;

            /* Extra draw callback supplied by the host on each redraw */
            this._extraDraw  = null;

            this._attachEvents();
        }

        /* ── Public API ─────────────────────────────────────────────── */

        /**
         * Replace the polygon list. Pass the same array objects the host uses —
         * the editor mutates them in-place during drag/edit.
         */
        setPolygons(arr) {
            this._polygons = arr || [];
            /* Clamp selected index in case array shrank */
            if (this._editIdx >= this._polygons.length) this._editIdx = -1;
            this._updateHint();
        }

        getPolygons() { return this._polygons; }

        /** Enter polygon draw mode. */
        startDraw() {
            this._editIdx   = -1;
            this._drawMode  = true;
            this._drawPoints = [];
            this._canvas.classList.add('draw-mode');
            this._updateHint();
        }

        /** Cancel draw or deselect. */
        cancelDraw() {
            this._drawMode   = false;
            this._drawPoints = [];
            this._editIdx    = -1;
            this._canvas.classList.remove('draw-mode');
            this._updateHint();
            this.redraw();
        }

        /**
         * Render all polygons + any in-progress drawing onto the canvas.
         * @param {function} [extraFn]  called as extraFn(ctx) before polygons —
         *                              use it to draw detection boxes etc.
         */
        redraw(extraFn) {
            if (extraFn) this._extraDraw = extraFn;
            const ctx = this._ctx;
            ctx.clearRect(0, 0, 1000, 1000);

            /* Host-supplied background layer (e.g. detection boxes) */
            if (this._extraDraw) this._extraDraw(ctx);

            /* Committed polygons */
            this._polygons.forEach((area, idx) => {
                if (!area.polygon || area.polygon.length < 3) return;
                const color    = this._colors[idx % this._colors.length];
                const selected = (idx === this._editIdx);

                ctx.beginPath();
                ctx.moveTo(area.polygon[0].x, area.polygon[0].y);
                for (let i = 1; i < area.polygon.length; i++)
                    ctx.lineTo(area.polygon[i].x, area.polygon[i].y);
                ctx.closePath();
                ctx.fillStyle = hexToRgba(color, area.active ? 0.20 : 0.07);
                ctx.fill();

                if (selected) {
                    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
                    ctx.lineWidth   = 6;
                    ctx.stroke();
                }
                ctx.strokeStyle = color;
                ctx.lineWidth   = selected ? 2.5 : 2;
                ctx.stroke();

                const c = polygonCentroid(area.polygon);
                ctx.font         = 'bold 28px sans-serif';
                ctx.fillStyle    = color;
                ctx.textAlign    = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(area.name, c.x, c.y);

                if (selected) {
                    const hovV = hitVertex(area.polygon, this._mx, this._my);
                    const hovE = (this._shiftKey && hovV < 0)
                                 ? hitEdge(area.polygon, this._mx, this._my) : -1;

                    area.polygon.forEach((pt, vi) => {
                        const isHov = (vi === hovV);
                        ctx.beginPath();
                        ctx.arc(pt.x, pt.y, isHov ? VERTEX_R + 4 : VERTEX_R, 0, Math.PI * 2);
                        ctx.fillStyle   = isHov ? '#ffffff' : hexToRgba(color, 0.85);
                        ctx.strokeStyle = isHov ? color : 'rgba(255,255,255,0.9)';
                        ctx.lineWidth   = 2;
                        ctx.fill();
                        ctx.stroke();
                    });

                    if (hovE >= 0) {
                        ctx.beginPath();
                        ctx.arc(this._mx, this._my, VERTEX_R, 0, Math.PI * 2);
                        ctx.fillStyle   = '#00ff88';
                        ctx.strokeStyle = 'rgba(255,255,255,0.9)';
                        ctx.lineWidth   = 2;
                        ctx.fill();
                        ctx.stroke();
                    }
                }
            });

            /* In-progress polygon */
            if (this._drawMode && this._drawPoints.length > 0) {
                ctx.setLineDash([6, 4]);
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth   = 2;
                ctx.beginPath();
                ctx.moveTo(this._drawPoints[0].x, this._drawPoints[0].y);
                for (let i = 1; i < this._drawPoints.length; i++)
                    ctx.lineTo(this._drawPoints[i].x, this._drawPoints[i].y);
                ctx.lineTo(this._mx, this._my);
                ctx.stroke();
                ctx.setLineDash([]);

                this._drawPoints.forEach((p, i) => {
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, i === 0 ? 8 : 5, 0, Math.PI * 2);
                    ctx.fillStyle   = i === 0 ? '#00ff88' : '#ffffff';
                    ctx.strokeStyle = '#333';
                    ctx.lineWidth   = 1.5;
                    ctx.fill();
                    ctx.stroke();
                });
            }
        }

        /** Returns the color for a given polygon index. */
        colorFor(idx) { return this._colors[idx % this._colors.length]; }

        /** Currently selected polygon index (-1 = none). */
        get selectedIndex() { return this._editIdx; }

        /** Deselect without cancelling draw mode. */
        deselect() {
            this._editIdx = -1;
            this._updateHint();
            this.redraw();
        }

        /* ── Private helpers ────────────────────────────────────────── */

        _getCursor(px, py, shiftKey) {
            if (this._drawMode) return 'crosshair';
            if (this._editIdx >= 0 && this._polygons[this._editIdx]) {
                const poly = this._polygons[this._editIdx].polygon || [];
                if (poly.length && hitVertex(poly, px, py) >= 0)           return 'grab';
                if (shiftKey && poly.length && hitEdge(poly, px, py) >= 0) return 'copy';
                if (poly.length && pointInPoly(poly, px, py))              return 'move';
                return 'default';
            }
            for (const area of this._polygons)
                if (area.polygon && area.polygon.length >= 3 &&
                    pointInPoly(area.polygon, px, py)) return 'pointer';
            return 'default';
        }

        _updateHint() {
            if (!this._hintEl) return;
            if (this._drawMode) {
                this._hintEl.style.display = '';
                this._hintEl.textContent   =
                    'Click to place vertices · Click first point (green) or double-click to close · Esc to cancel';
            } else if (this._editIdx >= 0) {
                this._hintEl.style.display = '';
                this._hintEl.textContent   =
                    'Drag vertex · Shift+click edge to add vertex · Right-click vertex to remove · Drag inside to move · Click outside or Esc to deselect';
            } else {
                this._hintEl.style.display = 'none';
            }
        }

        _commit() {
            if (this._drawPoints.length < 3) {
                alert('A polygon needs at least 3 points.');
                return;
            }
            const newPoly = this._drawPoints.map(p => ({ x: p.x, y: p.y }));
            this._drawMode   = false;
            this._drawPoints = [];
            this._canvas.classList.remove('draw-mode');
            this._updateHint();
            /* Delegate to host — host creates the area object and calls setPolygons() */
            if (this._onCommit) this._onCommit(newPoly);
        }

        _notify() {
            if (this._onChanged) this._onChanged(this._polygons);
        }

        /* ── Event wiring ───────────────────────────────────────────── */
        _attachEvents() {
            const canvas = this._canvas;

            /* Right-click: remove vertex */
            canvas.addEventListener('contextmenu', evt => {
                evt.preventDefault();
                if (this._drawMode || this._editIdx < 0) return;
                const p    = canvasCoords(canvas, evt);
                const area = this._polygons[this._editIdx];
                if (!area) return;
                const vi = hitVertex(area.polygon, p.x, p.y);
                if (vi < 0) return;
                if (area.polygon.length <= 3) {
                    alert('A polygon must have at least 3 vertices.');
                    return;
                }
                area.polygon.splice(vi, 1);
                this.redraw();
                this._notify();
            });

            /* Mouse move */
            canvas.addEventListener('mousemove', evt => {
                const p = canvasCoords(canvas, evt);
                this._mx = p.x; this._my = p.y; this._shiftKey = evt.shiftKey;

                if (this._dragVertex >= 0 && this._editIdx >= 0) {
                    const area = this._polygons[this._editIdx];
                    if (area && area.polygon[this._dragVertex]) {
                        area.polygon[this._dragVertex] = { x: p.x, y: p.y };
                        this._isDragging = true;
                    }
                } else if (this._dragPoly && this._editIdx >= 0) {
                    const area = this._polygons[this._editIdx];
                    if (area) {
                        const dx = p.x - this._dragLastX, dy = p.y - this._dragLastY;
                        area.polygon.forEach(pt => { pt.x += dx; pt.y += dy; });
                        this._isDragging = true;
                    }
                    this._dragLastX = p.x; this._dragLastY = p.y;
                }

                canvas.style.cursor = this._getCursor(p.x, p.y, evt.shiftKey);
                this.redraw();
            });

            /* Mouse down */
            canvas.addEventListener('mousedown', evt => {
                if (evt.button !== 0) return;
                const p = canvasCoords(canvas, evt);
                this._isDragging = false;
                if (this._drawMode) return;

                if (this._editIdx >= 0) {
                    const area = this._polygons[this._editIdx];
                    if (area && area.polygon) {
                        const vi = hitVertex(area.polygon, p.x, p.y);
                        if (vi >= 0) { this._dragVertex = vi; return; }

                        if (evt.shiftKey) {
                            const ei = hitEdge(area.polygon, p.x, p.y);
                            if (ei >= 0) {
                                area.polygon.splice(ei + 1, 0, { x: p.x, y: p.y });
                                this._dragVertex = ei + 1;
                                this._isDragging = true;
                                this.redraw();
                                return;
                            }
                        }

                        if (pointInPoly(area.polygon, p.x, p.y)) {
                            this._dragPoly  = true;
                            this._dragLastX = p.x; this._dragLastY = p.y;
                            return;
                        }
                    }
                    this._editIdx = -1;
                    this._updateHint();
                    canvas.style.cursor = 'default';
                    this.redraw();
                }

                for (let i = 0; i < this._polygons.length; i++) {
                    const area = this._polygons[i];
                    if (area.polygon && area.polygon.length >= 3 &&
                        pointInPoly(area.polygon, p.x, p.y)) {
                        this._editIdx = i;
                        this._updateHint();
                        this.redraw();
                        return;
                    }
                }
            });

            /* Mouse up */
            canvas.addEventListener('mouseup', evt => {
                if (evt.button !== 0) return;
                const wasDragging   = this._isDragging;
                this._dragVertex    = -1;
                this._dragPoly      = false;
                this._isDragging    = false;
                if (wasDragging) this._notify();
            });

            /* Click: draw-mode vertex placement */
            canvas.addEventListener('click', evt => {
                if (!this._drawMode) return;
                const p = canvasCoords(canvas, evt);
                if (this._drawPoints.length >= 3) {
                    const fp = this._drawPoints[0];
                    if (Math.hypot(p.x - fp.x, p.y - fp.y) < 20) { this._commit(); return; }
                }
                this._drawPoints.push(p);
                this.redraw();
            });

            /* Double-click: commit polygon */
            canvas.addEventListener('dblclick', () => {
                if (!this._drawMode) return;
                this._drawPoints.pop(); /* second click already added a point */
                this._commit();
            });

            /* Escape */
            document.addEventListener('keydown', evt => {
                if (evt.key !== 'Escape') return;
                if (this._drawMode) {
                    this._drawMode = false; this._drawPoints = [];
                    this._canvas.classList.remove('draw-mode');
                    this._updateHint(); canvas.style.cursor = 'default'; this.redraw();
                    return;
                }
                if (this._editIdx >= 0) {
                    this._editIdx = -1;
                    this._updateHint(); canvas.style.cursor = 'default'; this.redraw();
                }
            });
        }
    }

    return PolygonEditor;
})();
