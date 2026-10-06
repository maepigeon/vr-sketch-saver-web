# vr-sketch-saver

Draw freehand in VR with the [vrscaffolding](https://github.com/yig/vrscaffolding) sketcher, with **Save** and
**Undo** buttons, and get your strokes as OBJ polylines in the same layout as `sketch.obj`:

```
v x y z            <- all vertices first, one per line
...
l 1 2 3 ... 100    <- then one line per stroke, 1-based vertex indices
...
```

The two folders sit side by side. vrscaffolding is used as-is and never changed:

```
other/
  vrscaffolding/      <- unchanged
  vr-sketch-saver/    <- this folder
```

---

## Step by step

### 1. One-time setup (computer)

```bash
pip3 install "numpy<2" scipy websockets
```

```bash
brew install --cask android-platform-tools
```

(vrscaffolding's line fitting uses a function that NumPy 2 removed, so NumPy has to stay below 2.)

Check that everything works before putting the headset on. This should print `OK`:

```bash
cd ~/workspace/other/vr-sketch-saver && python3 test_export.py
```

### 2. One-time setup (Quest)

1. Turn on Developer Mode for the headset (in the Meta Horizon phone app: Devices > Headset settings > Developer mode).
2. Plug the Quest into the computer with USB. In the headset, accept "Allow USB debugging" (tick "always allow").
3. Check that the computer sees it. This should list one device:

```bash
adb devices
```

### 3. Every session: start the server

**Terminal 1** (leave it running while you draw; it doesn't return to the prompt until you press Ctrl+C):

```bash
cd ~/workspace/other/vr-sketch-saver && python3 sketch_server.py
```

That one command replaces both `python3 -m http.server` and `python3 ping.py` from vrscaffolding's
instructions. Don't run those as well.

**Terminal 2** (a second tab): let the headset reach the server. Run these each time you plug the Quest in:

```bash
adb reverse tcp:8000 tcp:8000
```

```bash
adb reverse tcp:9001 tcp:9001
```

The sketch connection uses port **9001**, not vrscaffolding's 9000, because the Quest 3 refuses
`adb reverse tcp:9000` ("cannot bind listener: Permission denied").

### 4. Draw in VR

1. In the Quest's browser, open `http://localhost:8000/paint.html`.
2. Press **Enter VR**.
3. Controls:

| Button | What it does |
| --- | --- |
| **Trigger** (either hand) | Hold and move to draw a stroke. It stays exactly as you drew it. |
| **Grip** (left, labelled "undo") | Undo the last stroke. Press again to keep undoing. |
| **X** (left, labelled "X: save") | Save the sketch to a new file in `vr-sketch-saver/exports/`. A message above the left controller shows the file name, or why nothing was saved. |
| **Grip** (right) + move up/down | Change the brush thickness (only affects how strokes look). |

Terminal 1 logs every stroke, undo and save, e.g. `[::1:51234] 12 strokes`.

**Scaffold mode (optional).** `http://localhost:8000/paint.html?mode=scaffold` gives you vrscaffolding's original
behavior instead: strokes are straightened into yellow construction lines, and **A** (right) switches to shape mode,
where a stroke becomes a white curve snapped through the endpoints and midpoints of those lines. **B** (right) hides
or shows the yellow lines. Saving still saves your strokes as drawn unless you pick other layers (see below).

### 5. Export the sketch from the computer (any time)

Pressing **X** in VR is the quickest way. You can also export from a terminal while drawing or afterwards:

```bash
cd ~/workspace/other/vr-sketch-saver && python3 export_sketch.py my_sketch.obj
```

Leave out the file name to get `sketch-<date>-<time>.obj`. Each export is a snapshot of the sketch at
that moment, so export as often as you like.

**What gets saved by default** (both for X in VR and for `export_sketch.py`):
- every stroke **exactly as you drew it** (undone strokes and single-point taps left out);
- each resampled to **100 points**, evenly spaced along the stroke;
- the whole sketch **centered on the origin and scaled so its largest side is 1**.

To change that, add options to `export_sketch.py`. The same options on `sketch_server.py` change what
the X button saves.

```bash
python3 export_sketch.py my_sketch.obj --coords world --points 0
```

| Option | Meaning |
| --- | --- |
| `--layers raw` | Which strokes to save, comma separated: `raw` (strokes exactly as drawn), and from scaffold mode only, `shapes` (white fitted curves) and `scaffold` (yellow lines). |
| `--points 100` | Points per stroke. `0` keeps every point the controller recorded (about one per frame). |
| `--coords fit` | `fit`: centered, largest side 1. `center`: centered, original size in meters. `world`: VR coordinates as drawn (meters, y up, floor at y = 0). |
| `--port 9001` | Where `sketch_server.py` is listening, if you changed it. |

### 6. Finish

Press Ctrl+C in Terminal 1. **The sketch lives only in the running server**, so save before you stop it.

---

## Good to know

- **Starting a new sketch:** reload `paint.html`. The page and a new session both start empty. Until you draw in
  the new session, `export_sketch.py` still exports the previous sketch.
- **Quick trigger taps** are ignored. (In scaffold mode the server logs `Skipped construction-stroke ...` and carries
  on; with the original `ping.py`, a tap closed the connection and the sketch was lost.)
- **Trying it without the headset:** open `http://localhost:8000/paint.html` on the computer. Drawing needs VR,
  but the page connects, and Ctrl+Z / Ctrl+S (Cmd on a Mac) send undo / save.
- **"not saved: no connection to sketch_server.py"** in VR: the page can't reach the websocket. Check that Terminal 1
  is still running and that `adb reverse --list` shows both `tcp:8000` and `tcp:9001`; then reload the page.
- **"Could not reach the sketch server"** when exporting: Terminal 1 isn't running.
- **"Nothing has been drawn since the server started"**: no strokes reached the server. Check the
  `adb reverse` commands, and that the headset opened the page from `localhost:8000`.
- **Server says a port is in use:** an old `python3 -m http.server` or `ping.py` is still running. Stop it, or
  move the server with `--http-port` / `--port` (`paint.html?port=9002` points the page at another websocket port; `adb reverse` that port too).
- **vrscaffolding somewhere else:** `python3 sketch_server.py --vrscaffolding /path/to/vrscaffolding/threejs`
  (or set `VRSCAFFOLDING`).

## Reading the files from another program

`read_obj()` in `obj_polylines.py` is a short reference reader. Collect the `v` lines into a vertex list;
each `l` line is one stroke, listing 1-based indices into that list. Files contain only `v` and `l` lines,
written exactly like `sketch.obj` (the test checks that re-writing `sketch.obj` gives an identical file).

## Files

- `sketch_server.py`: serves the VR page, keeps the sketch, and handles undo and save. In scaffold mode it replaces
  `threejs/ping.py`, fitting strokes with vrscaffolding's code.
- `web/paint.html`: the VR page, a copy of `vrscaffolding/threejs/paint.html` with freehand drawing, save and undo
  added (see below).
  Everything else the page loads (three.js, fonts, CSS) is served straight from vrscaffolding.
- `export_sketch.py`: the export command.
- `sketch_export.py`: export options shared by the command and the X button.
- `obj_polylines.py`: OBJ polyline writer and reader, plus resampling.
- `test_export.py`: end-to-end test of the server, undo, save, and export.
- `exports/`: where the X button saves (created on the first save).

## Changes to vrscaffolding

**None.** vrscaffolding's Python is imported unchanged (`threejs/scaffold_sketch.py`, `fit_line.py`,
`fit_curve.py`), and the server turns off `.pyc` writing so nothing is added to that checkout.

`web/paint.html` is a separate copy. Compared with `vrscaffolding/threejs/paint.html`
(`diff ../vrscaffolding/threejs/paint.html web/paint.html`), it:

- draws freehand unless the URL has `?mode=scaffold`: each finished stroke becomes its own tube (so undo can remove
  it) and is sent as `freehand-stroke` instead of being fitted. In freehand mode, A does nothing;
- makes the left grip (already labelled "undo") send `undo`, and removes the matching drawing on reply. The left
  grip no longer resizes the left brush, so resizing doesn't undo by accident;
- adds the left X button and an "X: save" label; it sends `save-sketch`;
- shows replies ("saved ... / not saved: ... / undid ...") above the left controller and on the page;
- adds Ctrl/Cmd+Z and Ctrl/Cmd+S for trying it on a computer;
- connects to websocket port 9001 instead of 9000 by default (the Quest 3 won't `adb reverse` 9000);
- guards two first-frame crashes: the "undo" label used before its font loaded, and button checks before the
  controller's previous state exists.

New messages on the websocket, in addition to `ping.py`'s `construction-stroke` / `shape-stroke`:
`freehand-stroke [points]` (no reply); `undo` → `undone {"layer": ...}` or `nothing-to-undo {}`; `save-sketch` → `saved {"file", "strokes"}` or
`save-failed {"message"}`; `get-sketch` (used by `export_sketch.py`) → `sketch {...}`.
